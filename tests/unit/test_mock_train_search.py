"""Unit tests for Phase 10: Asynchronous Mock Train-Search Tool.

Requirements tested:
1. Typed search_trains tool with source, destination, date, time constraint, class constraint.
2. Configurable artificial latency (default 3000ms).
3. Association with request_id, conversation_version, task_id.
4. Cancellation on obsolescence.
5. Late tool completion discarded by version gate.
6. Structured events emitted:
   - TOOL_STARTED
   - TOOL_CANCEL_REQUESTED
   - TOOL_CANCELLED
   - TOOL_COMPLETED
   - TOOL_RESULT_ACCEPTED
   - STALE_RESULT_DISCARDED
7. Deterministic test fixtures.
8. Scenarios:
   - normal tool completion
   - cancellation before completion
   - late completion after cancellation
   - interruption followed by new request
   - two overlapping requests
   - rapid successive interruptions
   - simultaneous completion of old and new requests
"""

import asyncio
import pytest
from app.models import RequestStatus, ToolStatus, VoiceEventType
from app.tools.train_search import TrainSearchParams, search_trains, search_trains_sync


@pytest.fixture
def sample_search_params():
    return TrainSearchParams(
        source="Nagpur",
        destination="Mumbai",
        date="tomorrow",
        time_constraint="after 8 PM",
        class_constraint="3A",
        delay_ms=50,
    )


def test_train_search_sync_filtering():
    """Verifies synchronous filtering logic for source, destination, time, and class constraints."""
    # 1. Base route search
    params = TrainSearchParams(source="Nagpur", destination="Mumbai", date="tomorrow")
    res = search_trains_sync(params)
    assert res.total_found >= 4
    assert all("mumbai" in t.destination.lower() for t in res.trains)

    # 2. Time filtered search (after 8 PM -> departure >= 20:00)
    params_evening = TrainSearchParams(
        source="Nagpur", 
        destination="Mumbai", 
        date="tomorrow",
        time_constraint="after 8 PM",
    )
    res_evening = search_trains_sync(params_evening)
    assert res_evening.total_found >= 2
    assert all(t.departure >= "20:00" for t in res_evening.trains)
    # Sewagram (21:15) and Gitanjali (23:30) must be included
    train_numbers = [t.train_no for t in res_evening.trains]
    assert "12140" in train_numbers  # Sewagram Superfast Express
    assert "12860" in train_numbers  # Gitanjali Express

    # 3. Class filtered search
    params_class = TrainSearchParams(
        source="Nagpur",
        destination="Mumbai",
        date="tomorrow",
        class_constraint="1A",
    )
    res_class = search_trains_sync(params_class)
    assert res_class.total_found >= 2
    assert all("1A" in t.classes for t in res_class.trains)


@pytest.mark.asyncio
async def test_search_trains_async_latency(sample_search_params):
    """Verifies asynchronous search with configurable artificial latency."""
    t_start = asyncio.get_event_loop().time()
    res = await search_trains(sample_search_params)
    t_elapsed = asyncio.get_event_loop().time() - t_start

    assert t_elapsed >= 0.04  # 50ms delay
    assert res.total_found >= 1
    assert all(t.departure >= "20:00" for t in res.trains)


@pytest.mark.asyncio
async def test_tool_normal_completion(test_session):
    """Test 1: Normal tool completion.
    
    Verifies metadata association (request_id, version, task_id) and event emissions:
    - TOOL_STARTED
    - TOOL_COMPLETED
    - TOOL_RESULT_ACCEPTED
    """
    req = test_session.create_request(prompt="Find trains from Nagpur to Mumbai tomorrow")
    
    task = await test_session.run_tool(
        request_id=req.request_id,
        version=req.version,
        tool_name="train_search",
        args={"source": "Nagpur", "destination": "Mumbai", "date": "tomorrow"},
        delay_seconds=0.02,
    )

    assert task.status == ToolStatus.COMPLETED_VALID
    assert task.request_id == req.request_id
    assert task.conversation_version == req.version
    assert task.task_id.startswith("task-")
    assert task.result is not None
    assert task.result["total_found"] >= 4

    # Verify structured events emitted
    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    event_types = [e.event_type for e in events]
    assert VoiceEventType.TOOL_STARTED in event_types
    assert VoiceEventType.TOOL_COMPLETED in event_types
    assert VoiceEventType.TOOL_RESULT_ACCEPTED in event_types


@pytest.mark.asyncio
async def test_tool_cancellation_before_completion(test_session):
    """Test 2: Cancellation before completion.
    
    Verifies cooperative cancellation in-flight:
    - TOOL_CANCEL_REQUESTED
    - TOOL_CANCELLED
    """
    req = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    
    coro = test_session.run_tool(
        request_id=req.request_id,
        version=req.version,
        tool_name="train_search",
        args={"source": "Nagpur", "destination": "Mumbai"},
        delay_seconds=0.3,
    )
    task_handle = asyncio.create_task(coro)

    # Interrupt after 0.04s
    await asyncio.sleep(0.04)
    test_session.interrupt(reason="User said Wait")

    result_task = await task_handle
    assert result_task.status == ToolStatus.CANCELLED
    assert result_task.result is None
    assert "Cancelled" in result_task.discard_reason

    # Verify structured events
    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    event_types = [e.event_type for e in events]
    assert VoiceEventType.TOOL_CANCEL_REQUESTED in event_types
    assert VoiceEventType.TOOL_CANCELLED in event_types


@pytest.mark.asyncio
async def test_tool_late_completion_after_cancellation(test_session):
    """Test 3: Late completion after cancellation (Non-cooperative tool).
    
    Even if the underlying tool finishes after cancellation, its result must pass
    the existing version gate and be discarded as obsolete:
    - STALE_RESULT_DISCARDED
    """
    req1 = test_session.create_request(prompt="Find trains Nagpur to Mumbai")
    v1 = req1.version
    req1_id = req1.request_id

    # Launch tool with force_non_cooperative=True so it ignores early cancel token
    # and computes all the way to the version gate
    coro1 = test_session.run_tool(
        request_id=req1_id,
        version=v1,
        tool_name="train_search",
        args={"source": "Nagpur", "destination": "Mumbai"},
        delay_seconds=0.1,
        force_non_cooperative=True,
    )
    task_handle_1 = asyncio.create_task(coro1)

    # Advance session to v2 before tool completes
    await asyncio.sleep(0.02)
    req2 = test_session.create_request(prompt="Only evening trains")
    assert test_session.active_version == 2

    # Await tool 1 completion
    result1 = await task_handle_1

    # Assert Tool 1 passed through the gate and was DISCARDED
    assert result1.status == ToolStatus.COMPLETED_STALE_DISCARDED
    assert result1.result is None
    assert "Stale tool result" in result1.discard_reason

    # Verify STALE_RESULT_DISCARDED event emitted
    events = test_session.event_logger.get_events(session_id=test_session.session_id)
    event_types = [e.event_type for e in events]
    assert VoiceEventType.STALE_RESULT_DISCARDED in event_types

    # Assert stale result was explicitly recorded
    assert len(test_session.stale_discards) >= 1
    stale_rec = test_session.stale_discards[-1]
    assert stale_rec.request_id == req1_id
    assert stale_rec.result_version == v1
    assert stale_rec.active_version_when_delivered == 2


@pytest.mark.asyncio
async def test_interruption_followed_by_new_request(test_session):
    """Test 4: Interruption followed by new request (Acceptance Test 3 Scenario).
    
    1. User asks: Nagpur to Mumbai (v1) -> Tool running
    2. User interrupts -> "Actually, only trains after 8 PM" (v2)
    3. Tool v1 is discarded.
    4. Tool v2 completes with evening trains and triggers Rime TTS.
    """
    # 1. Start Request #1
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    t1 = asyncio.create_task(
        test_session.run_tool(
            request_id=req1.request_id,
            version=req1.version,
            tool_name="train_search",
            args={"source": "Nagpur", "destination": "Mumbai"},
            delay_seconds=0.15,
        )
    )

    # 2. Interruption & Request #2
    await asyncio.sleep(0.03)
    test_session.interrupt(reason="User changed instruction")
    req2 = test_session.create_request(prompt="Actually, only trains after 8 PM")
    assert test_session.active_version == 2

    # 3. Tool #2
    t2 = asyncio.create_task(
        test_session.run_tool(
            request_id=req2.request_id,
            version=req2.version,
            tool_name="train_search",
            args={"source": "Nagpur", "destination": "Mumbai", "time_constraint": "after 8 PM"},
            delay_seconds=0.03,
        )
    )

    res2 = await t2
    res1 = await t1

    assert res1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert res2.status == ToolStatus.COMPLETED_VALID
    assert res2.result["total_found"] == 2
    assert all(t["departure"] >= "20:00" for t in res2.result["trains"])

    # Complete turn 2
    turn_ok = test_session.complete_turn(
        request_id=req2.request_id,
        version=req2.version,
        assistant_response="I found 2 trains after 8 PM: Sewagram Express and Gitanjali Express.",
        tool_call={"name": "train_search", "result": res2.result},
        trigger_rime=True,
    )
    assert turn_ok is True
    assert "Sewagram Express" in test_session.current_answer


@pytest.mark.asyncio
async def test_two_overlapping_requests(test_session):
    """Test 5: Two overlapping requests with delayed tool tasks."""
    req1 = test_session.create_request(prompt="Search A")
    t1 = asyncio.create_task(
        test_session.run_tool(req1.request_id, req1.version, "train_search", {"source": "Nagpur", "destination": "Mumbai"}, delay_seconds=0.08)
    )

    await asyncio.sleep(0.02)
    req2 = test_session.create_request(prompt="Search B")
    t2 = asyncio.create_task(
        test_session.run_tool(req2.request_id, req2.version, "train_search", {"source": "Delhi", "destination": "Mumbai"}, delay_seconds=0.08)
    )

    r2 = await t2
    r1 = await t1

    assert r1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r2.status == ToolStatus.COMPLETED_VALID
    assert r2.result["source"] == "Delhi"


@pytest.mark.asyncio
async def test_rapid_successive_interruptions(test_session):
    """Test 6: Rapid successive interruptions across 4 turns."""
    req1 = test_session.create_request(prompt="Turn 1")
    t1 = asyncio.create_task(test_session.run_tool(req1.request_id, req1.version, "train_search", {}, delay_seconds=0.2))

    await asyncio.sleep(0.01)
    req2 = test_session.create_request(prompt="Turn 2")
    t2 = asyncio.create_task(test_session.run_tool(req2.request_id, req2.version, "train_search", {}, delay_seconds=0.2))

    await asyncio.sleep(0.01)
    req3 = test_session.create_request(prompt="Turn 3")
    t3 = asyncio.create_task(test_session.run_tool(req3.request_id, req3.version, "train_search", {}, delay_seconds=0.2))

    await asyncio.sleep(0.01)
    req4 = test_session.create_request(prompt="Turn 4 (Final)")
    t4 = asyncio.create_task(test_session.run_tool(req4.request_id, req4.version, "train_search", {"source": "Nagpur", "destination": "Mumbai", "time_constraint": "after 8 PM"}, delay_seconds=0.02))

    r4 = await t4
    r3 = await t3
    r2 = await t2
    r1 = await t1

    assert test_session.active_version == 4
    assert r1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r2.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r3.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r4.status == ToolStatus.COMPLETED_VALID


@pytest.mark.asyncio
async def test_simultaneous_completion_of_old_and_new_requests(test_session):
    """Test 7: Simultaneous completion of old and new requests via asyncio.gather."""
    req1 = test_session.create_request(prompt="Turn 1")
    v1 = req1.version
    req1_id = req1.request_id

    req2 = test_session.create_request(prompt="Turn 2")
    v2 = req2.version
    req2_id = req2.request_id

    # Run both with 0.01s delay so they complete on the exact same event loop tick
    t1 = test_session.run_tool(req1_id, v1, "train_search", {"query": "1"}, delay_seconds=0.01)
    t2 = test_session.run_tool(req2_id, v2, "train_search", {"query": "2"}, delay_seconds=0.01)

    r1, r2 = await asyncio.gather(t1, t2)

    assert r1.status in (ToolStatus.COMPLETED_STALE_DISCARDED, ToolStatus.CANCELLED)
    assert r2.status == ToolStatus.COMPLETED_VALID
    assert r2.result is not None

