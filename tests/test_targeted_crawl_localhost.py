"""Targeted crawling on a fake recipe site served from 127.0.0.1.

Each test reads the server's request log and the files written, so it
states exactly which pages a set of options fetches and keeps.
Every recipe and page here is invented.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.scraper import run_scraper

WORDS = " ".join(["Plain readable words make this page long enough to convert."] * 8)
R1, R2, R3, R4, R5 = (
    "/recipes/banana-oat-pancake-fingers",
    "/recipes/sweet-potato-mini-muffins",
    "/recipes/pea-and-mint-fritter-bites",
    "/recipes/apple-cinnamon-porridge-cups",
    "/recipes/carrot-and-lentil-soup-pots",
)


def recipe(path, links=()):
    name = path.rsplit("/", 1)[1].replace("-", " ").title()
    data = json.dumps({"@context": "https://schema.org", "@type": "Recipe", "name": name,
                       "recipeIngredient": ["1 ripe banana", "2 tbsp oats"],
                       "recipeInstructions": ["Mash the banana.", "Stir in the oats."]})
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    return (f'<html><head><title>{name}</title><script type="application/ld+json">{data}'
            f"</script></head><body><article><h1>{name}</h1><p>{WORDS}</p>{anchors}"
            "</article></body></html>")


def page(title, links=(), head=""):
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    return (f"<html><head><title>{title}</title>{head}</head><body><article><h1>{title}</h1>"
            f"<p>{WORDS}</p>{anchors}</article></body></html>")


SITE = {
    "/": page("Home", ["/category/baby/", "/about"]),
    "/category/baby": page("Baby recipes", [R1, R2, "/about", "/category/baby/page/2/"],
                           head='<link rel="next" href="/category/baby/page/2/">'),
    "/category/baby/page/2": page("Baby recipes, page 2", [R3, R4, "/privacy"]),
    R1: recipe(R1, ["/about", R5]),
    R2: recipe(R2, ["/privacy"]),
    R3: recipe(R3),
    R4: recipe(R4),
    R5: recipe(R5),
    "/about": page("About us"),
    "/privacy": page("Privacy policy"),
}
SEARCH = page("Search results", [R1, R3, "/about"])


class Handler(BaseHTTPRequestHandler):
    log: list = []

    def do_GET(self):
        Handler.log.append(self.path)
        path, _, query = self.path.partition("?")
        if path == "/" and query.startswith("s="):
            body = SEARCH
        else:
            body = SITE.get(path.rstrip("/") or "/")
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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


def crawl(base, out, start="/", **kw):
    Handler.log = []
    kw.setdefault("wait", 0)
    kw.setdefault("concurrent", 1)
    kw.setdefault("markdown_only", True)
    if "start_urls" in kw:
        kw["start_urls"] = [base + u for u in kw["start_urls"]]
    config = ScrapeConfig(url=base + start, output_root=str(out), **kw)
    code = asyncio.run(run_scraper(config))
    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    files = set()
    for md in Path(config.markdown_dir).rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        url = next(line[5:] for line in text.splitlines() if line.startswith("url: "))
        files.add(url[len(base):])
    pages = [p for p in Handler.log if p != "/robots.txt"]
    return config, code, summary, pages, files


def test_include_with_max_depth(site, tmp_path):
    _, code, summary, pages, files = crawl(
        site, tmp_path, "/category/baby/", include=["^/recipes/"], max_depth=1)
    assert code == 0
    # No sitemap, feed or homepage requests: only the start URL seeds the crawl
    assert pages == ["/category/baby", R1, R2]
    # /category/ pages are never written (WordPress boilerplate paths)
    assert files == {R1, R2}
    t = summary["targeting"]
    assert t["skipped_by_include"] == 3          # /about, page/2, /privacy
    assert t["skipped_by_depth"] == 1            # R5, two steps from the start
    assert t["skipped_by_exclude"] == 0 and t["stopped_by_limit"] is False


def test_follow_next_reads_the_listing_pages_without_depth(site, tmp_path):
    _, _, summary, pages, files = crawl(
        site, tmp_path, "/category/baby/", include=["^/recipes/"], max_depth=1,
        follow_next=True)
    # The rel="next" <link> in the head is the first link on the page
    assert pages == ["/category/baby", "/category/baby/page/2", R1, R2, R3, R4]
    assert files == {R1, R2, R3, R4}
    assert summary["targeting"]["skipped_by_include"] == 2   # /about, /privacy


def test_only_recipes_writes_only_recipe_pages(site, tmp_path):
    _, code, summary, pages, files = crawl(
        site, tmp_path, "/category/baby/", include=["^/recipes/"], max_depth=1,
        follow_next=True, only_recipes=True)
    assert code == 0
    assert pages == ["/category/baby", "/category/baby/page/2", R1, R2, R3, R4]
    assert files == {R1, R2, R3, R4}
    t = summary["targeting"]
    assert t["skipped_not_recipe"] == 2
    assert t["not_recipe_sample"] == [site + "/category/baby", site + "/category/baby/page/2"]
    assert len(summary["new_markdown"]) == 4


def test_max_pages_is_an_exact_cap(site, tmp_path):
    _, _, summary, pages, _ = crawl(site, tmp_path, "/category/baby/",
                                    include=["^/recipes/"], max_pages=2)
    assert pages == ["/category/baby", R1]
    assert summary["counts"]["fetched"] == 2
    assert summary["targeting"]["stopped_by_limit"] is True


def test_max_pages_not_reached(site, tmp_path):
    _, _, summary, pages, _ = crawl(site, tmp_path, "/category/baby/",
                                    include=["^/recipes/"], max_depth=1, max_pages=3)
    assert pages == ["/category/baby", R1, R2]
    assert summary["targeting"]["stopped_by_limit"] is False


def test_max_depth_zero_fetches_only_the_start(site, tmp_path):
    _, _, summary, pages, files = crawl(site, tmp_path, "/category/baby/", max_depth=0)
    assert pages == ["/category/baby"]
    assert summary["targeting"]["skipped_by_depth"] == 4   # R1, R2, /about, page/2


def test_exclude(site, tmp_path):
    _, _, summary, pages, _ = crawl(site, tmp_path, "/category/baby/",
                                    include=["^/recipes/"], max_depth=1,
                                    exclude=["sweet-potato"])
    assert pages == ["/category/baby", R1]
    assert summary["targeting"]["skipped_by_exclude"] == 1


def test_exclude_never_stops_the_start_url(site, tmp_path):
    _, _, _, pages, _ = crawl(site, tmp_path, "/category/baby/", max_depth=0,
                              exclude=["^/category/"])
    assert pages == ["/category/baby"]


def test_include_or_stay_in_path_scope(site, tmp_path):
    # With -s, links under the start path are followed even without an include match
    _, _, _, pages, _ = crawl(site, tmp_path, "/category/baby/", stay_in_path=True,
                              include=[f"^{R1}$"], max_depth=1)
    assert pages == ["/category/baby", "/category/baby/page/2", R1]


def test_urls_file_with_duplicates(site, tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text(f"# recipes to fetch\n{site}{R3}\n\n{site}{R4}\n{site}{R3}\n")
    config = parse_args(["--urls-file", str(urls), "-o", str(tmp_path / "out"),
                         "--max-depth", "0", "--markdown-only"])
    assert config.start_urls == [site + R3, site + R4]
    assert config.url == site + R3
    Handler.log = []
    config.wait = 0
    code = asyncio.run(run_scraper(config))
    assert code == 0
    assert [p for p in Handler.log if p != "/robots.txt"] == [R3, R4]


@pytest.mark.parametrize("line,message", [
    ("ftp://example.test/x", "line 2: not an http or https URL"),
    ("not a url", "line 2: not an http or https URL"),
    ("https://elsewhere.test/r", "line 2: not on the same site"),
])
def test_urls_file_bad_line(tmp_path, capsys, line, message):
    urls = tmp_path / "urls.txt"
    urls.write_text(f"https://site.test/a\n{line}\n")
    with pytest.raises(SystemExit) as exc:
        parse_args(["--urls-file", str(urls)])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_site_name_shares_one_folder_across_runs(site, tmp_path):
    first, _, _, _, files1 = crawl(site, tmp_path, "/category/baby/", stay_in_path=True,
                                   include=["^/recipes/"], max_depth=1, only_recipes=True,
                                   site_name="kitchen")
    assert Path(first.markdown_dir).name == "kitchen"
    assert files1 == {R1, R2}
    assert (Path(first.markdown_dir) / "recipes" / "banana-oat-pancake-fingers.md").is_file()
    second, _, summary, pages, files2 = crawl(site, tmp_path, "/", start_urls=[R1, R3],
                                              max_depth=0, site_name="kitchen")
    assert second.markdown_dir == first.markdown_dir
    assert pages == [R1, R3]
    assert files2 == {R1, R2, R3}
    assert summary["counts"]["unchanged"] == 1           # R1, from the first run
    assert summary["unchanged_urls"] == [site + R1]
    assert [Path(p).name for p in summary["new_markdown"]] == ["pea-and-mint-fritter-bites.md"]
    assert summary["renamed"] == []


def test_search_url_is_fetched_as_given(site, tmp_path):
    _, code, summary, pages, files = crawl(site, tmp_path, "/?s=baby+led+weaning",
                                           include=["^/recipes/"], max_depth=1)
    assert code == 0
    assert pages == ["/?s=baby+led+weaning", R1, R3]
    assert files == {R1, R3}           # the search page itself is not written


def test_recipe_site_example_from_the_readme(site, tmp_path):
    _, code, summary, pages, files = crawl(
        site, tmp_path, "/category/baby/", include=["^/recipes/"], only_recipes=True,
        max_depth=1, follow_next=True, max_pages=5, site_name="fake_recipes")
    assert pages == ["/category/baby", "/category/baby/page/2", R1, R2, R3]
    assert files == {R1, R2, R3}
    assert summary["targeting"]["stopped_by_limit"] is True
    assert summary["options"]["site_name"] == "fake_recipes"


def test_without_targeting_options_nothing_changes(site, tmp_path):
    _, code, summary, pages, files = crawl(site, tmp_path, "/")
    assert "targeting" not in summary
    for key in ("include", "exclude", "max_depth", "max_pages", "urls_file", "start_urls",
                "only_recipes", "site_name", "follow_next"):
        assert key not in summary["options"]
    # Reconnaissance runs and every linked page is crawled
    assert "/sitemap.xml" in pages and "/privacy" in pages and R5 in pages


def test_cli_rejects_targeting_with_other_modes(capsys):
    for extra in (["--feed"], ["-1"], ["--swagger"]):
        with pytest.raises(SystemExit):
            parse_args(["https://site.test/", "--max-depth", "1", *extra])
    with pytest.raises(SystemExit):
        parse_args(["https://site.test/", "--include", "("])
    with pytest.raises(SystemExit):
        parse_args(["https://site.test/", "--site-name", "../x"])
