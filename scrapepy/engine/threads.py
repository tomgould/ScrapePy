"""Run blocking calls (Scrapling's synchronous fetches) without holding up exit.

loop.run_in_executor() uses a ThreadPoolExecutor whose threads are joined
when asyncio.run() finishes and again when the interpreter exits, so after
Ctrl+C or SIGTERM the process would wait for any request still in flight,
up to its full timeout and retries. run_blocking() uses a daemon thread per
call instead: an abandoned request is simply dropped when the process exits.
"""

import asyncio
import threading


async def run_blocking(fn, *args):
    """Await fn(*args) running in a daemon thread. Cancelling abandons the thread."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def _deliver(setter, value):
        if not future.done():
            setter(value)

    def _worker():
        try:
            result = fn(*args)
        except BaseException as e:  # Hand every outcome back to the event loop
            try:
                loop.call_soon_threadsafe(_deliver, future.set_exception, e)
            except RuntimeError:
                pass  # The loop has closed: nobody is waiting any more
        else:
            try:
                loop.call_soon_threadsafe(_deliver, future.set_result, result)
            except RuntimeError:
                pass

    threading.Thread(target=_worker, name="scrapepy-fetch", daemon=True).start()
    return await future
