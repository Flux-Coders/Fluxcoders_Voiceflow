"""VoiceFlow FastAPI Main Application."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.models import ConversationState, EventLevel, Request, ToolTask, VoiceEvent
from app.core.metrics import VoiceMetricsSnapshot
from app.core.session import SessionManager
from app.api.websocket import router as websocket_router

app = FastAPI(
    title="VoiceFlow Backend API",
    description="Interruption-safe realtime voice agent orchestration with version-gated state management.",
    version="0.1.0",
)

# CORS middleware for React frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(websocket_router)

session_manager = SessionManager()


# Request / Response Schemas
class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = None
    initial_version: int = 0


class CreateTurnRequest(BaseModel):
    prompt: str


class InterruptRequest(BaseModel):
    reason: Optional[str] = "User spoken interruption"


class ExecuteToolRequest(BaseModel):
    request_id: str
    version: int
    tool_name: str
    args: Dict[str, Any]
    delay_seconds: float = 0.0


class CompleteTurnRequest(BaseModel):
    request_id: str
    version: int
    assistant_response: str
    tool_call: Optional[Dict[str, Any]] = None


@app.get("/health", tags=["System"])
def health_check() -> Dict[str, str]:
    return {"status": "ok", "app": "VoiceFlow Backend", "version": "0.1.0"}


@app.post("/api/sessions", tags=["Session"], status_code=status.HTTP_201_CREATED)
def create_session(body: Optional[CreateSessionRequest] = None) -> Dict[str, Any]:
    b = body or CreateSessionRequest()
    session = session_manager.get_or_create_session(
        session_id=b.session_id,
        initial_version=b.initial_version,
    )
    return {
        "session_id": session.session_id,
        "active_version": session.active_version,
        "state": session.state_mgr.get_snapshot(),
    }


@app.get("/api/sessions/{session_id}", tags=["Session"])
def get_session_state(session_id: str) -> Dict[str, Any]:
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "active_version": session.active_version,
        "active_request_id": session.active_request_id,
        "state": session.state_mgr.get_snapshot(),
        "metrics": session.metrics.get_snapshot(),
    }


@app.post("/api/sessions/{session_id}/requests", tags=["Turn"], status_code=status.HTTP_201_CREATED)
def start_request(session_id: str, body: CreateTurnRequest) -> Request:
    session = session_manager.get_or_create_session(session_id=session_id)
    request = session.create_request(prompt=body.prompt)
    return request


@app.post("/api/sessions/{session_id}/interrupt", tags=["Turn"])
def interrupt_session(session_id: str, body: Optional[InterruptRequest] = None) -> Dict[str, Any]:
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    reason = (body.reason if body else None) or "User spoken interruption"
    result = session.interrupt(reason=reason)
    return {
        "status": "interrupted",
        "session_id": session_id,
        "active_version": session.active_version,
        **result,
    }


@app.post("/api/sessions/{session_id}/tools/execute", tags=["Tools"])
async def execute_tool(session_id: str, body: ExecuteToolRequest) -> ToolTask:
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    task = await session.run_tool(
        request_id=body.request_id,
        version=body.version,
        tool_name=body.tool_name,
        args=body.args,
        delay_seconds=body.delay_seconds,
    )
    return task


@app.post("/api/sessions/{session_id}/complete", tags=["Turn"])
def complete_turn(session_id: str, body: CompleteTurnRequest) -> Dict[str, Any]:
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    accepted = session.complete_turn(
        request_id=body.request_id,
        version=body.version,
        assistant_response=body.assistant_response,
        tool_call=body.tool_call,
    )
    return {
        "accepted": accepted,
        "session_id": session_id,
        "active_version": session.active_version,
        "active_request_id": session.active_request_id,
    }


@app.get("/api/sessions/{session_id}/events", tags=["Telemetry"])
def get_events(
    session_id: str,
    level: Optional[EventLevel] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> List[VoiceEvent]:
    events = session_manager.event_logger.get_events(
        session_id=session_id,
        level=level,
        limit=limit,
    )
    return events


@app.get("/api/sessions/{session_id}/metrics", tags=["Telemetry"])
def get_metrics(session_id: str) -> VoiceMetricsSnapshot:
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.metrics.get_snapshot()

