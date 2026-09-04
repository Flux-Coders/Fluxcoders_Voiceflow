"""VoiceFlow Asynchronous Tool Execution Module.

Implements version-gated async tool invocation with realistic mock train search.
Emits fine-grained events:
- TOOL_STARTED
- TOOL_CANCEL_REQUESTED
- TOOL_CANCELLED
- TOOL_COMPLETED
- TOOL_RESULT_ACCEPTED
- STALE_RESULT_DISCARDED

Guarantees: Rule 5 & Rule 6 (Stale results validation and rejection).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional
from app.models import EventLevel, ToolStatus, ToolTask, VoiceEventType
from app.core.cancellation import CancellationToken
from app.core.event_logger import VoiceEventLogger
from app.core.metrics import MetricsCollector
from app.core.state import ConversationStateManager
from app.core.versioning import RequestVersionGate
from app.tools.train_search import TrainSearchParams, search_trains_sync


class ToolExecutor:
    """Executes tools asynchronously with strict version validation and fine-grained event emissions."""

    def __init__(
        self,
        event_logger: VoiceEventLogger,
        metrics_collector: MetricsCollector,
    ) -> None:
        self.event_logger = event_logger
        self.metrics_collector = metrics_collector

    async def execute_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        request_id: str,
        version: int,
        session_id: str,
        state_mgr: ConversationStateManager,
        delay_seconds: float = 3.0,
        cancellation_token: Optional[CancellationToken] = None,
        force_non_cooperative: bool = False,
    ) -> ToolTask:
        """Runs a tool with artificial delay and enforces version-gate checking before return."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        started_at = time.time()
        t_perf_start = time.perf_counter()

        task = ToolTask(
            task_id=task_id,
            tool_name=tool_name,
            args=args,
            request_id=request_id,
            conversation_version=version,
            session_id=session_id,
            status=ToolStatus.EXECUTING,
            started_at=started_at,
        )

        # 1. EMIT TOOL_STARTED & TOOL_DISPATCHED
        self.event_logger.log_event(
            event_type=VoiceEventType.TOOL_STARTED,
            session_id=session_id,
            request_id=request_id,
            version=version,
            message=f"Tool '{tool_name}' started (Task ID: {task_id}, Version: v{version}, Delay: {delay_seconds}s)",
            level=EventLevel.INFO,
            payload={"task_id": task_id, "args": args, "delay_seconds": delay_seconds},
        )
        self.event_logger.log_event(
            event_type=VoiceEventType.TOOL_DISPATCHED,
            session_id=session_id,
            request_id=request_id,
            version=version,
            message=f"Dispatched tool {tool_name} under request {request_id}",
            level=EventLevel.INFO,
            payload={"task_id": task_id, "args": args},
        )

        try:
            # 2. Simulate network / execution delay with cooperative cancellation awareness
            if delay_seconds > 0:
                step_interval = 0.05
                elapsed = 0.0
                while elapsed < delay_seconds:
                    if not force_non_cooperative and cancellation_token and cancellation_token.is_cancelled:
                        # EMIT TOOL_CANCEL_REQUESTED & TOOL_CANCELLED
                        self.event_logger.log_event(
                            event_type=VoiceEventType.TOOL_CANCEL_REQUESTED,
                            session_id=session_id,
                            request_id=request_id,
                            version=version,
                            message=f"Tool cancel requested for Task {task_id} (v{version})",
                            level=EventLevel.WARN,
                            payload={"task_id": task_id},
                        )
                        self.event_logger.log_event(
                            event_type=VoiceEventType.TOOL_CANCELLED,
                            session_id=session_id,
                            request_id=request_id,
                            version=version,
                            message=f"Tool Task {task_id} cancelled during sleep interval.",
                            level=EventLevel.WARN,
                            payload={"task_id": task_id},
                        )
                        task.status = ToolStatus.CANCELLED
                        task.discard_reason = "Cancelled in-flight due to user interruption"
                        task.completed_at = time.time()
                        return task

                    await asyncio.sleep(min(step_interval, delay_seconds - elapsed))
                    elapsed += step_interval

            # Check for cancellation before executing actual logic
            if not force_non_cooperative and cancellation_token and cancellation_token.is_cancelled:
                self.event_logger.log_event(
                    event_type=VoiceEventType.TOOL_CANCEL_REQUESTED,
                    session_id=session_id,
                    request_id=request_id,
                    version=version,
                    message=f"Tool cancel requested for Task {task_id} before computation",
                    level=EventLevel.WARN,
                )
                self.event_logger.log_event(
                    event_type=VoiceEventType.TOOL_CANCELLED,
                    session_id=session_id,
                    request_id=request_id,
                    version=version,
                    message=f"Tool Task {task_id} cancelled before computation.",
                    level=EventLevel.WARN,
                )
                task.status = ToolStatus.CANCELLED
                task.discard_reason = "Cancelled in-flight due to user interruption"
                task.completed_at = time.time()
                return task

            # 3. Execute Tool logic
            if tool_name in ("train_search", "search_trains"):
                params = TrainSearchParams(
                    source=args.get("source") or args.get("origin", "Nagpur"),
                    destination=args.get("destination", "Mumbai"),
                    date=args.get("date", "tomorrow"),
                    time_constraint=args.get("time_constraint") or args.get("min_departure"),
                    class_constraint=args.get("class_constraint"),
                    delay_ms=0,
                )
                search_res = search_trains_sync(params)
                raw_result = search_res.model_dump()
            else:
                raw_result = {"status": "ok", "custom_args": args}

            t_perf_end = time.perf_counter()
            task.completed_at = time.time()
            task.duration_ms = round((t_perf_end - t_perf_start) * 1000.0, 2)

            # 4. EMIT TOOL_COMPLETED
            self.event_logger.log_event(
                event_type=VoiceEventType.TOOL_COMPLETED,
                session_id=session_id,
                request_id=request_id,
                version=version,
                message=f"Tool {tool_name} completed computation in {task.duration_ms}ms (Task: {task_id})",
                level=EventLevel.INFO,
                payload={"task_id": task_id, "duration_ms": task.duration_ms},
            )

            # 5. CRITICAL VERSION GATE CHECK (Rule 5 & Rule 6)
            is_valid, discard_reason = RequestVersionGate.validate_tool_result_active(
                tool_version=version,
                tool_request_id=request_id,
                state=state_mgr.state,
            )

            if not is_valid:
                # STALE RESULT DISCARDED!
                task.status = ToolStatus.COMPLETED_STALE_DISCARDED
                task.discard_reason = discard_reason
                task.result = None  # Result payload stripped to prevent pollution

                self.metrics_collector.record_stale_discard()
                self.event_logger.log_event(
                    event_type=VoiceEventType.STALE_RESULT_DISCARDED,
                    session_id=session_id,
                    request_id=request_id,
                    version=version,
                    message=f"STALE RESULT DISCARDED: {discard_reason}",
                    level=EventLevel.ERROR,
                    payload={"task_id": task_id, "discard_reason": discard_reason},
                )
                self.event_logger.log_event(
                    event_type=VoiceEventType.TOOL_RETURN_STALE_DISCARDED,
                    session_id=session_id,
                    request_id=request_id,
                    version=version,
                    message=f"Tool return dropped by version gate: {discard_reason}",
                    level=EventLevel.WARN,
                    payload={"task_id": task_id},
                )
                return task

            # 6. VALID RESULT ACCEPTED
            task.status = ToolStatus.COMPLETED_VALID
            task.result = raw_result
            self.metrics_collector.record_valid_accept()

            self.event_logger.log_event(
                event_type=VoiceEventType.TOOL_RESULT_ACCEPTED,
                session_id=session_id,
                request_id=request_id,
                version=version,
                message=f"Tool result accepted for active version v{version} (Task: {task_id})",
                level=EventLevel.SUCCESS,
                payload={"task_id": task_id, "result": raw_result},
            )
            self.event_logger.log_event(
                event_type=VoiceEventType.TOOL_RETURN_VALID,
                session_id=session_id,
                request_id=request_id,
                version=version,
                message=f"Tool {tool_name} returned valid results for v{version}",
                level=EventLevel.SUCCESS,
                payload={"task_id": task_id},
            )
            return task

        except asyncio.CancelledError:
            self.event_logger.log_event(
                event_type=VoiceEventType.TOOL_CANCELLED,
                session_id=session_id,
                request_id=request_id,
                version=version,
                message=f"Tool Task {task_id} coroutine cancelled by asyncio Task handle.",
                level=EventLevel.WARN,
                payload={"task_id": task_id},
            )
            task.status = ToolStatus.CANCELLED
            task.completed_at = time.time()
            task.discard_reason = "Asyncio Task cancelled by registry"
            return task
