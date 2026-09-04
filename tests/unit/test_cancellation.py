"""Unit tests for CancellationToken and TaskRegistry."""

import asyncio
import pytest
from app.core.cancellation import CancellationToken, TaskRegistry


@pytest.mark.asyncio
async def test_cancellation_token():
    token = CancellationToken(request_id="req-1", version=1)
    assert token.is_cancelled is False

    token.cancel()
    assert token.is_cancelled is True


@pytest.mark.asyncio
async def test_task_registry_cancellation():
    registry = TaskRegistry()
    token = registry.register_token(request_id="req-10", version=10)

    started_event = asyncio.Event()
    cancelled_flag = False

    async def dummy_worker():
        nonlocal cancelled_flag
        started_event.set()
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            cancelled_flag = True
            raise

    task = asyncio.create_task(dummy_worker())
    registry.register_task(request_id="req-10", task=task)

    # Ensure worker has entered its execution loop
    await started_event.wait()

    cancelled_count = registry.cancel_request(request_id="req-10")
    assert cancelled_count == 1
    assert token.is_cancelled is True

    # Await task cancellation handling
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert cancelled_flag is True
    assert task.cancelled() is True

