"""Which signal stopped the run, so the exit code and summary can say so.

Ctrl+C (SIGINT) is handled by asyncio.run(), which cancels the main task.
SIGTERM is turned into the same cancellation by run_async(). Either way the
crawl saves its cache and writes run_summary.json before exiting.
"""

import asyncio
import signal

_received: int | None = None


def record(sig: int) -> None:
    global _received
    if _received is None:
        _received = sig


def received() -> int | None:
    return _received


def reset() -> None:
    global _received
    _received = None


def signal_name(sig: int | None) -> str:
    return signal.Signals(sig or signal.SIGINT).name


def exit_code(sig: int | None) -> int:
    """Shell convention: 128 + signal number (130 for SIGINT, 143 for SIGTERM)."""
    return 128 + int(sig or signal.SIGINT)


def run_async(coro):
    """asyncio.run() with SIGTERM handled like Ctrl+C: cancel the main task."""
    reset()

    async def main():
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()

        def on_sigterm():
            record(signal.SIGTERM)
            task.cancel()

        installed = False
        try:
            loop.add_signal_handler(signal.SIGTERM, on_sigterm)
            installed = True
        except (NotImplementedError, RuntimeError, ValueError, AttributeError):
            pass  # Not on the main thread, or a platform without signal handlers
        try:
            return await coro
        finally:
            if installed:
                loop.remove_signal_handler(signal.SIGTERM)

    return asyncio.run(main())
