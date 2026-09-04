"""VoiceFlow Phase 12 Rime TTS Integration Tests.

Comprehensive deterministic tests using httpx.MockTransport covering:
1. successful streaming
2. correct chunk metadata
3. first-audio timestamp
4. stale request before synthesis
5. cancellation before connection
6. cancellation during streaming
7. stale request after chunks have already been received
8. buffered stale audio being discarded
9. request replacement during synthesis
10. HTTP 401 authentication error
11. HTTP 429 rate limit error
12. HTTP 5xx server error
13. timeout error
14. network failure
15. configuration error
16. level 3 final playback gate
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator, List
import httpx
import pytest

from app.core.cancellation import CancellationToken
from app.core.event_logger import VoiceEventLogger
from app.core.rime_gate import RimeTTSGate
from app.core.session import Session
from app.core.state import ConversationStateManager
from app.core.versioning import StaleRimeGenerationError
from app.models import ConversationState, VoiceEventType
from app.tts.base import StreamedAudioChunk
from app.tts.rime_client import (
    RimeAuthenticationError,
    RimeBadRequestError,
    RimeClient,
    RimeConfig,
    RimeConfigError,
    RimeConnectionError,
    RimeRateLimitError,
    RimeServerError,
    RimeTimeoutError,
)


# -----------------------------------------------------------------------------
# Test Fixtures & Mock Transports
# -----------------------------------------------------------------------------
class MockAudioByteStream(httpx.AsyncByteStream):
    def __init__(self, chunk_count: int = 3, chunk_delay: float = 0.0) -> None:
        self.chunk_count = chunk_count
        self.chunk_delay = chunk_delay

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunk_count):
            if self.chunk_delay > 0:
                await asyncio.sleep(self.chunk_delay)
            yield b"\x00\x01\x02\x03\x04\x05\x06\x07" * 32


def create_mock_transport_success(chunk_count: int = 3, chunk_delay: float = 0.0) -> httpx.AsyncBaseTransport:
    """Creates a mock transport that yields simulated raw audio byte chunks."""
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(status_code=200, stream=MockAudioByteStream(chunk_count, chunk_delay))

    return httpx.MockTransport(mock_handler)


@pytest.fixture
def mock_config() -> RimeConfig:
    return RimeConfig(
        api_key="mock_test_key_abc123",
        endpoint="https://users.rime.ai/v1/rime-tts",
        model="mistv3",
        speaker="astra",
        language="eng",
        audio_format="pcm",
        sample_rate=16000,
        timeout_seconds=5.0,
    )


@pytest.fixture
def event_logger() -> VoiceEventLogger:
    return VoiceEventLogger()


@pytest.fixture
def session(event_logger: VoiceEventLogger) -> Session:
    return Session(session_id="test-rime-session", event_logger=event_logger)


# -----------------------------------------------------------------------------
# Test 1: Successful Streaming
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_streaming(mock_config: RimeConfig, event_logger: VoiceEventLogger):
    """Verifies that RimeClient streams chunks over HTTP chunked streaming cleanly."""
    transport = create_mock_transport_success(chunk_count=3)
    client = RimeClient(config=mock_config, transport=transport)

    chunks: List[StreamedAudioChunk] = []
    async for chunk in client.stream_speech(
        text="Nagpur to Mumbai express",
        request_id="req-test-01",
        version=1,
    ):
        chunks.append(chunk)

    # 3 data chunks + 1 final zero-byte chunk
    assert len(chunks) == 4
    assert chunks[0].data != b""
    assert chunks[-1].is_final is True
    assert chunks[-1].data == b""


# -----------------------------------------------------------------------------
# Test 2: Correct Chunk Metadata
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correct_chunk_metadata(mock_config: RimeConfig):
    """Verifies all typed metadata fields on every StreamedAudioChunk."""
    transport = create_mock_transport_success(chunk_count=2)
    client = RimeClient(config=mock_config, transport=transport)

    chunks: List[StreamedAudioChunk] = []
    async for chunk in client.stream_speech(
        text="Testing metadata",
        request_id="req-meta-99",
        version=42,
        audio_stream_id="stream-fixed-123",
    ):
        chunks.append(chunk)

    assert len(chunks) == 3
    c0 = chunks[0]
    assert c0.request_id == "req-meta-99"
    assert c0.conversation_version == 42
    assert c0.version == 42
    assert c0.audio_stream_id == "stream-fixed-123"
    assert c0.chunk_index == 0
    assert c0.format == "pcm"
    assert c0.sample_rate == 16000
    assert c0.timestamp > 0.0
    assert c0.is_final is False

    c_final = chunks[-1]
    assert c_final.chunk_index == 2
    assert c_final.is_final is True


# -----------------------------------------------------------------------------
# Test 3: First-Audio Timestamp & Telemetry
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_first_audio_timestamp(mock_config: RimeConfig, event_logger: VoiceEventLogger, session: Session):
    """Verifies that RIME_FIRST_AUDIO_CHUNK event is emitted with real latency delta."""
    transport = create_mock_transport_success(chunk_count=3, chunk_delay=0.01)
    client = RimeClient(config=mock_config, transport=transport)
    gate = RimeTTSGate(event_logger=event_logger, tts_client=client)

    req = session.create_request("Find trains")
    
    streamed = []
    async for chunk in gate.stream_synthesize(
        text="Trains found",
        request_id=req.request_id,
        version=req.conversation_version,
        session_id=session.session_id,
        state_mgr=session.state_mgr,
    ):
        streamed.append(chunk)

    assert len(streamed) == 4

    types = [e.event_type for e in event_logger.get_events()]
    assert VoiceEventType.RIME_STREAM_STARTED in types
    assert VoiceEventType.RIME_FIRST_AUDIO_CHUNK in types
    assert VoiceEventType.RIME_CHUNK_RECEIVED in types
    assert VoiceEventType.RIME_STREAM_COMPLETED in types

    first_chunk_evt = next(e for e in event_logger.get_events() if e.event_type == VoiceEventType.RIME_FIRST_AUDIO_CHUNK)
    assert first_chunk_evt.payload.get("latency_first_ms") > 0.0


# -----------------------------------------------------------------------------
# Test 4: Stale Request Before Synthesis (Level 1 Pre-flight Gate)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_request_before_synthesis(mock_config: RimeConfig, event_logger: VoiceEventLogger, session: Session):
    """Verifies Level 1 Pre-flight Gate blocks obsolete version before network connection."""
    transport = create_mock_transport_success()
    client = RimeClient(config=mock_config, transport=transport)
    gate = RimeTTSGate(event_logger=event_logger, tts_client=client)

    req1 = session.create_request("Request 1")
    # Advance version so req1 is obsolete
    req2 = session.create_request("Request 2")

    with pytest.raises(StaleRimeGenerationError):
        async for _ in gate.stream_synthesize(
            text="Obsolete audio",
            request_id=req1.request_id,
            version=req1.conversation_version,
            session_id=session.session_id,
            state_mgr=session.state_mgr,
            on_stale_discard=session.record_stale_discard,
        ):
            pass

    types = [e.event_type for e in event_logger.get_events()]
    assert VoiceEventType.RIME_STREAM_BLOCKED_STALE in types
    assert len(session.stale_discards) == 1
    assert session.stale_discards[0].request_id == req1.request_id


# -----------------------------------------------------------------------------
# Test 5: Cancellation Before Connection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancellation_before_connection(mock_config: RimeConfig):
    """Verifies client returns immediately without opening stream if token is cancelled."""
    transport = create_mock_transport_success()
    client = RimeClient(config=mock_config, transport=transport)

    token = CancellationToken(request_id="req-pre-cancel", version=1)
    token.cancel()

    chunks = []
    async for chunk in client.stream_speech(
        text="Cancelled audio",
        request_id="req-pre-cancel",
        version=1,
        cancellation_token=token,
    ):
        chunks.append(chunk)

    assert len(chunks) == 0


# -----------------------------------------------------------------------------
# Test 6: Cancellation During Streaming
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancellation_during_streaming(mock_config: RimeConfig, event_logger: VoiceEventLogger, session: Session):
    """Verifies mid-stream cancellation immediately closes HTTP response and emits RIME_STREAM_CANCELLED."""
    transport = create_mock_transport_success(chunk_count=10, chunk_delay=0.02)
    client = RimeClient(config=mock_config, transport=transport)
    gate = RimeTTSGate(event_logger=event_logger, tts_client=client)

    req = session.create_request("Stream test")
    token = session.task_registry.get_token(req.request_id)

    received_chunks = []
    
    async def cancel_midway():
        await asyncio.sleep(0.05)
        token.cancel()

    asyncio.create_task(cancel_midway())

    async for chunk in gate.stream_synthesize(
        text="Long text generating many chunks",
        request_id=req.request_id,
        version=req.conversation_version,
        session_id=session.session_id,
        state_mgr=session.state_mgr,
        cancellation_token=token,
        on_stale_discard=session.record_stale_discard,
    ):
        received_chunks.append(chunk)

    # Should have stopped early before all 10 chunks
    assert len(received_chunks) < 10
    types = [e.event_type for e in event_logger.get_events()]
    assert VoiceEventType.RIME_STREAM_CANCELLED in types


# -----------------------------------------------------------------------------
# Test 7: Stale Request After Chunks Already Received
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_request_after_chunks_received(mock_config: RimeConfig, event_logger: VoiceEventLogger, session: Session):
    """Verifies that bumping active version mid-stream immediately cuts off synthesis."""
    transport = create_mock_transport_success(chunk_count=10, chunk_delay=0.02)
    client = RimeClient(config=mock_config, transport=transport)
    gate = RimeTTSGate(event_logger=event_logger, tts_client=client)

    req1 = session.create_request("First request")
    
    async def bump_version_midway():
        await asyncio.sleep(0.04)
        session.create_request("Second request supersedes")

    asyncio.create_task(bump_version_midway())

    yielded_chunks = []
    async for chunk in gate.stream_synthesize(
        text="First request speech",
        request_id=req1.request_id,
        version=req1.conversation_version,
        session_id=session.session_id,
        state_mgr=session.state_mgr,
        on_stale_discard=session.record_stale_discard,
    ):
        yielded_chunks.append(chunk)

    assert len(yielded_chunks) < 10
    types = [e.event_type for e in event_logger.get_events()]
    assert VoiceEventType.RIME_STREAM_CANCELLED in types
    assert len(session.stale_discards) >= 1


# -----------------------------------------------------------------------------
# Test 8: Buffered Stale Audio Discarded (Level 2 Buffer Gate)
# -----------------------------------------------------------------------------
def test_buffered_stale_audio_discarded(event_logger: VoiceEventLogger, session: Session):
    """Verifies Level 2 Gate purges buffered chunks when active version advances."""
    gate = RimeTTSGate(event_logger=event_logger)

    req1 = session.create_request("Turn 1")
    buffered = [
        StreamedAudioChunk(request_id=req1.request_id, conversation_version=1, chunk_index=0, data=b"chunk0"),
        StreamedAudioChunk(request_id=req1.request_id, conversation_version=1, chunk_index=1, data=b"chunk1"),
    ]

    # Advance to Turn 2
    session.create_request("Turn 2")

    filtered = gate.filter_buffered_chunks(
        chunks=buffered,
        state=session.state_mgr.state,
        session_id=session.session_id,
        on_stale_discard=session.record_stale_discard,
    )

    assert len(filtered) == 0
    types = [e.event_type for e in event_logger.get_events()]
    assert VoiceEventType.STALE_AUDIO_DISCARDED in types
    assert len(session.stale_discards) >= 2


# -----------------------------------------------------------------------------
# Test 9: Request Replacement During Synthesis
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_replacement_during_synthesis(mock_config: RimeConfig, event_logger: VoiceEventLogger, session: Session):
    """Stress test: Req 1 starts synthesis, Req 2 supersedes; Req 1 is discarded, Req 2 succeeds."""
    transport = create_mock_transport_success(chunk_count=5, chunk_delay=0.02)
    client = RimeClient(config=mock_config, transport=transport)
    gate = RimeTTSGate(event_logger=event_logger, tts_client=client)

    req1 = session.create_request("Turn 1")
    t1_chunks = []
    
    async def run_req1():
        try:
            async for chunk in gate.stream_synthesize(
                text="Turn 1 speech",
                request_id=req1.request_id,
                version=req1.conversation_version,
                session_id=session.session_id,
                state_mgr=session.state_mgr,
                on_stale_discard=session.record_stale_discard,
            ):
                t1_chunks.append(chunk)
        except Exception:
            pass

    task1 = asyncio.create_task(run_req1())

    await asyncio.sleep(0.03)

    req2 = session.create_request("Turn 2 supersedes")
    t2_chunks = []

    async for chunk in gate.stream_synthesize(
        text="Turn 2 speech",
        request_id=req2.request_id,
        version=req2.conversation_version,
        session_id=session.session_id,
        state_mgr=session.state_mgr,
        on_stale_discard=session.record_stale_discard,
    ):
        t2_chunks.append(chunk)

    await task1

    assert len(t1_chunks) < 5  # Cut short
    assert len(t2_chunks) == 6  # 5 data chunks + 1 final chunk
    assert t2_chunks[-1].is_final is True


# -----------------------------------------------------------------------------
# Test 10: HTTP 401 Authentication Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_401_authentication_error(mock_config: RimeConfig):
    """Verifies HTTP 401 raises RimeAuthenticationError without credential leakage."""
    async def mock_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, text="Unauthorized: Invalid API Key")

    client = RimeClient(config=mock_config, transport=httpx.MockTransport(mock_401))

    with pytest.raises(RimeAuthenticationError) as exc_info:
        async for _ in client.stream_speech("Hello", "req-1", 1):
            pass

    assert "401" in str(exc_info.value)
    assert mock_config.api_key not in str(exc_info.value)


# -----------------------------------------------------------------------------
# Test 11: HTTP 429 Rate Limit Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_429_rate_limit(mock_config: RimeConfig):
    """Verifies HTTP 429 raises RimeRateLimitError."""
    async def mock_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=429, text="Too Many Requests")

    client = RimeClient(config=mock_config, transport=httpx.MockTransport(mock_429))

    with pytest.raises(RimeRateLimitError):
        async for _ in client.stream_speech("Hello", "req-1", 1):
            pass


# -----------------------------------------------------------------------------
# Test 12: HTTP 5xx Server Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_5xx_server_error(mock_config: RimeConfig):
    """Verifies HTTP 500 raises RimeServerError."""
    async def mock_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, text="Internal Server Error")

    client = RimeClient(config=mock_config, transport=httpx.MockTransport(mock_500))

    with pytest.raises(RimeServerError):
        async for _ in client.stream_speech("Hello", "req-1", 1):
            pass


# -----------------------------------------------------------------------------
# Test 13: Timeout Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_timeout_error(mock_config: RimeConfig):
    """Verifies timeout raises RimeTimeoutError."""
    async def mock_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out")

    client = RimeClient(config=mock_config, transport=httpx.MockTransport(mock_timeout))

    with pytest.raises(RimeTimeoutError):
        async for _ in client.stream_speech("Hello", "req-1", 1):
            pass


# -----------------------------------------------------------------------------
# Test 14: Network Connection Failure
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_network_connection_failure(mock_config: RimeConfig):
    """Verifies connection failure raises RimeConnectionError."""
    async def mock_connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to resolve host")

    client = RimeClient(config=mock_config, transport=httpx.MockTransport(mock_connect_error))

    with pytest.raises(RimeConnectionError):
        async for _ in client.stream_speech("Hello", "req-1", 1):
            pass


# -----------------------------------------------------------------------------
# Test 15: Configuration Error
# -----------------------------------------------------------------------------
def test_configuration_error():
    """Verifies missing API key or invalid parameters trigger RimeConfigError / RimeAuthenticationError."""
    bad_config = RimeConfig(api_key="", endpoint="https://users.rime.ai/v1/rime-tts")
    with pytest.raises(RimeAuthenticationError):
        bad_config.validate_config(require_key=True)

    invalid_url = RimeConfig(api_key="valid_key", endpoint="invalid_url_no_scheme")
    with pytest.raises(RimeConfigError):
        invalid_url.validate_config(require_key=True)

    invalid_rate = RimeConfig(api_key="valid_key", sample_rate=999999)
    with pytest.raises(RimeConfigError):
        invalid_rate.validate_config(require_key=True)


# -----------------------------------------------------------------------------
# Test 16: Level 3 Final Playback Gate
# -----------------------------------------------------------------------------
def test_level_3_final_playback_gate(event_logger: VoiceEventLogger, session: Session):
    """Verifies Level 3 Playback Gate rejects obsolete frames right before speaker rendering."""
    gate = RimeTTSGate(event_logger=event_logger)

    req1 = session.create_request("Request 1")
    chunk_v1 = StreamedAudioChunk(request_id=req1.request_id, conversation_version=1, chunk_index=0, data=b"frame1")

    # While Request 1 is active, playback is permitted
    assert gate.can_play_chunk(chunk_v1, session.state_mgr.state, session.session_id) is True

    # User interrupts or starts Request 2
    session.interrupt("User vocalized 'Stop'")

    # Playback of Request 1 is now blocked
    assert gate.can_play_chunk(chunk_v1, session.state_mgr.state, session.session_id, on_stale_discard=session.record_stale_discard) is False
    types = [e.event_type for e in event_logger.get_events()]
    assert VoiceEventType.AUDIO_OUTPUT_STOPPED in types
    assert len(session.stale_discards) >= 1
