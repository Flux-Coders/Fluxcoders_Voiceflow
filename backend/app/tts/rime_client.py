"""VoiceFlow Official Rime Text-to-Speech Streaming Client.

Implements the official Rime HTTP streaming synthesis path:
POST https://users.rime.ai/v1/rime-tts

Features:
- Configurable via environment variables (model, speaker, language, endpoint, format, sample_rate).
- Bearer token authentication without credential leakage.
- Asynchronous chunked audio streaming via httpx.
- Cancellation and version gate association on every yielded chunk.
- Comprehensive HTTP status and network exception translation.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import AsyncIterator, Dict, Optional
import httpx
from pydantic import BaseModel, Field
from app.core.cancellation import CancellationToken
from app.tts.base import BaseTTSClient, StreamedAudioChunk


# -----------------------------------------------------------------------------
# Rime Domain Exceptions
# -----------------------------------------------------------------------------
class RimeError(Exception):
    """Base exception for all Rime TTS errors."""
    pass


class RimeConfigError(RimeError):
    """Raised when Rime configuration parameters are invalid or missing."""
    pass


class RimeAuthenticationError(RimeError):
    """Raised on HTTP 401/403 or missing API credentials."""
    pass


class RimeBadRequestError(RimeError):
    """Raised on HTTP 400 bad request (e.g. invalid speaker or unsupported language)."""
    pass


class RimeRateLimitError(RimeError):
    """Raised on HTTP 429 rate limit exceeded."""
    pass


class RimeServerError(RimeError):
    """Raised on HTTP 5xx upstream server errors."""
    pass


class RimeTimeoutError(RimeError):
    """Raised when Rime HTTP request times out."""
    pass


class RimeConnectionError(RimeError):
    """Raised on network connection failures."""
    pass


class RimeMalformedResponseError(RimeError):
    """Raised when response body is unexpectedly malformed."""
    pass


# -----------------------------------------------------------------------------
# Rime Configuration Model
# -----------------------------------------------------------------------------
class RimeConfig(BaseModel):
    """Configuration options for Rime TTS loaded from environment variables."""
    api_key: Optional[str] = Field(default_factory=lambda: os.getenv("RIME_API_KEY"))
    endpoint: str = Field(default_factory=lambda: os.getenv("RIME_ENDPOINT", "https://users.rime.ai/v1/rime-tts"))
    model: str = Field(default_factory=lambda: os.getenv("RIME_MODEL", "mistv3"))
    speaker: str = Field(default_factory=lambda: os.getenv("RIME_SPEAKER", "astra"))
    language: str = Field(default_factory=lambda: os.getenv("RIME_LANGUAGE", "eng"))
    audio_format: str = Field(default_factory=lambda: os.getenv("RIME_AUDIO_FORMAT", "pcm"))
    sample_rate: int = Field(default_factory=lambda: int(os.getenv("RIME_SAMPLE_RATE", "16000")))
    timeout_seconds: float = Field(default=10.0)
    chunk_size: int = Field(default=1024)

    def get_accept_header(self) -> str:
        """Translates configured audio format to the exact HTTP Accept header required by Rime."""
        fmt = self.audio_format.lower().strip()
        if fmt in ("pcm", "l16", "raw"):
            return "audio/pcm"
        elif fmt in ("mp3", "mpeg"):
            return "audio/mpeg"
        elif fmt in ("wav", "wave"):
            return "audio/wav"
        return "audio/pcm"

    def validate_config(self, require_key: bool = True) -> None:
        """Validates configuration parameters."""
        if require_key and (not self.api_key or self.api_key.strip() in ("", "your_real_key_here")):
            raise RimeAuthenticationError("RIME_API_KEY is not configured in environment.")
        if not self.endpoint.startswith(("http://", "https://")):
            raise RimeConfigError(f"Invalid RIME_ENDPOINT URL: '{self.endpoint}'. Must start with http:// or https://")
        if self.sample_rate not in (8000, 16000, 22050, 24000, 44100, 48000):
            raise RimeConfigError(f"Unsupported sample rate: {self.sample_rate} Hz.")


# -----------------------------------------------------------------------------
# Concrete Rime TTS Client
# -----------------------------------------------------------------------------
class RimeClient(BaseTTSClient):
    """Official Rime Text-to-Speech streaming HTTP client."""

    def __init__(
        self,
        config: Optional[RimeConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.config = config or RimeConfig()
        self._custom_transport = transport
        self._external_http_client = http_client

    def _get_client(self) -> httpx.AsyncClient:
        """Creates or returns an httpx.AsyncClient."""
        if self._external_http_client:
            return self._external_http_client
        if self._custom_transport:
            return httpx.AsyncClient(transport=self._custom_transport, timeout=self.config.timeout_seconds)
        return httpx.AsyncClient(timeout=self.config.timeout_seconds)

    async def stream_speech(
        self,
        text: str,
        request_id: str,
        version: int,
        cancellation_token: Optional[CancellationToken] = None,
        audio_stream_id: Optional[str] = None,
    ) -> AsyncIterator[StreamedAudioChunk]:
        """Streams audio chunks from Rime TTS with cancellation checks and version tagging."""
        # 1. Pre-connection Cancellation Check
        if cancellation_token and cancellation_token.is_cancelled:
            return

        # 2. Configuration Validation (skip key validation if custom mock transport provided in tests)
        require_key = (self._custom_transport is None and self._external_http_client is None)
        self.config.validate_config(require_key=require_key)

        stream_id = audio_stream_id or f"stream-{uuid.uuid4().hex[:8]}"

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": self.config.get_accept_header(),
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload: Dict[str, object] = {
            "speaker": self.config.speaker,
            "text": text,
            "modelId": self.config.model,
            "lang": self.config.language,
            "samplingRate": self.config.sample_rate,
            "speedAlpha": 1.0,
        }

        client = self._get_client()
        should_close_client = (self._external_http_client is None)

        try:
            # Check cancellation immediately before network request
            if cancellation_token and cancellation_token.is_cancelled:
                return

            req = client.build_request("POST", self.config.endpoint, headers=headers, json=payload)
            response = await client.send(req, stream=True)

            # Error handling based on HTTP status
            if response.status_code == 401 or response.status_code == 403:
                await response.aclose()
                raise RimeAuthenticationError(f"Rime authentication failed (HTTP {response.status_code}).")

            if response.status_code == 400:
                body = await response.aread()
                await response.aclose()
                err_msg = body.decode("utf-8", errors="replace")
                raise RimeBadRequestError(f"Rime bad request (HTTP 400): {err_msg}")

            if response.status_code == 429:
                await response.aclose()
                raise RimeRateLimitError("Rime rate limit exceeded (HTTP 429).")

            if response.status_code >= 500:
                await response.aclose()
                raise RimeServerError(f"Rime server error (HTTP {response.status_code}).")

            if response.status_code >= 300:
                await response.aclose()
                raise RimeError(f"Unexpected HTTP status from Rime: {response.status_code}")

            # Stream audio byte chunks
            chunk_index = 0
            async for raw_chunk in response.aiter_raw():
                # Check cancellation token on each received chunk
                if cancellation_token and cancellation_token.is_cancelled:
                    await response.aclose()
                    return

                if raw_chunk:
                    yield StreamedAudioChunk(
                        request_id=request_id,
                        conversation_version=version,
                        audio_stream_id=stream_id,
                        chunk_index=chunk_index,
                        data=raw_chunk,
                        is_final=False,
                        format=self.config.audio_format,
                        sample_rate=self.config.sample_rate,
                        timestamp=time.time(),
                    )
                    chunk_index += 1

            # Final terminating chunk if stream was not cancelled
            if not (cancellation_token and cancellation_token.is_cancelled):
                yield StreamedAudioChunk(
                    request_id=request_id,
                    conversation_version=version,
                    audio_stream_id=stream_id,
                    chunk_index=chunk_index,
                    data=b"",
                    is_final=True,
                    format=self.config.audio_format,
                    sample_rate=self.config.sample_rate,
                    timestamp=time.time(),
                )

            await response.aclose()

        except httpx.TimeoutException as exc:
            raise RimeTimeoutError(f"Rime request timed out after {self.config.timeout_seconds}s.") from exc
        except httpx.NetworkError as exc:
            raise RimeConnectionError("Failed to connect to Rime TTS endpoint.") from exc
        except (RimeError, Exception):
            raise
        finally:
            if should_close_client:
                await client.aclose()
