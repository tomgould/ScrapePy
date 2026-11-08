"""Hostile robots.txt files and redirects into private networks, on fake
sites served from 127.0.0.1.

The site under test answers on 127.0.0.1; "localhost" names the same server
under another host name, which stands in for a sitemap on another host. A
second server on another port stands in for an internal service: its request
log shows whether a redirect reached it. Redirects to 169.254.169.254 and
10.0.0.1 are only followed with --refuse-private-redirects, which refuses
them before any connection is made. Every page here is invented.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scrapepy.cli import parse_args
from scrapepy.scraper import run_scraper

ROBOTS_MAX_BYTES = 512 * 1024
ROBOTS_MAX_SITEMAPS = 50

WORDS = " ".join(["Plain readable words make this page long enough to convert."] * 8)
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
       b"\x00\x00\x00IEND\xaeB`\x82")


def page(title, links=(), images=()):
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    imgs = "".join(f'<img src="{s}" alt="picture">' for s in images)
    return (f"<html><head><title>{title}</title></head><body><nav>"
            + "".join(f'<a href="{h}">{h}</a> ' for h in links)
            + f"</nav><article><h1>{title}</h1><p>{WORDS}</p>{imgs}{anchors}</article></body></html>")


def urlset(*locs):
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{u}</loc><lastmod>2026-01-02</lastmod></url>" for u in locs)
            + "</urlset>")


def sitemap_index(*locs):
    return ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in locs) + "</sitemapindex>")


class Server:
    """A 127.0.0.1 server with its own routes and request log. A route is
    (status, content type, body) or (30x, location)."""

    def __init__(self):
        server = self
        self.routes: dict = {}
        self.log: list = []     # (host header, path)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                server.log.append((self.headers.get("Host", ""), self.path))
                entry = server.routes.get(self.path)
                if entry is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if len(entry) == 2:
                    self.send_response(entry[0])
                    self.send_header("Location", entry[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, ctype, body = entry
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def paths(self):
        return [p for _, p in self.log]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def servers():
    site, internal = Server(), Server()
    internal.routes = {
        "/landing": (200, "text/html", page("Internal landing")),
        "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n"),
        "/img.png": (200, "image/png", PNG),
        "/sm.xml": (200, "application/xml", urlset()),
        "/feed.xml": (200, "application/rss+xml", "<rss></rss>"),
    }
    yield site, internal
    site.close()
    internal.close()


def run(base, out, *flags, start="/", markdown_only=True):
    config = parse_args([base + start, "-o", str(out), "--wait", "0", "-c", "1",
                         *(["--markdown-only"] if markdown_only else []), *flags])
    code = asyncio.run(run_scraper(config))
    summary_path = Path(config.output_dir) / "run_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    return config, code, summary


# -- robots.txt --------------------------------------------------------------

def test_robots_txt_past_512_kb_is_not_read(servers, tmp_path):
    site, _ = servers
    early = f"Sitemap: {site.base}/early.xml\n"
    padding = "# " + "x" * 1000 + "\n"
    late = f"Sitemap: {site.base}/late.xml\nUser-agent: *\nDisallow: /\n"
    robots = "User-agent: *\nAllow: /\n" + early + padding * (ROBOTS_MAX_BYTES // len(padding) + 50) + late
    assert len(robots) > ROBOTS_MAX_BYTES + 40_000
    site.routes = {
        "/robots.txt": (200, "text/plain", robots),
        "/early.xml": (200, "application/xml", urlset()),
        "/late.xml": (200, "application/xml", urlset()),
        "/": (200, "text/html", page("Home")),
    }
    _, code, summary = run(site.base, tmp_path, "--obey-robots")
    assert code == 0
    assert "/early.xml" in site.paths()
    assert "/late.xml" not in site.paths()
    # The Disallow past the cap is not applied either: the homepage is written
    assert summary["counts"]["blocked_by_robots"] == 0
    assert len(summary["new_markdown"]) == 1


def test_thousands_of_sitemap_lines_fetch_only_the_first_50(servers, tmp_path):
    site, _ = servers
    site.routes = {
        "/robots.txt": (200, "text/plain", "".join(f"Sitemap: {site.base}/sm/{i}.xml\n"
                                                   for i in range(3000))),
        "/": (200, "text/html", page("Home")),
    }
    run(site.base, tmp_path)
    fetched = [p for p in site.paths() if p.startswith("/sm/")]
    assert sorted(fetched) == sorted(f"/sm/{i}.xml" for i in range(ROBOTS_MAX_SITEMAPS))


def test_cross_host_sitemaps_are_refused_unless_allowed(servers, tmp_path):
    site, _ = servers
    other = f"http://localhost:{site.port}"
    site.routes = {
        "/robots.txt": (200, "text/plain",
                        f"Sitemap: {other}/cross.xml\nSitemap: {site.base}/same.xml\n"
                        "Sitemap: file:///etc/passwd\n"),
        "/sitemap.xml": (200, "application/xml",
                         sitemap_index(f"{other}/cross-child.xml", f"{site.base}/same-child.xml")),
        "/same.xml": (200, "application/xml", urlset()),
        "/same-child.xml": (200, "application/xml",
                            sitemap_index(f"{other}/cross-level2.xml", f"{site.base}/same-level2.xml")),
        "/same-level2.xml": (200, "application/xml", urlset()),
        "/cross-level2.xml": (200, "application/xml", urlset()),
        "/cross.xml": (200, "application/xml", urlset()),
        "/cross-child.xml": (200, "application/xml", urlset()),
        "/": (200, "text/html", page("Home")),
    }
    run(site.base, tmp_path / "default")
    assert {"/same.xml", "/same-child.xml", "/same-level2.xml"} <= set(site.paths())
    assert "/cross.xml" not in site.paths()
    assert "/cross-child.xml" not in site.paths()
    assert "/cross-level2.xml" not in site.paths()
    assert all(host == f"127.0.0.1:{site.port}" for host, _ in site.log)

    site.log.clear()
    run(site.base, tmp_path / "allowed", "--allow-sitemap-host", "localhost")
    by_path = {p: host for host, p in site.log}
    assert by_path.get("/cross.xml") == f"localhost:{site.port}"
    assert by_path.get("/cross-child.xml") == f"localhost:{site.port}"
    assert by_path.get("/cross-level2.xml") == f"localhost:{site.port}"
    assert {"/same.xml", "/same-child.xml"} <= set(by_path)


def test_robots_redirect_to_a_private_address(servers, tmp_path):
    site, internal = servers
    site.routes = {
        "/robots.txt": (302, f"{internal.base}/robots.txt"),
        "/": (200, "text/html", page("Home")),
    }
    # Without the flag the redirect is followed (to any host, RFC 9309)
    _, code, _ = run(site.base, tmp_path / "default", "--obey-robots")
    assert "/robots.txt" in internal.paths()
    internal.log.clear()
    # With it the file is unreadable, so --obey-robots treats the site as disallowed
    _, code, summary = run(site.base, tmp_path / "flag", "--obey-robots", "--refuse-private-redirects")
    assert internal.log == []
    assert summary["new_markdown"] == []
    assert code != 0


# -- redirects ---------------------------------------------------------------

def _redirect_site(site, internal):
    site.routes = {
        "/": (200, "text/html", page("Home", ["/moved", "/to-loopback", "/to-localhost",
                                              "/to-meta", "/to-ten"])),
        "/moved": (301, "/about"),
        "/about": (200, "text/html", page("About us")),
        "/to-loopback": (302, f"{internal.base}/landing"),
        "/to-localhost": (302, f"http://localhost:{internal.port}/landing"),
        "/to-meta": (302, "http://169.254.169.254/latest/meta-data/"),
        "/to-ten": (307, "http://10.0.0.1/admin"),
    }


def test_flag_refuses_private_redirects_and_records_them(servers, tmp_path):
    site, internal = servers
    _redirect_site(site, internal)
    _, code, summary = run(site.base, tmp_path, "--refuse-private-redirects")
    assert code == 0
    assert internal.log == []
    failed = {f["url"][len(site.base):]: f["reason"] for f in summary["failed"]}
    assert set(failed) == {"/to-loopback", "/to-localhost", "/to-meta", "/to-ten"}
    assert "private address refused (127.0.0.1)" in failed["/to-loopback"]
    assert "local host name refused (localhost)" in failed["/to-localhost"]
    assert "private address refused (169.254.169.254)" in failed["/to-meta"]
    assert "private address refused (10.0.0.1)" in failed["/to-ten"]
    assert all(r.startswith("RedirectRefused: ") for r in failed.values())
    # A redirect within the site is still followed
    assert "/about" in site.paths()
    assert any(p.endswith("/moved/_index.md") for p in summary["new_markdown"])
    assert summary["options"]["refuse_private_redirects"] is True


def test_without_the_flag_a_loopback_redirect_is_followed(servers, tmp_path):
    site, internal = servers
    site.routes = {
        "/": (200, "text/html", page("Home", ["/to-loopback"])),
        "/to-loopback": (302, f"{internal.base}/landing"),
    }
    _, code, summary = run(site.base, tmp_path)
    assert code == 0
    assert internal.paths() == ["/landing"]
    assert summary["failed"] == []
    assert "refuse_private_redirects" not in summary["options"]


def test_flag_covers_asset_downloads(servers, tmp_path):
    site, internal = servers
    site.routes = {
        "/": (200, "text/html", page("Home", images=["/img.png"])),
        "/img.png": (302, f"{internal.base}/img.png"),
    }
    run(site.base, tmp_path / "flag", "--refuse-private-redirects", markdown_only=False)
    assert internal.log == []
    assert "/img.png" in site.paths()
    site.log.clear()
    run(site.base, tmp_path / "default", markdown_only=False)
    assert internal.paths() == ["/img.png"]


def test_flag_covers_sitemap_fetches(servers, tmp_path):
    site, internal = servers
    site.routes = {
        "/robots.txt": (200, "text/plain", f"Sitemap: {site.base}/sm-redirect.xml\n"),
        "/sm-redirect.xml": (302, f"{internal.base}/sm.xml"),
        "/": (200, "text/html", page("Home")),
    }
    run(site.base, tmp_path / "flag", "--refuse-private-redirects")
    assert "/sm-redirect.xml" in site.paths()
    assert internal.log == []
    run(site.base, tmp_path / "default")
    assert internal.paths() == ["/sm.xml"]


def test_flag_covers_feed_mode(servers, tmp_path):
    site, internal = servers
    site.routes = {"/feed.xml": (302, f"{internal.base}/feed.xml")}
    _, code, _ = run(site.base, tmp_path, "--feed", "--refuse-private-redirects", start="/feed.xml")
    assert internal.log == []
    assert code != 0
