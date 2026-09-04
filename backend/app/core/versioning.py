"""VoiceFlow Request Versioning & Stale-Result Protection Strategy."""

from __future__ import annotations

from typing import Optional, Tuple
from app.models import ConversationState, Request, RequestStatus


class VersionGateError(Exception):
    """Raised when an operation attempts to commit state for an obsolete version."""
    pass


class StaleRimeGenerationError(Exception):
    """Raised when an obsolete request attempts to trigger Rime TTS."""
    pass


class RequestVersionGate:
    """Enforces atomic validation of request IDs and conversation versions.
    
    Guarantees:
    1. Obsolete requests cannot mutate conversation state.
    2. Late results from interrupted or superseded requests are discarded.
    3. Active requests must match the exact (active_request_id, active_version) tuple.
    4. Stale results never trigger Rime TTS or appear as current answers.
    """

    @staticmethod
    def validate_request_active(
        request: Request, 
        state: ConversationState
    ) -> Tuple[bool, Optional[str]]:
        """Checks if a request is currently active and eligible to produce output."""
        if request.is_cancelled:
            return False, f"Request {request.request_id} (v{request.conversation_version}) is explicitly cancelled."

        if request.status == RequestStatus.OBSOLETE:
            return False, f"Request {request.request_id} (v{request.conversation_version}) has been marked OBSOLETE."

        if request.conversation_version != state.active_version:
            return False, (
                f"Version mismatch: request has version v{request.conversation_version}, "
                f"but active conversation version is v{state.active_version}."
            )

        if state.active_request_id and request.request_id != state.active_request_id:
            return False, (
                f"Request ID mismatch: request has ID {request.request_id}, "
                f"but active request ID is {state.active_request_id}."
            )

        return True, None

    @staticmethod
    def validate_tool_result_active(
        tool_version: int,
        tool_request_id: str,
        state: ConversationState
    ) -> Tuple[bool, Optional[str]]:
        """Checks if an asynchronous tool result is valid against the active conversation state."""
        if tool_version != state.active_version:
            return False, (
                f"Stale tool result: tool executed under v{tool_version}, "
                f"but active session has advanced to v{state.active_version}."
            )

        if state.active_request_id and tool_request_id != state.active_request_id:
            return False, (
                f"Stale tool result: tool executed for request {tool_request_id}, "
                f"but active request is {state.active_request_id}."
            )

        return True, None

    @staticmethod
    def validate_rime_synthesis_active(
        version: int,
        request_id: str,
        state: ConversationState
    ) -> Tuple[bool, Optional[str]]:
        """Validates that Rime TTS is only triggered for the currently active request and version."""
        if version != state.active_version:
            return False, (
                f"Stale Rime TTS trigger blocked: TTS requested for obsolete version v{version}, "
                f"while active conversation version is v{state.active_version}."
            )

        if state.active_request_id and request_id != state.active_request_id:
            return False, (
                f"Stale Rime TTS trigger blocked: TTS requested for request {request_id}, "
                f"while active request is {state.active_request_id}."
            )

        return True, None
