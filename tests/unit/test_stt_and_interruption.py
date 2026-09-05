"""Unit tests for VoiceFlow STT abstraction, MockSTTClient, and WebSocket Realtime Control Channel."""

import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models import RequestStatus, VoiceEventType
from app.stt.base import STTEvent, STTEventType
from app.stt.mock_stt import MockSTTClient


@pytest.mark.asyncio
async def test_mock_stt_client_lifecycle_and_event_dispatch():
    """Verify MockSTTClient start/stop lifecycle and event subscription/dispatch."""
    client = MockSTTClient()
    assert client.is_running is False

    await client.start()
    assert client.is_running is True

    received_events = []

    def handle_event(event: STTEvent):
        received_events.append(event)

    client.add_listener(handle_event)

    # 1. Emit SPEECH_STARTED
    e1 = await client.emit_speech_started()
    assert e1.event_type == STTEventType.SPEECH_STARTED
    assert len(received_events) == 1

    # 2. Emit INTERIM_TRANSCRIPT
    e2 = await client.emit_interim_transcript("Find me a")
    assert e2.event_type == STTEventType.INTERIM_TRANSCRIPT
    assert e2.text == "Find me a"
    assert e2.is_final is False
    assert len(received_events) == 2

    # 3. Emit FINAL_TRANSCRIPT
    e3 = await client.emit_final_transcript("Find me a train from Nagpur to Mumbai")
    assert e3.event_type == STTEventType.FINAL_TRANSCRIPT
    assert e3.text == "Find me a train from Nagpur to Mumbai"
    assert e3.is_final is True
    assert len(received_events) == 3

    # 4. Emit SPEECH_ENDED
    e4 = await client.emit_speech_ended()
    assert e4.event_type == STTEventType.SPEECH_ENDED
    assert len(received_events) == 4

    # Remove listener
    client.remove_listener(handle_event)
    await client.emit_speech_started()
    assert len(received_events) == 4  # Unchanged

    await client.stop()
    assert client.is_running is False


@pytest.mark.asyncio
async def test_stt_speech_started_triggers_interruption_and_fast_cut(test_session):
    """Verify STT voice onset / SPEECH_STARTED interrupts active turn and silences output."""
    mock_stt = MockSTTClient()
    await mock_stt.start()

    # User creates turn v1
    req = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1
    assert req.status == RequestStatus.RUNNING

    # Attach STT listener that interrupts session on SPEECH_STARTED
    async def on_stt_event(event: STTEvent):
        if event.event_type == STTEventType.SPEECH_STARTED:
            test_session.interrupt(reason="User voice onset detected")

    mock_stt.add_listener(on_stt_event)

    # User speaks while turn is running
    await mock_stt.emit_speech_started()

    # Verify turn v1 was invalidated and audio output halted
    assert req.status == RequestStatus.OBSOLETE
    assert req.is_cancelled is True

    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    interrupt_events = [e for e in events if e.event_type == VoiceEventType.INTERRUPT_TRIGGERED]
    assert len(interrupt_events) == 1
    assert "User voice onset detected" in interrupt_events[0].message

    await mock_stt.stop()


@pytest.mark.asyncio
async def test_stt_interim_transcript_does_not_advance_version(test_session):
    """Verify interim transcripts provide partial text without advancing version or creating requests."""
    mock_stt = MockSTTClient()
    await mock_stt.start()

    req = test_session.create_request(prompt="Initial turn")
    assert test_session.active_version == 1

    # Simulate interim stream
    await mock_stt.emit_interim_transcript("Find")
    await mock_stt.emit_interim_transcript("Find me")
    await mock_stt.emit_interim_transcript("Find me a train")

    # Version and active request must remain completely unchanged
    assert test_session.active_version == 1
    assert test_session.active_request_id == req.request_id
    assert req.status == RequestStatus.RUNNING

    await mock_stt.stop()


@pytest.mark.asyncio
async def test_stt_final_transcript_advances_turn_and_updates_version(test_session):
    """Verify final transcript advances conversation version and creates a new request."""
    mock_stt = MockSTTClient()
    await mock_stt.start()

    assert test_session.active_version == 0

    async def on_stt_event(event: STTEvent):
        if event.event_type == STTEventType.FINAL_TRANSCRIPT and event.text.strip():
            test_session.create_request(prompt=event.text)

    mock_stt.add_listener(on_stt_event)

    # Emit complete turn
    await mock_stt.simulate_turn(
        interim_texts=["Find me", "Find me a train"],
        final_text="Find me a train from Nagpur to Mumbai",
        step_delay_ms=0,
    )

    assert test_session.active_version == 1
    assert test_session.active_request_id is not None
    assert test_session.state_mgr.state.history[-1].role == "user"
    assert "Nagpur to Mumbai" in test_session.state_mgr.state.history[-1].content

    await mock_stt.stop()


def test_websocket_endpoint_connection_and_protocol():
    """Verify WebSocket connection, handshake, state sync, ping/pong, and speech events."""
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws/session/sess-ws-test") as ws:
        # 1. First message is initial STATE_SYNC
        initial_sync = ws.receive_json()
        assert initial_sync["type"] == "STATE_SYNC"
        assert initial_sync["session_id"] == "sess-ws-test"
        assert initial_sync["agent_status"] == "idle"

        # 2. Ping-Pong test
        ws.send_json({"type": "PING", "timestamp": 12345})
        pong = ws.receive_json()
        assert pong["type"] == "PONG"
        assert pong["timestamp"] == 12345

        # 3. Interim transcript broadcast test
        ws.send_json({"type": "INTERIM_TRANSCRIPT", "text": "Looking for"})
        interim_res = ws.receive_json()
        assert interim_res["type"] == "TRANSCRIPT_INTERIM"
        assert interim_res["text"] == "Looking for"

        # 4. Speech started / barge-in test
        ws.send_json({"type": "SPEECH_STARTED", "reason": "Barge-in detected"})
        ack = ws.receive_json()
        assert ack["type"] == "INTERRUPT_ACKNOWLEDGED"
        assert ack["session_id"] == "sess-ws-test"

        state_after_interrupt = ws.receive_json()
        assert state_after_interrupt["type"] == "STATE_SYNC"
        assert state_after_interrupt["agent_status"] == "listening"

        # 5. GET_STATE query test
        ws.send_json({"type": "GET_STATE"})
        state_res = ws.receive_json()
        assert state_res["type"] == "STATE_SYNC"
        assert "slots" in state_res
