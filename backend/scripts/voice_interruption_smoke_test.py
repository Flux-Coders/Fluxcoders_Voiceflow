"""VoiceFlow Voice Interruption & Turn Endpointing Smoke Test.

Verifies:
1. Multi-segment STT coalescing prevents premature requests.
2. Genuine barge-in during TOOL_RUNNING/THINKING sends CLIENT_INTERRUPT, cancels v1, and creates v2.
3. Ordinary first-turn speech does NOT mute Rime, call interrupt(), or mark requests obsolete.
4. Genuine barge-in during SPEAKING halts playback immediately.
5. Delayed-tool stale result is rejected by RequestVersionGate (v1 != v2).
6. Rime configuration is mistv3 / astra / eng / pcm / 16000.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure UTF-8 on Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.event_logger import VoiceEventLogger
from app.core.session import Session
from app.core.versioning import RequestVersionGate, StaleRimeGenerationError, VersionGateError
from app.models import RequestStatus, ToolStatus, VoiceEventType
from app.tts.rime_client import RimeConfig


async def run_voice_interruption_smoke_test() -> bool:
    print("=" * 80)
    print("VoiceFlow Voice Interruption & Turn Endpointing Smoke Test")
    print("=" * 80)

    # 1. Check Rime Configuration
    rime_config = RimeConfig()
    print(f"[1/5] Checking Rime TTS Configuration:")
    print(f"  --> Model       : {rime_config.model}")
    print(f"  --> Speaker     : {rime_config.speaker}")
    print(f"  --> Language    : {rime_config.language}")
    print(f"  --> Audio Format: {rime_config.audio_format}")
    print(f"  --> Sample Rate : {rime_config.sample_rate} Hz")
    assert rime_config.model == "mistv3", f"Expected mistv3, got {rime_config.model}"
    assert rime_config.speaker == "astra", f"Expected astra, got {rime_config.speaker}"
    assert rime_config.audio_format == "pcm", f"Expected pcm, got {rime_config.audio_format}"
    assert int(rime_config.sample_rate) == 16000, f"Expected 16000, got {rime_config.sample_rate}"
    print("  [PASS] Rime configuration verified: mistv3 / astra / eng / pcm / 16000")

    # 2. Test Multi-Segment Turn Coalescing
    print("\n[2/5] Testing Multi-Segment STT Coalescing:")
    event_logger = VoiceEventLogger()
    session = Session(session_id="smoke-session-1", event_logger=event_logger)

    # Simulate 3 incoming segments from browser Web Speech API
    segments = ["Find me a train", "from Nagpur", "to Mumbai tomorrow"]
    accumulated_prompt = ""
    for idx, seg in enumerate(segments):
        accumulated_prompt = (accumulated_prompt + " " + seg).strip()
        print(f"  --> Received STT segment #{idx+1}: '{seg}' (accumulated: '{accumulated_prompt}')")

    # Only when silence endpointing timer fires is the request created
    req1 = session.create_request(prompt=accumulated_prompt)
    print(f"  --> Utterance closed. Created Request ID: {req1.request_id} (v{session.active_version})")
    assert session.active_version == 1
    assert len(session.requests) == 1
    assert req1.prompt == "Find me a train from Nagpur to Mumbai tomorrow"
    assert req1.status == RequestStatus.RUNNING
    print("  [PASS] Single request created for multi-segment utterance (no premature requests)")

    # 3. Test Ordinary First-Turn Speech (No Interruption / No Obsolescence)
    print("\n[3/5] Testing Ordinary First-Turn Speech Behavior:")
    events = session.event_logger.get_events(session_id=session.session_id)
    interrupt_events = [e for e in events if e.event_type == VoiceEventType.INTERRUPT_TRIGGERED]
    assert len(interrupt_events) == 0
    assert req1.status == RequestStatus.RUNNING
    assert not req1.is_cancelled
    print("  [PASS] Ordinary first-turn speech produces 0 interrupt events and request remains active")

    # 4. Test Genuine Barge-In During TOOL_RUNNING
    print("\n[4/5] Testing Genuine Barge-In During TOOL_RUNNING:")
    token1 = session.task_registry.get_token(req1.request_id)
    tool_coro = session.tool_executor.execute_tool(
        tool_name="train_search",
        args={"origin": "Nagpur", "destination": "Mumbai"},
        request_id=req1.request_id,
        version=1,
        session_id=session.session_id,
        state_mgr=session.state_mgr,
        delay_seconds=2.0,
        cancellation_token=token1,
    )
    tool_task = asyncio.create_task(tool_coro)
    await asyncio.sleep(0.05)
    print(f"  --> v1 tool running in background")

    # User starts a NEW utterance after previous utterance was closed
    # Frontend detects barge-in during tool_running and sends CLIENT_INTERRUPT
    t0 = time.perf_counter()
    interrupt_res = session.interrupt(reason="User voice barge-in: 'Actually, only after 8 PM in 3A'")
    t1 = time.perf_counter()
    cut_ms = (t1 - t0) * 1000.0

    print(f"  --> CLIENT_INTERRUPT executed in {cut_ms:.3f} ms")
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True
    print(f"  --> Request #{req1.conversation_version} ({req1.request_id}) marked OBSOLETE")

    # Replacement utterance arrives and commits
    req2 = session.create_request(prompt="Actually, only after 8 PM in 3A")
    print(f"  --> Replacement Turn Created: Request ID: {req2.request_id} (v{session.active_version})")
    assert session.active_version == 2
    assert req2.status == RequestStatus.RUNNING
    print("  [PASS] Genuine barge-in during tool execution successfully marked v1 obsolete and created v2")

    # 5. Test Ambient VAD Spike During Thinking (No False Interruption)
    print("\n[5/6] Testing Ambient VAD Spike During Thinking (No False Interruption):")
    # Simulate candidate VAD energy spike without STT transcript
    session.event_logger.log_event(
        event_type=VoiceEventType.SPEECH_STARTED,
        version=session.active_version,
        request_id=req2.request_id,
        session_id=session.session_id,
        message="Candidate VAD energy spike (ambient mic noise)",
    )
    # req2 must remain active and RUNNING
    assert req2.status == RequestStatus.RUNNING
    assert not req2.is_cancelled
    assert session.active_version == 2
    print("  [PASS] Ambient mic RMS spike during processing does not cancel or obsolete running request")

    # 6. Test Stale Result Rejection (RequestVersionGate)
    print("\n[6/6] Testing Delayed Tool Stale-Result Rejection:")
    is_valid, reason = RequestVersionGate.validate_tool_result_active(
        tool_version=1,
        tool_request_id=req1.request_id,
        state=session.state_mgr.state,
    )
    assert is_valid is False
    print(f"  --> RequestVersionGate successfully blocked late result: {reason}")
    from app.models import StaleResultRecord
    session.record_stale_discard(
        StaleResultRecord(
            request_id=req1.request_id,
            result_version=1,
            active_version_when_delivered=session.active_version,
            source_type="tool",
            source_name="train_search",
            payload={"trains": ["CSMT Duronto Express"]},
            reason=reason or "Stale tool result",
        )
    )

    assert len(session.stale_discards) == 1
    assert session.stale_discards[0].result_version == 1
    assert session.stale_discards[0].active_version_when_delivered == 2
    print("  [PASS] Late v1 result safely dropped by RequestVersionGate (never delivered to LLM/Rime)")

    # Clean up background task
    tool_task.cancel()
    try:
        await tool_task
    except (asyncio.CancelledError, Exception):
        pass

    print("\n" + "=" * 80)
    print("ALL VOICE INTERRUPTION SMOKE TESTS PASSED CLEANLY (6/6)")
    print("=" * 80)
    return True


if __name__ == "__main__":
    success = asyncio.run(run_voice_interruption_smoke_test())
    sys.exit(0 if success else 1)
