"""Bounded job queue.

Submissions used to call ``asyncio.create_task`` and discard the handle, which
meant unbounded concurrency and a task the garbage collector was free to drop
mid-flight. Jobs now go through a queue served by a fixed number of long-lived
workers whose references are held for the life of the process.

Concurrency defaults to 1. On a small host the rendering step is the memory
ceiling, and two of them at once is what pushes the container over it.

The queue lives in this process. A job still waiting in it when the process
stops is settled as interrupted at the next startup, and can be retried.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[tuple[str, Coroutine[Any, Any, None]]] | None = None
_workers: list[asyncio.Task[None]] = []
_queued_ids: list[str] = []


class QueueFullError(RuntimeError):
    """Raised when the backlog is at its limit."""


def _get_queue() -> asyncio.Queue[tuple[str, Coroutine[Any, Any, None]]]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=settings.job_queue_max)
    return _queue


async def _worker(index: int) -> None:
    queue = _get_queue()
    while True:
        job_id, coro = await queue.get()
        try:
            logger.info("Worker %d starting job %s", index, job_id)
            await coro
        except asyncio.CancelledError:
            coro.close()
            raise
        except Exception:  # noqa: BLE001 - a bad job must not kill the worker
            logger.exception("Worker %d: job %s raised", index, job_id)
        finally:
            if job_id in _queued_ids:
                _queued_ids.remove(job_id)
            queue.task_done()


async def start_workers() -> None:
    """Spin up the worker pool. Idempotent."""
    if _workers:
        return
    for i in range(max(1, settings.job_concurrency)):
        _workers.append(asyncio.create_task(_worker(i), name=f"job-worker-{i}"))
    logger.info("Started %d job worker(s)", len(_workers))


async def stop_workers() -> None:
    """Cancel workers and drop anything still queued."""
    for task in _workers:
        task.cancel()
    for task in _workers:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
            pass
    _workers.clear()
    _queued_ids.clear()
    global _queue
    if _queue is not None:
        while not _queue.empty():
            _, coro = _queue.get_nowait()
            coro.close()
    _queue = None


async def enqueue(job_id: str, coro: Coroutine[Any, Any, None]) -> int:
    """Queue a job and return its position (0 means it starts immediately).

    Raises QueueFullError when the backlog is at its limit, so the caller can
    reject the submission rather than let it pile up.
    """
    await start_workers()
    queue = _get_queue()
    try:
        queue.put_nowait((job_id, coro))
    except asyncio.QueueFull:
        coro.close()
        raise QueueFullError("The job queue is full. Try again shortly.") from None
    _queued_ids.append(job_id)
    return max(0, queue.qsize() - len(_workers))


def queue_position(job_id: str) -> int | None:
    """How many jobs are ahead of this one, or None if it is not waiting."""
    if job_id not in _queued_ids:
        return None
    return _queued_ids.index(job_id)


def stats() -> dict[str, int]:
    queue = _queue
    return {
        "workers": len(_workers),
        "queued": queue.qsize() if queue is not None else 0,
        "capacity": settings.job_queue_max,
    }
