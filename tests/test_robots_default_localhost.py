"""The robots.txt default, on a fake site served from 127.0.0.1.

By default no Disallow rule and no Crawl-delay is applied, and robots.txt is
requested only for its Sitemap: lines. --obey-robots applies both, and
--ignore-robots is accepted and does nothing. Each test reads the server's
request log with its timestamps, so it states exactly what was requested and
when. Every page here is invented.
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scrapepy.cli import parse_args
from scrapepy.scraper import run_scraper

WORDS = " ".join(["Plain readable words make this page long enough to convert."] * 8)
CRAWL_DELAY = 0.5
FEED_LINK = '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
RSS = ('<?xml version="1.0"?><rss version="2.0"><channel><title>Little Cooks</title>'
       "<link>/</link><description>Invented</description></channel></rss>")


def page(title, links=(), head=""):
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    return (f"<html><head><title>{title}</title>{head}</head><body><nav>"
            + "".join(f'<a href="{h}">{h}</a> ' for h in links)
            + f"</nav><article><h1>{title}</h1><p>{WORDS}</p>{anchors}</article></body></html>")


def routes_for(base):
    robots = ("User-agent: *\n"
              "Disallow: /private/\n"
              f"Crawl-delay: {CRAWL_DELAY}\n"
              f"Sitemap: {base}/robots-sitemap.xml\n")
    sitemap = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               f"<url><loc>{base}/about</loc><lastmod>2026-01-02</lastmod></url></urlset>")
    return {
        "/robots.txt": ("text/plain", robots),
        "/robots-sitemap.xml": ("application/xml", sitemap),
        "/feed.xml": ("application/rss+xml", RSS),
        "/": ("text/html", page("Home", ["/about", "/private/secret"], head=FEED_LINK)),
        "/about": ("text/html", page("About us")),
        "/private/secret": ("text/html", page("Private notes")),
    }


class Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    log: list = []      # (path, time.monotonic())

    def do_GET(self):
        Handler.log.append((self.path, time.monotonic()))
        entry = Handler.routes.get(self.path.rstrip("/") or "/")
        if entry is None:
            self.send_response(404)
            self.end_headers()
            return
        data = entry[1].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", entry[0] + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    Handler.routes = routes_for(base)
    Handler.log = []
    yield base
    httpd.shutdown()
    httpd.server_close()


def crawl(base, out, *flags, start="/"):
    """Run the CLI's own argument parsing, then the crawl. Returns the request log and results."""
    Handler.log = []
    config = parse_args([base + start, "-o", str(out), "--wait", "0", "-c", "1",
                         "--markdown-only", *flags])
    code = asyncio.run(run_scraper(config))
    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    files = {}
    for md in sorted(Path(config.markdown_dir).rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        url = next(line[5:] for line in text.splitlines() if line.startswith("url: "))
        files[url[len(base):]] = text
    log = list(Handler.log)
    paths = [p for p, _ in log]
    times = sorted(t for _, t in log)
    gaps = [b - a for a, b in zip(times, times[1:])]
    return config, code, summary, paths, files, gaps


def test_default_fetches_a_disallowed_page_and_ignores_crawl_delay(site, tmp_path):
    config, code, summary, paths, files, gaps = crawl(site, tmp_path)
    assert code == 0
    assert "/private/secret" in paths
    assert "/private/secret" in files
    assert summary["blocked_by_robots"] == []
    assert summary["counts"]["blocked_by_robots"] == 0
    assert summary["options"]["obey_robots"] is False
    assert gaps and max(gaps) < CRAWL_DELAY * 0.8, gaps


def test_obey_robots_skips_the_disallowed_page_and_honours_crawl_delay(site, tmp_path):
    config, code, summary, paths, files, gaps = crawl(site, tmp_path / "obey", "--obey-robots")
    assert code == 0
    assert "/private/secret" not in paths
    assert "/private/secret" not in files
    assert summary["blocked_by_robots"] == [site + "/private/secret"]
    assert summary["counts"]["blocked_by_robots"] == 1
    assert summary["options"]["obey_robots"] is True
    assert gaps and min(gaps) >= CRAWL_DELAY * 0.8, gaps
    # It reads the sitemaps robots.txt names, as the default does
    assert "/robots-sitemap.xml" in paths
    outline = json.loads((Path(config.output_dir) / "site_outline.json").read_text())
    assert outline["robots_sitemaps"] == [site + "/robots-sitemap.xml"]
    # Against the default, the only difference is the page robots.txt disallows
    _, _, _, default_paths, default_files, _ = crawl(site, tmp_path / "default")
    assert sorted(default_paths) == sorted(paths + ["/private/secret"])
    assert set(default_files) == set(files) | {"/private/secret"}


def test_ignore_robots_alone_behaves_like_the_default(site, tmp_path):
    _, code_a, summary_a, paths_a, files_a, _ = crawl(site, tmp_path / "default")
    _, code_b, summary_b, paths_b, files_b, _ = crawl(site, tmp_path / "ignore", "--ignore-robots")
    assert code_a == code_b == 0
    # The sitemap and the homepage are fetched together, so their order can vary
    assert paths_a[0] == paths_b[0] == "/robots.txt"
    assert sorted(paths_a) == sorted(paths_b)
    assert files_a.keys() == files_b.keys()
    assert summary_a["options"] == summary_b["options"]
    assert summary_a["counts"] == summary_b["counts"]
    assert "/private/secret" in paths_b


def test_default_reads_sitemaps_named_in_robots_txt(site, tmp_path):
    config, _, _, paths, _, _ = crawl(site, tmp_path)
    assert "/robots.txt" in paths
    assert "/robots-sitemap.xml" in paths
    outline = json.loads((Path(config.output_dir) / "site_outline.json").read_text())
    assert outline["robots_sitemaps"] == [site + "/robots-sitemap.xml"]
    assert outline["sitemap_url_count"] >= 1


def test_default_unreadable_robots_txt_does_not_stop_the_crawl(site, tmp_path):
    del Handler.routes["/robots.txt"]
    _, code, summary, paths, files, _ = crawl(site, tmp_path)
    assert code == 0
    assert "/private/secret" in files


def test_robots_txt_is_not_requested_when_no_discovery_runs(site, tmp_path):
    _, code, _, paths, files, _ = crawl(site, tmp_path, "--include", "^/about")
    assert code == 0
    assert "/robots.txt" not in paths
    assert "/about" in paths


def test_obey_robots_requests_robots_txt_even_when_no_discovery_runs(site, tmp_path):
    _, code, _, paths, _, _ = crawl(site, tmp_path, "--include", "^/about", "--obey-robots")
    assert code == 0
    assert paths[0] == "/robots.txt"


@pytest.fixture
def quick_audit(monkeypatch, tmp_path):
    """Audit mode spaces its requests by at least a second of its own: skip that wait here."""
    from scrapepy.modes import audit
    from scrapepy.processing.robots import HostRateLimiter

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(audit, "HostRateLimiter", lambda interval: HostRateLimiter(0))

    async def no_sleep(_):
        return None
    monkeypatch.setattr(audit.asyncio, "sleep", no_sleep)


def test_audit_default_fetches_a_disallowed_section_and_saves_robots_txt(site, tmp_path, quick_audit):
    Handler.routes["/"] = ("text/html", page("Home", ["/private/area"]))
    Handler.routes["/private/area"] = ("text/html", page("Private area"))
    Handler.log = []
    config = parse_args([site + "/", "--audit"])
    asyncio.run(run_scraper(config))
    paths = [p for p, _ in Handler.log]
    assert "/private/area" in paths
    saved = list((tmp_path / "tests" / "fixtures").glob("*/meta/robots.txt"))
    assert len(saved) == 1
    assert "Disallow: /private/" in saved[0].read_text()
    assert "robots-sitemap.xml" in json.dumps(
        json.loads(next((tmp_path / "tests" / "fixtures").glob("*/audit_report.json")).read_text()))


def test_audit_obey_robots_skips_a_disallowed_section(site, tmp_path, quick_audit):
    Handler.routes["/"] = ("text/html", page("Home", ["/private/area"]))
    Handler.routes["/private/area"] = ("text/html", page("Private area"))
    Handler.log = []
    config = parse_args([site + "/", "--audit", "--obey-robots"])
    asyncio.run(run_scraper(config))
    assert "/private/area" not in [p for p, _ in Handler.log]
