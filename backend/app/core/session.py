"""VoiceFlow Session Management Module.

Coordinates conversation state, request versioning, tool workers,
cancellation, Rime TTS gate, event logging, and dynamic metrics for each session.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional
from app.models import (
    ConversationMessage,
    ConversationState,
    EventLevel,
    Request,
    RequestStatus,
    StaleResultRecord,
    ToolStatus,
    ToolTask,
    VoiceEventType,
)
from app.core.cancellation import CancellationToken, TaskRegistry
from app.core.event_logger import VoiceEventLogger
from app.core.metrics import MetricsCollector, VoiceMetricsSnapshot
from app.core.rime_gate import RimeTTSGate
from app.core.state import ConversationStateManager
from app.core.tool_executor import ToolExecutor
from app.core.versioning import RequestVersionGate, StaleRimeGenerationError, VersionGateError


class Session:
    """An active VoiceFlow conversational session."""

    def __init__(
        self,
        session_id: str,
        event_logger: VoiceEventLogger,
        initial_version: int = 0,
    ) -> None:
        self.session_id = session_id
        self.state_mgr = ConversationStateManager(session_id=session_id, initial_version=initial_version)
        self.task_registry = TaskRegistry()
        self.metrics = MetricsCollector()
        self.event_logger = event_logger
        self.rime_gate = RimeTTSGate(event_logger=event_logger)
        self.tool_executor = ToolExecutor(
            event_logger=self.event_logger,
            metrics_collector=self.metrics,
        )
        self.requests: Dict[str, Request] = {}
        self.stale_discards: List[StaleResultRecord] = []
        self.created_at = time.time()

    @property
    def active_version(self) -> int:
        return self.state_mgr.active_version

    @property
    def active_request_id(self) -> Optional[str]:
        return self.state_mgr.active_request_id

    @property
    def current_answer(self) -> Optional[str]:
        """Returns the active assistant answer strictly for the latest active request."""
        if not self.state_mgr.state.history:
            return None
        # Walk backwards to find the latest valid assistant message for active version
        for msg in reversed(self.state_mgr.state.history):
            if (
                msg.role == "assistant" 
                and msg.version == self.active_version 
                and not msg.is_interrupted 
                and not msg.is_invalidated
            ):
                return msg.content
        return None

    def record_stale_discard(self, record: StaleResultRecord) -> None:
        """Explicitly records a rejected stale result."""
        self.stale_discards.append(record)
        self.metrics.record_stale_discard()

    def create_request(self, prompt: str) -> Request:
        """Starts a new request turn, superseding any previous active request turn."""
        prev_req_id = self.active_request_id
        prev_version = self.active_version

        # 1. When a new request supersedes an old request: MARK THE OLD REQUEST OBSOLETE
        if prev_req_id and prev_req_id in self.requests:
            old_req = self.requests[prev_req_id]
            if old_req.status in (RequestStatus.RUNNING, RequestStatus.PENDING):
                old_req.status = RequestStatus.OBSOLETE
                old_req.is_cancelled = True
                old_req.updated_at = time.time()
                self.task_registry.cancel_request(prev_req_id)
                self.event_logger.log_event(
                    event_type=VoiceEventType.REQUEST_SUPERSEDED,
                    session_id=self.session_id,
                    request_id=prev_req_id,
                    version=prev_version,
                    message=f"Request #{prev_version} ({prev_req_id}) marked OBSOLETE, superseded by new utterance.",
                    level=EventLevel.WARN,
                )

        # 2. UPDATE THE CURRENT CONVERSATION VERSION MONOTONICALLY
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        new_version = self.state_mgr.advance_turn(new_request_id=req_id)

        # 3. ALLOW THE NEW REQUEST TO PROCEED
        req = Request(
            request_id=req_id,
            conversation_version=new_version,
            session_id=self.session_id,
            prompt=prompt,
            status=RequestStatus.RUNNING,
            created_at=time.time(),
            updated_at=time.time(),
        )
        self.requests[req_id] = req
        self.metrics.record_request_start()

        # Register cancellation token for the new request
        self.task_registry.register_token(request_id=req_id, version=new_version)

        # Append user utterance to conversation history
        self.state_mgr.append_user_message(text=prompt, request_id=req_id, version=new_version)

        # Log initialization
        self.event_logger.log_event(
            event_type=VoiceEventType.REQUEST_INITIALIZED,
            session_id=self.session_id,
            request_id=req_id,
            version=new_version,
            message=f"Request #{new_version} initialized (ID: {req_id}) for: '{prompt}'",
            level=EventLevel.INFO,
            payload={"prompt": prompt},
        )
        return req

    def interrupt(self, reason: str = "User interruption") -> Dict[str, Any]:
        """Triggers sub-50ms audio cut, marks the active request obsolete/invalidated, and cancels tasks."""
        prev_version = self.active_version
        prev_req_id = self.active_request_id or "none"

        # 1. Monotonic start timing for metrics
        self.metrics.record_interruption_start(request_id=prev_req_id)

        # 2. Flag state as interrupted
        self.state_mgr.set_interrupted()

        # 3. Mark previous request as OBSOLETE / INVALIDATED
        if prev_req_id in self.requests:
            req = self.requests[prev_req_id]
            req.status = RequestStatus.OBSOLETE
            req.is_cancelled = True
            req.updated_at = time.time()

        # 4. Hard-cancel in-flight async tasks in the registry
        cancelled_tasks = self.task_registry.cancel_request(prev_req_id)

        # 5. Measure audio suppression delta
        latency_ms = self.metrics.record_audio_stop(request_id=prev_req_id, version=prev_version)

        # 6. Log domain events
        self.event_logger.log_event(
            event_type=VoiceEventType.INTERRUPT_TRIGGERED,
            session_id=self.session_id,
            request_id=prev_req_id,
            version=prev_version,
            message=f"INTERRUPTION TRIGGERED: {reason} (Audio stopped in {latency_ms:.2f}ms)",
            level=EventLevel.CRITICAL,
            payload={"reason": reason, "audio_stop_latency_ms": latency_ms},
        )
        self.event_logger.log_event(
            event_type=VoiceEventType.REQUEST_INVALIDATED,
            session_id=self.session_id,
            request_id=prev_req_id,
            version=prev_version,
            message=f"Request #{prev_version} ({prev_req_id}) marked OBSOLETE/INVALIDATED.",
            level=EventLevel.WARN,
            payload={"cancelled_tasks_count": cancelled_tasks},
        )
        self.event_logger.log_event(
            event_type=VoiceEventType.AUDIO_OUTPUT_STOPPED,
            session_id=self.session_id,
            request_id=prev_req_id,
            version=prev_version,
            message=f"Rime TTS audio stream aborted & output queue flushed.",
            level=EventLevel.INFO,
        )

        return {
            "invalidated_request_id": prev_req_id,
            "invalidated_version": prev_version,
            "audio_stop_latency_ms": latency_ms,
            "cancelled_tasks_count": cancelled_tasks,
        }

    async def run_tool(
        self,
        request_id: str,
        version: int,
        tool_name: str,
        args: Dict[str, Any],
        delay_seconds: float = 3.0,
        force_non_cooperative: bool = False,
    ) -> ToolTask:
        """Runs an async tool task with version validation, cancellation check, and stale logging."""
        token = self.task_registry.get_token(request_id)
        
        task = await self.tool_executor.execute_tool(
            tool_name=tool_name,
            args=args,
            request_id=request_id,
            version=version,
            session_id=self.session_id,
            state_mgr=self.state_mgr,
            delay_seconds=delay_seconds,
            cancellation_token=token,
            force_non_cooperative=force_non_cooperative,
        )

        if task.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED):
            record = StaleResultRecord(
                request_id=request_id,
                result_version=version,
                active_version_when_delivered=self.active_version,
                source_type="tool",
                source_name=tool_name,
                payload=task.args,
                reason=task.discard_reason or "Version mismatch / cancelled",
            )
            self.record_stale_discard(record)

        return task

    def synthesize_rime(self, text: str, request_id: str, version: int) -> Dict[str, Any]:
        """Synthesizes speech via Rime TTS with strict version gate check."""
        return self.rime_gate.synthesize(
            text=text,
            request_id=request_id,
            version=version,
            session_id=self.session_id,
            state_mgr=self.state_mgr,
            on_stale_discard=self.record_stale_discard,
        )

    def complete_turn(
        self,
        request_id: str,
        version: int,
        assistant_response: str,
        tool_call: Optional[Dict[str, Any]] = None,
        trigger_rime: bool = True,
    ) -> bool:
        """Completes the turn, triggers Rime TTS, and commits assistant message if version is valid."""
        is_valid, reason = RequestVersionGate.validate_tool_result_active(
            tool_version=version,
            tool_request_id=request_id,
            state=self.state_mgr.state,
        )

        if not is_valid:
            if request_id in self.requests:
                self.requests[request_id].status = RequestStatus.DISCARDED
                self.requests[request_id].updated_at = time.time()

            record = StaleResultRecord(
                request_id=request_id,
                result_version=version,
                active_version_when_delivered=self.active_version,
                source_type="turn_completion",
                source_name="complete_turn",
                payload={"assistant_response": assistant_response},
                reason=reason or "Version mismatch",
            )
            self.record_stale_discard(record)

            self.event_logger.log_event(
                event_type=VoiceEventType.TOOL_RETURN_STALE_DISCARDED,
                session_id=self.session_id,
                request_id=request_id,
                version=version,
                message=f"Turn output blocked by version gate: {reason}",
                level=EventLevel.WARN,
            )
            return False

        # If trigger_rime is requested, synthesize via Rime TTS
        if trigger_rime:
            self.synthesize_rime(text=assistant_response, request_id=request_id, version=version)

        # Apply assistant message to active conversation history
        self.state_mgr.append_assistant_message(
            text=assistant_response,
            request_id=request_id,
            version=version,
            tool_call=tool_call,
        )

        if request_id in self.requests:
            req = self.requests[request_id]
            req.status = RequestStatus.COMPLETED
            req.completed_at = time.time()
            req.updated_at = time.time()
            self.metrics.record_turn_duration(
                request_id=request_id,
                version=version,
                t_start=req.created_at,
            )

        self.event_logger.log_event(
            event_type=VoiceEventType.TURN_COMPLETED,
            session_id=self.session_id,
            request_id=request_id,
            version=version,
            message=f"Turn completed successfully for Request #{version} (Rime audio active)",
            level=EventLevel.SUCCESS,
        )
        return True

    async def process_turn(
        self,
        prompt: str,
        orchestrator: Optional[Any] = None,
        delay_tool_ms: Optional[int] = None,
        trigger_rime: bool = False,
    ) -> Any:
        """Convenience method to create a new request and orchestrate the full conversational turn."""
        from app.engine.llm_orchestrator import LLMOrchestrator
        orch = orchestrator or LLMOrchestrator()
        req = self.create_request(prompt=prompt)
        return await orch.process_turn(
            session=self,
            request_id=req.request_id,
            version=req.conversation_version,
            prompt=prompt,
            delay_tool_ms=delay_tool_ms,
            trigger_rime=trigger_rime,
        )



class SessionManager:
    """Global manager for all VoiceFlow sessions."""

    def __init__(self) -> None:
        self.event_logger = VoiceEventLogger()
        self._sessions: Dict[str, Session] = {}

    def get_or_create_session(
        self, 
        session_id: Optional[str] = None, 
        initial_version: int = 0
    ) -> Session:
        sid = session_id or f"sess-{uuid.uuid4().hex[:8]}"
        if sid not in self._sessions:
            self._sessions[sid] = Session(
                session_id=sid,
                event_logger=self.event_logger,
                initial_version=initial_version,
            )
        return self._sessions[sid]

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            self._sessions[session_id].task_registry.cancel_all()
            del self._sessions[session_id]
            return True
        return False
