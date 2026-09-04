"""VoiceFlow Conversation State Management Module."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from app.models import ConversationMessage, ConversationState, Request, SlotPatch
from app.core.versioning import RequestVersionGate, VersionGateError


class ConversationStateManager:
    """Manages the conversation state and history with version integrity checks."""

    def __init__(self, session_id: str, initial_version: int = 0) -> None:
        self.state = ConversationState(
            session_id=session_id,
            active_version=initial_version,
            active_request_id=None,
            history=[],
            slots={},
            is_interrupted=False,
            created_at=time.time(),
            updated_at=time.time(),
        )

    @property
    def active_version(self) -> int:
        return self.state.active_version

    @property
    def active_request_id(self) -> Optional[str]:
        return self.state.active_request_id

    def advance_turn(self, new_request_id: str) -> int:
        """Monotonically increments conversation version and sets active request ID."""
        self.state.active_version += 1
        self.state.active_request_id = new_request_id
        self.state.is_interrupted = False
        self.state.updated_at = time.time()
        return self.state.active_version

    def set_interrupted(self) -> None:
        """Flags the conversation as currently interrupted."""
        self.state.is_interrupted = True
        self.state.updated_at = time.time()

        # Mark latest assistant message as interrupted if applicable
        if self.state.history and self.state.history[-1].role == "assistant":
            self.state.history[-1].is_interrupted = True

    def append_user_message(self, text: str, request_id: str, version: int) -> ConversationMessage:
        """Appends a user utterance to conversation history."""
        msg = ConversationMessage(
            role="user",
            content=text,
            version=version,
            request_id=request_id,
            timestamp=time.time(),
        )
        self.state.history.append(msg)
        self.state.updated_at = time.time()
        return msg

    def append_assistant_message(
        self, 
        text: str, 
        request_id: str, 
        version: int,
        tool_call: Optional[Dict[str, Any]] = None
    ) -> ConversationMessage:
        """Appends assistant output only if the version gate check passes."""
        is_valid, reason = RequestVersionGate.validate_tool_result_active(
            tool_version=version,
            tool_request_id=request_id,
            state=self.state,
        )
        if not is_valid:
            raise VersionGateError(f"Cannot append assistant message: {reason}")

        msg = ConversationMessage(
            role="assistant",
            content=text,
            version=version,
            request_id=request_id,
            timestamp=time.time(),
            tool_call=tool_call,
        )
        self.state.history.append(msg)
        self.state.updated_at = time.time()
        return msg

    def update_slots(self, new_slots: Dict[str, Any], version: int, request_id: str) -> None:
        """Merges slot updates with version validation. Supports None values as removals."""
        is_valid, reason = RequestVersionGate.validate_tool_result_active(
            tool_version=version,
            tool_request_id=request_id,
            state=self.state,
        )
        if not is_valid:
            raise VersionGateError(f"Cannot update slots: {reason}")

        for k, v in new_slots.items():
            if v is None:
                self.state.slots.pop(k, None)
            else:
                self.state.slots[k] = v
        self.state.updated_at = time.time()

    def apply_slot_patch(self, patch: SlotPatch, version: int, request_id: str) -> None:
        """Applies explicit slot updates (add, replace, clear) with version verification."""
        is_valid, reason = RequestVersionGate.validate_tool_result_active(
            tool_version=version,
            tool_request_id=request_id,
            state=self.state,
        )
        if not is_valid:
            raise VersionGateError(f"Cannot apply slot patch: {reason}")

        # 1. Explicit clear list
        for key in patch.clear_slots:
            self.state.slots.pop(key, None)

        # 2. Add or replace slots
        for key, val in patch.set_slots.items():
            if val is None:
                self.state.slots.pop(key, None)
            else:
                self.state.slots[key] = val

        self.state.updated_at = time.time()

    def get_snapshot(self) -> ConversationState:
        """Returns a copy of the current state."""
        return self.state.model_copy(deep=True)

