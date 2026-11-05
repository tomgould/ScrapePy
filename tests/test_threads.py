"""Tests for run_blocking(): daemon threads that never delay exit."""

import asyncio
import threading
import time

import pytest

from scrapepy.engine.threads import run_blocking


def test_returns_result():
    assert asyncio.run(run_blocking(lambda a, b: a + b, 2, 3)) == 5


def test_raises_exception():
    def boom():
        raise ValueError("nope")
    with pytest.raises(ValueError):
        asyncio.run(run_blocking(boom))


def test_runs_in_daemon_thread():
    seen = {}

    def probe():
        seen["daemon"] = threading.current_thread().daemon
    asyncio.run(run_blocking(probe))
    assert seen["daemon"] is True


def test_cancel_does_not_wait_for_thread():
    release = threading.Event()

    async def main():
        task = asyncio.create_task(run_blocking(release.wait, 30))
        await asyncio.sleep(0.05)
        task.cancel()
        start = time.perf_counter()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.perf_counter() - start

    start = time.perf_counter()
    elapsed = asyncio.run(main())
    total = time.perf_counter() - start
    release.set()
    assert elapsed < 0.2
    # asyncio.run() must not wait for the abandoned thread either
    assert total < 1.0
