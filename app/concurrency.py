from __future__ import annotations

import asyncio


class ConcurrencyManager:
    """In-process gate that limits the number of scans running in parallel.

    This implementation uses a plain integer counter protected by an
    asyncio.Lock. The key design choice is that ``try_acquire`` performs a
    non-blocking check: if capacity is exhausted it returns ``False``
    immediately instead of suspending the caller. This satisfies the
    requirement that the 6th concurrent scan request must be rejected at once
    rather than silently queued.

    Why not asyncio.Semaphore?
    ``asyncio.Semaphore.acquire()`` suspends the calling coroutine until a slot
    becomes free, which is exactly the queuing behaviour we must avoid.
    A manual counter with a non-blocking check makes the rejection path
    explicit and easy to reason about.

    Single-process constraint:
    This gate lives entirely in memory. It works correctly only when the
    application runs as a single uvicorn worker (the default when you run
    ``uvicorn app.main:app`` without ``--workers``). Running multiple workers
    would give each process its own counter, allowing up to
    ``max_concurrent * N`` simultaneous scans across the deployment. For this
    local POC, always use a single worker.
    """

    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._max = max_concurrent
        self._active = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        """Attempt to claim one scan slot.

        Returns:
            True if a slot was available and has been claimed.
            False if all slots are currently occupied; the caller must reject
            the request immediately — no retry, no queuing.
        """
        async with self._lock:
            if self._active >= self._max:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        """Release one scan slot.

        Always call this in a ``finally`` block after ``try_acquire`` returns
        True so that a slot is never permanently lost due to an exception.
        Guards against underflow so a double-release does not corrupt the
        counter.
        """
        async with self._lock:
            if self._active > 0:
                self._active -= 1

    @property
    def active_count(self) -> int:
        """Current number of claimed slots (for debugging and tests only)."""
        return self._active
