"""VoiceFlow Cancellation Token & In-Flight Task Registry."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Set


class CancellationToken:
    """Cooperative cancellation token passed across async tasks."""

    def __init__(self, request_id: str, version: int) -> None:
        self.request_id = request_id
        self.version = version
        self._event = asyncio.Event()
        self._is_cancelled = False

    @property
    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def cancel(self) -> None:
        """Triggers cancellation."""
        self._is_cancelled = True
        self._event.set()

    async def wait_cancelled(self) -> None:
        """Async wait until token is cancelled."""
        await self._event.wait()


class TaskRegistry:
    """Registers and coordinates cancellation of in-flight async tasks."""

    def __init__(self) -> None:
        self._tasks: Dict[str, Set[asyncio.Task]] = {}
        self._tokens: Dict[str, CancellationToken] = {}

    def register_token(self, request_id: str, version: int) -> CancellationToken:
        token = CancellationToken(request_id=request_id, version=version)
        self._tokens[request_id] = token
        return token

    def get_token(self, request_id: str) -> Optional[CancellationToken]:
        return self._tokens.get(request_id)

    def register_task(self, request_id: str, task: asyncio.Task) -> None:
        if request_id not in self._tasks:
            self._tasks[request_id] = set()
        self._tasks[request_id].add(task)

        # Remove from set when completed
        task.add_done_callback(
            lambda t: self._tasks[request_id].discard(t) if request_id in self._tasks else None
        )

    def cancel_request(self, request_id: str) -> int:
        """Cancels token and all in-flight tasks for a specific request ID."""
        cancelled_count = 0

        # Cancel token
        token = self._tokens.get(request_id)
        if token and not token.is_cancelled:
            token.cancel()

        # Cancel async tasks
        tasks = self._tasks.get(request_id, set())
        for task in list(tasks):
            if not task.done():
                task.cancel()
                cancelled_count += 1
        tasks.clear()

        return cancelled_count

    def cancel_all(self) -> int:
        """Cancels all registered in-flight tasks across all requests."""
        total = 0
        for req_id in list(self._tasks.keys()):
            total += self.cancel_request(req_id)
        return total

