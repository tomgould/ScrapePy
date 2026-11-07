"""Crawl a local test server (127.0.0.1 only) whose pages declare alternate
formats (JSON, Markdown) and link to them, as small CMS sites do."""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from scrapepy.config import ScrapeConfig
from scrapepy.modes import crawl
from scrapepy.output.writer import create_output_dirs

WORDS = "Plenty of readable words appear here so the page passes the length check. " * 6
SLUGS = ("alpha", "beta")


def project_page(slug):
    return (
        f'<html><head><title>Project {slug}</title>'
        f'<link rel="alternate" type="application/json" href="/projects/{slug}.json">'
        f'<link rel="alternate" type="text/markdown" href="/projects/{slug}.md">'
        f'<link rel="alternate" hreflang="fr" href="/projects/{slug}/fr/">'
        f'</head><body><article><h1>Project {slug}</h1><p>{WORDS}</p>'
        f'<p>Also as <a href="/projects/{slug}.json">JSON</a>, '
        f'<a href="/projects/{slug}.xml">XML</a> and <a href="/projects/{slug}.md">Markdown</a>.</p>'
        f'</article></body></html>'
    )


def routes():
    pages = {
        "/projects/": ("text/html", "<html><head><title>Projects</title></head><body><article>"
                       "<h1>Projects</h1><p>" + WORDS + "</p>"
                       + "".join(f'<a href="/projects/{s}/">{s}</a> ' for s in SLUGS)
                       + "</article></body></html>"),
    }
    for s in SLUGS:
        pages[f"/projects/{s}/"] = ("text/html", project_page(s))
        pages[f"/projects/{s}/fr/"] = ("text/html", project_page(s).replace(
            f"<h1>Project {s}</h1>", f"<h1>Projet {s}</h1>"))
        pages[f"/projects/{s}.json"] = ("application/json", json.dumps(
            {"title": f"Project {s}", "body": WORDS}))
        pages[f"/projects/{s}.xml"] = ("application/xml", f"<project><t>{s}</t></project>")
        pages[f"/projects/{s}.md"] = ("text/markdown", f"# Project {s}\n\n{WORDS}\n")
    return pages


class Handler(BaseHTTPRequestHandler):
    log: list[str] = []
    pages: dict = {}

    def do_GET(self):
        Handler.log.append(self.path)
        path = self.path.split("?")[0]
        if path not in Handler.pages and path + "/" in Handler.pages:
            path += "/"  # the crawler requests /projects/alpha for /projects/alpha/
        if path not in Handler.pages:
            self.send_response(404)
            self.end_headers()
            return
        ctype, body = Handler.pages[path]
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.log = []
    Handler.pages = routes()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def crawl_md_only(base, out):
    config = ScrapeConfig(url=base + "/projects/", output_root=str(out), markdown_only=True,
                          stay_in_path=True, wait=0, concurrent=1)
    create_output_dirs(config)
    return config, asyncio.run(crawl.run_crawl_mode(config))


def markdown_files(config):
    found = {}
    for d, _, files in os.walk(config.markdown_dir):
        for f in files:
            path = os.path.join(d, f)
            text = open(path, encoding="utf-8").read()
            front = yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))
            found[os.path.relpath(path, config.markdown_dir)] = (front, text)
    return found


def test_alternate_formats_are_not_fetched_or_written(server, tmp_path):
    config, summary = crawl_md_only(server, tmp_path)
    requested = set(Handler.log)
    for s in SLUGS:
        assert f"/projects/{s}.json" not in requested
        assert f"/projects/{s}.xml" not in requested
    files = markdown_files(config)
    for name, (front, text) in files.items():
        assert not front["url"].endswith((".json", ".xml")), name
        assert '{"title"' not in text, name
    for s in SLUGS:
        front, _ = next(v for k, v in files.items() if v[0]["url"] == f"{server}/projects/{s}")
        assert front["title"] == f"Project {s}"


def test_raw_markdown_link_is_not_written(server, tmp_path):
    # /projects/<slug>.md is linked from the body and served as text/markdown
    config, summary = crawl_md_only(server, tmp_path)
    files = markdown_files(config)
    assert not [k for k, v in files.items() if v[0]["url"].endswith(".md")]
    assert summary["counts"]["changed"] == 0


def test_non_html_responses_are_listed(server, tmp_path):
    Handler.pages = routes()
    Handler.pages["/projects/"] = ("text/html", Handler.pages["/projects/"][1].replace(
        "</article>", '<a href="/projects/plain.md">plain</a></article>'))
    Handler.pages["/projects/plain.md"] = ("text/plain", "Just text.")
    config, summary = crawl_md_only(server, tmp_path)
    skipped = {e["url"]: e["content_type"] for e in summary["skipped_non_html"]}
    assert skipped[f"{server}/projects/plain.md"] == "text/plain"
    for s in SLUGS:
        assert skipped[f"{server}/projects/{s}.md"] == "text/markdown"
    assert summary["counts"]["skipped_non_html"] == len(summary["skipped_non_html"])
    urls = [v[0]["url"] for v in markdown_files(config).values()]
    assert f"{server}/projects/plain.md" not in urls


def test_markdown_url_served_as_html_is_converted(server, tmp_path):
    Handler.pages = routes()
    Handler.pages["/projects/"] = ("text/html", Handler.pages["/projects/"][1].replace(
        "</article>", '<a href="/projects/guide.md">guide</a></article>'))
    Handler.pages["/projects/guide.md"] = ("text/html", project_page("guide"))
    config, _ = crawl_md_only(server, tmp_path)
    urls = [v[0]["url"] for v in markdown_files(config).values()]
    assert f"{server}/projects/guide.md" in urls


def test_alternate_markdown_link_tag_is_not_queued(server, tmp_path):
    Handler.pages = routes()
    for path, (ctype, body) in list(Handler.pages.items()):
        # Only the <link rel="alternate"> points at the .md file now
        for s in SLUGS:
            body = body.replace(f' and <a href="/projects/{s}.md">Markdown</a>', "")
        Handler.pages[path] = (ctype, body)
    crawl_md_only(server, tmp_path)
    for s in SLUGS:
        assert f"/projects/{s}.md" not in Handler.log


def test_rerun_is_stable(server, tmp_path):
    config, first = crawl_md_only(server, tmp_path)
    before = {k: v[0]["url"] for k, v in markdown_files(config).items()}
    config, second = crawl_md_only(server, tmp_path)
    after = {k: v[0]["url"] for k, v in markdown_files(config).items()}
    assert before == after
    assert second["counts"]["changed"] == 0
