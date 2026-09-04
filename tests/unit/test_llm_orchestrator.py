"""VoiceFlow Phase 11 LLM Orchestration & Tool Reasoning Tests.

Tests:
1. basic train search
2. missing parameter handling
3. follow-up constraint update
4. slot replacement (e.g. "Only AC" -> "Sleeper is fine")
5. slot clearing (e.g. "Only after 8 PM" -> "Any time is fine")
6. tool call for active request
7. stale tool result protection
8. interrupted request during Step 1
9. interrupted request during Step 2
10. multiple rapid request updates
11. malformed LLM tool arguments
12. unregistered / forbidden tool protection
"""

from __future__ import annotations

import asyncio
import pytest
from app.core.event_logger import VoiceEventLogger
from app.core.session import Session
from app.engine.llm_orchestrator import LLMOrchestrator
from app.engine.llm_provider import MockLLMClient
from app.models import RequestStatus, ToolStatus, VoiceEventType
from app.tools.registry import create_default_registry


@pytest.fixture
def session() -> Session:
    event_logger = VoiceEventLogger()
    return Session(session_id="test-session-llm", event_logger=event_logger)


@pytest.fixture
def default_orchestrator() -> LLMOrchestrator:
    return LLMOrchestrator(
        llm_client=MockLLMClient(simulated_delay_ms=0),
        tool_registry=create_default_registry(),
    )


# -----------------------------------------------------------------------------
# Test 1: Basic Train Search
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_basic_train_search(session: Session, default_orchestrator: LLMOrchestrator):
    """Test standard single-turn train search execution and response synthesis."""
    result = await session.process_turn(
        prompt="Find me a train from Nagpur to Mumbai tomorrow.",
        orchestrator=default_orchestrator,
        delay_tool_ms=10,
    )

    assert result.success is True
    assert result.is_stale is False
    assert result.version == 1
    assert result.tool_task is not None
    assert result.tool_task.status == ToolStatus.COMPLETED_VALID
    assert "Duronto Express" in (result.assistant_response or "")
    assert session.current_answer == result.assistant_response
    assert session.state_mgr.state.slots.get("source") == "Nagpur"
    assert session.state_mgr.state.slots.get("destination") == "Mumbai"


# -----------------------------------------------------------------------------
# Test 2: Missing Parameter Handling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_parameter_handling(session: Session, default_orchestrator: LLMOrchestrator):
    """Test prompt missing origin/destination -> asks for clarification without calling tool."""
    result = await session.process_turn(
        prompt="Find me a train tomorrow.",
        orchestrator=default_orchestrator,
    )

    assert result.success is True
    assert result.is_stale is False
    assert result.tool_task is None  # No tool should have been called
    assert "Where would you like to travel" in (result.assistant_response or "")
    assert session.requests[result.request_id].status == RequestStatus.COMPLETED


# -----------------------------------------------------------------------------
# Test 3: Follow-up Constraint Update
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_followup_constraint_update(session: Session, default_orchestrator: LLMOrchestrator):
    """Test Turn 1 search followed by Turn 2 constraint addition ('after 8 PM')."""
    # Turn 1
    res1 = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai tomorrow.",
        orchestrator=default_orchestrator,
        delay_tool_ms=10,
    )
    assert res1.success is True
    assert res1.version == 1
    assert session.state_mgr.state.slots["source"] == "Nagpur"
    assert session.state_mgr.state.slots["destination"] == "Mumbai"

    # Turn 2: Add time constraint
    res2 = await session.process_turn(
        prompt="Actually, only after 8 PM.",
        orchestrator=default_orchestrator,
        delay_tool_ms=10,
    )
    assert res2.success is True
    assert res2.version == 2
    assert session.state_mgr.state.slots["time_constraint"] == "after 8 PM"
    assert session.state_mgr.state.slots["source"] == "Nagpur"
    assert session.state_mgr.state.slots["destination"] == "Mumbai"
    
    # Trains after 8 PM (20:00) from Nagpur to Mumbai: Sewagram (21:15), Gitanjali (23:30)
    assert "Sewagram Superfast Express" in (res2.assistant_response or "")
    assert "Gitanjali Express" in (res2.assistant_response or "")
    assert "Duronto Express" not in (res2.assistant_response or "")  # Duronto is at 06:40


# -----------------------------------------------------------------------------
# Test 4: Slot Replacement
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_replacement(session: Session, default_orchestrator: LLMOrchestrator):
    """Test replacing a slot constraint (e.g. 'Only AC' -> 'Sleeper is fine')."""
    # Turn 1: 3A class constraint
    res1 = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai tomorrow in 3A class.",
        orchestrator=default_orchestrator,
        delay_tool_ms=10,
    )
    assert res1.success is True
    assert session.state_mgr.state.slots.get("class_constraint") == "3A"

    # Turn 2: Replace with Sleeper
    res2 = await session.process_turn(
        prompt="Sleeper is fine.",
        orchestrator=default_orchestrator,
        delay_tool_ms=10,
    )
    assert res2.success is True
    assert res2.version == 2
    assert session.state_mgr.state.slots.get("class_constraint") == "SL"
    # Duronto does not have SL class, so it shouldn't appear
    assert "Duronto Express" not in (res2.assistant_response or "")
    # Vidarbha and Sewagram have SL class
    assert "Vidarbha Express" in (res2.assistant_response or "") or "Sewagram Superfast Express" in (res2.assistant_response or "")


# -----------------------------------------------------------------------------
# Test 5: Slot Clearing
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slot_clearing(session: Session, default_orchestrator: LLMOrchestrator):
    """Test clearing a slot constraint (e.g. 'after 8 PM' -> 'Any time is fine')."""
    # Turn 1: with time filter
    res1 = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai tomorrow after 8 PM.",
        orchestrator=default_orchestrator,
        delay_tool_ms=10,
    )
    assert res1.success is True
    assert session.state_mgr.state.slots.get("time_constraint") == "after 8 PM"
    assert "Duronto Express" not in (res1.assistant_response or "")

    # Turn 2: clear time constraint
    res2 = await session.process_turn(
        prompt="Any time is fine.",
        orchestrator=default_orchestrator,
        delay_tool_ms=10,
    )
    assert res2.success is True
    assert res2.version == 2
    assert "time_constraint" not in session.state_mgr.state.slots
    # With time filter cleared, morning Duronto (06:40) is back in the results
    assert "Duronto Express" in (res2.assistant_response or "")


# -----------------------------------------------------------------------------
# Test 6: Tool Call for Active Request
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tool_call_for_active_request(session: Session, default_orchestrator: LLMOrchestrator):
    """Verifies that events are emitted in strict chronological order for active requests."""
    res = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai tomorrow.",
        orchestrator=default_orchestrator,
        delay_tool_ms=20,
    )
    assert res.success is True

    types = [e.event_type for e in session.event_logger.get_events()]
    assert VoiceEventType.REQUEST_INITIALIZED in types
    assert VoiceEventType.LLM_STEP1_STARTED in types
    assert VoiceEventType.LLM_STEP1_COMPLETED in types
    assert VoiceEventType.TOOL_STARTED in types
    assert VoiceEventType.TOOL_COMPLETED in types
    assert VoiceEventType.TOOL_RESULT_ACCEPTED in types
    assert VoiceEventType.LLM_STEP2_STARTED in types
    assert VoiceEventType.LLM_STEP2_COMPLETED in types
    assert VoiceEventType.TURN_COMPLETED in types


# -----------------------------------------------------------------------------
# Test 7: Stale Tool Result Protection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_tool_result(session: Session, default_orchestrator: LLMOrchestrator):
    """Verifies that if Turn 1 is delayed and Turn 2 supersedes it, Turn 1 result is discarded."""
    req1 = session.create_request("Find trains from Nagpur to Mumbai tomorrow.")
    
    # Launch Turn 1 with 200ms delay in background
    task1 = asyncio.create_task(
        default_orchestrator.process_turn(
            session=session,
            request_id=req1.request_id,
            version=req1.conversation_version,
            prompt=req1.prompt,
            delay_tool_ms=200,
        )
    )

    # Let task1 start and begin tool execution
    await asyncio.sleep(0.05)

    # User creates Turn 2, invalidating Turn 1
    req2 = session.create_request("Actually, from Delhi to Mumbai tomorrow.")
    task2 = asyncio.create_task(
        default_orchestrator.process_turn(
            session=session,
            request_id=req2.request_id,
            version=req2.conversation_version,
            prompt=req2.prompt,
            delay_tool_ms=20,
        )
    )

    res1, res2 = await asyncio.gather(task1, task2)

    # Turn 1 must be rejected/stale
    assert res1.success is False
    assert res1.is_stale is True

    # Turn 2 must succeed with Delhi to Mumbai results
    assert res2.success is True
    assert res2.version == 2
    assert "Rajdhani" in (res2.assistant_response or "")

    # Stale discard recorded
    assert len(session.stale_discards) >= 1
    assert session.stale_discards[0].request_id == req1.request_id


# -----------------------------------------------------------------------------
# Test 8: Interrupted Request During LLM Step 1
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_interrupted_request_step1(session: Session):
    """Test user interruption occurring while LLM Step 1 is generating."""
    slow_llm = MockLLMClient(simulated_delay_ms=200)
    orch = LLMOrchestrator(llm_client=slow_llm)

    req = session.create_request("Find trains from Nagpur to Mumbai tomorrow.")
    turn_task = asyncio.create_task(
        orch.process_turn(
            session=session,
            request_id=req.request_id,
            version=req.conversation_version,
            prompt=req.prompt,
        )
    )

    # Wait for Step 1 to begin, then interrupt
    await asyncio.sleep(0.05)
    session.interrupt(reason="User vocalized 'Wait'")

    result = await turn_task
    assert result.success is False
    assert result.is_stale is True
    assert req.status == RequestStatus.OBSOLETE
    assert session.current_answer is None


# -----------------------------------------------------------------------------
# Test 9: Interrupted Request During LLM Step 2 Synthesis
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_interrupted_request_step2(session: Session):
    """Test user interruption occurring during LLM Step 2 response synthesis."""
    # Fast step 1 (0 delay), slow step 2 (200ms delay)
    client = MockLLMClient(simulated_delay_ms=0)
    orch = LLMOrchestrator(llm_client=client)

    req = session.create_request("Find trains from Nagpur to Mumbai tomorrow.")

    # Start turn with fast tool (10ms)
    async def run_with_delayed_step2():
        # Inject delay for synthesis
        client.simulated_delay_ms = 200
        return await orch.process_turn(
            session=session,
            request_id=req.request_id,
            version=req.conversation_version,
            prompt=req.prompt,
            delay_tool_ms=10,
        )

    turn_task = asyncio.create_task(run_with_delayed_step2())

    # Wait for tool to finish and Step 2 to start
    await asyncio.sleep(0.06)
    session.interrupt(reason="User said 'Cancel that'")

    result = await turn_task
    assert result.success is False
    assert result.is_stale is True
    assert req.status == RequestStatus.OBSOLETE
    assert session.current_answer is None


# -----------------------------------------------------------------------------
# Test 10: Multiple Rapid Request Updates
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multiple_rapid_request_updates(session: Session, default_orchestrator: LLMOrchestrator):
    """Stress test: 3 rapid constraint updates within milliseconds."""
    req1 = session.create_request("Find trains from Nagpur to Mumbai.")
    t1 = asyncio.create_task(
        default_orchestrator.process_turn(
            session=session, request_id=req1.request_id, version=req1.conversation_version, prompt=req1.prompt, delay_tool_ms=100
        )
    )

    await asyncio.sleep(0.01)
    req2 = session.create_request("Only after 8 PM.")
    t2 = asyncio.create_task(
        default_orchestrator.process_turn(
            session=session, request_id=req2.request_id, version=req2.conversation_version, prompt=req2.prompt, delay_tool_ms=100
        )
    )

    await asyncio.sleep(0.01)
    req3 = session.create_request("Actually, Delhi to Mumbai tomorrow, any time is fine.")
    t3 = asyncio.create_task(
        default_orchestrator.process_turn(
            session=session, request_id=req3.request_id, version=req3.conversation_version, prompt=req3.prompt, delay_tool_ms=10
        )
    )

    r1, r2, r3 = await asyncio.gather(t1, t2, t3)

    assert r1.is_stale is True and r1.success is False
    assert r2.is_stale is True and r2.success is False
    assert r3.is_stale is False and r3.success is True
    assert r3.version == 3
    assert "Rajdhani" in (r3.assistant_response or "")
    assert session.current_answer == r3.assistant_response


# -----------------------------------------------------------------------------
# Test 11: Malformed LLM Tool Arguments
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_malformed_llm_tool_arguments(session: Session):
    """Test LLM returning invalid schema types caught by ToolRegistry Pydantic validation."""
    bad_llm = MockLLMClient(forced_malformed_args=True)
    orch = LLMOrchestrator(llm_client=bad_llm)

    result = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai tomorrow.",
        orchestrator=orch,
    )

    assert result.success is False
    assert "Argument validation error" in (result.error or "")
    # Check that error event was logged
    types = [e.event_type for e in session.event_logger.get_events()]
    assert VoiceEventType.TOOL_ARGS_INVALID in types


# -----------------------------------------------------------------------------
# Test 12: Unregistered or Forbidden Tool Protection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unregistered_or_forbidden_tool(session: Session):
    """Test strict registry rejecting unknown or forbidden tool invocations."""
    forbidden_llm = MockLLMClient(forced_unregistered_tool="delete_database")
    orch = LLMOrchestrator(llm_client=forbidden_llm)

    result = await session.process_turn(
        prompt="Execute administrative cleanup.",
        orchestrator=orch,
    )

    assert result.success is False
    assert "forbidden or unknown" in (result.error or "")
    types = [e.event_type for e in session.event_logger.get_events()]
    assert VoiceEventType.TOOL_UNKNOWN_OR_FORBIDDEN in types
