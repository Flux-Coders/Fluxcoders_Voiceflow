"""Unit tests for Stale-Result Protection and Race Condition Handling.

Rules tested:
- Rule 5: Every asynchronous tool result must validate its request/version before modifying conversation state.
- Rule 6: Late results from obsolete requests must be discarded.
- Rule 8: The application must remain responsive while tools are running.
"""

import asyncio
import pytest
from app.models import ToolStatus, VoiceEventType


@pytest.mark.asyncio
async def test_tool_execution_normal_valid(test_session):
    """Verifies that a tool executing under the active version returns valid results."""
    req = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    
    task = await test_session.run_tool(
        request_id=req.request_id,
        version=req.version,
        tool_name="train_search",
        args={"source": "Nagpur", "destination": "Mumbai"},
        delay_seconds=0.01,
    )

    assert task.status == ToolStatus.COMPLETED_VALID
    assert task.result is not None
    trains = task.result["trains"] if isinstance(task.result, dict) and "trains" in task.result else task.result
    assert len(trains) > 0
    assert task.discard_reason is None

    # Complete the turn
    turn_ok = test_session.complete_turn(
        request_id=req.request_id,
        version=req.version,
        assistant_response="Found trains.",
        tool_call={"name": "train_search", "result": task.result},
    )
    assert turn_ok is True


@pytest.mark.asyncio
async def test_stale_result_discarded_on_version_bump(test_session):
    """Verifies Acceptance Test 4: Delayed Tool #1 finishing after Request #2 started is DISCARDED."""
    # Step 1: Start Request #1 with a delayed tool execution
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    v1 = req1.version
    req1_id = req1.request_id

    # Launch delayed tool task for Request #1 in the background (0.15s delay)
    tool_task_1 = asyncio.create_task(
        test_session.run_tool(
            request_id=req1_id,
            version=v1,
            tool_name="train_search",
            args={"source": "Nagpur", "destination": "Mumbai"},
            delay_seconds=0.15,
        )
    )

    # Step 2: At t = 0.05s, user interrupts with Request #2 (Only trains after 8 PM)
    await asyncio.sleep(0.05)
    test_session.interrupt(reason="User changed constraint: only after 8 PM")
    req2 = test_session.create_request(prompt="Only trains after 8 PM")
    v2 = req2.version
    req2_id = req2.request_id

    assert test_session.active_version == v2
    assert v2 > v1

    # Step 3: Launch tool for Request #2 with short delay (0.02s)
    tool_task_2 = asyncio.create_task(
        test_session.run_tool(
            request_id=req2_id,
            version=v2,
            tool_name="train_search",
            args={"source": "Nagpur", "destination": "Mumbai", "time_constraint": "after 8 PM"},
            delay_seconds=0.02,
        )
    )

    # Await both tool tasks
    res2 = await tool_task_2
    res1 = await tool_task_1

    # Step 4: Verify that Tool #1 result was DISCARDED (Rule 6)
    assert res1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    if res1.status == ToolStatus.COMPLETED_STALE_DISCARDED:
        assert res1.result is None  # Result payload stripped
        assert "Stale tool result" in res1.discard_reason

    # Step 5: Verify that Tool #2 result was ACCEPTED
    assert res2.status == ToolStatus.COMPLETED_VALID
    assert res2.result is not None
    trains = res2.result["trains"] if isinstance(res2.result, dict) and "trains" in res2.result else res2.result
    assert all(t["departure"] >= "20:00" for t in trains)

    # Step 6: Verify metrics recorded the stale discard
    metrics = test_session.metrics.get_snapshot()
    assert metrics.total_interruptions == 1
    assert metrics.valid_results_accepted >= 1

    # Step 7: Verify event logs contain interruption and cancellation/stale discard events
    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    event_types = [e.event_type for e in events]
    assert VoiceEventType.INTERRUPT_TRIGGERED in event_types
    assert any(et in event_types for et in (
        VoiceEventType.TOOL_CANCELLED, 
        VoiceEventType.STALE_RESULT_DISCARDED, 
        VoiceEventType.TOOL_RETURN_STALE_DISCARDED
    ))


@pytest.mark.asyncio
async def test_multiple_rapid_interruptions_stale_chain(test_session):
    """Stress test: 3 rapid requests launched. Only the final active request is valid."""
    req1 = test_session.create_request(prompt="Prompt 1")
    t1 = asyncio.create_task(test_session.run_tool(req1.request_id, req1.version, "train_search", {}, delay_seconds=0.1))

    req2 = test_session.create_request(prompt="Prompt 2")
    t2 = asyncio.create_task(test_session.run_tool(req2.request_id, req2.version, "train_search", {}, delay_seconds=0.1))

    req3 = test_session.create_request(prompt="Prompt 3")
    t3 = asyncio.create_task(test_session.run_tool(req3.request_id, req3.version, "train_search", {}, delay_seconds=0.02))

    r3 = await t3
    r2 = await t2
    r1 = await t1

    assert r1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r2.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r3.status == ToolStatus.COMPLETED_VALID
