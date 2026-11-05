"""Send a real SIGINT or SIGTERM to a crawl of a local test server (127.0.0.1 only).

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


@pytest.mark.parametrize("sig,code,name", [
    (signal.SIGINT, 130, "SIGINT"),
    (signal.SIGTERM, 143, "SIGTERM"),
])
def test_signal_writes_interrupted_summary_and_cache(server, tmp_path, sig, code, name):
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
        proc.send_signal(sig)
        rest, _ = proc.communicate(timeout=60)
        lines.append(rest)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    output = "".join(lines)
    assert proc.returncode == code, output[-2000:]
    summary = json.loads((site_dir / "run_summary.json").read_text())
    assert "stale" not in summary
    assert summary["status"] == "interrupted"
    assert summary["interrupted"] is True
    assert summary["exit_code"] == code
    assert summary["interrupted_by"] == name
    assert 0 < summary["counts"]["fetched"] < len(PAGES) + 1
    cache = json.loads((site_dir / ".scrapepy_cache.json").read_text())
    assert "/" in cache["pages"]
    assert not any(name.endswith(".tmp") for name in os.listdir(site_dir))


@pytest.fixture
def silent_server():
    """Accepts connections and never answers, like a hung robots.txt."""
    import socket
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    held = []
    stop = threading.Event()

    def accept():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
                held.append(conn)
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    stop.set()
    thread.join(timeout=2)
    for conn in held:
        conn.close()
    sock.close()


@pytest.mark.parametrize("sig,code,name", [
    (signal.SIGINT, 130, "SIGINT"),
    (signal.SIGTERM, 143, "SIGTERM"),
])
def test_signal_while_robots_txt_hangs_exits_promptly(silent_server, tmp_path, sig, code, name):
    out = tmp_path / "out"
    out.mkdir()
    proc = _crawl(silent_server + "/", out)
    try:
        for line in proc.stdout:
            if "Running site reconnaissance" in line:
                break
        time.sleep(1.0)  # robots.txt request is now waiting on the silent server
        sent = time.monotonic()
        proc.send_signal(sig)
        output, _ = proc.communicate(timeout=30)
        elapsed = time.monotonic() - sent
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == code, output[-2000:]
    assert elapsed < 5, elapsed
    summary_path = next(out.glob("ScrapedSites/*/run_summary.json"))
    summary = json.loads(summary_path.read_text())
    assert summary["status"] == "interrupted"
    assert summary["interrupted_by"] == name
    assert summary["exit_code"] == code


def test_robots_txt_timeout_disallows_everything(silent_server, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    proc = _crawl(silent_server + "/", out)
    output, _ = proc.communicate(timeout=60)
    assert proc.returncode == 1, output[-2000:]
    assert "robots.txt could not be read (unreachable)" in output
    summary = json.loads(next(out.glob("ScrapedSites/*/run_summary.json")).read_text())
    assert summary["status"] == "complete"
    assert summary["counts"]["fetched"] == 0
