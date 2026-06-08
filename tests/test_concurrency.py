from __future__ import annotations

import pytest

from app.concurrency import ConcurrencyManager


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

def test_constructor_rejects_zero():
    with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
        ConcurrencyManager(0)


def test_constructor_rejects_negative():
    with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
        ConcurrencyManager(-1)


def test_constructor_accepts_one():
    mgr = ConcurrencyManager(1)
    assert mgr.active_count == 0


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------

async def test_five_acquisitions_all_succeed():
    mgr = ConcurrencyManager(5)
    results = [await mgr.try_acquire() for _ in range(5)]
    assert all(results)
    assert mgr.active_count == 5


async def test_sixth_acquisition_is_rejected():
    mgr = ConcurrencyManager(5)
    for _ in range(5):
        await mgr.try_acquire()

    rejected = await mgr.try_acquire()
    assert rejected is False
    assert mgr.active_count == 5


async def test_active_count_starts_at_zero():
    mgr = ConcurrencyManager(3)
    assert mgr.active_count == 0


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

async def test_release_decrements_active_count():
    mgr = ConcurrencyManager(5)
    await mgr.try_acquire()
    await mgr.try_acquire()
    assert mgr.active_count == 2

    await mgr.release()
    assert mgr.active_count == 1


async def test_release_allows_new_acquisition_after_full():
    mgr = ConcurrencyManager(2)
    await mgr.try_acquire()
    await mgr.try_acquire()

    assert await mgr.try_acquire() is False  # full

    await mgr.release()
    assert await mgr.try_acquire() is True   # slot freed


async def test_release_does_not_go_below_zero():
    mgr = ConcurrencyManager(5)
    await mgr.release()  # release without any prior acquisition
    assert mgr.active_count == 0


async def test_double_release_does_not_corrupt_counter():
    mgr = ConcurrencyManager(5)
    await mgr.try_acquire()
    await mgr.release()
    await mgr.release()  # second release should be a no-op
    assert mgr.active_count == 0


# ---------------------------------------------------------------------------
# max_concurrent = 1 edge case
# ---------------------------------------------------------------------------

async def test_single_slot_manager():
    mgr = ConcurrencyManager(1)
    assert await mgr.try_acquire() is True
    assert await mgr.try_acquire() is False
    await mgr.release()
    assert await mgr.try_acquire() is True
