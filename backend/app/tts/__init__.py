"""VoiceFlow Text-to-Speech (TTS) Package."""

from app.tts.base import BaseTTSClient, StreamedAudioChunk
from app.tts.rime_client import (
    RimeAuthenticationError,
    RimeBadRequestError,
    RimeClient,
    RimeConfig,
    RimeConfigError,
    RimeConnectionError,
    RimeError,
    RimeMalformedResponseError,
    RimeRateLimitError,
    RimeServerError,
    RimeTimeoutError,
)

__all__ = [
    "BaseTTSClient",
    "StreamedAudioChunk",
    "RimeClient",
    "RimeConfig",
    "RimeError",
    "RimeConfigError",
    "RimeAuthenticationError",
    "RimeBadRequestError",
    "RimeRateLimitError",
    "RimeServerError",
    "RimeTimeoutError",
    "RimeConnectionError",
    "RimeMalformedResponseError",
]

