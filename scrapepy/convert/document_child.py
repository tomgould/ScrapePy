"""Child process for document conversion: python -m scrapepy.convert.document_child

Reads one JSON request on stdin, {"target": "module:function", "args": [...],
"memory_bytes": N, "cpu_seconds": N, "result_path": "..."}, applies the
limits before importing anything heavy, runs the target and writes one JSON
reply to result_path: ["ok", value] or ["error", text]. Stdout is not the
result channel, so anything a library prints there cannot spoil the reply;
the parent reports it. It is a plain subprocess, not multiprocessing, so
the caller's own script is never imported again.
"""

import importlib
import json
import signal
import sys


def _limit(memory_bytes: int, cpu_seconds: int) -> None:
    try:
        import resource
        if memory_bytes:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if cpu_seconds:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    except (ImportError, ValueError, OSError):
        pass   # no resource module (Windows) or limits not allowed: the timeout still applies


def _exit_with_parent() -> None:
    """Stop when the parent is gone (killed with SIGKILL, say), rather than
    run on to the CPU limit: a thread checks every second whether this
    process has been handed to another parent."""
    import os
    import threading
    import time

    parent = os.getppid()

    def watch():
        while True:
            time.sleep(1)
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def main() -> int:
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # Ctrl+C is the parent's to handle
    _exit_with_parent()
    request = json.loads(sys.stdin.read())
    _limit(int(request.get("memory_bytes") or 0), int(request.get("cpu_seconds") or 0))
    try:
        module, _, name = request["target"].partition(":")
        result = ["ok", getattr(importlib.import_module(module), name)(*request.get("args", []))]
    except MemoryError:
        result = ["error", "too large to convert (memory limit reached)"]
    except BaseException as e:
        result = ["error", f"{type(e).__name__}: {e}"[:200]]
    try:
        reply = json.dumps(result)
    except MemoryError:
        reply = json.dumps(["error", "too large to convert (memory limit reached)"])
    except (TypeError, ValueError) as e:
        reply = json.dumps(["error", f"result is not JSON ({e})"[:200]])
    with open(request["result_path"], "w", encoding="utf-8") as f:
        f.write(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
