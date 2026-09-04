"""VoiceFlow Base TTS Interface and Audio Chunk Models.

Defines:
- StreamedAudioChunk: Strongly typed audio chunk carrying request and version identity.
- BaseTTSClient: Abstract provider-agnostic interface for streaming text-to-speech.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.core.cancellation import CancellationToken


class StreamedAudioChunk(BaseModel):
    """A strongly typed audio chunk produced during streaming TTS synthesis."""
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(..., description="Unique ID of originating request")
    conversation_version: int = Field(..., alias="version", description="Monotonic conversation version")
    audio_stream_id: str = Field(default_factory=lambda: f"stream-{uuid.uuid4().hex[:8]}")
    chunk_index: int = Field(..., description="0-indexed sequence position of the chunk")
    data: bytes = Field(..., description="Raw audio byte payload (PCM, MP3, or WAV)")
    is_final: bool = Field(default=False, description="Flag indicating final terminating chunk")
    format: str = Field(default="pcm", description="Audio format identifier: 'pcm', 'mp3', 'wav'")
    sample_rate: int = Field(default=16000, description="Sampling rate in Hz")
    timestamp: float = Field(default_factory=time.time, description="Wall-clock timestamp of chunk generation")
    duration_ms: Optional[float] = Field(default=None, description="Calculated duration of audio chunk in ms")

    @property
    def version(self) -> int:
        return self.conversation_version


class BaseTTSClient(ABC):
    """Abstract interface for all Text-to-Speech clients."""

    @abstractmethod
    async def stream_speech(
        self,
        text: str,
        request_id: str,
        version: int,
        cancellation_token: Optional[CancellationToken] = None,
        audio_stream_id: Optional[str] = None,
    ) -> AsyncIterator[StreamedAudioChunk]:
        """Streams audio chunks for the given text with strict version and cancellation association."""
        pass

