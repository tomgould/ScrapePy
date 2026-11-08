"""Crawl robustness on a fake site served from 127.0.0.1: pages wrongly
taken for error pages, bodies that do not decode with their declared
charset, the reason every page without Markdown gets, and pages that need
JavaScript. Every page here is invented.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scrapepy.config import ScrapeConfig
from scrapepy.convert.to_markdown import HTMLToMarkdown
from scrapepy.scraper import run_scraper

WORDS = " ".join(["Plain readable words make this page long enough to convert."] * 8)


def page(title, body="", h1=None):
    return (f"<html><head><title>{title}</title></head><body><article>"
            f"<h1>{h1 or title}</h1><p>{WORDS}</p>{body}</article></body></html>")


CONTROL_FLOW = page(
    "4. More Control Flow Tools - Example Docs",
    '<pre><span class="k">match</span> status:\n'
    '    <span class="k">case</span> <span class="mi">404</span>:\n'
    '        <span class="k">return</span> <span class="s2">Not Found</span>\n'
    '<span>404</span> <span>Not Found</span></pre>',
    h1="4. More Control Flow Tools")
ERRORS_CHAPTER = page("8. Errors and Exceptions - Example Docs",
                      "<p>Errors detected during execution are called exceptions.</p>",
                      h1="8. Errors and Exceptions")
SOFT_404 = page("Page not found - Example Docs", h1="Page not found")
SHELL = ('<html><head><title>App</title><script src="/static/app.js"></script></head>'
         '<body><div id="root"></div><noscript>You need to enable JavaScript to run this app.'
         '</noscript></body></html>')

PAGES = {
    "/": ("text/html; charset=utf-8", page("Home", "".join(
        f'<p><a href="{p}">{p}</a></p>' for p in (
            "/tutorial/controlflow", "/tutorial/errors", "/gone", "/missing",
            "/latin", "/meta-latin", "/short", "/brief", "/notes.txt", "/blob", "/app", "/broken")))
    .encode("utf-8")),
    "/tutorial/controlflow": ("text/html; charset=utf-8", CONTROL_FLOW.encode("utf-8")),
    "/tutorial/errors": ("text/html; charset=utf-8", ERRORS_CHAPTER.encode("utf-8")),
    "/gone": ("text/html; charset=utf-8", SOFT_404.encode("utf-8")),
    # Declared UTF-8, written in Windows-1252
    "/latin": ("text/html; charset=utf-8",
               page("Cafe menu", "<p>Café crème brûlée</p>").encode("cp1252")),
    # No charset in the header, ISO-8859-1 in a <meta> tag
    "/meta-latin": ("text/html", ('<html><head><meta charset="iso-8859-1"><title>Garçon'
                                  f'</title></head><body><article><h1>Garçon</h1><p>{WORDS}'
                                  ' à la carte</p></article></body></html>').encode("latin-1")),
    "/short": ("text/html; charset=utf-8", b"<html><head><title>Short</title></head>"
                                            b"<body><p>Only a few words.</p></body></html>"),
    "/brief": ("text/html; charset=utf-8", page("Brief note").replace(WORDS, "Twelve words of text are not enough for a page here.</p><p>Nor are twelve more words in a second short paragraph.").encode("utf-8")),
    "/notes.txt": ("text/plain; charset=utf-8", b"plain notes"),
    # Binary data served as a page
    "/blob": ("text/html; charset=utf-8", b"PK\x03\x04\x00\x00\x81\x9d binary \x00\xff"),
    "/app": ("text/html; charset=utf-8", SHELL.encode("utf-8")),
    "/broken": ("text/html; charset=utf-8", page("Broken page").encode("utf-8")),
}


class Handler(BaseHTTPRequestHandler):
    log: list = []

    def do_GET(self):
        Handler.log.append(self.path)
        found = PAGES.get(self.path.split("?")[0].rstrip("/") or "/")
        if found is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><title>404 Not Found</title></html>")
            return
        ctype, data = found
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    Handler.log = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def run(site, tmp_path, monkeypatch):
    real_prepare = HTMLToMarkdown.prepare

    def prepare(self, filepath, metadata=None, output_path=None, html=None):
        # /broken makes the converter fail, as a bug in it would
        if (metadata or {}).get("url", "").endswith("/broken"):
            html = None
            filepath = Path("/nonexistent/broken.html")
        return real_prepare(self, filepath, metadata, output_path, html)

    monkeypatch.setattr(HTMLToMarkdown, "prepare", prepare)

    def go(**kw):
        kw.setdefault("wait", 0)
        kw.setdefault("concurrent", 1)
        kw.setdefault("markdown_only", True)
        config = ScrapeConfig(url=site + "/", output_root=str(tmp_path), **kw)
        code = asyncio.run(run_scraper(config))
        summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
        files = {}
        for md in Path(config.markdown_dir).rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            front = yaml.safe_load(text.split("---")[1])
            files[front["url"][len(site):]] = text
        return code, summary, files
    return go


def test_tutorial_pages_are_not_error_pages(run):
    code, summary, files = run()
    assert code == 0
    assert "/tutorial/controlflow" in files and "/tutorial/errors" in files
    assert "Errors detected during execution" in files["/tutorial/errors"]
    failed = {f["url"].rsplit("/", 1)[1]: f["reason"] for f in summary["failed"]}
    assert "controlflow" not in failed and "errors" not in failed
    # A 200 page whose title and H1 say "Page not found" is still an error page
    assert failed["gone"] == "error page"
    assert failed["missing"] == "HTTP 404"


def test_pages_that_do_not_decode_with_their_charset(run):
    _, summary, files = run()
    assert "Café crème brûlée" in files["/latin"]
    assert "à la carte" in files["/meta-latin"]
    fallbacks = {f["url"].rsplit("/", 1)[1]: (f["declared"], f["used"])
                 for f in summary["encoding_fallbacks"]}
    assert fallbacks == {"latin": ("utf-8", "cp1252"), "meta-latin": ("utf-8", "iso8859-1"),
                         "blob": ("utf-8", "utf-8 (undecodable bytes replaced)")}
    assert summary["counts"]["encoding_fallbacks"] == 3


def test_every_page_without_markdown_says_why(run):
    _, summary, files = run()
    reasons = {n["url"].rsplit("/", 1)[1]: n["reason"] for n in summary["no_markdown"]}
    assert reasons == {
        "gone": "error page",
        "short": "empty",
        "brief": "too short",
        "notes.txt": "non-HTML",
        "blob": "non-HTML",
        "app": "empty",
        "broken": "converter failure",
    }
    assert summary["no_markdown_reasons"] == {
        "converter failure": 1, "empty": 2, "error page": 1, "non-HTML": 2, "too short": 1}
    assert summary["counts"]["no_markdown"] == 7
    skipped = {s["url"].rsplit("/", 1)[1]: s["content_type"] for s in summary["skipped_non_html"]}
    assert skipped["blob"] == "text/html"
    # Every page fetched (the home page and each page it links) has a file,
    # a reason, or a failed status
    fetched = {p for p in PAGES if p in Handler.log} | {"/missing"}
    written = {u.rstrip("/") or "/" for u in files}
    without = {"/" + r for r in reasons}
    assert fetched == written | without | {"/missing"}


def test_javascript_shell_is_listed_for_a_browser_fetch(run):
    _, summary, _ = run()
    assert [(n["url"].rsplit("/", 1)[1], n["bytes"]) for n in summary["needs_render"]] == [
        ("app", len(SHELL.encode("utf-8")))]
    assert summary["counts"]["needs_render"] == 1


def test_no_markdown_reasons_are_not_recorded_without_markdown(run):
    _, summary, _ = run(markdown=False, markdown_only=False, include_assets=False)
    assert summary["no_markdown"] == [] and summary["no_markdown_reasons"] == {}
    # The shell is still listed: it is about the fetch, not the conversion
    assert len(summary["needs_render"]) == 1


UTF16_PAGE = page("Wide page", "<p>Encoded as UTF-16 with a byte order mark.</p>")
EXTRA = {
    "/klingon": ("text/html; charset=klingon",
                 page("Unknown charset", "<p>Café au lait</p>").encode("cp1252")),
    "/nul": ("text/html; charset=utf-8",
             page("Stray NUL", "<p>A NUL byte \x00 sits in valid UTF-8.</p>").encode("utf-8")),
    "/wide": ("text/html", UTF16_PAGE.encode("utf-16")),
    "/tutorial/errors-copy": ("text/html; charset=utf-8", ERRORS_CHAPTER.encode("utf-8")),
    "/listing-feed": ("text/html; charset=utf-8",
                      b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
                      b"</channel></rss>"),
    "/author/jane-doe": ("text/html; charset=utf-8", page("Jane Doe").encode("utf-8")),
}


@pytest.fixture
def extra(monkeypatch):
    links = ["/tutorial/errors"] + list(EXTRA)
    monkeypatch.setitem(PAGES, "/", ("text/html; charset=utf-8", page("Home", "".join(
        f'<p><a href="{p}">{p}</a></p>' for p in links)).encode("utf-8")))
    for path, value in EXTRA.items():
        monkeypatch.setitem(PAGES, path, value)


def test_hard_encodings_are_written(run, extra):
    code, summary, files = run()
    assert code == 0
    assert "Encoded as UTF-16" in files["/wide"]
    # An unknown Content-Type charset fails the page with a clear reason,
    # after one request only
    failed = {f["url"].rsplit("/", 1)[1]: f["reason"] for f in summary["failed"]}
    assert failed["klingon"] == "unknown charset 'klingon' in Content-Type"
    assert "/klingon" not in files
    assert Handler.log.count("/klingon") == 1
    # Valid UTF-8 with a NUL byte is a page, not binary data
    assert "sits in valid UTF-8" in files["/nul"]
    used = {f["url"].rsplit("/", 1)[1]: f["used"] for f in summary["encoding_fallbacks"]}
    assert used == {"wide": "utf-16"}
    assert summary["skipped_non_html"] == []


def test_other_no_markdown_reasons(run, extra):
    _, summary, files = run()
    reasons = {n["url"].rsplit("/", 1)[1]: n["reason"] for n in summary["no_markdown"]}
    assert reasons["listing-feed"] == "feed or XML"
    assert reasons["jane-doe"] == "skipped path"
    # The chapter and its copy have one body: one file, one duplicate
    pair = {reasons.get("errors"), reasons.get("errors-copy")}
    assert pair == {None, "duplicate"}
    assert len([u for u in files if u.startswith("/tutorial/errors")]) == 1


def test_not_a_recipe_reason(run, extra):
    _, summary, _ = run(only_recipes=True)
    reasons = {n["url"].rsplit("/", 1)[1]: n["reason"] for n in summary["no_markdown"]}
    assert reasons["errors"] == "not a recipe" and reasons["wide"] == "not a recipe"


def test_unchanged_page_without_markdown_is_listed_again(run):
    run()
    _, summary, _ = run()
    reasons = {n["url"].rsplit("/", 1)[1]: n["reason"] for n in summary["no_markdown"]}
    unchanged = {u.rsplit("/", 1)[1] for u in summary["unchanged_urls"]}
    assert {"short", "brief", "app"} <= unchanged
    assert reasons["short"] == "empty" and reasons["brief"] == "too short"
    assert reasons["app"] == "empty"


def test_needs_render_is_not_listed_in_js_mode(site, tmp_path, monkeypatch):
    """-j renders pages in the browser, so it never lists them for -j."""
    from scrapepy.modes import crawl as crawl_mode

    class Response:
        status = 200
        encoding = "utf-8"

        def __init__(self, url):
            self.url = url
            self.body = SHELL.encode("utf-8") if url.endswith("/app") else page(
                "Home", '<p><a href="/app">app</a></p>').encode("utf-8")

    async def fake_browser(url, config, page_action=None, wait_time=None, **kwargs):
        return Response(url)

    monkeypatch.setattr(crawl_mode, "fetch_page_browser", fake_browser)
    config = ScrapeConfig(url=site + "/", output_root=str(tmp_path), js_render=True, wait=0,
                          markdown_only=True, max_depth=1)
    asyncio.run(run_scraper(config))
    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    assert summary["needs_render"] == []
    assert any(n["url"].endswith("/app") for n in summary["no_markdown"])


def test_unknown_charset_reason_is_narrow():
    from scrapepy.modes.crawl import _fetch_error
    config = ScrapeConfig(url="http://example.com/")
    assert _fetch_error(LookupError("unknown encoding: 'b'klingon''"), config) == \
        "unknown charset 'klingon' in Content-Type"
    assert _fetch_error(LookupError("unknown encoding: x-mac-klingon"), config) == \
        "unknown charset 'x-mac-klingon' in Content-Type"
    # Other lookup errors keep their own text
    assert _fetch_error(KeyError("unknown encoding: x"), config) == "KeyError: 'unknown encoding: x'"
    assert _fetch_error(LookupError("no such thing"), config) == "LookupError: no such thing"


def test_unknown_charset_reason_needs_a_plain_lookup_error():
    from scrapepy.modes.crawl import _fetch_error

    class CodecRegistryError(LookupError):
        pass

    config = ScrapeConfig(url="http://example.com/")
    assert _fetch_error(CodecRegistryError("unknown encoding: x"), config) == \
        "CodecRegistryError: unknown encoding: x"
