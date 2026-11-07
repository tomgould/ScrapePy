"""Child process for document conversion: python -m scrapepy.convert.document_child

Reads one JSON request on stdin, {"target": "module:function", "args": [...],
"memory_bytes": N, "cpu_seconds": N}, applies the limits before importing
anything heavy, runs the target and writes one JSON reply on stdout:
["ok", value] or ["error", text]. It is a plain subprocess, not
multiprocessing, so the caller's own script is never imported again.
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


def main() -> int:
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # Ctrl+C is the parent's to handle
    request = json.loads(sys.stdin.read())
    _limit(int(request.get("memory_bytes") or 0), int(request.get("cpu_seconds") or 0))
    out = sys.stdout
    try:
        module, _, name = request["target"].partition(":")
        result = ["ok", getattr(importlib.import_module(module), name)(*request.get("args", []))]
    except MemoryError:
        result = ["error", "too large to convert (memory limit reached)"]
    except BaseException as e:
        result = ["error", f"{type(e).__name__}: {e}"[:200]]
    try:
        out.write(json.dumps(result))
    except MemoryError:
        out.write(json.dumps(["error", "too large to convert (memory limit reached)"]))
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
