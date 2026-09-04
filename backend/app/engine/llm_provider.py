"""VoiceFlow LLM Provider Abstraction, Mock Client, and OpenAI-Compatible Client.

Provides:
- BaseLLMClient: Provider-agnostic abstract interface.
- MockLLMClient: Deterministic mock implementation supporting slot extraction,
  constraint addition, replacement, clearing, tool-calling, and response synthesis.
- OpenAILLMClient: Production OpenAI-compatible chat completions client.
- OpenAIConfig: Pydantic configuration for OpenAI-compatible LLM endpoints.
- create_llm_client: Factory for instantiating configured LLM clients.
- Typed LLM domain exceptions (LLMError, LLMConfigError, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

from app.core.cancellation import CancellationToken
from app.models import (
    LLMMessage,
    LLMResponse,
    SlotPatch,
    ToolCallRequest,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# LLM Provider Domain Exceptions
# -----------------------------------------------------------------------------
class LLMError(Exception):
    """Base exception for all LLM provider errors."""

    def __init__(
        self,
        message: str,
        provider: str = "openai",
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code


class LLMConfigError(LLMError):
    """Raised when LLM configuration parameters are invalid or missing."""
    pass


class LLMAuthenticationError(LLMError):
    """Raised on HTTP 401/403 authentication failures."""
    pass


class LLMBadRequestError(LLMError):
    """Raised on HTTP 400 Bad Request."""
    pass


class LLMRateLimitError(LLMError):
    """Raised on HTTP 429 rate limit exceeded."""
    pass


class LLMServerError(LLMError):
    """Raised on HTTP 5xx upstream server errors."""
    pass


class LLMTimeoutError(LLMError):
    """Raised when LLM HTTP request times out."""
    pass


class LLMConnectionError(LLMError):
    """Raised on network connection failures."""
    pass


class LLMCancellationError(LLMError):
    """Raised when LLM generation is cancelled by user interruption."""
    pass


class BaseLLMClient(ABC):
    """Abstract interface for all LLM providers (Mock, OpenAI, Gemini, Anthropic, etc.)."""

    @abstractmethod
    async def generate(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        cancellation_token: Optional[CancellationToken] = None,
        request_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> LLMResponse:
        """Generates a text completion or structured tool call."""
        pass


class MockLLMClient(BaseLLMClient):
    """Deterministic Mock LLM client for intent extraction, slot patching, and response synthesis."""

    def __init__(
        self,
        simulated_delay_ms: int = 0,
        forced_malformed_args: bool = False,
        forced_unregistered_tool: Optional[str] = None,
        canned_responses: Optional[Dict[str, str]] = None,
    ) -> None:
        self.simulated_delay_ms = simulated_delay_ms
        self.forced_malformed_args = forced_malformed_args
        self.forced_unregistered_tool = forced_unregistered_tool
        self.canned_responses = canned_responses or {}

    async def _handle_delay_with_cancellation(
        self,
        delay_ms: int,
        cancellation_token: Optional[CancellationToken],
    ) -> bool:
        """Sleeps in small increments, checking cancellation. Returns False if cancelled."""
        if delay_ms <= 0:
            if cancellation_token and cancellation_token.is_cancelled:
                return False
            return True

        step_sec = 0.02
        elapsed = 0.0
        total_sec = delay_ms / 1000.0
        while elapsed < total_sec:
            if cancellation_token and cancellation_token.is_cancelled:
                return False
            await asyncio.sleep(min(step_sec, total_sec - elapsed))
            elapsed += step_sec
        return True

    def _extract_slots_and_patch(
        self,
        prompt: str,
        current_slots: Dict[str, Any],
    ) -> tuple[Dict[str, Any], SlotPatch]:
        """Parses prompt text and produces updated slots along with an explicit SlotPatch."""
        text = prompt.lower().strip()
        set_slots: Dict[str, Any] = {}
        clear_slots: List[str] = []

        # 1. Source & Destination extraction
        # Pattern: "from <source> to <destination>"
        match_route = re.search(r'from\s+([a-zA-Z\s]+?)\s+to\s+([a-zA-Z\s]+?)(?:\s+tomorrow|\s+today|\s+after|\s+on|\s+only|\.|\,|$)', text)
        if match_route:
            set_slots["source"] = match_route.group(1).strip().title()
            set_slots["destination"] = match_route.group(2).strip().title()
        else:
            # Match "nagpur to mumbai"
            match_pair = re.search(r'([a-zA-Z]+)\s+to\s+([a-zA-Z]+)', text)
            if match_pair and match_pair.group(1).lower() not in ("back", "switch", "change"):
                set_slots["source"] = match_pair.group(1).strip().title()
                set_slots["destination"] = match_pair.group(2).strip().title()
            else:
                # Individual source / destination mentions
                match_src = re.search(r'(?:from|origin|leaving|departing)\s+([a-zA-Z]+)', text)
                if match_src:
                    set_slots["source"] = match_src.group(1).strip().title()
                match_dst = re.search(r'(?:to|destination|reaching)\s+([a-zA-Z]+)', text)
                if match_dst:
                    set_slots["destination"] = match_dst.group(1).strip().title()

        # 2. Date extraction
        if "tomorrow" in text:
            set_slots["date"] = "tomorrow"
        elif "today" in text:
            set_slots["date"] = "today"
        elif re.search(r'\d{4}-\d{2}-\d{2}', text):
            date_match = re.search(r'\d{4}-\d{2}-\d{2}', text)
            if date_match:
                set_slots["date"] = date_match.group(0)

        # 3. Time Constraint: Clearing vs Adding/Replacing
        time_clear_phrases = [
            "any time is fine", "any time", "no time constraint", 
            "no time restriction", "clear time constraint", "whenever",
            "no time filter", "all times", "any departure"
        ]
        if any(phrase in text for phrase in time_clear_phrases):
            clear_slots.append("time_constraint")
        else:
            # Time constraint additions/replacements
            if "after 8 pm" in text or "after 8pm" in text or "after 20:00" in text:
                set_slots["time_constraint"] = "after 8 PM"
            elif "after 8 am" in text or "after 8am" in text:
                set_slots["time_constraint"] = "after 8 AM"
            elif "evening" in text or "night" in text:
                set_slots["time_constraint"] = "evening"
            elif "morning" in text:
                set_slots["time_constraint"] = "morning"
            elif "afternoon" in text:
                set_slots["time_constraint"] = "afternoon"
            elif "after" in text:
                match_after = re.search(r'after\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)', text)
                if match_after:
                    set_slots["time_constraint"] = f"after {match_after.group(1).strip().upper()}"

        # 4. Class Constraint: Clearing vs Adding/Replacing
        class_clear_phrases = [
            "any class is fine", "no class constraint", "any class",
            "no class restriction", "clear class constraint", "all classes"
        ]
        if any(phrase in text for phrase in class_clear_phrases):
            clear_slots.append("class_constraint")
        else:
            # Class constraint replacements/additions
            if "sleeper" in text or " sl " in f" {text} " or "sleeper is fine" in text:
                set_slots["class_constraint"] = "SL"
            elif "3a" in text or "3rd ac" in text or "third ac" in text:
                set_slots["class_constraint"] = "3A"
            elif "2a" in text or "2nd ac" in text or "second ac" in text:
                set_slots["class_constraint"] = "2A"
            elif "1a" in text or "1st ac" in text or "first ac" in text or "first class" in text:
                set_slots["class_constraint"] = "1A"
            elif "only ac" in text or "ac only" in text:
                set_slots["class_constraint"] = "3A"

        # Construct merged slots
        merged = dict(current_slots)
        for c in clear_slots:
            merged.pop(c, None)
        merged.update(set_slots)

        # Default date to "tomorrow" if source and destination exist but date is unspecified
        if "source" in merged and "destination" in merged and "date" not in merged:
            merged["date"] = "tomorrow"
            set_slots["date"] = "tomorrow"

        patch = SlotPatch(set_slots=set_slots, clear_slots=clear_slots)
        return merged, patch

    async def generate(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        cancellation_token: Optional[CancellationToken] = None,
        request_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> LLMResponse:
        """Generates mock intent extraction, tool calls, or synthesized answers."""
        if cancellation_token and cancellation_token.is_cancelled:
            return LLMResponse(content=None, finish_reason="cancelled")

        # Simulate latency if configured
        if self.simulated_delay_ms > 0:
            ok = await self._handle_delay_with_cancellation(self.simulated_delay_ms, cancellation_token)
            if not ok or (cancellation_token and cancellation_token.is_cancelled):
                return LLMResponse(content=None, finish_reason="cancelled")

        if not messages:
            return LLMResponse(content="How can I assist you with your travel today?", finish_reason="stop")

        last_msg = messages[-1]

        # STEP 2: Response Synthesis from Tool Result
        if last_msg.role == "tool":
            return self._synthesize_tool_response(last_msg)

        # STEP 1: Intent Extraction & Tool Calling from User Utterance
        user_text = last_msg.content or ""

        # Check for canned overrides
        if user_text in self.canned_responses:
            return LLMResponse(content=self.canned_responses[user_text], finish_reason="stop")

        # Check forced testing hooks
        if self.forced_unregistered_tool:
            return LLMResponse(
                tool_calls=[ToolCallRequest(name=self.forced_unregistered_tool, arguments={})],
                finish_reason="tool_calls",
            )

        if self.forced_malformed_args:
            return LLMResponse(
                tool_calls=[ToolCallRequest(
                    name="search_trains",
                    arguments={"source": 12345, "destination": None},  # Invalid types
                )],
                finish_reason="tool_calls",
            )

        # Extract current slots from system message if present
        current_slots: Dict[str, Any] = {}
        for m in messages:
            if m.role == "system" and m.content and "CURRENT_SLOTS:" in m.content:
                try:
                    slot_part = m.content.split("CURRENT_SLOTS:")[1].strip()
                    current_slots = json.loads(slot_part)
                except Exception:
                    current_slots = {}

        merged_slots, patch = self._extract_slots_and_patch(user_text, current_slots)

        # Check if user query is not a travel search (e.g. general chit-chat)
        if not merged_slots.get("source") and not merged_slots.get("destination") and any(
            greet in user_text.lower() for greet in ("hello", "hi", "hey", "who are you", "what can you do")
        ):
            return LLMResponse(
                content="Hello! I am VoiceFlow. I can help you search trains and plan your travel. Where would you like to travel?",
                finish_reason="stop",
            )

        # Missing required parameter handling
        if not merged_slots.get("source") and not merged_slots.get("destination"):
            return LLMResponse(
                content="Where would you like to travel from and to?",
                slot_patch=patch,
                finish_reason="stop",
            )
        if not merged_slots.get("source"):
            return LLMResponse(
                content=f"Where are you departing from to travel to {merged_slots['destination']}?",
                slot_patch=patch,
                finish_reason="stop",
            )
        if not merged_slots.get("destination"):
            return LLMResponse(
                content=f"Where would you like to travel to from {merged_slots['source']}?",
                slot_patch=patch,
                finish_reason="stop",
            )

        # Both source and destination present -> emit structured tool call
        tool_args: Dict[str, Any] = {
            "source": merged_slots["source"],
            "destination": merged_slots["destination"],
            "date": merged_slots.get("date", "tomorrow"),
        }
        if merged_slots.get("time_constraint"):
            tool_args["time_constraint"] = merged_slots["time_constraint"]
        if merged_slots.get("class_constraint"):
            tool_args["class_constraint"] = merged_slots["class_constraint"]

        return LLMResponse(
            tool_calls=[ToolCallRequest(
                name="search_trains",
                arguments=tool_args,
            )],
            slot_patch=patch,
            finish_reason="tool_calls",
        )

    def _synthesize_tool_response(self, tool_msg: LLMMessage) -> LLMResponse:
        """Synthesizes natural language summary from tool JSON output."""
        try:
            data = json.loads(tool_msg.content or "{}")
        except Exception:
            return LLMResponse(
                content="I received the search results but could not format them.",
                finish_reason="stop",
            )

        trains = data.get("trains", [])
        total = data.get("total_found", len(trains))
        src = data.get("source", "origin")
        dst = data.get("destination", "destination")
        date_str = data.get("date", "tomorrow")
        time_filter = data.get("applied_time_filter")
        class_filter = data.get("applied_class_filter")

        if total == 0:
            qualifier = ""
            if time_filter:
                qualifier += f" departing after {time_filter}"
            if class_filter:
                qualifier += f" in {class_filter} class"
            return LLMResponse(
                content=f"I couldn't find any trains{qualifier} from {src} to {dst} for {date_str}.",
                finish_reason="stop",
            )

        train_summaries = []
        for t in trains[:3]:  # Summarize top 3
            t_name = t.get("name", "Train")
            t_no = t.get("train_no", "")
            dep = t.get("departure", "")
            arr = t.get("arrival", "")
            dur = t.get("duration", "")
            train_summaries.append(f"{t_name} ({t_no}) departing at {dep} (arrives {arr}, {dur})")

        joined = ", ".join(train_summaries)
        filter_mention = ""
        if time_filter:
            filter_mention = f" after {time_filter}"
        if class_filter:
            filter_mention += f" with {class_filter} class"

        response_text = f"I found {total} train{'s' if total != 1 else ''}{filter_mention} from {src} to {dst} for {date_str}: {joined}."
        return LLMResponse(
            content=response_text,
            finish_reason="stop",
        )


# -----------------------------------------------------------------------------
# OpenAI Configuration Model
# -----------------------------------------------------------------------------
class OpenAIConfig(BaseModel):
    """Configuration options for OpenAI-compatible LLM provider."""

    api_key: Optional[str] = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    base_url: str = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    temperature: float = Field(default_factory=lambda: float(os.getenv("OPENAI_TEMPERATURE", "0.1")))
    timeout_seconds: float = Field(default_factory=lambda: float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15.0")))

    @classmethod
    def from_env(cls) -> OpenAIConfig:
        """Loads configuration from environment variables."""
        return cls()

    def validate_config(self, require_key: bool = True) -> None:
        """Validates configuration parameters."""
        if require_key and (not self.api_key or self.api_key.strip() in ("", "your_openai_api_key_here")):
            raise LLMConfigError("OPENAI_API_KEY is not configured in environment.")
        if not self.base_url.startswith(("http://", "https://")):
            raise LLMConfigError(f"Invalid OPENAI_BASE_URL: '{self.base_url}'. Must start with http:// or https://")
        if not self.model or not self.model.strip():
            raise LLMConfigError("OPENAI_MODEL cannot be empty.")


# -----------------------------------------------------------------------------
# Concrete OpenAI LLM Client
# -----------------------------------------------------------------------------
class OpenAILLMClient(BaseLLMClient):
    """Official OpenAI-compatible Chat Completions LLM Client."""

    def __init__(
        self,
        config: Optional[OpenAIConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.config = config or OpenAIConfig.from_env()
        self._custom_transport = transport
        self._external_http_client = http_client

    def _get_client(self) -> httpx.AsyncClient:
        """Creates or returns an httpx.AsyncClient."""
        if self._external_http_client:
            return self._external_http_client
        if self._custom_transport:
            return httpx.AsyncClient(transport=self._custom_transport, timeout=self.config.timeout_seconds)
        return httpx.AsyncClient(timeout=self.config.timeout_seconds)

    async def generate(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        cancellation_token: Optional[CancellationToken] = None,
        request_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> LLMResponse:
        """Generates a text completion or structured tool call via OpenAI-compatible endpoint."""
        # Pre-call cancellation check
        if cancellation_token and cancellation_token.is_cancelled:
            return LLMResponse(content=None, finish_reason="cancelled")

        # Validate configuration before making the call
        self.config.validate_config(require_key=True)

        # Convert LLMMessage list to OpenAI messages format
        openai_messages: List[Dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                openai_messages.append({"role": "system", "content": msg.content or ""})
            elif msg.role == "user":
                openai_messages.append({"role": "user", "content": msg.content or ""})
            elif msg.role == "assistant":
                m_dict: Dict[str, Any] = {"role": "assistant"}
                if msg.content is not None:
                    m_dict["content"] = msg.content
                else:
                    m_dict["content"] = None
                if msg.tool_calls:
                    m_dict["tool_calls"] = [
                        {
                            "id": tc.id or f"call-{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else (tc.arguments or "{}"),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                openai_messages.append(m_dict)
            elif msg.role == "tool":
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "name": msg.name or "",
                    "content": msg.content or "",
                })

        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": openai_messages,
            "temperature": self.config.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"

        # Execute HTTP request
        client = self._get_client()
        should_close = (self._external_http_client is None)

        try:
            # Cancellation check right before network dispatch
            if cancellation_token and cancellation_token.is_cancelled:
                return LLMResponse(content=None, finish_reason="cancelled")

            response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                f"OpenAI request timed out after {self.config.timeout_seconds}s.",
                provider="openai",
            ) from e
        except (httpx.ConnectError, httpx.NetworkError, httpx.TransportError) as e:
            raise LLMConnectionError(
                f"Network error connecting to OpenAI endpoint at {self.config.base_url}.",
                provider="openai",
            ) from e
        finally:
            if should_close:
                await client.aclose()

        # Cancellation check right after response received
        if cancellation_token and cancellation_token.is_cancelled:
            return LLMResponse(content=None, finish_reason="cancelled")

        # Handle HTTP error status codes
        status = response.status_code
        if status in (401, 403):
            raise LLMAuthenticationError(
                f"OpenAI authentication failed with HTTP {status}. Please check your OPENAI_API_KEY.",
                provider="openai",
                status_code=status,
            )
        elif status == 400:
            err_text = response.text[:200]
            raise LLMBadRequestError(
                f"OpenAI bad request (HTTP 400): {err_text}",
                provider="openai",
                status_code=status,
            )
        elif status == 429:
            raise LLMRateLimitError(
                "OpenAI rate limit exceeded (HTTP 429).",
                provider="openai",
                status_code=status,
            )
        elif status >= 500:
            raise LLMServerError(
                f"OpenAI server error (HTTP {status}).",
                provider="openai",
                status_code=status,
            )
        elif not (200 <= status < 300):
            raise LLMError(
                f"OpenAI unexpected HTTP status {status}.",
                provider="openai",
                status_code=status,
            )

        try:
            data = response.json()
        except Exception as json_err:
            raise LLMError(f"Failed to parse OpenAI JSON response: {json_err}", provider="openai") from json_err

        choices = data.get("choices", [])
        if not choices:
            return LLMResponse(content="", finish_reason="stop")

        choice = choices[0]
        msg = choice.get("message", {})
        content = msg.get("content")
        finish_reason = choice.get("finish_reason", "stop")

        parsed_tool_calls: List[ToolCallRequest] = []
        raw_tool_calls = msg.get("tool_calls") or []
        for tc in raw_tool_calls:
            tc_id = tc.get("id") or f"call-{uuid.uuid4().hex[:8]}"
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            fn_args_raw = fn.get("arguments", "{}")
            if isinstance(fn_args_raw, str):
                try:
                    fn_args = json.loads(fn_args_raw)
                except Exception:
                    fn_args = {}
            elif isinstance(fn_args_raw, dict):
                fn_args = fn_args_raw
            else:
                fn_args = {}
            parsed_tool_calls.append(ToolCallRequest(id=tc_id, name=fn_name, arguments=fn_args))

        # Build slot patch if single tool call was made
        slot_patch: Optional[SlotPatch] = None
        if len(parsed_tool_calls) == 1:
            single_args = parsed_tool_calls[0].arguments
            set_slots = {k: v for k, v in single_args.items() if v is not None and k not in ("delay_ms",)}
            if set_slots:
                slot_patch = SlotPatch(set_slots=set_slots)

        return LLMResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            slot_patch=slot_patch,
            finish_reason=finish_reason,
        )


# -----------------------------------------------------------------------------
# LLM Client Factory
# -----------------------------------------------------------------------------
def create_llm_client(
    provider_name: Optional[str] = None,
    config: Optional[OpenAIConfig] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> BaseLLMClient:
    """Factory creating an LLM client instance according to configuration."""
    provider = (provider_name or os.getenv("VOICEFLOW_LLM_PROVIDER", "mock")).lower().strip()
    if provider == "mock":
        return MockLLMClient()
    elif provider in ("openai", "real"):
        cfg = config or OpenAIConfig.from_env()
        cfg.validate_config(require_key=True)
        return OpenAILLMClient(config=cfg, http_client=http_client, transport=transport)
    else:
        raise LLMConfigError(f"Unsupported LLM provider: '{provider}'. Supported: 'mock', 'openai'.")


