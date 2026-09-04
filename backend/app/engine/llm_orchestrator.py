"""VoiceFlow LLM Reasoning & Tool Orchestrator.

Orchestrates the multi-step conversational loop:
User Utterance
  → Conversation Context & Slot Assembly
  → Pre-flight Version Gate
  → LLM Step 1: Intent/Slot Extraction & Function Call
  → Strict Tool Registry & Argument Validation
  → Version-Gated Slot Patching
  → Asynchronous Tool Execution via ToolExecutor
  → Post-Tool Version Gate
  → LLM Step 2: Natural Language Response Synthesis
  → Final Version Gate & Atomic Turn Completion
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from app.core.cancellation import CancellationToken
from app.core.session import Session
from app.core.versioning import RequestVersionGate
from app.engine.llm_provider import BaseLLMClient, MockLLMClient
from app.models import (
    EventLevel,
    LLMMessage,
    LLMResponse,
    SlotPatch,
    ToolCallRequest,
    ToolStatus,
    ToolTask,
    TurnExecutionResult,
    VoiceEventType,
)
from app.tools.registry import ToolRegistry, default_tool_registry

logger = logging.getLogger(__name__)


class LLMOrchestrator:
    """Coordinates intent extraction, tool registry checks, async execution, and response synthesis."""

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self.llm_client = llm_client or MockLLMClient()
        self.tool_registry = tool_registry or default_tool_registry

    def _build_context_messages(
        self,
        session: Session,
        prompt: str,
        current_slots: Dict[str, Any],
    ) -> List[LLMMessage]:
        """Assembles system context, past turn history, and latest user prompt."""
        messages: List[LLMMessage] = []

        # 1. System instruction containing current slot state
        slot_json = json.dumps(current_slots)
        system_content = (
            "You are VoiceFlow, an interruption-safe voice assistant for travel search. "
            f"CURRENT_SLOTS: {slot_json}"
        )
        messages.append(LLMMessage(role="system", content=system_content))

        # 2. Conversation history
        for msg in session.state_mgr.state.history:
            if not msg.is_invalidated:
                messages.append(
                    LLMMessage(
                        role=msg.role,
                        content=msg.content,
                        tool_calls=[ToolCallRequest(name=msg.tool_call.get("name", ""), arguments=msg.tool_call.get("args", {}))]
                        if msg.tool_call else None,
                    )
                )

        # 3. Current user prompt
        # Note: In session.create_request(), the prompt is already added to history.
        # If the last message in history is the current prompt, we don't duplicate it.
        if not (messages and messages[-1].role == "user" and messages[-1].content == prompt):
            messages.append(LLMMessage(role="user", content=prompt))

        return messages

    async def process_turn(
        self,
        session: Session,
        request_id: str,
        version: int,
        prompt: str,
        delay_tool_ms: Optional[int] = None,
        trigger_rime: bool = False,
    ) -> TurnExecutionResult:
        """Executes the full turn lifecycle with strict version validation and cancellation checkpoints."""
        token = session.task_registry.get_token(request_id)

        # -------------------------------------------------------------
        # CHECKPOINT 1: Pre-flight Version Gate Check
        # -------------------------------------------------------------
        if token and token.is_cancelled:
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                is_stale=True,
                error="Request was cancelled before LLM processing began.",
            )

        if session.active_version != version or session.active_request_id != request_id:
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                is_stale=True,
                error=f"Version mismatch: request #{version} is no longer active (current: #{session.active_version}).",
            )

        # -------------------------------------------------------------
        # STEP 1: Context Assembly & LLM Step 1 Generation
        # -------------------------------------------------------------
        current_slots = dict(session.state_mgr.state.slots)
        messages = self._build_context_messages(session, prompt, current_slots)
        schemas = self.tool_registry.get_schemas()

        session.event_logger.log_event(
            event_type=VoiceEventType.LLM_STEP1_STARTED,
            session_id=session.session_id,
            request_id=request_id,
            version=version,
            message=f"LLM Step 1 started for Request #{version} ({request_id}): intent & slot extraction",
            level=EventLevel.INFO,
            payload={"prompt": prompt, "current_slots": current_slots},
        )

        llm_resp: LLMResponse = await self.llm_client.generate(
            messages=messages,
            tools=schemas,
            cancellation_token=token,
            request_id=request_id,
            version=version,
        )

        # -------------------------------------------------------------
        # CHECKPOINT 2: Post-Step-1 Cancellation / Version Check
        # -------------------------------------------------------------
        if (token and token.is_cancelled) or session.active_version != version:
            session.event_logger.log_event(
                event_type=VoiceEventType.LLM_STEP1_CANCELLED,
                session_id=session.session_id,
                request_id=request_id,
                version=version,
                message=f"LLM Step 1 cancelled / obsolete for Request #{version}",
                level=EventLevel.WARN,
            )
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                is_stale=True,
                error="LLM Step 1 cancelled during generation.",
            )

        session.event_logger.log_event(
            event_type=VoiceEventType.LLM_STEP1_COMPLETED,
            session_id=session.session_id,
            request_id=request_id,
            version=version,
            message=f"LLM Step 1 completed for Request #{version}",
            level=EventLevel.INFO,
            payload={"finish_reason": llm_resp.finish_reason, "has_tool_calls": len(llm_resp.tool_calls) > 0},
        )

        # Apply slot patch if returned by LLM
        if llm_resp.slot_patch:
            try:
                session.state_mgr.apply_slot_patch(
                    patch=llm_resp.slot_patch,
                    version=version,
                    request_id=request_id,
                )
                session.event_logger.log_event(
                    event_type=VoiceEventType.SLOTS_UPDATED,
                    session_id=session.session_id,
                    request_id=request_id,
                    version=version,
                    message=f"Slots updated: {session.state_mgr.state.slots}",
                    level=EventLevel.INFO,
                    payload={"slots": session.state_mgr.state.slots},
                )
            except Exception as e:
                logger.warning("Failed to apply slot patch: %s", e)

        # -------------------------------------------------------------
        # BRANCH A: Direct Text Response (Missing Slots / Chit-chat)
        # -------------------------------------------------------------
        if not llm_resp.tool_calls:
            direct_text = llm_resp.content or "How can I help you?"
            committed = session.complete_turn(
                request_id=request_id,
                version=version,
                assistant_response=direct_text,
                trigger_rime=trigger_rime,
            )
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=committed,
                assistant_response=direct_text if committed else None,
                is_stale=(not committed),
            )

        # -------------------------------------------------------------
        # BRANCH B: Tool Call Requested
        # -------------------------------------------------------------
        tool_call = llm_resp.tool_calls[0]
        tool_name = tool_call.name
        raw_args = dict(tool_call.arguments)

        # 1. Strict ToolRegistry Check
        if not self.tool_registry.is_permitted(tool_name):
            session.event_logger.log_event(
                event_type=VoiceEventType.TOOL_UNKNOWN_OR_FORBIDDEN,
                session_id=session.session_id,
                request_id=request_id,
                version=version,
                message=f"Tool '{tool_name}' is not registered or not permitted.",
                level=EventLevel.ERROR,
                payload={"tool_name": tool_name},
            )
            clarification = f"I am sorry, but the tool '{tool_name}' is not available."
            committed = session.complete_turn(
                request_id=request_id,
                version=version,
                assistant_response=clarification,
                trigger_rime=trigger_rime,
            )
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                assistant_response=clarification if committed else None,
                error=f"Tool '{tool_name}' is forbidden or unknown.",
            )

        # 2. Argument Validation with Pydantic Model
        try:
            validated_model = self.tool_registry.validate_arguments(tool_name, raw_args)
            validated_args = validated_model.model_dump()
        except Exception as val_err:
            session.event_logger.log_event(
                event_type=VoiceEventType.TOOL_ARGS_INVALID,
                session_id=session.session_id,
                request_id=request_id,
                version=version,
                message=f"Invalid arguments for tool '{tool_name}': {val_err}",
                level=EventLevel.WARN,
                payload={"tool_name": tool_name, "raw_args": raw_args, "error": str(val_err)},
            )
            err_msg = f"I could not process the search details. Please specify valid travel parameters."
            committed = session.complete_turn(
                request_id=request_id,
                version=version,
                assistant_response=err_msg,
                trigger_rime=trigger_rime,
            )
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                assistant_response=err_msg if committed else None,
                error=f"Argument validation error: {val_err}",
            )

        # -------------------------------------------------------------
        # CHECKPOINT 3: Pre-Tool Execution Version Gate
        # -------------------------------------------------------------
        if (token and token.is_cancelled) or session.active_version != version:
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                is_stale=True,
                error="Cancelled before tool dispatch.",
            )

        # 3. Asynchronous Tool Execution via Session & ToolExecutor
        tool_delay_sec = (
            (delay_tool_ms / 1000.0)
            if delay_tool_ms is not None
            else (validated_args.get("delay_ms", 3000) / 1000.0)
        )

        tool_task = await session.run_tool(
            request_id=request_id,
            version=version,
            tool_name=tool_name,
            args=validated_args,
            delay_seconds=tool_delay_sec,
        )

        # -------------------------------------------------------------
        # CHECKPOINT 4: Post-Tool Version Gate Check
        # -------------------------------------------------------------
        if tool_task.status != ToolStatus.COMPLETED_VALID:
            # Tool result was discarded or cancelled by version gate!
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                tool_task=tool_task,
                is_stale=True,
                error=f"Tool result discarded or cancelled: {tool_task.discard_reason or tool_task.status}",
            )

        if (token and token.is_cancelled) or session.active_version != version:
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                tool_task=tool_task,
                is_stale=True,
                error="Request became obsolete after tool execution completed.",
            )

        # -------------------------------------------------------------
        # STEP 2: Response Synthesis from Tool Result
        # -------------------------------------------------------------
        session.event_logger.log_event(
            event_type=VoiceEventType.LLM_STEP2_STARTED,
            session_id=session.session_id,
            request_id=request_id,
            version=version,
            message=f"LLM Step 2 started for Request #{version}: response synthesis",
            level=EventLevel.INFO,
        )

        # Append tool output to context messages
        synthesis_messages = list(messages)
        synthesis_messages.append(
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCallRequest(id=tool_task.task_id, name=tool_name, arguments=validated_args)],
            )
        )
        synthesis_messages.append(
            LLMMessage(
                role="tool",
                name=tool_name,
                tool_call_id=tool_task.task_id,
                content=json.dumps(tool_task.result if isinstance(tool_task.result, dict) else (tool_task.result.model_dump() if hasattr(tool_task.result, "model_dump") else tool_task.result)),
            )
        )

        synthesis_resp = await self.llm_client.generate(
            messages=synthesis_messages,
            cancellation_token=token,
            request_id=request_id,
            version=version,
        )

        # -------------------------------------------------------------
        # CHECKPOINT 5: Post-Step-2 Cancellation / Version Check
        # -------------------------------------------------------------
        if (token and token.is_cancelled) or session.active_version != version:
            session.event_logger.log_event(
                event_type=VoiceEventType.LLM_STEP2_CANCELLED,
                session_id=session.session_id,
                request_id=request_id,
                version=version,
                message=f"LLM Step 2 cancelled / obsolete for Request #{version}",
                level=EventLevel.WARN,
            )
            return TurnExecutionResult(
                request_id=request_id,
                version=version,
                success=False,
                tool_task=tool_task,
                is_stale=True,
                error="LLM Step 2 cancelled during response synthesis.",
            )

        session.event_logger.log_event(
            event_type=VoiceEventType.LLM_STEP2_COMPLETED,
            session_id=session.session_id,
            request_id=request_id,
            version=version,
            message=f"LLM Step 2 completed for Request #{version}",
            level=EventLevel.INFO,
        )

        # -------------------------------------------------------------
        # FINAL GATE CHECK & ATOMIC TURN COMMIT
        # -------------------------------------------------------------
        final_answer = synthesis_resp.content or "Search complete."
        committed = session.complete_turn(
            request_id=request_id,
            version=version,
            assistant_response=final_answer,
            tool_call={"name": tool_name, "args": validated_args},
            trigger_rime=trigger_rime,
        )

        return TurnExecutionResult(
            request_id=request_id,
            version=version,
            success=committed,
            assistant_response=final_answer if committed else None,
            tool_task=tool_task,
            is_stale=(not committed),
        )

