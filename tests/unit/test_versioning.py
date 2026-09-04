"""Unit tests for Request Versioning and State Management invariants.

Rules tested:
- Rule 2: Every user request must have a unique request ID.
- Rule 3: Every user request must have a conversation version.
- Rule 4: Obsolete requests must never produce active output.
"""

import pytest
from app.core.versioning import RequestVersionGate, VersionGateError
from app.models import RequestStatus


def test_unique_request_id_and_monotonic_version(test_session):
    """Verifies that each request gets a distinct UUID and monotonic version integer."""
    req1 = test_session.create_request(prompt="Find trains from Nagpur to Mumbai")
    assert req1.version == 1
    assert req1.conversation_version == 1
    assert req1.request_id.startswith("req-")
    assert test_session.active_version == 1
    assert test_session.active_request_id == req1.request_id

    req2 = test_session.create_request(prompt="Only trains after 8 PM")
    assert req2.version == 2
    assert req2.conversation_version == 2
    assert req2.request_id.startswith("req-")
    assert req2.request_id != req1.request_id
    assert test_session.active_version == 2
    assert test_session.active_request_id == req2.request_id


def test_request_active_validation(test_session):
    """Verifies RequestVersionGate.validate_request_active checks."""
    req1 = test_session.create_request(prompt="Initial search")
    
    # When req1 is active, validation succeeds
    is_valid, reason = RequestVersionGate.validate_request_active(
        request=req1, 
        state=test_session.state_mgr.state
    )
    assert is_valid is True
    assert reason is None

    # When req2 is created, req1 becomes obsolete / cancelled
    req2 = test_session.create_request(prompt="Second search")
    is_valid, reason = RequestVersionGate.validate_request_active(
        request=req1, 
        state=test_session.state_mgr.state
    )
    assert is_valid is False
    assert any(w in reason for w in ["OBSOLETE", "cancelled", "Version mismatch"])


def test_cancelled_request_rejected(test_session):
    """Verifies that cancelled requests fail validation immediately."""
    req = test_session.create_request(prompt="Trip to Delhi")
    req.is_cancelled = True

    is_valid, reason = RequestVersionGate.validate_request_active(
        request=req,
        state=test_session.state_mgr.state,
    )
    assert is_valid is False
    assert "cancelled" in reason.lower()


def test_obsolete_version_cannot_append_assistant_message(test_session):
    """Verifies Rule 4: Obsolete requests cannot append assistant speech to state."""
    req1 = test_session.create_request(prompt="Search 1")
    req2 = test_session.create_request(prompt="Search 2")  # Advances to v2

    # Attempt to complete turn using obsolete version 1
    with pytest.raises(VersionGateError) as exc_info:
        test_session.state_mgr.append_assistant_message(
            text="Late response from Search 1",
            request_id=req1.request_id,
            version=1,
        )
    assert "Stale tool result" in str(exc_info.value)

    # Valid turn 2 succeeds
    msg = test_session.state_mgr.append_assistant_message(
        text="Valid response for Search 2",
        request_id=req2.request_id,
        version=2,
    )
    assert msg.role == "assistant"
    assert msg.version == 2


def test_interruption_invalidates_active_request(test_session):
    """Verifies that interruption marks the active request obsolete."""
    req1 = test_session.create_request(prompt="Search Nagpur to Mumbai")
    assert req1.status == RequestStatus.RUNNING

    interrupt_info = test_session.interrupt(reason="User said Wait")
    assert interrupt_info["invalidated_version"] == 1
    assert interrupt_info["invalidated_request_id"] == req1.request_id
    assert req1.status == RequestStatus.OBSOLETE
    assert req1.is_cancelled is True
