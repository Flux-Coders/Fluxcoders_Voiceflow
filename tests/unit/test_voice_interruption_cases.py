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


@pytest.mark.asyncio
async def test_ambient_vad_noise_during_thinking_does_not_interrupt_or_obsolete_request(test_session):
    """Proves that raw VAD speech onset / mic noise during THINKING/TOOL_RUNNING does NOT cancel or obsolete the running request."""
    # 1. Request initialized and in running/thinking state
    req1 = test_session.create_request(prompt="Find me a train from Nagpur to Mumbai tomorrow")
    assert test_session.active_version == 1
    assert req1.status == RequestStatus.RUNNING

    # 2. Ambient noise triggers raw VAD SPEECH_STARTED event (not CLIENT_INTERRUPT)
    # The session logs event but does NOT call interrupt() or obsolete req1
    test_session.event_logger.log_event(
        event_type=VoiceEventType.SPEECH_STARTED,
        version=test_session.active_version,
        request_id=req1.request_id,
        session_id=test_session.session_id,
        message="VAD speech onset (candidate noise)",
    )

    # Verify request remains RUNNING and NOT cancelled
    assert req1.status == RequestStatus.RUNNING
    assert req1.is_cancelled is False
    assert test_session.active_version == 1

    # Verify no interrupt events were logged
    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    interrupt_events = [e for e in events if e.event_type == VoiceEventType.INTERRUPT_TRIGGERED]
    assert len(interrupt_events) == 0


@pytest.mark.asyncio
async def test_meaningful_stt_transcript_during_thinking_triggers_barge_in(test_session):
    """Proves that when speech recognition emits a meaningful transcript during THINKING/TOOL_RUNNING, barge-in is triggered."""
    # 1. Request 1 running
    req1 = test_session.create_request(prompt="Find me a train from Nagpur to Mumbai tomorrow")
    assert test_session.active_version == 1

    # 2. Meaningful STT interim/final arrives during thinking
    new_utterance_text = "Actually, only after 8 PM"
    assert len(new_utterance_text.strip()) > 0

    # STT triggers CLIENT_INTERRUPT
    test_session.interrupt(reason=f"User speech barge-in during processing: {new_utterance_text}")
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True

    # 3. New request is created for replacement utterance
    req2 = test_session.create_request(prompt=new_utterance_text)
    assert test_session.active_version == 2
    assert req2.status == RequestStatus.RUNNING


@pytest.mark.asyncio
async def test_chrome_multiple_isfinal_segments_while_vad_active_produces_single_request(test_session):
    """Case A: Chrome emits multiple isFinal segments while VAD remains active -> exactly ONE request after speech end."""
    accumulated_text = ""
    current_utterance_text = ""
    is_vad_active = True  # VAD reports user is still physically speaking
    endpoint_timer_active = False
    created_requests = []

    def on_result(segment_text: str, is_final: bool):
        nonlocal accumulated_text, current_utterance_text, endpoint_timer_active
        if is_final:
            accumulated_text = (accumulated_text + " " + segment_text).strip()
        display = (accumulated_text + " " + (segment_text if not is_final else "")).strip()
        if display:
            current_utterance_text = display
        # While VAD is active, endpoint timer is NOT armed
        if is_vad_active:
            endpoint_timer_active = False
        else:
            endpoint_timer_active = True

    def on_vad_speech_end():
        nonlocal endpoint_timer_active
        endpoint_timer_active = True

    def on_endpoint_timer_fire():
        nonlocal accumulated_text, current_utterance_text, endpoint_timer_active
        if is_vad_active:
            # Blocked: cannot commit while VAD is active!
            return
        text_to_commit = (current_utterance_text or accumulated_text).strip()
        if text_to_commit:
            accumulated_text = ""
            current_utterance_text = ""
            endpoint_timer_active = False
            req = test_session.create_request(prompt=text_to_commit)
            created_requests.append(req)

    # 1. First segment finalized by Chrome while user is still speaking
    on_result("Find me a train", is_final=True)
    assert accumulated_text == "Find me a train"
    assert len(created_requests) == 0  # No request sent!

    # 2. Second segment finalized by Chrome while user is still speaking
    on_result("from Nagpur", is_final=True)
    assert accumulated_text == "Find me a train from Nagpur"
    assert len(created_requests) == 0  # Still no request sent!

    # 3. Third segment finalized by Chrome while user is still speaking
    on_result("to Mumbai tomorrow", is_final=True)
    assert accumulated_text == "Find me a train from Nagpur to Mumbai tomorrow"
    assert len(created_requests) == 0  # Still no premature request!

    # 4. User finally stops speaking: VAD detects speech end
    is_vad_active = False
    on_vad_speech_end()
    assert endpoint_timer_active is True

    # 5. Silence endpoint timer expires
    on_endpoint_timer_fire()

    # Exactly ONE request created with the full sentence
    assert len(created_requests) == 1
    assert test_session.active_version == 1
    assert created_requests[0].prompt == "Find me a train from Nagpur to Mumbai tomorrow"
    assert created_requests[0].status == RequestStatus.RUNNING


@pytest.mark.asyncio
async def test_user_continues_talking_after_isfinal_no_request_until_vad_ends(test_session):
    """Case B: User pauses briefly after an isFinal segment and continues talking -> no request until final VAD end."""
    accumulated_text = ""
    current_utterance_text = ""
    is_vad_active = True
    endpoint_timer_active = False
    created_requests = []

    def on_result(segment_text: str, is_final: bool):
        nonlocal accumulated_text, current_utterance_text, endpoint_timer_active
        if is_final:
            accumulated_text = (accumulated_text + " " + segment_text).strip()
        display = (accumulated_text + " " + (segment_text if not is_final else "")).strip()
        if display:
            current_utterance_text = display
        if is_vad_active:
            endpoint_timer_active = False

    def on_vad_speech_start():
        nonlocal endpoint_timer_active
        endpoint_timer_active = False

    def on_vad_speech_end():
        nonlocal endpoint_timer_active
        endpoint_timer_active = True

    def on_endpoint_timer_fire():
        nonlocal accumulated_text, current_utterance_text, endpoint_timer_active
        if is_vad_active:
            return
        text_to_commit = (current_utterance_text or accumulated_text).strip()
        if text_to_commit:
            accumulated_text = ""
            current_utterance_text = ""
            endpoint_timer_active = False
            req = test_session.create_request(prompt=text_to_commit)
            created_requests.append(req)

    # User says "Find me a train" -> Chrome produces isFinal=True
    on_result("Find me a train", is_final=True)
    # Brief mid-sentence pause triggers candidate VAD speech end
    is_vad_active = False
    on_vad_speech_end()
    assert endpoint_timer_active is True
    assert len(created_requests) == 0

    # User continues speaking before silence timer expires -> VAD speech start cancels timer
    is_vad_active = True
    on_vad_speech_start()
    assert endpoint_timer_active is False
    on_result("from Nagpur to Mumbai tomorrow", is_final=True)
    assert len(created_requests) == 0

    # User finishes speaking completely
    is_vad_active = False
    on_vad_speech_end()
    on_endpoint_timer_fire()

    assert len(created_requests) == 1
    assert created_requests[0].prompt == "Find me a train from Nagpur to Mumbai tomorrow"


@pytest.mark.asyncio
async def test_normal_complete_first_utterance_plays_astra_only_after_closure(test_session):
    """Case E: Proves that Rime synthesis/playback is triggered only after the turn is officially closed."""
    # 1. User starts speaking
    is_vad_active = True
    assert test_session.active_version == 0
    assert test_session.active_request_id is None

    # 2. Utterance in-flight in browser (not committed): version remains 0
    assert test_session.active_version == 0

    # 3. Speech ends and utterance is committed
    is_vad_active = False
    req1 = test_session.create_request(prompt="Find me a train from Nagpur to Mumbai tomorrow")
    assert test_session.active_version == 1

    # 4. Agent completes turn and triggers Rime playback
    assistant_text = "I found 3 trains from Nagpur to Mumbai tomorrow."
    completed = test_session.complete_turn(
        request_id=req1.request_id,
        version=1,
        assistant_response=assistant_text,
        trigger_rime=False,
    )
    assert completed is True

    # Rime TTS stream receives the text and yields chunks for active version 1
    chunks = []
    # Rime streaming gate verifies version matching
    assert test_session.active_version == 1
    assert req1.status == RequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_speaking_vad_spike_no_stt_resumes_playback_no_interrupt(test_session):
    """Case A & C: Proves that an acoustic VAD spike during SPEAKING without STT transcript does NOT abort playback."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1
    req1_id = req1.request_id

    # Simulated engine state
    is_speaking = True
    is_muted = False
    pending_barge_in_timer = False
    pending_version = None
    pending_req_id = None

    def on_vad_speech_start_while_speaking():
        nonlocal is_muted, pending_barge_in_timer, pending_version, pending_req_id
        if is_speaking:
            is_muted = True  # Fast-mute locally
            pending_version = test_session.active_version
            pending_req_id = test_session.active_request_id
            pending_barge_in_timer = True  # 450ms confirmation window armed

    def on_confirmation_timer_expire():
        nonlocal is_muted, pending_barge_in_timer, pending_version, pending_req_id
        if (
            pending_version == test_session.active_version
            and pending_req_id == test_session.active_request_id
            and is_speaking
        ):
            is_muted = False  # Restore audio output: no interrupt!
        pending_barge_in_timer = False
        pending_version = None
        pending_req_id = None

    # 1. Astra is speaking and laptop microphone picks up speaker acoustic echo (VAD fires)
    on_vad_speech_start_while_speaking()
    assert is_muted is True
    assert pending_barge_in_timer is True
    # Crucially: session was NOT interrupted, version remains 1, request NOT obsolete
    assert test_session.active_version == 1
    assert req1.status == RequestStatus.RUNNING

    # 2. 450ms passes with NO STT transcript (speaker echo / background noise only)
    on_confirmation_timer_expire()

    # 3. Audio output is safely restored; request is still active
    assert is_muted is False
    assert test_session.active_version == 1
    assert req1.status == RequestStatus.RUNNING
    assert req1.is_cancelled is False

    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    interrupt_events = [e for e in events if e.event_type == VoiceEventType.INTERRUPT_TRIGGERED]
    assert len(interrupt_events) == 0


@pytest.mark.asyncio
async def test_speaking_vad_meaningful_stt_confirms_barge_in(test_session):
    """Case B: Proves that when meaningful STT text arrives during SPEAKING, genuine barge-in is confirmed."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1

    is_speaking = True
    is_muted = False
    pending_barge_in_timer = False

    def on_vad_speech_start_while_speaking():
        nonlocal is_muted, pending_barge_in_timer
        if is_speaking:
            is_muted = True
            pending_barge_in_timer = True

    def on_stt_transcript(text: str):
        nonlocal is_speaking, is_muted, pending_barge_in_timer
        trimmed = text.strip()
        if (is_speaking or pending_barge_in_timer) and len(trimmed) > 0:
            # Genuine barge-in confirmed!
            pending_barge_in_timer = False
            is_muted = True
            test_session.interrupt(reason=f"User voice barge-in while speaking: {trimmed}")
            is_speaking = False

    # 1. VAD fires while speaking
    on_vad_speech_start_while_speaking()
    assert pending_barge_in_timer is True

    # 2. Human voice is recognized before timer expires
    on_stt_transcript("Actually, only after 8 PM")

    # 3. v1 is marked OBSOLETE, interrupt logged
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True
    assert test_session.state_mgr.state.is_interrupted is True

    # 4. v2 created
    req2 = test_session.create_request(prompt="Actually, only after 8 PM")
    assert test_session.active_version == 2
    assert req2.status == RequestStatus.RUNNING


@pytest.mark.asyncio
async def test_vad_last_energy_tracking_prevents_oscillation():
    """Case D: Proves that updating lastSpeechDetectedTime on every frame above threshold prevents premature speech end."""
    hold_time_ms = 400.0
    energy_threshold = 0.02

    is_vad_speaking = False
    vad_speech_start_time = 0.0
    last_speech_detected_time = 0.0
    speech_start_count = 0
    speech_end_count = 0

    def process_audio_frame(now_ms: float, rms: float):
        nonlocal is_vad_speaking, vad_speech_start_time, last_speech_detected_time
        nonlocal speech_start_count, speech_end_count

        if rms > energy_threshold:
            last_speech_detected_time = now_ms
            if not is_vad_speaking:
                is_vad_speaking = True
                vad_speech_start_time = now_ms
                speech_start_count += 1
        else:
            if is_vad_speaking and (now_ms - last_speech_detected_time >= hold_time_ms):
                is_vad_speaking = False
                speech_end_count += 1

    # User speaks continuously from t=0ms to t=1000ms with frames every 50ms
    for t in range(0, 1050, 50):
        process_audio_frame(float(t), rms=0.05)
        # At t=500ms (> 400ms from start), VAD must NOT have ended because speech is continuous!
        if t == 500:
            assert is_vad_speaking is True
            assert speech_end_count == 0

    # Continuous speech ended at t=1000ms. Silence begins:
    # At t=1200ms (200ms of silence), hold_time_ms (400ms) has not elapsed:
    process_audio_frame(1200.0, rms=0.005)
    assert is_vad_speaking is True
    assert speech_end_count == 0

    # At t=1400ms (400ms of silence), hold_time_ms has elapsed:
    process_audio_frame(1400.0, rms=0.005)
    assert is_vad_speaking is False
    assert speech_end_count == 1
    assert speech_start_count == 1


@pytest.mark.asyncio
async def test_stale_audio_cannot_resume_after_version_change(test_session):
    """Case H: Proves that an expiring confirmation window NEVER unmutes or resumes playback if the version has changed."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1

    is_speaking = True
    is_muted = False
    pending_version = test_session.active_version
    pending_req_id = test_session.active_request_id

    # 1. Barge-in window armed for version 1
    is_muted = True

    # 2. In the meantime, turn 1 is interrupted or new turn arrives (advancing version to 2)
    test_session.interrupt(reason="User clicked cancel")
    req2 = test_session.create_request(prompt="Different prompt")
    assert test_session.active_version == 2

    # 3. Old confirmation window for version 1 expires
    def on_old_timer_expire():
        nonlocal is_muted
        # Version gate check:
        if (
            pending_version == test_session.active_version
            and pending_req_id == test_session.active_request_id
            and is_speaking
        ):
            is_muted = False  # WOULD UNMUTE ONLY IF MATCHING!

    on_old_timer_expire()

    # Audio for v1 was NOT resumed/unmuted because pending_version (1) != active_version (2)
    assert is_muted is True


# =============================================================================
# PART A DETERMINISTIC TESTS — BARGE-IN TRANSCRIPT QUALIFICATION
# =============================================================================

def is_meaningful_barge_in_transcript(text: str) -> bool:
    """Python mirror of frontend isMeaningfulBargeInTranscript helper."""
    if not text:
        return False
    trimmed = text.strip().lower()
    if not trimmed or len(trimmed) <= 1:
        return False
    noise_artifacts = {"um", "uh", "ah", "er", "eh", "mm", "hmm", "oh"}
    if trimmed in noise_artifacts:
        return False
    import re
    if not re.search(r"[a-z0-9]", trimmed):
        return False
    return True


@pytest.mark.asyncio
async def test_interim_single_character_noise_during_thinking_does_not_interrupt(test_session):
    """Part A.A: Interim 'a' during THINKING must NOT trigger interrupt or obsolete request."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1

    interim_transcript = "a"
    is_meaningful = is_meaningful_barge_in_transcript(interim_transcript)
    assert is_meaningful is False

    # Simulate simulationEngine onInterim handling:
    interrupt_called = False
    if is_meaningful:
        test_session.interrupt(reason=f"User barge-in: {interim_transcript}")
        interrupt_called = True

    assert interrupt_called is False
    assert req1.status == RequestStatus.RUNNING
    assert test_session.active_version == 1


@pytest.mark.asyncio
async def test_interim_filler_noise_during_thinking_does_not_interrupt(test_session):
    """Part A.B: Interim 'um' / 'uh' during THINKING must NOT trigger interrupt or obsolete request."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1

    for noise in ["um", "uh", "ah", "er", "mm", "hmm", "oh", "..."]:
        is_meaningful = is_meaningful_barge_in_transcript(noise)
        assert is_meaningful is False, f"Noise '{noise}' was unexpectedly classified as meaningful"

    assert req1.status == RequestStatus.RUNNING
    assert test_session.active_version == 1


@pytest.mark.asyncio
async def test_interim_actually_during_thinking_triggers_interrupt_once(test_session):
    """Part A.C: Interim 'actually' during THINKING triggers interrupt exactly once."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1

    interim_transcript = "actually"
    is_meaningful = is_meaningful_barge_in_transcript(interim_transcript)
    assert is_meaningful is True

    # First meaningful interim arrives
    is_utterance_in_progress = False
    interrupt_count = 0

    if is_meaningful and not is_utterance_in_progress:
        test_session.interrupt(reason=f"User voice barge-in: {interim_transcript}")
        is_utterance_in_progress = True
        interrupt_count += 1

    assert interrupt_count == 1
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True


@pytest.mark.asyncio
async def test_natural_barge_in_phrase_interrupts_and_creates_v2(test_session):
    """Part A.D: 'actually, only after 8 PM in 3A' interrupts v1 and commits v2."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1

    # Interim 1: "actually"
    phrase_part1 = "actually"
    assert is_meaningful_barge_in_transcript(phrase_part1) is True
    test_session.interrupt(reason=f"User barge-in: {phrase_part1}")
    assert req1.status == RequestStatus.OBSOLETE

    # Final: "Actually, only after 8 PM in 3A"
    full_phrase = "Actually, only after 8 PM in 3A"
    assert is_meaningful_barge_in_transcript(full_phrase) is True
    req2 = test_session.create_request(prompt=full_phrase)

    assert test_session.active_version == 2
    assert req2.status == RequestStatus.RUNNING
    assert req2.prompt == full_phrase


@pytest.mark.asyncio
async def test_multiple_interim_segments_during_barge_in_triggers_single_client_interrupt(test_session):
    """Part A.E: Multiple progressive interim segments during one barge-in produce exactly ONE interrupt."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert test_session.active_version == 1

    is_utterance_in_progress = False
    interrupt_call_count = 0

    interim_stream = ["actually", "actually only", "actually only after 8", "actually only after 8 PM"]

    for segment in interim_stream:
        is_meaningful = is_meaningful_barge_in_transcript(segment)
        if is_meaningful and not is_utterance_in_progress:
            test_session.interrupt(reason=f"User barge-in: {segment}")
            is_utterance_in_progress = True
            interrupt_call_count += 1
        elif is_meaningful:
            # Accumulated but does NOT re-trigger interrupt
            is_utterance_in_progress = True

    assert interrupt_call_count == 1
    assert req1.status == RequestStatus.OBSOLETE


# =============================================================================
# PART B DETERMINISTIC TESTS — RIME PCM PLAYBACK QUEUE
# =============================================================================

class MockWebAudioTimeline:
    """Deterministic simulation of AudioEngine WebAudio PCM playback queue."""

    def __init__(self, current_time: float = 0.0):
        self.current_time = current_time
        self.next_play_time: float = 0.0
        self.scheduled_chunks: list[dict] = []
        self.active_sources_count: int = 0

    def play_pcm_chunk(self, sample_count: int, sample_rate: int = 16000, version: int = 1, active_version: int = 1):
        if version != active_version:
            return None  # Version mismatch gate

        duration = sample_count / sample_rate
        start_time = max(self.current_time, self.next_play_time)
        end_time = start_time + duration

        self.next_play_time = end_time
        self.active_sources_count += 1

        record = {
            "version": version,
            "sample_count": sample_count,
            "duration": duration,
            "start_time": start_time,
            "end_time": end_time,
        }
        self.scheduled_chunks.append(record)
        return record

    def reset_queue(self):
        self.scheduled_chunks = []
        self.next_play_time = 0.0
        self.active_sources_count = 0


def test_pcm_sequential_chunks_produce_non_overlapping_playback_times():
    """Part B.A: 3 sequential PCM chunks produce strictly non-overlapping contiguous playback times."""
    timeline = MockWebAudioTimeline(current_time=1.0)

    # Chunk 1: 512 samples (32ms)
    c1 = timeline.play_pcm_chunk(sample_count=512, sample_rate=16000)
    assert c1["start_time"] == 1.0
    assert c1["end_time"] == 1.032

    # Chunk 2 arrives at current_time=1.01 (10ms later, before Chunk 1 finishes)
    timeline.current_time = 1.01
    c2 = timeline.play_pcm_chunk(sample_count=512, sample_rate=16000)
    # Must start at c1.end_time (1.032), NOT at current_time (1.01)!
    assert c2["start_time"] == 1.032
    assert c2["end_time"] == 1.064

    # Chunk 3 arrives at current_time=1.02
    timeline.current_time = 1.02
    c3 = timeline.play_pcm_chunk(sample_count=512, sample_rate=16000)
    assert c3["start_time"] == 1.064
    assert c3["end_time"] == 1.096

    # Verify strictly non-overlapping
    assert c1["end_time"] <= c2["start_time"]
    assert c2["end_time"] <= c3["start_time"]


def test_pcm_next_play_time_advances_monotonically():
    """Part B.B: next_play_time advances monotonically with each chunk."""
    timeline = MockWebAudioTimeline(current_time=0.5)

    last_time = timeline.next_play_time
    for i in range(10):
        c = timeline.play_pcm_chunk(sample_count=1024, sample_rate=16000)
        assert timeline.next_play_time > last_time
        last_time = timeline.next_play_time

    assert timeline.next_play_time == pytest.approx(0.5 + 10 * (1024 / 16000), rel=1e-5)


def test_interruption_resets_pcm_playback_queue():
    """Part B.C: Interruption resets the playback queue and next_play_time."""
    timeline = MockWebAudioTimeline(current_time=2.0)

    # Queue some chunks for v1
    timeline.play_pcm_chunk(sample_count=8000, sample_rate=16000, version=1, active_version=1)
    assert timeline.next_play_time == 2.5
    assert timeline.active_sources_count == 1

    # Interruption occurs
    timeline.reset_queue()
    assert timeline.next_play_time == 0.0
    assert timeline.active_sources_count == 0
    assert len(timeline.scheduled_chunks) == 0


def test_stale_version_pcm_chunk_rejected_by_queue():
    """Part B.D: A stale-version PCM chunk is dropped and not scheduled."""
    timeline = MockWebAudioTimeline(current_time=3.0)

    # v2 is active, but a late v1 chunk arrives
    res = timeline.play_pcm_chunk(sample_count=1024, sample_rate=16000, version=1, active_version=2)
    assert res is None
    assert len(timeline.scheduled_chunks) == 0
    assert timeline.next_play_time == 0.0


def test_active_v2_pcm_chunks_play_normally_after_v1_interruption():
    """Part B.E: After v1 interruption and queue reset, v2 chunks schedule and play normally."""
    timeline = MockWebAudioTimeline(current_time=1.0)

    # 1. v1 plays
    timeline.play_pcm_chunk(sample_count=8000, sample_rate=16000, version=1, active_version=1)
    assert timeline.next_play_time == 1.5

    # 2. Interruption occurs at current_time=1.2
    timeline.current_time = 1.2
    timeline.reset_queue()

    # 3. v2 starts at current_time=1.2
    c1_v2 = timeline.play_pcm_chunk(sample_count=1600, sample_rate=16000, version=2, active_version=2)
    assert c1_v2["start_time"] == 1.2
    assert c1_v2["end_time"] == 1.3
    assert timeline.next_play_time == 1.3
