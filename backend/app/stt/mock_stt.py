"""VoiceFlow Deterministic Mock STT Provider.

Provides programmatic speech event emission for unit, integration,
and failure testing of VoiceFlow's interruption-safe state machine.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from app.stt.base import BaseSTTClient, STTEvent, STTEventType


class MockSTTClient(BaseSTTClient):
    """Deterministic Mock STT Client for automated test suites."""

    def __init__(self) -> None:
        super().__init__()
        self.is_running: bool = False
        self.emitted_events: List[STTEvent] = []

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False

    async def emit_speech_started(self) -> STTEvent:
        """Simulates voice onset / VAD speech start detection."""
        event = STTEvent(
            event_type=STTEventType.SPEECH_STARTED,
            timestamp=time.time(),
        )
        self.emitted_events.append(event)
        await self.emit_event(event)
        return event

    async def emit_interim_transcript(self, text: str) -> STTEvent:
        """Simulates tentative interim speech recognition output."""
        event = STTEvent(
            event_type=STTEventType.INTERIM_TRANSCRIPT,
            text=text,
            is_final=False,
            timestamp=time.time(),
        )
        self.emitted_events.append(event)
        await self.emit_event(event)
        return event

    async def emit_final_transcript(self, text: str) -> STTEvent:
        """Simulates final recognized utterance at speech endpoint."""
        event = STTEvent(
            event_type=STTEventType.FINAL_TRANSCRIPT,
            text=text,
            is_final=True,
            timestamp=time.time(),
        )
        self.emitted_events.append(event)
        await self.emit_event(event)
        return event

    async def emit_speech_ended(self) -> STTEvent:
        """Simulates voice offset / silence boundary."""
        event = STTEvent(
            event_type=STTEventType.SPEECH_ENDED,
            timestamp=time.time(),
        )
        self.emitted_events.append(event)
        await self.emit_event(event)
        return event

    async def simulate_turn(
        self,
        interim_texts: List[str],
        final_text: str,
        step_delay_ms: int = 10,
    ) -> None:
        """Simulates a complete voice utterance turn from onset to final text."""
        await self.emit_speech_started()
        for partial in interim_texts:
            if step_delay_ms > 0:
                await asyncio.sleep(step_delay_ms / 1000.0)
            await self.emit_interim_transcript(partial)

        if step_delay_ms > 0:
            await asyncio.sleep(step_delay_ms / 1000.0)
        await self.emit_final_transcript(final_text)
        await self.emit_speech_ended()

