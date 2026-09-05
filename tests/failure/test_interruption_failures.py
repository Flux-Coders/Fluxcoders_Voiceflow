"""Failure tests for VoiceFlow Voice Input and Interruption Handling.

Rule 10 compliance: Every realtime feature must have a failure test.
"""

import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models import RequestStatus, ToolStatus, VoiceEventType
from app.stt.base import STTEvent, STTEventType
from app.stt.mock_stt import MockSTTClient


@pytest.mark.asyncio
async def test_empty_or_whitespace_final_transcript_does_not_advance_version(test_session):
    """Failure test: STT emitting empty or whitespace-only transcript must not create invalid requests."""
    mock_stt = MockSTTClient()
    await mock_stt.start()

    initial_version = test_session.active_version

    async def on_stt_event(event: STTEvent):
        if event.event_type == STTEventType.FINAL_TRANSCRIPT:
            trimmed = event.text.strip()
            if trimmed:
                test_session.create_request(prompt=trimmed)

    mock_stt.add_listener(on_stt_event)

    # Emit blank/whitespace final transcripts
    await mock_stt.emit_final_transcript("")
    await mock_stt.emit_final_transcript("   \t\n  ")

    # Verify session version and state did not advance
    assert test_session.active_version == initial_version
    assert test_session.active_request_id is None

    await mock_stt.stop()


@pytest.mark.asyncio
async def test_rapid_triple_speech_started_interruption_storm(test_session):
    """Failure test: Rapid successive SPEECH_STARTED barge-ins during in-flight operations."""
    mock_stt = MockSTTClient()
    await mock_stt.start()

    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    v1 = req1.conversation_version

    # Launch tool for v1
    t1 = asyncio.create_task(
        test_session.run_tool(req1.request_id, v1, "train_search", {}, delay_seconds=0.20)
    )

    # Rapid interruption 1
    test_session.interrupt(reason="Interruption 1")
    assert req1.status == RequestStatus.OBSOLETE

    req2 = test_session.create_request(prompt="Actually after 8 PM")
    v2 = req2.conversation_version
    t2 = asyncio.create_task(
        test_session.run_tool(req2.request_id, v2, "train_search", {}, delay_seconds=0.20)
    )

    # Rapid interruption 2
    test_session.interrupt(reason="Interruption 2")
    assert req2.status == RequestStatus.OBSOLETE

    req3 = test_session.create_request(prompt="Only 3A class")
    v3 = req3.conversation_version
    t3 = asyncio.create_task(
        test_session.run_tool(req3.request_id, v3, "train_search", {}, delay_seconds=0.01)
    )

    # Await all tools
    r3 = await t3
    r2 = await t2
    r1 = await t1

    # Verify requests 1 & 2 are obsolete and their tool returns discarded
    assert r1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r2.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r3.status == ToolStatus.COMPLETED_VALID

    # Complete active turn
    test_session.complete_turn(req3.request_id, v3, "Found 3A trains after 8 PM")
    assert test_session.active_version == 3
    assert test_session.current_answer == "Found 3A trains after 8 PM"

    await mock_stt.stop()


@pytest.mark.asyncio
async def test_late_tool_result_after_voice_barge_in_discarded(test_session):
    """Failure test: Late tool completion arriving after voice barge-in is safely rejected."""
    req1 = test_session.create_request(prompt="Long train search")
    v1 = req1.version

    # Launch delayed tool
    tool_task = asyncio.create_task(
        test_session.run_tool(
            request_id=req1.request_id,
            version=v1,
            tool_name="train_search",
            args={"origin": "Nagpur", "destination": "Mumbai"},
            delay_seconds=0.10,
        )
    )

    # User barge-in occurs while tool is executing
    await asyncio.sleep(0.02)
    test_session.interrupt(reason="Voice barge-in: 'Wait, cancel that'")

    # New turn created
    req2 = test_session.create_request(prompt="Find flights instead")
    v2 = req2.version

    # Tool 1 finishes late
    res1 = await tool_task

    # Must be marked stale/cancelled and recorded in stale discards
    assert res1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert len(test_session.stale_discards) >= 1
    assert test_session.stale_discards[0].request_id == req1.request_id
    assert test_session.active_version == v2


def test_websocket_empty_final_transcript_handled_safely():
    """Failure test: WebSocket client sending blank FINAL_TRANSCRIPT does not crash or corrupt session."""
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws/session/sess-ws-failure") as ws:
        initial_sync = ws.receive_json()
        assert initial_sync["type"] == "STATE_SYNC"
        initial_version = initial_sync["active_version"]

        # Send empty final transcript
        ws.send_json({"type": "FINAL_TRANSCRIPT", "text": "   "})

        # Send GET_STATE to verify state integrity
        ws.send_json({"type": "GET_STATE"})
        state = ws.receive_json()
        assert state["type"] == "STATE_SYNC"
        assert state["active_version"] == initial_version
