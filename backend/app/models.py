"""VoiceFlow Typed Domain Models.

Defines the core data structures for:
- Request
- ConversationState
- ToolTask
- VoiceEvent
- RequestStatus
- StaleResultRecord
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class RequestStatus(str, Enum):
    """Lifecycle status of a user request turn."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    OBSOLETE = "OBSOLETE"
    INTERRUPTED = "INTERRUPTED"
    INVALIDATED = "INVALIDATED"
    DISCARDED = "DISCARDED"
    CANCELLED = "CANCELLED"


class ToolStatus(str, Enum):
    """Lifecycle status of an asynchronous tool invocation."""
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED_VALID = "COMPLETED_VALID"
    COMPLETED_STALE_DISCARDED = "COMPLETED_STALE_DISCARDED"
    CANCELLED = "CANCELLED"


class VoiceEventType(str, Enum):
    """Fine-grained domain event types for realtime voice orchestration."""
    SPEECH_STARTED = "SPEECH_STARTED"
    TRANSCRIPT_INTERIM = "TRANSCRIPT_INTERIM"
    TRANSCRIPT_FINAL = "TRANSCRIPT_FINAL"
    REQUEST_INITIALIZED = "REQUEST_INITIALIZED"
    REQUEST_SUPERSEDED = "REQUEST_SUPERSEDED"
    LLM_PROMPT_STARTED = "LLM_PROMPT_STARTED"
    LLM_STEP1_STARTED = "LLM_STEP1_STARTED"
    LLM_STEP1_COMPLETED = "LLM_STEP1_COMPLETED"
    LLM_STEP1_CANCELLED = "LLM_STEP1_CANCELLED"
    LLM_STEP1_FAILED = "LLM_STEP1_FAILED"
    LLM_STEP2_STARTED = "LLM_STEP2_STARTED"
    LLM_STEP2_COMPLETED = "LLM_STEP2_COMPLETED"
    LLM_STEP2_CANCELLED = "LLM_STEP2_CANCELLED"
    LLM_STEP2_FAILED = "LLM_STEP2_FAILED"
    TOOL_UNKNOWN_OR_FORBIDDEN = "TOOL_UNKNOWN_OR_FORBIDDEN"
    TOOL_ARGS_INVALID = "TOOL_ARGS_INVALID"
    SLOTS_UPDATED = "SLOTS_UPDATED"
    TOOL_DISPATCHED = "TOOL_DISPATCHED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_CANCEL_REQUESTED = "TOOL_CANCEL_REQUESTED"
    TOOL_CANCELLED = "TOOL_CANCELLED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_RESULT_ACCEPTED = "TOOL_RESULT_ACCEPTED"
    TOOL_RETURN_VALID = "TOOL_RETURN_VALID"
    TOOL_RETURN_STALE_DISCARDED = "TOOL_RETURN_STALE_DISCARDED"
    STALE_RESULT_DISCARDED = "STALE_RESULT_DISCARDED"
    INTERRUPT_TRIGGERED = "INTERRUPT_TRIGGERED"
    REQUEST_INVALIDATED = "REQUEST_INVALIDATED"
    RIME_STREAM_STARTED = "RIME_STREAM_STARTED"
    RIME_FIRST_AUDIO_CHUNK = "RIME_FIRST_AUDIO_CHUNK"
    RIME_CHUNK_RECEIVED = "RIME_CHUNK_RECEIVED"
    RIME_STOP_REQUESTED = "RIME_STOP_REQUESTED"
    RIME_STREAM_CANCELLED = "RIME_STREAM_CANCELLED"
    RIME_STREAM_COMPLETED = "RIME_STREAM_COMPLETED"
    RIME_STREAM_FAILED = "RIME_STREAM_FAILED"
    RIME_STREAM_ABORTED = "RIME_STREAM_ABORTED"
    RIME_STREAM_BLOCKED_STALE = "RIME_STREAM_BLOCKED_STALE"
    AUDIO_OUTPUT_STOPPED = "AUDIO_OUTPUT_STOPPED"
    STALE_AUDIO_DISCARDED = "STALE_AUDIO_DISCARDED"
    TURN_COMPLETED = "TURN_COMPLETED"


class EventLevel(str, Enum):
    """Severity classification for logged events."""
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Request(BaseModel):
    """A user request turn representing a distinct intent/utterance."""
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(default_factory=lambda: f"req-{uuid.uuid4().hex[:8]}")
    conversation_version: int = Field(..., alias="version", description="Monotonically increasing version number for this session")
    session_id: str = Field(..., description="ID of the parent session")
    prompt: str = Field(..., description="User utterance transcript or query text")
    status: RequestStatus = Field(default=RequestStatus.PENDING)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    is_cancelled: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def version(self) -> int:
        return self.conversation_version


class StaleResultRecord(BaseModel):
    """Detailed record of a rejected stale result."""
    record_id: str = Field(default_factory=lambda: f"stale-{uuid.uuid4().hex[:8]}")
    request_id: str
    result_version: int
    active_version_when_delivered: int
    source_type: str = Field(..., description="'tool' or 'rime_tts' or 'turn_completion'")
    source_name: str
    payload: Optional[Any] = None
    reason: str
    timestamp: float = Field(default_factory=time.time)


class ToolTask(BaseModel):
    """Represents an asynchronous tool execution bound to a specific request/version."""
    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}", alias="task_id")
    tool_name: str
    args: Dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(..., description="ID of the originating request")
    conversation_version: int = Field(..., alias="version", description="Version of the originating request")
    session_id: str = Field(...)
    status: ToolStatus = Field(default=ToolStatus.PENDING)
    started_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    duration_ms: Optional[float] = None
    result: Optional[Any] = None
    discard_reason: Optional[str] = None

    @property
    def task_id(self) -> str:
        return self.tool_id

    @property
    def version(self) -> int:
        return self.conversation_version


class VoiceEvent(BaseModel):
    """Structured telemetry/event log item emitted by the voice loop."""
    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}")
    event_type: VoiceEventType
    session_id: str
    request_id: str
    version: int
    timestamp: float = Field(default_factory=time.time)
    message: str
    level: EventLevel = Field(default=EventLevel.INFO)
    payload: Dict[str, Any] = Field(default_factory=dict)


class ConversationMessage(BaseModel):
    """A single turn within conversation history."""
    role: str = Field(..., description="'user', 'assistant', or 'tool'")
    content: str
    version: int
    request_id: str
    timestamp: float = Field(default_factory=time.time)
    is_interrupted: bool = False
    is_invalidated: bool = False
    tool_call: Optional[Dict[str, Any]] = None


class ConversationState(BaseModel):
    """Complete versioned state for a VoiceFlow session."""
    session_id: str
    active_version: int = 0
    active_request_id: Optional[str] = None
    history: List[ConversationMessage] = Field(default_factory=list)
    slots: Dict[str, Any] = Field(default_factory=dict, description="Extracted intent slots (e.g., origin, destination)")
    is_interrupted: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SlotPatch(BaseModel):
    """Explicit patch to apply to conversational slots."""
    set_slots: Dict[str, Any] = Field(default_factory=dict, description="Slots to add or replace")
    clear_slots: List[str] = Field(default_factory=list, description="Slot keys to remove/clear")


class ToolCallRequest(BaseModel):
    """Structured tool call produced by an LLM."""
    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    extra_content: Optional[Dict[str, Any]] = None


class LLMMessage(BaseModel):
    """A message exchanged with an LLM provider."""
    role: str = Field(..., description="'system', 'user', 'assistant', or 'tool'")
    content: Optional[str] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCallRequest]] = None


class LLMResponse(BaseModel):
    """Normalized response returned from any LLM provider."""
    content: Optional[str] = None
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)
    finish_reason: str = Field(default="stop", description="'stop', 'tool_calls', 'length'")
    slot_patch: Optional[SlotPatch] = None
    raw_response: Optional[Dict[str, Any]] = None


class TurnExecutionResult(BaseModel):
    """Result of an orchestrated turn execution."""
    request_id: str
    version: int
    success: bool
    assistant_response: Optional[str] = None
    tool_task: Optional[ToolTask] = None
    is_stale: bool = False
    error: Optional[str] = None

