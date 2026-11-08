"""--documents on a fake site served from 127.0.0.1.

Each test reads the server's request log, the files written and
run_summary.json. Every page and document is invented and generated in
tests/document_samples.py.
"""

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scrapepy import cli
from scrapepy.config import ScrapeConfig
from scrapepy.scraper import run_scraper

from .document_samples import (
    encrypt_pdf, make_docx, make_pdf, make_xlsx, zip_bomb,
)

WORDS = " ".join(["Plain readable words make this page long enough to convert."] * 8)

REPORT = make_pdf([["Annual report 2025", "Total members: 42"], ["Second page"]],
                  {"Title": "Garden Club Annual Report", "CreationDate": "D:20250314093000Z"})
PLAIN = make_pdf([["Plain notice without metadata"]])
REAL = make_pdf([["Moved document text"]])
SCAN = make_pdf([[]])
LOCKED = encrypt_pdf(make_pdf([["Secret text"]]))
DOCX = make_docx()
XLSX = make_xlsx({"Rota": [["Plot", "Holder"], ["A1", "Sam"], ["A2", "Kim"]]})
CSV = b"name,count\napples,3\npears,5\n"
BOMB = zip_bomb("docx", unpacked_mb=3)
BIG = make_pdf([["x" * 60] * 40]) + b"%" + b"0" * (2 * 1024 * 1024)

PDF, DOCX_T, XLSX_T, CSV_T = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
)

LINKS = [
    "/reports/annual-report.pdf", "/plain.pdf", "/files/handbook.docx", "/data/rota.xlsx",
    "/data/plots.csv", "/old/minutes.doc", "/old/budget.xls", "/scan.pdf", "/big.pdf",
    "/stream-big.pdf", "/broken.pdf", "/locked.pdf", "/bomb.docx", "/moved.pdf",
    "/away.pdf", "/private/secret.pdf", "/fake.pdf", "/a/b_c.pdf", "/a/b:c.pdf",
    "/to-private.pdf", "/liar.pdf", "https://elsewhere.example/other.pdf", "/about",
]


def page(title, links=()):
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    return (f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>"
            f"<p>{WORDS}</p>{anchors}</article></body></html>").encode()


class Handler(BaseHTTPRequestHandler):
    log: list = []
    port = 0
    docs: dict = {}

    def do_GET(self):
        Handler.log.append(self.path)
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            return self._send(200, b"User-agent: *\nDisallow: /private/\n", "text/plain")
        if path == "/moved.pdf":
            return self._redirect("/store/real.pdf")
        if path == "/away.pdf":
            return self._redirect(f"http://localhost:{Handler.port}/store/real.pdf")
        if path == "/to-private.pdf":
            return self._redirect("/private/secret.pdf")
        if path == "/liar.pdf":
            # Claims 50 MB, sends a few bytes and closes: refused on the header alone
            self.send_response(200)
            self.send_header("Content-Type", PDF)
            self.send_header("Content-Length", str(50 * 1024 * 1024))
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n")
            return
        if path == "/trickle.pdf":
            # One byte every half second: each read is quick, the whole never ends
            self.send_response(200)
            self.send_header("Content-Type", PDF)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b"%")
                    self.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                pass
            return
        if path == "/stream-big.pdf":
            self.send_response(200)
            self.send_header("Content-Type", PDF)
            self.end_headers()       # no Content-Length: the body ends when the connection does
            self.wfile.write(BIG)
            return
        if path in Handler.docs:
            body, ctype, etag = Handler.docs[path]
            if etag and self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.end_headers()
                return
            return self._send(200, body, ctype, etag)
        pages = {"/": page("Home", LINKS), "/about": page("About us", ["/files/handbook.docx"])}
        if path in pages:
            return self._send(200, pages[path], "text/html; charset=utf-8")
        self._send(404, b"missing", "text/plain")

    def _send(self, status, body, ctype, etag=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def default_docs():
    return {
        "/reports/annual-report.pdf": (REPORT, PDF, '"r1"'),
        "/plain.pdf": (PLAIN, PDF, None),
        "/store/real.pdf": (REAL, PDF, None),
        "/files/handbook.docx": (DOCX, DOCX_T, None),
        "/data/rota.xlsx": (XLSX, XLSX_T, None),
        "/data/plots.csv": (CSV, CSV_T, None),
        "/old/minutes.doc": (b"\xd0\xcf\x11\xe0old", "application/msword", None),
        "/old/budget.xls": (b"\xd0\xcf\x11\xe0old", "application/vnd.ms-excel", None),
        "/scan.pdf": (SCAN, PDF, None),
        "/big.pdf": (BIG, PDF, None),
        "/broken.pdf": (b"%PDF-1.4\nthis is not really a pdf\n%%EOF\n", PDF, None),
        "/locked.pdf": (LOCKED, PDF, None),
        "/bomb.docx": (BOMB, DOCX_T, None),
        "/private/secret.pdf": (PLAIN, PDF, None),
        "/fake.pdf": (b"<html><body>Sign in</body></html>", "text/html", None),
        "/a/b_c.pdf": (make_pdf([["Underscore name"]]), PDF, None),
        "/a/b:c.pdf": (make_pdf([["Colon name"]]), PDF, None),
    }


@pytest.fixture
def site():
    Handler.log = []
    Handler.docs = default_docs()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Handler.port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{Handler.port}"
    httpd.shutdown()
    httpd.server_close()


def crawl(base, out, start="/", small_limits=True, **kw):
    """Run a crawl; return (config, exit code, summary, requested paths)."""
    Handler.log = []
    kw.setdefault("wait", 0)
    kw.setdefault("concurrent", 1)
    kw.setdefault("markdown_only", True)
    if "start_urls" in kw:
        kw["start_urls"] = [base + u for u in kw["start_urls"]]
    config = ScrapeConfig(url=base + start, output_root=str(out), **kw)
    from scrapepy.convert import documents as conv
    saved = conv.DEFAULT_LIMITS
    if small_limits:
        # A 1 MB unpacked cap, so a 3 MB zip bomb is enough in the test
        conv.DEFAULT_LIMITS = conv.Limits(max_unzipped_bytes=1024 * 1024)
    try:
        code = asyncio.run(run_scraper(config))
    finally:
        conv.DEFAULT_LIMITS = saved
    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    # Reconnaissance (robots.txt, sitemap, feeds, the home page once more)
    # runs in untargeted crawls; it is left out here
    requests = [p for p in Handler.log if p not in RECON]
    if Handler.log.count("/") > 1:
        requests.remove("/")
    return config, code, summary, requests


RECON = {"/robots.txt", "/sitemap.xml", "/feed", "/feed.xml", "/feed/", "/atom.xml", "/rss",
         "/rss.xml", "/index.xml"}


def front(path):
    text = Path(path).read_text(encoding="utf-8")
    end = text.index("\n---\n", 3)
    return yaml.safe_load(text[4:end]), text[end + 5:].removeprefix("\n")


ALL = ["pdf", "docx", "xlsx", "csv"]


def reasons(items):
    return {i["url"].split("/", 3)[-1]: i["reason"] for i in items}


def test_default_run_ignores_documents(site, tmp_path):
    config, code, summary, requests = crawl(site, tmp_path)
    assert code == 0
    # As before: PDF, DOCX and DOC links are never requested; XLSX, XLS and
    # CSV links are requested as pages and skipped as not HTML
    assert sorted(requests) == sorted(["/", "/about", "/data/rota.xlsx", "/data/plots.csv",
                                       "/old/budget.xls"])
    assert "documents" not in summary
    assert "documents" not in summary["options"]
    assert not (tmp_path / "files").exists()
    # Binary files fetched as pages are decoded without failing and skipped
    # as not HTML, like the CSV file
    assert sorted(s["url"].rsplit("/", 1)[1] for s in summary["skipped_non_html"]) == [
        "budget.xls", "plots.csv", "rota.xlsx"]
    assert summary["failed"] == []


def test_documents_crawl(site, tmp_path):
    config, code, summary, requests = crawl(site, tmp_path, documents=ALL, max_document_mb=1,
                                            obey_robots=True)
    assert code == 0
    docs = summary["documents"]
    assert docs["types"] == ALL
    assert docs["files_dir"] == "files/" + config.output_dir_name
    assert summary["options"]["documents"] == ALL
    assert summary["options"]["max_document_mb"] == 1

    # Old formats, the other site's link and the robots.txt-blocked file are never requested
    for never in ("/old/minutes.doc", "/old/budget.xls", "/private/secret.pdf"):
        assert never not in requests
    assert not any("elsewhere" in r or "other.pdf" in r for r in requests)
    assert "/store/real.pdf" in requests        # same-site redirect followed
    assert requests.count("/files/handbook.docx") == 1

    assert reasons(docs["skipped"]) == {
        "old/minutes.doc": "old binary format (.doc), not converted",
        "old/budget.xls": "old binary format (.xls), not converted",
        "scan.pdf": "no text layer",
        "private/secret.pdf": "blocked by robots.txt",
    }
    failed = reasons(docs["failed"])
    assert failed["big.pdf"] == "larger than the 1 MB document size cap"
    assert failed["stream-big.pdf"] == "larger than the 1 MB document size cap"
    assert failed["locked.pdf"] == "encrypted (needs a password)"
    assert failed["bomb.docx"] == "unpacks to more than 1 MB (possible zip bomb)"
    assert failed["away.pdf"] == "redirect to another site refused (localhost)"
    assert failed["fake.pdf"] == "not a PDF file (server sent text/html)"
    assert failed["to-private.pdf"] == "redirect target blocked by robots.txt"
    assert failed["liar.pdf"] == "larger than the 1 MB document size cap"
    assert failed["broken.pdf"].startswith("corrupt or unreadable PDF file")
    assert set(failed) == {"big.pdf", "stream-big.pdf", "locked.pdf", "bomb.docx",
                           "away.pdf", "fake.pdf", "broken.pdf", "to-private.pdf", "liar.pdf"}
    # Failures are also in the run's failed list
    assert set(failed) <= set(reasons(summary["failed"]))
    assert docs["converted"] == 8
    assert docs["found"] == 21
    assert docs["fetched"] == 18           # found less the two old formats and the robots.txt block
    assert docs["unchanged"] == 0
    assert summary["counts"]["fetched"] == 2 + 18

    md = Path(config.markdown_dir)
    meta, body = front(md / "reports" / "annual-report.pdf.md")
    assert meta == {
        "title": "Garden Club Annual Report",
        "url": site + "/reports/annual-report.pdf",
        "date": "2025-03-14T09:30:00+00:00",
        "date_source": "DOCUMENT_METADATA",
        "page_type": "document",
        "word_count": meta["word_count"],
        "domain": "127.0.0.1:" + str(Handler.port),
        "file_type": "pdf",
        "pages": 2,
        "linked_from": site + "/",
        "scraped_at": meta["scraped_at"],
        "content_hash": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
    }
    assert body.startswith("# Garden Club Annual Report\n\n## Page 1\n\nAnnual report 2025\nTotal members: 42\n")
    assert "## Page 2\n\nSecond page" in body
    assert meta["word_count"] > 5

    # The original is saved outside MarkdownSites, byte for byte
    files = Path(config.files_dir)
    assert (files / "reports" / "annual-report.pdf").read_bytes() == REPORT
    assert (files / "scan.pdf").read_bytes() == SCAN          # saved though not converted
    assert not (md / "scan.pdf.md").exists()
    assert not (files / "big.pdf").exists()
    assert not list(md.rglob("*.pdf")) and not list(md.rglob("*.docx"))

    meta, body = front(md / "plain.pdf.md")
    assert meta["title"] == "plain.pdf" and "date" not in meta
    meta, body = front(md / "moved.pdf.md")           # written under the address requested
    assert "Moved document text" in body
    meta, body = front(md / "files" / "handbook.docx.md")
    assert meta["file_type"] == "docx" and meta["title"] == "Garden Club Handbook"
    assert "pages" not in meta
    meta, body = front(md / "data" / "rota.xlsx.md")
    assert meta["sheets"] == 1 and meta["rows"] == 2 and "pages" not in meta and "| A1 | Sam |" in body
    meta, body = front(md / "data" / "plots.csv.md")
    assert meta["title"] == "plots.csv" and meta["rows"] == 2 and "sheets" not in meta
    assert body == "# plots.csv\n\n| name | count |\n| --- | --- |\n| apples | 3 |\n| pears | 5 |\n"

    # Two URLs with the same file name: the second gets the hash suffix
    renamed = {r["url"].split("/", 3)[-1]: r for r in summary["renamed"]}
    assert set(renamed) == {"a/b:c.pdf"}
    assert renamed["a/b:c.pdf"]["natural_file"].endswith("a/b_c.pdf.md")
    assert front(md / "a" / "b_c.pdf.md")[1].count("Underscore name") == 1
    suffixed = [p for p in (md / "a").iterdir() if p.name != "b_c.pdf.md"]
    assert len(suffixed) == 1 and "Colon name" in suffixed[0].read_text()
    assert len(list((files / "a").iterdir())) == 2

    new = set(summary["new_markdown"])
    assert f"MarkdownSites/{config.output_dir_name}/reports/annual-report.pdf.md" in new
    assert not any(n.endswith("scan.pdf.md") for n in new)


def test_incremental_rerun_reports_documents_unchanged(site, tmp_path):
    crawl(site, tmp_path, documents=["pdf", "csv"], obey_robots=True)
    config, code, summary, requests = crawl(site, tmp_path, documents=["pdf", "csv"], obey_robots=True)
    assert code == 0
    docs = summary["documents"]
    assert docs["converted"] == 0
    unchanged = set(summary["unchanged_urls"])
    paths = ("/reports/annual-report.pdf", "/plain.pdf", "/data/plots.csv", "/moved.pdf",
             "/scan.pdf", "/a/b_c.pdf", "/a/b:c.pdf", "/big.pdf", "/stream-big.pdf")
    assert {site + p for p in paths} <= unchanged
    assert docs["unchanged"] == len(paths)
    assert not any(n.endswith(".pdf.md") for n in summary["new_markdown"] + summary["changed_markdown"])
    # The renamed document keeps its suffixed file and is listed again
    assert [r["url"] for r in summary["renamed"]] == [site + "/a/b:c.pdf"]

    # A changed document is converted again and counted as changed
    Handler.docs["/plain.pdf"] = (make_pdf([["Plain notice, second edition"]]), PDF, None)
    config, code, summary, requests = crawl(site, tmp_path, documents=["pdf", "csv"], obey_robots=True)
    assert summary["documents"]["converted"] == 1
    assert summary["changed_markdown"] == [f"MarkdownSites/{config.output_dir_name}/plain.pdf.md"]
    assert "second edition" in (Path(config.markdown_dir) / "plain.pdf.md").read_text()


def test_urls_file_document_is_fetched_as_document(site, tmp_path):
    config, code, summary, requests = crawl(
        site, tmp_path, start="/reports/annual-report.pdf", documents=["pdf"],
        start_urls=["/reports/annual-report.pdf", "/data/plots.csv"], max_depth=0)
    assert code == 0
    # csv is not listed: fetched as a page and skipped as not HTML, as before
    assert sorted(requests) == ["/data/plots.csv", "/reports/annual-report.pdf"]
    assert summary["documents"]["converted"] == 1
    assert summary["documents"]["found"] == 1
    meta, _ = front(Path(config.markdown_dir) / "reports" / "annual-report.pdf.md")
    assert "linked_from" not in meta
    assert [s["url"] for s in summary["skipped_non_html"]] == [site + "/data/plots.csv"]


def test_include_and_exclude_match_document_paths(site, tmp_path):
    _, _, summary, requests = crawl(site, tmp_path, documents=ALL, include=["^/reports/", "^/data/"],
                                    exclude=[r"\.xlsx$"])
    assert sorted(requests) == ["/", "/data/plots.csv", "/reports/annual-report.pdf"]
    assert summary["documents"]["found"] == 2
    assert summary["targeting"]["skipped_by_exclude"] == 1


def test_max_depth_counts_document_links(site, tmp_path):
    _, _, summary, requests = crawl(site, tmp_path, documents=["docx"], max_depth=0)
    assert requests == ["/"]
    assert summary["documents"]["found"] == 0


def test_max_pages_counts_documents(site, tmp_path):
    _, _, summary, requests = crawl(site, tmp_path, documents=ALL, max_pages=3)
    assert requests == ["/", "/reports/annual-report.pdf", "/plain.pdf"]
    # The documents the budget did not reach are counted and listed
    docs = summary["documents"]
    # .doc and .xls links would only be skipped, so they are not listed
    assert docs["found_unvisited"] == 17
    assert docs["found_unvisited_sample"][:2] == [site + "/files/handbook.docx", site + "/data/rota.xlsx"]
    assert len(docs["found_unvisited_sample"]) == 17
    assert not any(u.endswith((".doc", ".xls")) for u in docs["found_unvisited_sample"])
    assert site + "/plain.pdf" not in docs["found_unvisited_sample"]
    assert summary["counts"]["fetched"] == 3
    assert summary["targeting"]["stopped_by_limit"] is True


def test_symlink_in_files_folder_is_refused(site, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    files = tmp_path / "out" / "files" / f"127_0_0_1:{Handler.port}"
    files.mkdir(parents=True)
    os.symlink(outside, files / "reports")
    _, _, summary, _ = crawl(site, tmp_path / "out", documents=["pdf"])
    failed = reasons(summary["documents"]["failed"])
    assert failed["reports/annual-report.pdf"] == (
        f"refused: symbolic link in the output path (127_0_0_1:{Handler.port}/reports)")
    assert list(outside.iterdir()) == []


def test_symlink_at_site_files_folder_is_refused(site, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "out" / "files").mkdir(parents=True)
    os.symlink(outside, tmp_path / "out" / "files" / f"127_0_0_1:{Handler.port}")
    _, _, summary, _ = crawl(site, tmp_path / "out", documents=["pdf"])
    failed = reasons(summary["documents"]["failed"])
    assert failed["plain.pdf"] == f"refused: symbolic link in the output path (127_0_0_1:{Handler.port})"
    assert list(outside.iterdir()) == []


def test_deleted_markdown_is_written_again(site, tmp_path):
    config, _, _, _ = crawl(site, tmp_path, documents=["pdf"])
    md = Path(config.markdown_dir) / "reports" / "annual-report.pdf.md"
    md.unlink()
    _, _, summary, requests = crawl(site, tmp_path, documents=["pdf"])
    assert md.exists()
    assert site + "/reports/annual-report.pdf" not in summary["unchanged_urls"]
    assert f"MarkdownSites/{config.output_dir_name}/reports/annual-report.pdf.md" in \
        summary["changed_markdown"]


def test_slow_download_hits_the_deadline(site, tmp_path, monkeypatch):
    from scrapepy.processing import documents as docs_mod
    monkeypatch.setattr(docs_mod, "DOWNLOAD_DEADLINE_SECONDS", 2)
    _, _, summary, _ = crawl(site, tmp_path, documents=["pdf"], start="/trickle.pdf",
                             start_urls=["/trickle.pdf"], max_depth=0)
    assert reasons(summary["documents"]["failed"]) == {
        "trickle.pdf": "download took longer than 2 seconds"}


def test_pages_saved_line_leaves_documents_out(site, tmp_path, capsys):
    crawl(site, tmp_path, documents=ALL, include=["^/reports/"])
    assert "Pages saved: 1\n" in capsys.readouterr().out


def test_documents_with_html_mirror(site, tmp_path):
    config, code, summary, _ = crawl(site, tmp_path, documents=["pdf"], markdown_only=False)
    assert code == 0
    assert (Path(config.files_dir) / "reports" / "annual-report.pdf").read_bytes() == REPORT
    assert (Path(config.markdown_dir) / "reports" / "annual-report.pdf.md").exists()
    assert not (Path(config.output_dir) / "reports").exists()


def test_missing_library_exits_2(site, tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    code = cli.main([site + "/", "--documents", "csv,pdf", "-o", str(tmp_path), "--wait", "0"])
    assert code == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert err == ['--documents pdf needs the optional document libraries: '
                   'pip install "scrapepy[documents]"']
    assert Handler.log == []
    # The same check guards run_scraper for library callers
    config = ScrapeConfig(url=site + "/", output_root=str(tmp_path), documents=["pdf"])
    assert asyncio.run(run_scraper(config)) == 2


def test_csv_needs_no_extra_library(site, tmp_path, monkeypatch):
    for name in ("pypdf", "docx", "openpyxl"):
        monkeypatch.setitem(sys.modules, name, None)
    _, code, summary, _ = crawl(site, tmp_path, documents=["csv"])
    assert code == 0
    assert summary["documents"]["converted"] == 1


def test_robots_txt_applies_to_documents_only_with_obey_robots(site, tmp_path):
    _, _, summary, requests = crawl(site, tmp_path, documents=["pdf"], include=["^/private/", "^/to-private"])
    assert "/private/secret.pdf" in requests
    assert summary["blocked_by_robots"] == []
    assert "to-private.pdf" not in reasons(summary["documents"]["failed"])
    _, _, summary, requests = crawl(site, tmp_path / "obey", documents=["pdf"], obey_robots=True,
                                    include=["^/private/", "^/to-private"])
    assert "/private/secret.pdf" not in requests
    assert reasons(summary["documents"]["failed"]) == {
        "to-private.pdf": "redirect target blocked by robots.txt"}


def test_child_end_reason_reaches_the_summary(site, tmp_path, monkeypatch):
    from scrapepy.processing import documents as docs_mod

    async def crashed(*args, **kwargs):
        return "died", "crashed (SIGSEGV)"
    monkeypatch.setattr(docs_mod, "run_in_child", crashed)
    _, _, summary, _ = crawl(site, tmp_path, documents=["pdf"], start="/plain.pdf",
                             start_urls=["/plain.pdf"], max_depth=0)
    assert reasons(summary["documents"]["failed"]) == {
        "plain.pdf": "conversion stopped: crashed (SIGSEGV)"}


def test_pdf_layout_is_opt_in(site, tmp_path):
    config, _, summary, _ = crawl(site, tmp_path, documents=["pdf"], pdf_layout=True,
                                  start="/reports/annual-report.pdf",
                                  start_urls=["/reports/annual-report.pdf"], max_depth=0)
    assert summary["options"]["pdf_layout"] is True
    meta, body = front(Path(config.markdown_dir) / "reports" / "annual-report.pdf.md")
    assert meta["layout_pages"] == 2 and "fallback_pages" not in meta
    assert "Annual report 2025 Total members: 42" in body
    config, _, summary, _ = crawl(site, tmp_path / "default", documents=["pdf"],
                                  start="/reports/annual-report.pdf",
                                  start_urls=["/reports/annual-report.pdf"], max_depth=0)
    assert "pdf_layout" not in summary["options"]
    meta, _ = front(Path(config.markdown_dir) / "reports" / "annual-report.pdf.md")
    assert "layout_pages" not in meta
