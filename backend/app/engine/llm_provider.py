"""VoiceFlow LLM Provider Abstraction and Deterministic Mock Client.

Provides:
- BaseLLMClient: Provider-agnostic abstract interface.
- MockLLMClient: Deterministic mock implementation supporting slot extraction,
  constraint addition, replacement, clearing, tool-calling, and response synthesis.
"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from app.core.cancellation import CancellationToken
from app.models import (
    LLMMessage,
    LLMResponse,
    SlotPatch,
    ToolCallRequest,
)


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

