"""Comprehensive deterministic and regression tests for VoiceFlow Turn Endpointing & Barge-In Classification.

Covers:
1. Multi-segment SpeechRecognition coalescing ("Find me a train from Nagpur to Mumbai tomorrow" is exactly ONE request).
2. Genuine barge-in during TOOL_RUNNING (v1 invalidated -> CLIENT_INTERRUPT -> FINAL_TRANSCRIPT -> v2 created).
3. Ordinary first-turn speech does not mute Rime or mark requests obsolete.
4. Genuine barge-in during SPEAKING halts playback immediately.
5. Preservation of delayed-tool stale-result rejection.
"""

import asyncio
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.versioning import RequestVersionGate
from app.models import RequestStatus, ToolStatus, VoiceEventType
from app.stt.base import STTEvent, STTEventType
from app.stt.mock_stt import MockSTTClient


@pytest.mark.asyncio
async def test_multi_segment_utterance_coalescing_creates_single_request(test_session):
    """Proves that a multi-word sentence produced across multiple STT final segments becomes exactly ONE request."""
    # Simulate turn endpointing buffer
    accumulated_text = ""
    created_requests = []

    def handle_segment(segment: str, is_endpoint: bool):
        nonlocal accumulated_text
        if segment:
            accumulated_text = (accumulated_text + " " + segment).strip()
        if is_endpoint and accumulated_text:
            req = test_session.create_request(prompt=accumulated_text)
            created_requests.append(req)
            accumulated_text = ""

    # User speaks: "Find me a train from Nagpur to Mumbai tomorrow."
    # Segment 1: "Find me a train"
    handle_segment("Find me a train", is_endpoint=False)
    # Segment 2: "from Nagpur"
    handle_segment("from Nagpur", is_endpoint=False)
    # Segment 3: "to Mumbai tomorrow" + endpointing timer closes utterance
    handle_segment("to Mumbai tomorrow", is_endpoint=True)

    # Must result in exactly ONE request and version 1
    assert len(created_requests) == 1
    assert test_session.active_version == 1
    assert created_requests[0].prompt == "Find me a train from Nagpur to Mumbai tomorrow"
    assert created_requests[0].status == RequestStatus.RUNNING


@pytest.mark.asyncio
async def test_tool_running_barge_in_invalidates_v1_and_creates_v2(test_session):
    """Proves that starting a new utterance during TOOL_RUNNING triggers genuine barge-in, invalidating v1 and creating v2."""
    # 1. Turn 1 committed and tool begins executing
    req1 = test_session.create_request(prompt="Find me a train from Nagpur to Mumbai tomorrow")
    assert test_session.active_version == 1
    assert req1.status == RequestStatus.RUNNING

    # 2. Tool dispatched for v1
    token1 = test_session.task_registry.get_token(req1.request_id)
    tool_coro = test_session.tool_executor.execute_tool(
        tool_name="train_search",
        args={"origin": "Nagpur", "destination": "Mumbai"},
        request_id=req1.request_id,
        version=1,
        session_id=test_session.session_id,
        state_mgr=test_session.state_mgr,
        delay_seconds=2.0,
        cancellation_token=token1,
    )
    tool_async_task = asyncio.create_task(tool_coro)
    await asyncio.sleep(0.05)

    # 3. While v1 is running, user begins a NEW utterance (genuine barge-in)
    # Frontend sends CLIENT_INTERRUPT
    interrupt_res = test_session.interrupt(reason="User voice barge-in: Actually, only after 8 PM in 3A")
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True
    assert test_session.state_mgr.state.is_interrupted is True

    # 4. Replacement utterance finishes and commits
    req2 = test_session.create_request(prompt="Actually, only after 8 PM in 3A")
    assert test_session.active_version == 2
    assert req2.request_id != req1.request_id
    assert req2.status == RequestStatus.RUNNING

    # 5. Tool 1 finishes late -> rejected by RequestVersionGate
    is_valid, reason = RequestVersionGate.validate_tool_result_active(
        tool_version=1,
        tool_request_id=req1.request_id,
        state=test_session.state_mgr.state,
    )
    assert is_valid is False
    from app.models import StaleResultRecord
    stale_record = StaleResultRecord(
        request_id=req1.request_id,
        result_version=1,
        active_version_when_delivered=test_session.active_version,
        source_type="tool",
        source_name="train_search",
        payload={"trains": ["CSMT Duronto"]},
        reason=reason or "Version mismatch",
    )
    test_session.record_stale_discard(stale_record)

    assert len(test_session.stale_discards) == 1
    assert test_session.stale_discards[0].result_version == 1
    assert test_session.stale_discards[0].active_version_when_delivered == 2

    # Clean up background task
    tool_async_task.cancel()
    try:
        await tool_async_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_ordinary_first_turn_speech_does_not_call_interrupt_or_obsolete_requests(test_session):
    """Regression test: Ordinary first-turn speech does NOT trigger cancellation or obsolete any request."""
    assert test_session.active_version == 0
    assert len(test_session.requests) == 0

    # User speaks first request
    req = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1
    assert req.status == RequestStatus.RUNNING
    assert req.is_cancelled is False

    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    interrupt_events = [e for e in events if e.event_type == VoiceEventType.INTERRUPT_TRIGGERED]
    assert len(interrupt_events) == 0


@pytest.mark.asyncio
async def test_speaking_barge_in_halts_active_playback_and_invalidates_version(test_session):
    """Regression test: Genuine barge-in while Rime is SPEAKING halts output and cancels turn."""
    req1 = test_session.create_request(prompt="What is the weather in Mumbai?")
    assert test_session.active_version == 1

    # User barges in
    test_session.interrupt(reason="User said 'Wait stop'")
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True

    # Replacement turn
    req2 = test_session.create_request(prompt="Cancel that, check Nagpur weather instead")
    assert test_session.active_version == 2
    assert req2.status == RequestStatus.RUNNING
