"""Very long page names, crawled from a local test server (127.0.0.1 only):
every file and folder name written fits in 255 bytes, a hash-suffixed name
too, and the names stay the same in every run."""

import os
from urllib.parse import quote

import pytest

from scrapepy.output.naming import MAX_NAME_BYTES, fit_path, suffixed_path, url_suffix
from tests.test_collisions_localhost import (  # noqa: F401  (pytest fixture)
    Site, check, md_files, run, server,
)

LONG_NAMES = {
    "ascii": "a" * 250,
    "emoji": "\U0001F600" * 100,
    "mixed": ("café-über-日本-" * 20)[:200],
}


def all_names(root):
    for d, dirs, files in os.walk(root):
        yield from dirs
        yield from files


def serve(site):
    """Serve each page at its raw path and at its percent-encoded path."""
    site.serve()
    from tests.test_collisions_localhost import Handler
    for path in list(Handler.pages):
        Handler.pages[quote(path, safe="/")] = Handler.pages[path]


@pytest.mark.parametrize("markdown_only", [True, False], ids=["md-only", "default"])
@pytest.mark.parametrize("kind", ["ascii", "emoji", "mixed", "folder"])
def test_long_names_are_written_and_stable(server, tmp_path, kind, markdown_only):
    if kind == "folder":
        stem = "/" + "d" * 300 + "/page"
    else:
        stem = "/" + LONG_NAMES[kind]
    site = Site()
    site.pages = {stem: ("Plain page", "BODYPLAIN"), stem + ".html": ("Html page", "BODYHTML")}
    site.order = list(site.pages)
    serve(site)
    config, summary = run(server, tmp_path, markdown_only=markdown_only)
    assert summary["failed"] == []
    previous = check(server, site, config, summary)
    urls = {u for u, _ in md_files(config).values()}
    assert len(urls & {server + quote(p, safe="/") for p in site.pages}
               | urls & {server + p for p in site.pages}) == 2
    root = os.path.dirname(os.path.dirname(config.markdown_dir))
    for name in all_names(root):
        assert len(name.encode("utf-8")) <= MAX_NAME_BYTES, name
    # Second run: nothing fails, nothing moves
    config, summary = run(server, tmp_path, markdown_only=markdown_only)
    assert summary["failed"] == []
    check(server, site, config, summary, previous)


def test_fit_path_cuts_by_bytes_on_character_boundaries():
    name = "\U0001F600" * 100 + ".html"
    fitted = os.path.basename(fit_path("/x/" + name))
    assert len(fitted.encode("utf-8")) <= MAX_NAME_BYTES
    fitted.encode("utf-8").decode("utf-8")       # no half character
    assert fitted.endswith(".html")
    assert fit_path("/x/short.md") == "/x/short.md"
    assert fit_path("/x/" + name) == fit_path("/x/" + name)
    other = "\U0001F600" * 99 + "a.html"
    assert fit_path("/x/" + other) != fit_path("/x/" + name)


def test_suffixed_path_fits():
    path = "/x/" + "a" * 250 + ".html"
    out = os.path.basename(suffixed_path(path, "https://example.com/" + "a" * 250 + ".html"))
    assert len(out.encode("utf-8")) <= MAX_NAME_BYTES
    assert out.endswith(".html") and "-" in out
    assert suffixed_path("/x/short.md", "u") == f"/x/short-{url_suffix('u')}.md"
