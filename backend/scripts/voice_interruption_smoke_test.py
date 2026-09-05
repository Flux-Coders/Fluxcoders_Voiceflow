"""VoiceFlow Voice Input & Interruption Handling Smoke Test (Phase 14).

Developer-only smoke test exercising the core Hackathon Acceptance Scenario:
1. User starts request: "Find me a train from Nagpur to Mumbai tomorrow." (v1)
2. Tool execution begins with simulated latency.
3. User interrupts with speech barge-in:
   - Local VAD / SPEECH_STARTED triggers immediate audio cut and turn cancellation.
   - STT emits FINAL_TRANSCRIPT: "Actually, only after 8 PM in 3A." (v2)
4. Turn 1 late tool completion arrives -> safely rejected by RequestVersionGate as STALE.
5. Turn 2 tool completes -> accepted and synthesized via Rime TTS.
6. All latency metrics measured dynamically (zero hardcoded numbers).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure UTF-8 output encoding on Windows console
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.event_logger import VoiceEventLogger
from app.core.session import Session
from app.engine.llm_orchestrator import LLMOrchestrator
from app.engine.llm_provider import MockLLMClient
from app.models import RequestStatus, ToolStatus, VoiceEventType
from app.stt.base import STTEvent, STTEventType
from app.stt.mock_stt import MockSTTClient
from app.tools.registry import default_tool_registry


async def run_voice_interruption_smoke_test() -> None:
    print("=" * 80)
    print("VoiceFlow Phase 14: Voice Input & Interruption Handling Smoke Test")
    print("=" * 80)

    event_logger = VoiceEventLogger()
    session = Session(session_id="voice-smoke-session", event_logger=event_logger)
    mock_stt = MockSTTClient()
    llm_client = MockLLMClient()
    orchestrator = LLMOrchestrator(llm_client=llm_client, tool_registry=default_tool_registry)

    await mock_stt.start()
    print("[INIT] Session initialized (active_version=v0)")
    print("[INIT] MockSTTClient and LLMOrchestrator ready")

    # -------------------------------------------------------------------------
    # STEP 1: Turn 1 Voice Utterance
    # -------------------------------------------------------------------------
    turn1_prompt = "Find me a train from Nagpur to Mumbai tomorrow."
    print(f"\n[STEP 1] User begins speaking Turn 1: \"{turn1_prompt}\"")
    
    t_turn1_start = time.perf_counter()
    await mock_stt.emit_speech_started()
    await mock_stt.emit_interim_transcript("Find me a train")
    await mock_stt.emit_final_transcript(turn1_prompt)
    await mock_stt.emit_speech_ended()

    req1 = session.create_request(prompt=turn1_prompt)
    v1 = req1.conversation_version
    req1_id = req1.request_id

    print(f"  --> Turn 1 Created: Request ID={req1_id}, Version=v{v1}")
    print(f"  --> Active Version: v{session.active_version}")

    # Launch delayed tool task for Turn 1 (0.35s delay)
    print("  --> Dispatching Turn 1 tool search (0.35s simulated latency)...")
    tool1_task = asyncio.create_task(
        session.run_tool(
            request_id=req1_id,
            version=v1,
            tool_name="train_search",
            args={"origin": "Nagpur", "destination": "Mumbai", "date": "tomorrow"},
            delay_seconds=0.35,
        )
    )

    # -------------------------------------------------------------------------
    # STEP 2: Mid-flight User Voice Interruption (Barge-In)
    # -------------------------------------------------------------------------
    await asyncio.sleep(0.08)  # Interrupt while tool 1 is running
    
    turn2_prompt = "Actually, only after 8 PM in 3A."
    print(f"\n[STEP 2] User interrupts with barge-in: \"{turn2_prompt}\"")

    t_interrupt = time.perf_counter()
    # Fast-path VAD audio cut & session interruption
    interrupt_res = session.interrupt(reason="Live user voice barge-in detected")
    t_audio_cut = time.perf_counter()
    audio_cut_latency_ms = (t_audio_cut - t_interrupt) * 1000.0

    print(f"  --> Fast-Path Audio Cut Latency: {audio_cut_latency_ms:.3f} ms")
    print(f"  --> Invalidated Request: {interrupt_res['invalidated_request_id']} (v{interrupt_res['invalidated_version']})")
    print(f"  --> Request 1 Status: {req1.status.value}")

    # STT emits new utterance
    await mock_stt.emit_speech_started()
    await mock_stt.emit_interim_transcript("Actually, only after 8 PM")
    await mock_stt.emit_final_transcript(turn2_prompt)
    await mock_stt.emit_speech_ended()

    # Create Turn 2
    req2 = session.create_request(prompt=turn2_prompt)
    v2 = req2.conversation_version
    req2_id = req2.request_id

    print(f"  --> Turn 2 Created: Request ID={req2_id}, Version=v{v2}")
    print(f"  --> Active Version: v{session.active_version}")

    # -------------------------------------------------------------------------
    # STEP 3: Turn 2 Execution & Turn 1 Late Resolution
    # -------------------------------------------------------------------------
    print("\n[STEP 3] Running Turn 2 tool (0.05s latency) while Turn 1 tool completes late...")
    tool2_task = asyncio.create_task(
        session.run_tool(
            request_id=req2_id,
            version=v2,
            tool_name="train_search",
            args={"origin": "Nagpur", "destination": "Mumbai", "min_departure": "20:00", "class_type": "3A"},
            delay_seconds=0.05,
        )
    )

    res2 = await tool2_task
    res1 = await tool1_task

    print(f"  --> Turn 1 Late Tool Status: {res1.status.value}")
    print(f"  --> Turn 2 Tool Status     : {res2.status.value}")

    # Complete Turn 2
    comp_success = session.complete_turn(
        request_id=req2_id,
        version=v2,
        assistant_response="I found 2 trains after 8 PM with 3A availability: Sewagram Express (9:15 PM) and Gitanjali Express (11:30 PM).",
        tool_call={"name": "train_search", "result": res2.result},
        trigger_rime=True,
    )
    t_turn2_complete = time.perf_counter()
    recovery_latency_ms = (t_turn2_complete - t_interrupt) * 1000.0

    print(f"  --> Turn 2 Completion Accepted: {comp_success}")
    print(f"  --> Recovery Latency: {recovery_latency_ms:.2f} ms")
    print(f"  --> Active Session Answer: \"{session.current_answer}\"")

    # -------------------------------------------------------------------------
    # STEP 4: Verification & Audit
    # -------------------------------------------------------------------------
    print("\n" + "-" * 80)
    print("PHASE 14 VERIFICATION CHECKLIST")
    print("-" * 80)

    checks = []

    # Check 1: Request 1 invalidated promptly
    c1 = (req1.status == RequestStatus.OBSOLETE and req1.is_cancelled is True)
    checks.append(("Request 1 invalidated & marked OBSOLETE upon interruption", c1))

    # Check 2: Fast audio cut (< 20ms target)
    c2 = (audio_cut_latency_ms < 50.0)  # Safe threshold on Windows
    checks.append((f"Fast-path audio cut executed promptly ({audio_cut_latency_ms:.3f} ms)", c2))

    # Check 3: Active version monotonically advanced to v2
    c3 = (session.active_version == 2 and session.active_request_id == req2_id)
    checks.append(("Session active version advanced monotonically to v2", c3))

    # Check 4: Turn 1 late tool result was safely discarded
    c4 = (res1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED))
    checks.append(("Turn 1 late tool result discarded (Gate Check: v1 != v2)", c4))

    # Check 5: Stale discard recorded in metrics
    c5 = (len(session.stale_discards) >= 1 and session.stale_discards[0].request_id == req1_id)
    checks.append(("Stale discard explicitly logged in session audit trail", c5))

    # Check 6: Turn 2 tool completed successfully
    c6 = (res2.status == ToolStatus.COMPLETED_VALID and res2.result is not None)
    checks.append(("Turn 2 tool completed valid under active version v2", c6))

    # Check 7: Turn 2 answer is active in session
    c7 = (session.current_answer is not None and "Sewagram Express" in session.current_answer)
    checks.append(("Turn 2 response successfully committed to conversation history", c7))

    # Check 8: No obsolete assistant messages in conversation history
    c8 = all(msg.version != v1 for msg in session.state_mgr.state.history if msg.role == "assistant")
    checks.append(("No obsolete assistant messages or stale TTS in conversation history", c8))

    await mock_stt.stop()

    all_passed = True
    for label, passed in checks:
        status_str = "[PASS]" if passed else "[FAIL]"
        if not passed:
            all_passed = False
        print(f"  {status_str} {label}")

    print("=" * 80)
    if all_passed:
        print("OVERALL RESULT: ALL 8 PHASE 14 VERIFICATIONS PASSED SUCCESSFULLY.")
    else:
        print("OVERALL RESULT: SOME VERIFICATIONS FAILED.")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_voice_interruption_smoke_test())
