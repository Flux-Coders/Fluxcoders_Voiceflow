"""Unit tests for FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_session_lifecycle_api():
    # 1. Create session
    create_res = client.post("/api/sessions", json={"session_id": "sess-test-api"})
    assert create_res.status_code == 201
    data = create_res.json()
    assert data["session_id"] == "sess-test-api"
    assert data["active_version"] == 0

    # 2. Start request turn
    turn_res = client.post("/api/sessions/sess-test-api/requests", json={"prompt": "Find trains"})
    assert turn_res.status_code == 201
    turn_data = turn_res.json()
    assert turn_data["version"] == 1
    req_id = turn_data["request_id"]

    # 3. Execute tool
    tool_res = client.post(
        "/api/sessions/sess-test-api/tools/execute",
        json={
            "request_id": req_id,
            "version": 1,
            "tool_name": "train_search",
            "args": {"origin": "Nagpur", "destination": "Mumbai"},
            "delay_seconds": 0.0,
        }
    )
    assert tool_res.status_code == 200
    assert tool_res.json()["status"] == "COMPLETED_VALID"

    # 4. Complete turn
    comp_res = client.post(
        "/api/sessions/sess-test-api/complete",
        json={
            "request_id": req_id,
            "version": 1,
            "assistant_response": "I found trains for you.",
        }
    )
    assert comp_res.status_code == 200
    assert comp_res.json()["accepted"] is True

    # 5. Query events and metrics
    events_res = client.get("/api/sessions/sess-test-api/events")
    assert events_res.status_code == 200
    assert len(events_res.json()) > 0

    metrics_res = client.get("/api/sessions/sess-test-api/metrics")
    assert metrics_res.status_code == 200
    assert metrics_res.json()["total_requests"] == 1

