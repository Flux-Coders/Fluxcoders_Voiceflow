"""VoiceFlow Event Logging Module."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from app.models import EventLevel, VoiceEvent, VoiceEventType


class VoiceEventLogger:
    """Thread-safe event logger storing granular voice lifecycle domain events."""

    def __init__(self, max_events: int = 500) -> None:
        self._events: List[VoiceEvent] = []
        self._max_events = max_events

    def log_event(
        self,
        event_type: VoiceEventType,
        session_id: str,
        request_id: str,
        version: int,
        message: str,
        level: EventLevel = EventLevel.INFO,
        payload: Optional[Dict[str, Any]] = None,
    ) -> VoiceEvent:
        """Records a domain event with version and request identifiers."""
        event = VoiceEvent(
            event_type=event_type,
            session_id=session_id,
            request_id=request_id,
            version=version,
            timestamp=time.time(),
            message=message,
            level=level,
            payload=payload or {},
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events.pop(0)
        return event

    def get_events(
        self,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        version: Optional[int] = None,
        level: Optional[EventLevel] = None,
        limit: int = 100,
    ) -> List[VoiceEvent]:
        """Queries logged events with optional filters."""
        matched = self._events
        if session_id:
            matched = [e for e in matched if e.session_id == session_id]
        if request_id:
            matched = [e for e in matched if e.request_id == request_id]
        if version is not None:
            matched = [e for e in matched if e.version == version]
        if level:
            matched = [e for e in matched if e.level == level]
        return matched[-limit:]

    def clear(self) -> None:
        """Clears all stored events."""
        self._events.clear()

