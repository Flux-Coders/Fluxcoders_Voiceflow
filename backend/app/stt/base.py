"""VoiceFlow Base Speech-to-Text (STT) Abstraction Layer.

Defines standard event structures and abstract client interface for speech
recognition and turn endpointing across mock and real browser STT transports.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field


class STTEventType(str, Enum):
    """Event types emitted during the voice input lifecycle."""
    SPEECH_STARTED = "SPEECH_STARTED"
    INTERIM_TRANSCRIPT = "INTERIM_TRANSCRIPT"
    FINAL_TRANSCRIPT = "FINAL_TRANSCRIPT"
    SPEECH_ENDED = "SPEECH_ENDED"
    ERROR = "ERROR"


class STTEvent(BaseModel):
    """Normalized speech event emitted by an STT provider."""
    event_type: STTEventType
    text: str = ""
    confidence: float = 1.0
    timestamp: float = Field(default_factory=time.time)
    is_final: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseSTTClient(ABC):
    """Abstract interface for VoiceFlow STT providers."""

    def __init__(self) -> None:
        self._listeners: List[Callable[[STTEvent], Any]] = []

    def add_listener(self, callback: Callable[[STTEvent], Any]) -> None:
        """Subscribes a listener callback to receive STT events."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[STTEvent], Any]) -> None:
        """Unsubscribes a listener callback."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    async def emit_event(self, event: STTEvent) -> None:
        """Dispatches an STT event to all registered listeners."""
        for callback in list(self._listeners):
            try:
                res = callback(event)
                if hasattr(res, "__await__"):
                    await res
            except Exception as e:
                # Listener errors should never crash the STT event dispatcher
                pass

    @abstractmethod
    async def start(self) -> None:
        """Starts the speech recognition service."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stops the speech recognition service."""
        pass

