"""Send a real SIGINT to a crawl of a local test server (127.0.0.1 only).

This is the one test that uses sockets. The server runs in this process and
the crawl runs as a subprocess, as a user would run it.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORDS = "Plenty of readable words appear here so the page passes the length check. " * 6
PAGES = [f"/p{i}" for i in range(40)]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals")


class Handler(BaseHTTPRequestHandler):
    log: list[str] = []

    def do_GET(self):
        Handler.log.append(self.path)
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/":
            body = "".join(f'<a href="{p}">{p}</a> ' for p in PAGES)
        elif path in PAGES:
            time.sleep(0.3)
            body = f"<h1>{path}</h1><p>{WORDS}</p>"
        else:
            self.send_response(404)
            self.end_headers()
            return
        html = f"<html><head><title>{path}</title></head><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.log = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _crawl(url, out_dir):
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=str(REPO_ROOT))
    return subprocess.Popen(
        [sys.executable, "-m", "scrapepy", url, "-o", str(out_dir), "--wait", "0",
         "-c", "1", "--no-include-assets"],
        cwd=str(out_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def test_sigint_writes_interrupted_summary_and_cache(server, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    site_dir = out / "ScrapedSites" / "127_0_0_1:{}".format(server.rsplit(":", 1)[1])
    # A stale summary from an earlier run must not survive
    site_dir.mkdir(parents=True)
    (site_dir / "run_summary.json").write_text(json.dumps({"status": "complete", "stale": True}))

    proc = _crawl(server + "/", out)
    lines: list[str] = []
    deadline = time.monotonic() + 60
    try:
        for line in proc.stdout:
            lines.append(line)
            if line.startswith("Fetching page:") and "/p3" in line:
                break
            if time.monotonic() > deadline:
                break
        proc.send_signal(signal.SIGINT)
        rest, _ = proc.communicate(timeout=60)
        lines.append(rest)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    output = "".join(lines)
    assert proc.returncode == 130, output[-2000:]
    summary = json.loads((site_dir / "run_summary.json").read_text())
    assert "stale" not in summary
    assert summary["status"] == "interrupted"
    assert summary["interrupted"] is True
    assert summary["exit_code"] == 130
    assert 0 < summary["counts"]["fetched"] < len(PAGES) + 1
    cache = json.loads((site_dir / ".scrapepy_cache.json").read_text())
    assert "/" in cache["pages"]
    assert not any(name.endswith(".tmp") for name in os.listdir(site_dir))
