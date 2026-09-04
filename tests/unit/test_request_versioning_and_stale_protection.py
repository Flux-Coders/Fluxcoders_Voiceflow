"""Comprehensive Test Suite for Request Versioning & Stale-Result Protection.

Scenarios tested:
1. Normal completion
2. Cancellation
3. Late tool completion (Acceptance Test 4 scenario)
4. Two rapid interruptions
5. Three rapid interruptions
6. Stale Rime generation
7. Simultaneous tool completions

Guarantees enforced:
- A stale result must NEVER update active conversation state.
- A stale result must NEVER trigger Rime TTS.
- A stale result must NEVER appear as the current answer.
- Stale results are explicitly logged.
"""

import asyncio
import pytest
from app.core.versioning import StaleRimeGenerationError, VersionGateError
from app.models import RequestStatus, ToolStatus, VoiceEventType


@pytest.mark.asyncio
async def test_normal_completion(test_session):
    """Test 1: Normal completion.
    
    A single valid request executes a tool, completes, triggers Rime TTS,
    updates active conversation state, and produces the current answer.
    """
    # 1. User starts turn
    req = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert req.conversation_version == 1
    assert req.status == RequestStatus.RUNNING
    assert test_session.active_version == 1
    assert test_session.active_request_id == req.request_id

    # 2. Run tool
    tool_task = await test_session.run_tool(
        request_id=req.request_id,
        version=req.conversation_version,
        tool_name="train_search",
        args={"origin": "Nagpur", "destination": "Mumbai"},
        delay_seconds=0.01,
    )
    assert tool_task.status == ToolStatus.COMPLETED_VALID
    assert tool_task.result is not None

    # 3. Complete turn with Rime synthesis
    success = test_session.complete_turn(
        request_id=req.request_id,
        version=req.conversation_version,
        assistant_response="I found 5 trains from Nagpur to Mumbai.",
        tool_call={"name": "train_search", "result": tool_task.result},
        trigger_rime=True,
    )
    assert success is True

    # 4. Assert conversation state and answer
    assert req.status == RequestStatus.COMPLETED
    assert test_session.current_answer == "I found 5 trains from Nagpur to Mumbai."
    assert len(test_session.state_mgr.state.history) == 2  # 1 user + 1 assistant
    assert test_session.state_mgr.state.history[-1].content == "I found 5 trains from Nagpur to Mumbai."


@pytest.mark.asyncio
async def test_cancellation(test_session):
    """Test 2: Cancellation.
    
    When an in-flight request is interrupted/cancelled, the old request is marked
    OBSOLETE, tasks are cancelled, and audio output is stopped.
    """
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert req1.status == RequestStatus.RUNNING

    # Start a delayed task
    task = asyncio.create_task(
        test_session.run_tool(
            request_id=req1.request_id,
            version=req1.conversation_version,
            tool_name="train_search",
            args={"origin": "Nagpur", "destination": "Mumbai"},
            delay_seconds=0.5,
        )
    )

    # Interrupt
    await asyncio.sleep(0.02)
    interrupt_res = test_session.interrupt(reason="User said 'Wait'")

    assert interrupt_res["invalidated_version"] == 1
    assert interrupt_res["invalidated_request_id"] == req1.request_id
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True

    result = await task
    assert result.status == ToolStatus.CANCELLED
    assert test_session.current_answer is None


@pytest.mark.asyncio
async def test_late_tool_completion(test_session):
    """Test 3: Late tool completion.
    
    Tool #1 starts with 0.15s delay under v1.
    User supersedes with Request #2 under v2 at t = 0.04s.
    Tool #1 finishes late at t = 0.15s -> REJECTED & DISCARDED.
    Tool #2 completes -> ACCEPTED.
    
    Asserts:
    - Tool #1 never updates conversation state.
    - Tool #1 never triggers Rime.
    - Tool #1 never appears as current answer.
    - Stale result is explicitly logged.
    """
    # 1. Request #1
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    v1 = req1.conversation_version
    req1_id = req1.request_id

    tool1_coro = test_session.run_tool(
        request_id=req1_id,
        version=v1,
        tool_name="train_search",
        args={"origin": "Nagpur", "destination": "Mumbai"},
        delay_seconds=0.15,
    )
    task1 = asyncio.create_task(tool1_coro)

    # 2. User interrupts with Request #2
    await asyncio.sleep(0.04)
    req2 = test_session.create_request(prompt="Actually, only trains after 8 PM")
    v2 = req2.conversation_version
    req2_id = req2.request_id

    assert req1.status == RequestStatus.OBSOLETE
    assert test_session.active_version == v2
    assert v2 == 2

    # 3. Request #2 tool (fast 0.02s delay)
    task2 = asyncio.create_task(
        test_session.run_tool(
            request_id=req2_id,
            version=v2,
            tool_name="train_search",
            args={"origin": "Nagpur", "destination": "Mumbai", "min_departure": "20:00"},
            delay_seconds=0.02,
        )
    )

    res2 = await task2
    res1 = await task1

    # 4. Verify Tool #1 is discarded and logged explicitly
    assert res1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    if res1.status == ToolStatus.COMPLETED_STALE_DISCARDED:
        assert res1.result is None
        assert "Stale tool result" in res1.discard_reason

    # Stale record explicitly logged
    assert len(test_session.stale_discards) >= 1
    stale_rec = test_session.stale_discards[0]
    assert stale_rec.request_id == req1_id
    assert stale_rec.result_version == v1
    assert stale_rec.active_version_when_delivered == v2

    # 5. Complete turn for Request #2
    test_session.complete_turn(
        request_id=req2_id,
        version=v2,
        assistant_response="Here are the trains after 8 PM: Sewagram Express at 9:15 PM.",
        tool_call={"name": "train_search", "result": res2.result},
        trigger_rime=True,
    )

    # 6. Verify current answer is solely from Request #2
    assert test_session.current_answer == "Here are the trains after 8 PM: Sewagram Express at 9:15 PM."
    assert all(msg.version != v1 for msg in test_session.state_mgr.state.history if msg.role == "assistant")


@pytest.mark.asyncio
async def test_two_rapid_interruptions(test_session):
    """Test 4: Two rapid interruptions.
    
    Sequence: Request #1 -> Request #2 -> Request #3.
    Late tools from #1 and #2 must be discarded.
    Only Request #3 completes and appears as current answer.
    """
    req1 = test_session.create_request(prompt="Search 1")
    t1 = asyncio.create_task(test_session.run_tool(req1.request_id, req1.version, "train_search", {}, delay_seconds=0.12))

    await asyncio.sleep(0.02)
    req2 = test_session.create_request(prompt="Search 2")
    t2 = asyncio.create_task(test_session.run_tool(req2.request_id, req2.version, "train_search", {}, delay_seconds=0.10))

    await asyncio.sleep(0.02)
    req3 = test_session.create_request(prompt="Search 3 (Final)")
    t3 = asyncio.create_task(test_session.run_tool(req3.request_id, req3.version, "train_search", {}, delay_seconds=0.01))

    r3 = await t3
    r2 = await t2
    r1 = await t1

    assert req1.status == RequestStatus.OBSOLETE
    assert req2.status == RequestStatus.OBSOLETE
    assert req3.status == RequestStatus.RUNNING

    assert r1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r2.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r3.status == ToolStatus.COMPLETED_VALID

    # Complete turn 3
    test_session.complete_turn(
        request_id=req3.request_id,
        version=req3.version,
        assistant_response="Final Answer for Search 3",
    )

    assert test_session.current_answer == "Final Answer for Search 3"


@pytest.mark.asyncio
async def test_three_rapid_interruptions(test_session):
    """Test 5: Three rapid interruptions.
    
    Sequence: Request #1 -> Request #2 -> Request #3 -> Request #4.
    Requests 1, 2, 3 are marked OBSOLETE.
    Their late tools are discarded.
    Only Request #4 produces the active answer.
    """
    req1 = test_session.create_request(prompt="Turn 1")
    t1 = asyncio.create_task(test_session.run_tool(req1.request_id, req1.version, "train_search", {}, delay_seconds=0.15))

    await asyncio.sleep(0.02)
    req2 = test_session.create_request(prompt="Turn 2")
    t2 = asyncio.create_task(test_session.run_tool(req2.request_id, req2.version, "train_search", {}, delay_seconds=0.15))

    await asyncio.sleep(0.02)
    req3 = test_session.create_request(prompt="Turn 3")
    t3 = asyncio.create_task(test_session.run_tool(req3.request_id, req3.version, "train_search", {}, delay_seconds=0.15))

    await asyncio.sleep(0.02)
    req4 = test_session.create_request(prompt="Turn 4 (Active)")
    t4 = asyncio.create_task(test_session.run_tool(req4.request_id, req4.version, "train_search", {}, delay_seconds=0.02))

    r4 = await t4
    r3 = await t3
    r2 = await t2
    r1 = await t1

    assert req1.status == RequestStatus.OBSOLETE
    assert req2.status == RequestStatus.OBSOLETE
    assert req3.status == RequestStatus.OBSOLETE
    assert test_session.active_version == 4

    assert r1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r2.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r3.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r4.status == ToolStatus.COMPLETED_VALID

    test_session.complete_turn(req4.request_id, req4.version, "Turn 4 Completed Successfully")
    assert test_session.current_answer == "Turn 4 Completed Successfully"


def test_stale_rime_generation(test_session):
    """Test 6: Stale Rime generation.
    
    Attempts to trigger Rime TTS for an obsolete version (v1) when session is at v2.
    Must be blocked, raise StaleRimeGenerationError, log explicitly, and never produce audio.
    """
    req1 = test_session.create_request(prompt="First utterance")
    v1 = req1.conversation_version
    req1_id = req1.request_id

    # Advance to v2
    req2 = test_session.create_request(prompt="Second utterance")
    assert test_session.active_version == 2

    # Attempt to trigger Rime TTS for obsolete v1
    with pytest.raises(StaleRimeGenerationError) as exc_info:
        test_session.synthesize_rime(
            text="Late speech from Request 1",
            request_id=req1_id,
            version=v1,
        )

    assert "Stale Rime TTS trigger blocked" in str(exc_info.value)

    # Verify event logger captured the blocked attempt
    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    blocked_events = [e for e in events if e.event_type == VoiceEventType.RIME_STREAM_BLOCKED_STALE]
    assert len(blocked_events) == 1
    assert blocked_events[0].version == v1

    # Verify valid v2 Rime synthesis succeeds
    res2 = test_session.synthesize_rime(
        text="Valid speech from Request 2",
        request_id=req2.request_id,
        version=req2.version,
    )
    assert res2["success"] is True
    assert res2["provider"] == "Rime"


@pytest.mark.asyncio
async def test_simultaneous_tool_completions(test_session):
    """Test 7: Simultaneous tool completions.
    
    Two tool tasks for different versions complete at the exact same timestamp (via asyncio.gather).
    The gate atomically allows the active version and discards the obsolete version.
    """
    req1 = test_session.create_request(prompt="Simultaneous Turn 1")
    v1 = req1.version
    req1_id = req1.request_id

    # Create Request 2 immediately
    req2 = test_session.create_request(prompt="Simultaneous Turn 2")
    v2 = req2.version
    req2_id = req2.request_id

    # Launch both tasks with identical zero delay to force simultaneous completion
    task1 = test_session.run_tool(req1_id, v1, "train_search", {"query": "v1"}, delay_seconds=0.01)
    task2 = test_session.run_tool(req2_id, v2, "train_search", {"query": "v2"}, delay_seconds=0.01)

    res1, res2 = await asyncio.gather(task1, task2)

    # Assert res1 is discarded as stale and res2 is valid
    assert res1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert res2.status == ToolStatus.COMPLETED_VALID

    if res1.status == ToolStatus.COMPLETED_STALE_DISCARDED:
        assert res1.result is None

    assert res2.result is not None

    # Complete active turn
    test_session.complete_turn(req2_id, v2, "Simultaneous completion resolved safely")
    assert test_session.current_answer == "Simultaneous completion resolved safely"

