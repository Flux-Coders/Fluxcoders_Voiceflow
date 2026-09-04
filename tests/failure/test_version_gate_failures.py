"""Failure Tests for Version Gates, State Mismatches, and Error Boundaries.

Rule 10 compliance: Every realtime feature must have a failure test.
"""

import asyncio
import pytest
from app.core.versioning import RequestVersionGate, VersionGateError
from app.models import RequestStatus, ToolStatus


def test_tool_request_id_mismatch_failure(test_session):
    """Failure test: Tool returns with valid version number but incorrect request ID."""
    req1 = test_session.create_request(prompt="Turn 1")
    
    is_valid, reason = RequestVersionGate.validate_tool_result_active(
        tool_version=req1.version,
        tool_request_id="req-bogus-foreign-id",
        state=test_session.state_mgr.state,
    )
    assert is_valid is False
    assert "Stale tool result: tool executed for request" in reason


def test_update_slots_obsolete_version_raises_failure(test_session):
    """Failure test: Attempting to update extracted slots with obsolete version raises VersionGateError."""
    req1 = test_session.create_request(prompt="Nagpur to Mumbai")
    req2 = test_session.create_request(prompt="After 8 PM")  # Version advances to 2

    # Attempt to write slots from turn 1
    with pytest.raises(VersionGateError) as exc_info:
        test_session.state_mgr.update_slots(
            new_slots={"origin": "Nagpur", "destination": "Mumbai"},
            version=1,
            request_id=req1.request_id,
        )
    assert "Cannot update slots" in str(exc_info.value)

    # Valid turn 2 can write slots
    test_session.state_mgr.update_slots(
        new_slots={"origin": "Nagpur", "destination": "Mumbai", "min_departure": "20:00"},
        version=2,
        request_id=req2.request_id,
    )
    assert test_session.state_mgr.state.slots["min_departure"] == "20:00"


@pytest.mark.asyncio
async def test_tool_execution_cancellation_failure(test_session):
    """Failure test: In-flight tool coroutine interrupted during execution."""
    req = test_session.create_request(prompt="Long train query")
    
    # Run tool with 0.3s delay
    tool_coro = test_session.run_tool(
        request_id=req.request_id,
        version=req.version,
        tool_name="train_search",
        args={"origin": "Nagpur", "destination": "Mumbai"},
        delay_seconds=0.3,
    )
    task = asyncio.create_task(tool_coro)

    # Interrupt session after 0.05s
    await asyncio.sleep(0.05)
    test_session.interrupt(reason="User said Stop")

    result = await task
    assert result.status == ToolStatus.CANCELLED
    assert "Cancelled" in result.discard_reason

