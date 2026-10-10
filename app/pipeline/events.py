"""In-process change notification for job progress.

The SSE stream used to poll the database once a second per open connection,
each poll costing a handful of queries against a pool shared with the job
worker. Every write that changes a job now bumps a version here, and a stream
sleeps until its job's version moves - so an idle stream costs nothing and a
busy one wakes exactly when there is something new to send.

This only sees writes made by this process. That is the deployment model (one
process runs both the API and the job worker), and the stream still re-reads
the database on a slow timer, so a write from anywhere else is picked up late
rather than missed.
"""

import asyncio
from collections import OrderedDict

MAX_TRACKED_JOBS = 5000


class JobEvents:
    def __init__(self) -> None:
        self._versions: OrderedDict[str, int] = OrderedDict()
        # Keyed with the loop that created the event: an asyncio.Event binds to
        # the first loop that waits on it, and reusing one across loops raises.
        self._waiters: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Event]] = {}

    def version(self, job_id: str) -> int:
        return self._versions.get(job_id, 0)

    def notify(self, job_id: str) -> None:
        """Record that ``job_id`` changed and wake everything waiting on it."""
        self._versions[job_id] = self._versions.get(job_id, 0) + 1
        self._versions.move_to_end(job_id)
        while len(self._versions) > MAX_TRACKED_JOBS:
            stale, _ = self._versions.popitem(last=False)
            self._waiters.pop(stale, None)
        waiter = self._waiters.pop(job_id, None)
        if waiter is not None:
            waiter[1].set()

    def notify_all(self) -> None:
        """Wake every waiter, e.g. after a bulk update that touched many jobs."""
        for job_id in list(self._waiters):
            self.notify(job_id)

    async def wait(self, job_id: str, seen: int, timeout: float) -> int:
        """Block until ``job_id`` moves past version ``seen`` or ``timeout`` passes.

        Returns the current version either way, so the caller can tell a change
        from a timeout without a second lookup.
        """
        if self.version(job_id) != seen:
            return self.version(job_id)
        loop = asyncio.get_running_loop()
        waiter = self._waiters.get(job_id)
        if waiter is None or waiter[0] is not loop:
            waiter = (loop, asyncio.Event())
            self._waiters[job_id] = waiter
        event = waiter[1]
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            pass
        return self.version(job_id)


job_events = JobEvents()
"""The process-wide instance. Written by the job store, read by the SSE stream."""
