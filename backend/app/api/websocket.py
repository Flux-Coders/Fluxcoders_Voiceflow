"""VoiceFlow Realtime WebSocket Controller.

Manages bidirectional event, control, state sync, and Rime TTS audio streaming
between the browser client and the authoritative VoiceFlow Session orchestrator.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.session import Session, SessionManager
from app.engine.llm_orchestrator import LLMOrchestrator
from app.engine.llm_provider import create_llm_client
from app.models import RequestStatus, ToolStatus, TurnExecutionResult
from app.tools.registry import default_tool_registry

logger = logging.getLogger(__name__)

router = APIRouter()


class WebSocketConnectionManager:
    """Manages active WebSocket sessions and provides robust, non-blocking message broadcasting."""

    def __init__(self) -> None:
        self.active_connections: Dict[str, list[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    async def send_json(self, session_id: str, data: Dict[str, Any]) -> None:
        """Broadcasts JSON payload to all active connections in a session with independent delivery."""
        sockets = list(self.active_connections.get(session_id, []))
        dead_sockets: list[WebSocket] = []

        for ws in sockets:
            try:
                # Bounded timeout prevents single blocked connection from freezing session broadcast
                await asyncio.wait_for(ws.send_json(data), timeout=2.0)
            except Exception as e:
                logger.debug("Failed delivering message to websocket in session %s: %s", session_id, e)
                dead_sockets.append(ws)

        # Prune any disconnected or dead sockets
        for dead_ws in dead_sockets:
            self.disconnect(session_id, dead_ws)

    async def broadcast_state_sync(
        self,
        session: Session,
        agent_status: str = "idle",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        state_snap = session.state_mgr.get_snapshot()
        state_dict = state_snap.model_dump() if hasattr(state_snap, "model_dump") else state_snap
        metrics_snap = session.metrics.get_snapshot()
        metrics_dict = metrics_snap.model_dump() if hasattr(metrics_snap, "model_dump") else metrics_snap

        payload: Dict[str, Any] = {
            "type": "STATE_SYNC",
            "session_id": session.session_id,
            "active_version": session.active_version,
            "active_request_id": session.active_request_id,
            "agent_status": agent_status,
            "slots": session.state_mgr.state.slots,
            "current_answer": session.current_answer,
            "stale_discards_count": len(session.stale_discards),
            "state": state_dict,
            "metrics": metrics_dict,
        }
        if extra:
            payload.update(extra)
        await self.send_json(session.session_id, payload)


ws_manager = WebSocketConnectionManager()


@router.websocket("/ws/session/{session_id}")
async def session_websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
) -> None:
    """Realtime WebSocket endpoint coordinating voice events, state sync, and Rime TTS chunks."""
    from app.main import session_manager

    session = session_manager.get_or_create_session(session_id=session_id)
    await ws_manager.connect(session_id, websocket)

    # Initial state sync on connection
    await ws_manager.broadcast_state_sync(session=session, agent_status="idle")

    # Instantiate LLM orchestrator for this session
    llm_client = create_llm_client()
    orchestrator = LLMOrchestrator(llm_client=llm_client, tool_registry=default_tool_registry)

    # Background task tracker for active turn processing
    turn_task: Optional[asyncio.Task[None]] = None

    async def handle_turn_execution(prompt: str) -> None:
        nonlocal session
        # 1. Advance turn & create new request
        req = session.create_request(prompt=prompt)
        req_id = req.request_id
        version = req.conversation_version

        await ws_manager.broadcast_state_sync(
            session=session,
            agent_status="thinking",
            extra={"prompt": prompt, "request_id": req_id, "version": version},
        )

        try:
            # 2. Process turn via LLM orchestrator (with intermediate thinking/tool status)
            result: TurnExecutionResult = await orchestrator.process_turn(
                session=session,
                request_id=req_id,
                version=version,
                prompt=prompt,
                trigger_rime=False,  # We stream Rime audio directly through WebSocket
            )

            # Check if this request is still active
            if session.active_version != version or result.is_stale:
                await ws_manager.send_json(
                    session_id,
                    {
                        "type": "STALE_DISCARD_EVENT",
                        "request_id": req_id,
                        "version": version,
                        "reason": result.error or "Request became obsolete before completion",
                    },
                )
                await ws_manager.broadcast_state_sync(session=session, agent_status="idle")
                return

            if result.success and result.assistant_response:
                # 3. Stream Rime TTS chunks to client
                await ws_manager.broadcast_state_sync(
                    session=session,
                    agent_status="speaking",
                    extra={"assistant_response": result.assistant_response},
                )

                try:
                    token = session.task_registry.get_token(req_id)
                    chunk_idx = 0
                    async for chunk in session.rime_gate.stream_synthesize(
                        text=result.assistant_response,
                        request_id=req_id,
                        version=version,
                        session_id=session_id,
                        state_mgr=session.state_mgr,
                        cancellation_token=token,
                        on_stale_discard=session.record_stale_discard,
                    ):
                        # Verify version is still active before sending chunk
                        if session.active_version != version or (token and token.is_cancelled):
                            break

                        audio_b64 = base64.b64encode(chunk.audio_bytes).decode("ascii")
                        await ws_manager.send_json(
                            session_id,
                            {
                                "type": "RIME_AUDIO_CHUNK",
                                "request_id": req_id,
                                "version": version,
                                "chunk_index": chunk_idx,
                                "audio_base64": audio_b64,
                                "is_final": chunk.is_final,
                            },
                        )
                        chunk_idx += 1
                except Exception as tts_err:
                    logger.warning("Rime TTS stream error: %s", tts_err)

            # Turn completed normally
            await ws_manager.broadcast_state_sync(session=session, agent_status="idle")

        except asyncio.CancelledError:
            logger.info("Turn task cancelled for request %s (v%d)", req_id, version)
        except Exception as err:
            logger.error("Error executing turn: %s", err)
            await ws_manager.broadcast_state_sync(session=session, agent_status="error", extra={"error": str(err)})

    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                msg = json.loads(raw_msg)
            except Exception:
                continue

            msg_type = msg.get("type", "").upper()

            # -------------------------------------------------------------
            # Event 1: SPEECH_STARTED / CLIENT_INTERRUPT (Barge-In)
            # -------------------------------------------------------------
            if msg_type in ("SPEECH_STARTED", "CLIENT_INTERRUPT"):
                reason = msg.get("reason", "User voice detected")
                interrupt_result = session.interrupt(reason=reason)

                # Cancel active backend turn task immediately
                if turn_task and not turn_task.done():
                    turn_task.cancel()

                await ws_manager.send_json(
                    session_id,
                    {
                        "type": "INTERRUPT_ACKNOWLEDGED",
                        "session_id": session_id,
                        "active_version": session.active_version,
                        **interrupt_result,
                    },
                )
                await ws_manager.broadcast_state_sync(session=session, agent_status="listening")

            # -------------------------------------------------------------
            # Event 2: INTERIM_TRANSCRIPT (Live Visual Feedback Only)
            # -------------------------------------------------------------
            elif msg_type == "INTERIM_TRANSCRIPT":
                text = msg.get("text", "").strip()
                if text:
                    await ws_manager.send_json(
                        session_id,
                        {
                            "type": "TRANSCRIPT_INTERIM",
                            "text": text,
                            "active_version": session.active_version,
                        },
                    )

            # -------------------------------------------------------------
            # Event 3: FINAL_TRANSCRIPT (Commit New Utterance & Advance Version)
            # -------------------------------------------------------------
            elif msg_type == "FINAL_TRANSCRIPT":
                text = msg.get("text", "").strip()
                if text:
                    # Cancel any prior running turn task
                    if turn_task and not turn_task.done():
                        turn_task.cancel()
                    turn_task = asyncio.create_task(handle_turn_execution(prompt=text))

            # -------------------------------------------------------------
            # Event 4: GET_STATE
            # -------------------------------------------------------------
            elif msg_type == "GET_STATE":
                await ws_manager.broadcast_state_sync(session=session)

            # -------------------------------------------------------------
            # Event 5: PING
            # -------------------------------------------------------------
            elif msg_type == "PING":
                await websocket.send_json({"type": "PONG", "timestamp": msg.get("timestamp")})

            # -------------------------------------------------------------
            # Event 5: PING
            # -------------------------------------------------------------
            elif msg_type == "PING":
                await websocket.send_json({"type": "PONG", "timestamp": msg.get("timestamp")})

    except WebSocketDisconnect:
        ws_manager.disconnect(session_id, websocket)
        if turn_task and not turn_task.done():
            turn_task.cancel()
    except Exception as e:
        logger.warning("WebSocket error in session %s: %s", session_id, e)
        ws_manager.disconnect(session_id, websocket)
        if turn_task and not turn_task.done():
            turn_task.cancel()

