"""VoiceFlow Phase 13 Real LLM Provider Integration Tests.

Comprehensive offline deterministic unit tests covering:
1. Configuration loading and validation
2. Provider client factory (create_llm_client)
3. Direct conversational completion without tools (0 tool calls)
4. Single tool call generation and slot patch extraction (1 tool call)
5. Multiple tool calls rejected explicitly (>1 tool calls)
6. HTTP 401 authentication error handling & credential protection
7. HTTP 400 bad request error handling
8. HTTP 429 rate limit error handling
9. HTTP 5xx server error handling
10. Timeout error handling
11. Network connection error handling
12. Pre-call cancellation handling
13. Step 2 response synthesis with tool payload
14. End-to-end LLMOrchestrator turn execution with OpenAILLMClient
15. Step 1 provider error resilience in LLMOrchestrator
16. Step 2 provider error resilience in LLMOrchestrator
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
import httpx
import pytest

from app.core.cancellation import CancellationToken
from app.core.event_logger import VoiceEventLogger
from app.core.session import Session
from app.engine.llm_orchestrator import LLMOrchestrator
from app.engine.llm_provider import (
    BaseLLMClient,
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMConfigError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    MockLLMClient,
    OpenAIConfig,
    OpenAILLMClient,
    create_llm_client,
)
from app.models import LLMMessage, ToolCallRequest, ToolStatus, VoiceEventType
from app.tools.registry import create_default_registry


# -----------------------------------------------------------------------------
# Fixtures & Helpers
# -----------------------------------------------------------------------------
@pytest.fixture
def session() -> Session:
    event_logger = VoiceEventLogger()
    return Session(session_id="test-session-real-llm", event_logger=event_logger)


def make_openai_response(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    status_code: int = 200,
) -> httpx.Response:
    """Constructs a mock httpx.Response matching OpenAI chat completion schema."""
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
        if not content:
            message["content"] = None

    body = {
        "id": "chatcmpl-test-12345",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }
    return httpx.Response(status_code=status_code, json=body)


# -----------------------------------------------------------------------------
# Test 1: OpenAI Configuration Validation
# -----------------------------------------------------------------------------
def test_openai_config_validation(monkeypatch: pytest.MonkeyPatch):
    # Valid config passes validation
    cfg = OpenAIConfig(
        api_key="sk-test-valid-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
    )
    cfg.validate_config(require_key=True)

    # Missing / empty API key raises LLMConfigError
    invalid_cfg = OpenAIConfig(api_key="", base_url="https://api.openai.com/v1")
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY is not configured"):
        invalid_cfg.validate_config(require_key=True)

    # Invalid base URL raises LLMConfigError
    invalid_url_cfg = OpenAIConfig(api_key="sk-test", base_url="ftp://bad-url")
    with pytest.raises(LLMConfigError, match="Invalid OPENAI_BASE_URL"):
        invalid_url_cfg.validate_config(require_key=True)

    # Empty model raises LLMConfigError
    empty_model_cfg = OpenAIConfig(api_key="sk-test", model="")
    with pytest.raises(LLMConfigError, match="OPENAI_MODEL cannot be empty"):
        empty_model_cfg.validate_config(require_key=True)


def test_openai_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom.openai.endpoint/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_TEMPERATURE", "0.2")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "25.0")

    cfg = OpenAIConfig.from_env()
    assert cfg.api_key == "sk-env-test-key"
    assert cfg.base_url == "https://custom.openai.endpoint/v1"
    assert cfg.model == "gpt-4o"
    assert cfg.temperature == 0.2
    assert cfg.timeout_seconds == 25.0


# -----------------------------------------------------------------------------
# Test 2: Client Factory (create_llm_client)
# -----------------------------------------------------------------------------
def test_create_llm_client_factory(monkeypatch: pytest.MonkeyPatch):
    # Default mock
    monkeypatch.setenv("VOICEFLOW_LLM_PROVIDER", "mock")
    client_mock = create_llm_client()
    assert isinstance(client_mock, MockLLMClient)

    # Explicit mock
    client_mock_explicit = create_llm_client(provider_name="mock")
    assert isinstance(client_mock_explicit, MockLLMClient)

    # Explicit openai
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    client_real = create_llm_client(provider_name="openai")
    assert isinstance(client_real, OpenAILLMClient)

    # Unsupported provider raises LLMConfigError
    with pytest.raises(LLMConfigError, match="Unsupported LLM provider: 'unsupported'"):
        create_llm_client(provider_name="unsupported")


# -----------------------------------------------------------------------------
# Test 3: Direct Conversational Completion (0 Tool Calls)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_chat_completion_no_tools():
    expected_text = "Hello! I am VoiceFlow. Where would you like to travel today?"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test-key"
        body = json.loads(request.read())
        assert body["model"] == "gpt-4o-mini"
        assert len(body["messages"]) > 0
        return make_openai_response(content=expected_text, finish_reason="stop")

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    messages = [
        LLMMessage(role="system", content="You are VoiceFlow assistant."),
        LLMMessage(role="user", content="Hello!"),
    ]

    resp = await client.generate(messages=messages)
    assert resp.content == expected_text
    assert resp.finish_reason == "stop"
    assert len(resp.tool_calls) == 0
    assert resp.slot_patch is None


# -----------------------------------------------------------------------------
# Test 4: Single Tool Call Generation & Slot Patch Extraction (1 Tool Call)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_single_tool_call_extraction():
    tool_arguments = {
        "source": "Nagpur",
        "destination": "Mumbai",
        "date": "tomorrow",
        "class_constraint": "3A",
    }
    tool_call_mock = {
        "id": "call_12345",
        "type": "function",
        "function": {
            "name": "search_trains",
            "arguments": json.dumps(tool_arguments),
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return make_openai_response(
            content=None,
            tool_calls=[tool_call_mock],
            finish_reason="tool_calls",
        )

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    messages = [
        LLMMessage(role="system", content="You are VoiceFlow."),
        LLMMessage(role="user", content="Find 3A trains from Nagpur to Mumbai tomorrow."),
    ]
    tools = [{"type": "function", "function": {"name": "search_trains"}}]

    resp = await client.generate(messages=messages, tools=tools)
    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "search_trains"
    assert resp.tool_calls[0].arguments["source"] == "Nagpur"
    assert resp.tool_calls[0].arguments["destination"] == "Mumbai"
    assert resp.tool_calls[0].arguments["class_constraint"] == "3A"

    assert resp.slot_patch is not None
    assert resp.slot_patch.set_slots["source"] == "Nagpur"
    assert resp.slot_patch.set_slots["destination"] == "Mumbai"


# -----------------------------------------------------------------------------
# Test 5: Multiple Tool Calls are Explicitly Rejected (>1 Tool Calls)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multiple_tool_calls_are_rejected_explicitly(session: Session):
    tool_calls_mock = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "search_trains",
                "arguments": json.dumps({"source": "Nagpur", "destination": "Mumbai"}),
            },
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {
                "name": "search_hotels",
                "arguments": json.dumps({"city": "Mumbai"}),
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return make_openai_response(
            content=None,
            tool_calls=tool_calls_mock,
            finish_reason="tool_calls",
        )

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    llm_client = OpenAILLMClient(config=config, transport=transport)
    orchestrator = LLMOrchestrator(
        llm_client=llm_client,
        tool_registry=create_default_registry(),
    )

    result = await session.process_turn(
        prompt="Search trains from Nagpur to Mumbai and find hotels in Mumbai.",
        orchestrator=orchestrator,
        delay_tool_ms=10,
    )

    # Must be explicitly rejected
    assert result.success is False
    assert result.error == "Multiple tool calls are unsupported."
    assert "Multiple simultaneous tool calls are not supported" in (result.assistant_response or "")
    assert result.tool_task is None

    # Verify event logged
    events = [e for e in session.event_logger.get_events() if e.event_type == VoiceEventType.TOOL_UNKNOWN_OR_FORBIDDEN]
    assert len(events) >= 1
    assert events[0].payload.get("tool_count") == 2


# -----------------------------------------------------------------------------
# Test 6: HTTP 401 Authentication Error & No API Key Leakage
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_401_authentication_error_and_no_key_leak():
    secret_key = "sk-super-secret-key-12345"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            json={"error": {"message": "Invalid API key provided", "type": "invalid_request_error"}},
        )

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key=secret_key)
    client = OpenAILLMClient(config=config, transport=transport)

    with pytest.raises(LLMAuthenticationError) as exc_info:
        await client.generate(messages=[LLMMessage(role="user", content="Hello")])

    err_str = str(exc_info.value)
    assert "HTTP 401" in err_str
    assert secret_key not in err_str


# -----------------------------------------------------------------------------
# Test 7: HTTP 400 Bad Request Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_400_bad_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={"error": {"message": "Invalid model parameter"}},
        )

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    with pytest.raises(LLMBadRequestError) as exc_info:
        await client.generate(messages=[LLMMessage(role="user", content="Hello")])

    assert exc_info.value.status_code == 400


# -----------------------------------------------------------------------------
# Test 8: HTTP 429 Rate Limit Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_429_rate_limit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=429,
            json={"error": {"message": "Rate limit exceeded"}},
        )

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    with pytest.raises(LLMRateLimitError) as exc_info:
        await client.generate(messages=[LLMMessage(role="user", content="Hello")])

    assert exc_info.value.status_code == 429


# -----------------------------------------------------------------------------
# Test 9: HTTP 500 Server Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_500_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=500,
            json={"error": {"message": "Internal server error"}},
        )

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    with pytest.raises(LLMServerError) as exc_info:
        await client.generate(messages=[LLMMessage(role="user", content="Hello")])

    assert exc_info.value.status_code == 500


# -----------------------------------------------------------------------------
# Test 10: Request Timeout Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Read timeout on connection")

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key", timeout_seconds=2.0)
    client = OpenAILLMClient(config=config, transport=transport)

    with pytest.raises(LLMTimeoutError, match="timed out after 2.0s"):
        await client.generate(messages=[LLMMessage(role="user", content="Hello")])


# -----------------------------------------------------------------------------
# Test 11: Network Connection Error
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to resolve hostname")

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    with pytest.raises(LLMConnectionError, match="Network error connecting"):
        await client.generate(messages=[LLMMessage(role="user", content="Hello")])


# -----------------------------------------------------------------------------
# Test 12: Pre-call Cancellation Check
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pre_call_cancellation():
    transport_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return make_openai_response(content="Should not be reached")

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    token = CancellationToken(request_id="req-cancelled-1", version=1)
    token.cancel()

    resp = await client.generate(
        messages=[LLMMessage(role="user", content="Hello")],
        cancellation_token=token,
    )

    assert resp.finish_reason == "cancelled"
    assert resp.content is None
    assert transport_called is False


# -----------------------------------------------------------------------------
# Test 13: Step 2 Response Synthesis with Tool Payload
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step2_response_synthesis_with_tool_result():
    summary_text = "I found 2 trains departing tomorrow: Duronto Express and Vidarbha Express."

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        messages = body["messages"]
        # Verify tool role message was included
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "trains" in tool_msgs[0].get("content", "")
        return make_openai_response(content=summary_text, finish_reason="stop")

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    client = OpenAILLMClient(config=config, transport=transport)

    synthesis_messages = [
        LLMMessage(role="system", content="You are VoiceFlow assistant."),
        LLMMessage(role="user", content="Find trains from Nagpur to Mumbai tomorrow."),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[ToolCallRequest(id="call_1", name="search_trains", arguments={"source": "Nagpur", "destination": "Mumbai"})],
        ),
        LLMMessage(
            role="tool",
            name="search_trains",
            tool_call_id="call_1",
            content=json.dumps({"total_found": 2, "trains": [{"name": "Duronto Express"}, {"name": "Vidarbha Express"}]}),
        ),
    ]

    resp = await client.generate(messages=synthesis_messages)
    assert resp.content == summary_text
    assert resp.finish_reason == "stop"


# -----------------------------------------------------------------------------
# Test 14: End-to-End LLMOrchestrator Execution with OpenAILLMClient
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_end_to_end_orchestrator_with_openai_client(session: Session):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = json.loads(request.read())

        # Step 1: user utterance -> tool call
        if call_count == 1:
            return make_openai_response(
                content=None,
                tool_calls=[{
                    "id": "call_e2e_1",
                    "type": "function",
                    "function": {
                        "name": "search_trains",
                        "arguments": json.dumps({"source": "Nagpur", "destination": "Mumbai", "date": "tomorrow"}),
                    },
                }],
                finish_reason="tool_calls",
            )
        # Step 2: response synthesis
        else:
            return make_openai_response(
                content="I found 4 trains from Nagpur to Mumbai for tomorrow: Duronto Express at 20:40, Vidarbha Express at 17:15.",
                finish_reason="stop",
            )

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    llm_client = OpenAILLMClient(config=config, transport=transport)
    orchestrator = LLMOrchestrator(
        llm_client=llm_client,
        tool_registry=create_default_registry(),
    )

    result = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai tomorrow.",
        orchestrator=orchestrator,
        delay_tool_ms=10,
    )

    assert result.success is True
    assert result.is_stale is False
    assert result.version == 1
    assert result.tool_task is not None
    assert result.tool_task.status == ToolStatus.COMPLETED_VALID
    assert "Duronto Express" in (result.assistant_response or "")
    assert call_count == 2


# -----------------------------------------------------------------------------
# Test 15: Step 1 Provider Error Resilience in LLMOrchestrator
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_orchestrator_llm_step1_error_handling(session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=503, json={"error": {"message": "Service unavailable"}})

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    llm_client = OpenAILLMClient(config=config, transport=transport)
    orchestrator = LLMOrchestrator(
        llm_client=llm_client,
        tool_registry=create_default_registry(),
    )

    result = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai.",
        orchestrator=orchestrator,
        delay_tool_ms=10,
    )

    assert result.success is False
    assert "LLM Step 1 error" in (result.error or "")
    assert "trouble connecting" in (result.assistant_response or "")

    # Check that failure event was logged
    events = [e for e in session.event_logger.get_events() if e.event_type == VoiceEventType.LLM_STEP1_FAILED]
    assert len(events) == 1
    assert events[0].payload.get("status_code") == 503


# -----------------------------------------------------------------------------
# Test 16: Step 2 Provider Error Resilience in LLMOrchestrator
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_orchestrator_llm_step2_error_handling(session: Session):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Step 1 succeeds
            return make_openai_response(
                content=None,
                tool_calls=[{
                    "id": "call_step1",
                    "type": "function",
                    "function": {
                        "name": "search_trains",
                        "arguments": json.dumps({"source": "Nagpur", "destination": "Mumbai"}),
                    },
                }],
                finish_reason="tool_calls",
            )
        else:
            # Step 2 fails with 500
            return httpx.Response(status_code=500, json={"error": {"message": "Internal error"}})

    transport = httpx.MockTransport(handler)
    config = OpenAIConfig(api_key="sk-test-key")
    llm_client = OpenAILLMClient(config=config, transport=transport)
    orchestrator = LLMOrchestrator(
        llm_client=llm_client,
        tool_registry=create_default_registry(),
    )

    result = await session.process_turn(
        prompt="Find trains from Nagpur to Mumbai.",
        orchestrator=orchestrator,
        delay_tool_ms=10,
    )

    assert result.success is False
    assert "LLM Step 2 error" in (result.error or "")
    assert "error synthesizing the summary" in (result.assistant_response or "")

    # Check that step 2 failure event was logged
    events = [e for e in session.event_logger.get_events() if e.event_type == VoiceEventType.LLM_STEP2_FAILED]
    assert len(events) == 1
    assert events[0].payload.get("status_code") == 500

