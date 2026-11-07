"""RSS 2.0 and Atom feeds as content, through a real run against a server
on 127.0.0.1 (bound to 127.0.0.1 only). Every feed here is invented."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.processing.feeds import looks_like_feed_url, parse_feed
from scrapepy.scraper import run_scraper

LONG = " ".join(["Children can measure, stir and taste at every stage of this one."] * 12)

RSS_FULL = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
  <title>Little Cooks Blog</title>
  <link>/</link>
  <item>
    <title>Sticky Date Flapjacks</title>
    <link>/2026/03/sticky-date-flapjacks/</link>
    <pubDate>Sat, 14 Mar 2026 09:30:00 +0000</pubDate>
    <dc:creator><![CDATA[Robin Example]]></dc:creator>
    <description><![CDATA[<p>A short teaser that should not be used&#8230;</p>]]></description>
    <content:encoded><![CDATA[<h2>Why we love them</h2><p>{LONG}</p>
      <p>See <a href="/2026/02/oat-basics/">oat basics</a>.</p>
      <script>alert(1)</script>]]></content:encoded>
  </item>
  <item>
    <title>Fish &amp; Chip Traybake &nbsp;for Four</title>
    <link>https://feeds.example/2026/03/traybake</link>
    <pubDate>Fri, 13 Mar 2026 18:00:00 GMT</pubDate>
    <author>cook@example.org (Alex Example)</author>
    <description>&lt;p&gt;Crispy fish &amp;amp; chips on one tray &amp;#8211; ready in 30 minutes. &lt;a href="/2026/03/traybake"&gt;Read more&lt;/a&gt;&lt;/p&gt;</description>
  </item>
</channel>
</rss>
"""

ATOM = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:base="/blog/">
  <title type="text">Tiny Chefs</title>
  <author><name>Feed Author</name></author>
  <entry>
    <title type="html">Pea &amp;amp; Mint Dip</title>
    <link rel="alternate" type="text/html" href="pea-mint-dip"/>
    <link rel="edit" href="https://elsewhere.example/edit/1"/>
    <id>tag:tiny.example,2026:1</id>
    <published>2026-04-02T08:00:00+01:00</published>
    <updated>2026-04-03T08:00:00+01:00</updated>
    <author><name>Morgan Example</name></author>
    <content type="html">&lt;p&gt;{LONG}&lt;/p&gt;&lt;p&gt;&lt;img src="img/dip.jpg" alt="dip"&gt;&lt;/p&gt;</content>
  </entry>
  <entry>
    <title>Frozen Yoghurt Drops</title>
    <link href="https://tiny.example/blog/frozen-drops"/>
    <id>tag:tiny.example,2026:2</id>
    <updated>2026-04-01T10:00:00Z</updated>
    <summary type="text">Dot yoghurt onto a tray and freeze.

Pop them out after an hour.</summary>
  </entry>
  <entry>
    <title>Rainbow Wraps</title>
    <link href="rainbow-wraps"/>
    <id>tag:tiny.example,2026:3</id>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>{LONG}</p><ul><li>Red pepper</li><li>Grated carrot</li></ul></div></content>
  </entry>
</feed>
"""

RSS_ODD = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Odd</title>
  <item><title>No link here</title><description>Nothing to point at.</description></item>
  <item><title>Undated</title><link>/undated</link><description>No date at all.</description></item>
  <item><title>Undated again</title><link>/undated</link><description>Same link.</description></item>
  <item><title>Query one</title><link>/?p=101</link><description>First by id.</description></item>
  <item><title>Query two</title><link>/?p=102</link><description>Second by id.</description></item>
  <item><title>Guid only</title><guid>http://HOST/guid-post</guid><description>From guid.</description></item>
  <item><title>Bad date</title><link>/bad-date</link><pubDate>someday soon</pubDate><description>x</description></item>
</channel></rss>
"""


def many_items(n):
    items = "".join(
        f"<item><title>Recipe {i}</title><link>/r/{i}</link>"
        f"<pubDate>Mon, 02 Mar 2026 10:00:00 +0000</pubDate>"
        f"<description>Entry number {i}.</description></item>"
        for i in range(n))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Many</title>{items}</channel></rss>'


class Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    log: list = []

    def do_GET(self):
        Handler.log.append(self.path)
        route = Handler.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        ctype, body = route
        host = f"127.0.0.1:{self.server.server_address[1]}"
        data = body.replace("HOST", host).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.routes = {}
    Handler.log = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def run(url, tmp_path, **kw):
    kw.setdefault("wait", 0)
    config = ScrapeConfig(url=url, output_root=str(tmp_path), **kw)
    code = asyncio.run(run_scraper(config))
    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    return config, code, summary


def read(path):
    text = Path(path).read_text(encoding="utf-8")
    end = text.index("\n---\n", 3)
    return yaml.safe_load(text[4:end]), text[end + 5:].removeprefix("\n")


def files_by_url(config):
    out = {}
    for md in Path(config.markdown_dir).rglob("*.md"):
        front, body = read(md)
        out[front["url"]] = (front, body, md)
    return out


XML = "application/rss+xml; charset=utf-8"


def test_rss_with_and_without_content_encoded(server, tmp_path):
    Handler.routes = {"/feed/": (XML, RSS_FULL)}
    config, code, summary = run(server + "/feed/", tmp_path)
    assert code == 0
    files = files_by_url(config)
    url = server + "/2026/03/sticky-date-flapjacks/"
    front, body, path = files[url]
    assert front == {
        "title": "Sticky Date Flapjacks", "url": url,
        "date": "2026-03-14T09:30:00+00:00", "date_source": "FEED",
        "page_type": "feed_entry", "author": "Robin Example",
        "word_count": front["word_count"], "domain": config.domain,
        "scraped_at": front["scraped_at"], "content_hash": front["content_hash"],
    }
    assert body.startswith("# Sticky Date Flapjacks\n\n**14 Mar 2026, 09:30**\n\n## Why we love them\n")
    assert f"[oat basics]({server}/2026/02/oat-basics/)" in body
    assert "teaser" not in body and "alert(1)" not in body
    # The usual file-name rule: numeric segments move into the name
    assert path == Path(config.markdown_dir) / "sticky-date-flapjacks-2026-03.md"

    # An entry on another host, with only a description (a teaser)
    other = "https://feeds.example/2026/03/traybake"
    front, body, path = files[other]
    assert front["title"] == "Fish & Chip Traybake for Four"
    assert front["author"] == "Alex Example"
    assert front["date"] == "2026-03-13T18:00:00+00:00"
    assert "Crispy fish & chips on one tray – ready in 30 minutes." in body
    assert path == Path(config.markdown_dir) / "feeds_example/traybake-2026-03.md"

    assert summary["counts"]["fetched"] == 1
    assert summary["counts"]["new"] == 2
    assert summary["options"]["feed"] is False   # detected from the URL
    feed = summary["feed"]
    assert feed["format"] == "rss" and feed["title"] == "Little Cooks Blog"
    assert feed["entries_in_feed"] == 2 and feed["entries_written"] == 2
    assert feed["teaser_entries"] == 1 and feed["teaser_urls"] == [other]
    assert feed["entries_skipped"] == [] and feed["entries_over_cap"] == 0
    assert sorted(summary["new_markdown"]) == sorted(
        str(p.relative_to(tmp_path)) for p in Path(config.markdown_dir).rglob("*.md"))
    # Only robots.txt and the feed itself were requested
    assert Handler.log == ["/robots.txt", "/feed/"]


def test_atom_content_summary_xhtml_and_relative_links(server, tmp_path):
    Handler.routes = {"/blog/atom.xml": ("application/atom+xml", ATOM)}
    config, code, summary = run(server + "/blog/atom.xml", tmp_path)
    assert code == 0
    files = files_by_url(config)

    front, body, _ = files[server + "/blog/pea-mint-dip"]
    assert front["title"] == "Pea & Mint Dip"
    assert front["date"] == "2026-04-02T08:00:00+01:00"   # published wins over updated
    assert front["author"] == "Morgan Example"
    assert f"![dip]({server}/blog/img/dip.jpg)" in body

    front, body, _ = files["https://tiny.example/blog/frozen-drops"]
    assert front["date"] == "2026-04-01T10:00:00+00:00"   # updated when no published
    assert front["author"] == "Feed Author"               # feed-level author
    assert body.endswith("Dot yoghurt onto a tray and freeze.\n\nPop them out after an hour.\n")

    front, body, _ = files[server + "/blog/rainbow-wraps"]
    assert "date" not in front and "date_source" not in front
    assert "- Red pepper\n- Grated carrot" in body

    feed = summary["feed"]
    assert feed["format"] == "atom" and feed["title"] == "Tiny Chefs"
    assert feed["entries_written"] == 3
    assert feed["teaser_urls"] == ["https://tiny.example/blog/frozen-drops"]


def test_missing_links_dates_duplicates_and_queries(server, tmp_path):
    Handler.routes = {"/odd.rss": (XML, RSS_ODD)}
    config, code, summary = run(server + "/odd.rss", tmp_path)
    assert code == 0
    files = files_by_url(config)
    feed = summary["feed"]
    assert feed["entries_in_feed"] == 7
    assert feed["entries_skipped"] == [
        {"title": "No link here", "reason": "no link"},
        {"title": "Undated again", "url": server + "/undated", "reason": "duplicate link"},
    ]
    assert feed["entries_written"] == 5
    front, body, _ = files[server + "/undated"]
    assert "date" not in front and front["title"] == "Undated"
    assert "date" not in files[server + "/bad-date"][0]
    one, two = files[server + "/?p=101"], files[server + "/?p=102"]
    assert one[2].name == "index-p-101.md" and two[2].name == "index-p-102.md"
    assert files[server + "/guid-post"][0]["title"] == "Guid only"


def test_cap_defaults_to_50_and_follows_max_articles(server, tmp_path):
    Handler.routes = {"/rss.xml": (XML, many_items(60))}
    config, code, summary = run(server + "/rss.xml", tmp_path / "a")
    assert summary["feed"]["entries_written"] == 50
    assert summary["feed"]["entries_over_cap"] == 10
    assert len(summary["new_markdown"]) == 50
    config, code, summary = run(server + "/rss.xml", tmp_path / "b", max_articles=3)
    assert summary["feed"]["entries_written"] == 3
    assert summary["feed"]["entries_over_cap"] == 57
    assert sorted(files_by_url(config)) == sorted(server + f"/r/{i}" for i in range(3))


def test_second_run_reports_unchanged(server, tmp_path):
    Handler.routes = {"/feed": (XML, RSS_FULL)}
    run(server + "/feed", tmp_path)
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 0
    assert summary["counts"]["unchanged"] == 2 and summary["counts"]["new"] == 0
    assert summary["feed"]["entries_unchanged"] == 2
    assert summary["feed"]["entries_written"] == 0


def test_robots_txt_blocks_the_feed(server, tmp_path):
    Handler.routes = {"/robots.txt": ("text/plain", "User-agent: *\nDisallow: /feed\n"),
                      "/feed": (XML, RSS_FULL)}
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 1
    assert summary["blocked_by_robots"] == [server + "/feed"]
    assert summary["counts"]["fetched"] == 0
    assert "/feed" not in Handler.log


def test_explicit_feed_flag_on_html_fails(server, tmp_path):
    Handler.routes = {"/page": ("text/html", "<html><body><p>Hello</p></body></html>")}
    config, code, summary = run(server + "/page", tmp_path, feed=True)
    assert code == 1
    assert summary["failed"] == [{"url": server + "/page", "reason": "not an RSS or Atom feed"}]
    assert summary["options"]["feed"] is True


def test_feed_looking_url_that_is_html_is_crawled(server, tmp_path):
    words = "Plenty of readable words appear here so the page passes the length check. " * 6
    Handler.routes = {"/feed": ("text/html", f"<html><head><title>Feed page</title></head>"
                                f"<body><article><h1>Feed page</h1><p>{words}</p></article>"
                                f"</body></html>")}
    config, code, summary = run(server + "/feed", tmp_path, markdown_only=True)
    assert code == 0
    assert "feed" not in summary
    assert summary["counts"]["fetched"] >= 1


@pytest.mark.parametrize("url,expected", [
    ("https://a.example/feed", True), ("https://a.example/feed/", True),
    ("https://a.example/blog/rss.xml", True), ("https://a.example/atom.xml", True),
    ("https://a.example/news.rss", True), ("https://a.example/?feed=rss2", True),
    ("https://a.example/feeds/posts/default?format=atom", True),
    ("https://a.example/sitemap.xml", False), ("https://a.example/feeding-toddlers", False),
    ("https://a.example/recipes/", False),
])
def test_looks_like_feed_url(url, expected):
    assert looks_like_feed_url(url) is expected


def test_parse_feed_rejects_html_and_handles_cdata_entities():
    assert parse_feed("<html><body>hi</body></html>", "https://a.example/") is None
    feed = parse_feed(RSS_FULL, "https://a.example/feed")
    assert [e.title for e in feed.entries] == ["Sticky Date Flapjacks",
                                               "Fish & Chip Traybake for Four"]
    assert feed.entries[0].body_source == "content"
    assert feed.entries[1].body_source == "summary"


def test_cli_feed_flag():
    assert parse_args(["https://a.example/x", "--feed"]).feed is True
    for bad in (["--no-markdown"], ["-j"], ["-1"], ["--swagger"], ["-d"]):
        with pytest.raises(SystemExit):
            parse_args(["https://a.example/x", "--feed", *bad])
