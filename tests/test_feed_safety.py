"""Feed dates kept as published, DOCTYPEs neutralised (nothing read or
fetched), lost feeds reported, stable file names. Server on 127.0.0.1 only;
every feed is invented."""

import time

import pytest

from scrapepy.config import ScrapeConfig
from scrapepy.output.run_summary import RunStats, build_summary
from scrapepy.processing.feeds import parse_feed

from .test_feed import Handler, files_by_url, run, server  # noqa: F401 (fixture)

XML = "application/xml"


def atom(*entries):
    body = "".join(
        f'<entry><title>{t}</title><link href="/{slug}"/><id>tag:x,2026:{slug}</id>'
        f"<published>{date}</published><content type=\"html\">Body of {t}.</content></entry>"
        for t, slug, date in entries)
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>D</title>{body}</feed>'


def test_dates_are_kept_as_published(server, tmp_path):
    Handler.routes = {"/atom.xml": (XML, atom(
        ("Date only", "date-only", "2026-10-03"),
        ("With offset", "offset", "2026-10-03T10:30:00+02:00"),
        ("Naive", "naive", "2026-10-03T10:30:00"),
        ("Zulu", "zulu", "2026-10-03T10:30:00Z"),
    ))}
    config, code, summary = run(server + "/atom.xml", tmp_path)
    files = files_by_url(config)
    front, body, _ = files[server + "/date-only"]
    assert front["date"] == "2026-10-03"
    assert "**3 Oct 2026**" in body and "00:00" not in body
    front, body, _ = files[server + "/offset"]
    assert front["date"] == "2026-10-03T10:30:00+02:00"
    assert "**3 Oct 2026, 10:30**" in body
    front, body, _ = files[server + "/naive"]
    assert front["date"] == "2026-10-03T10:30:00"
    assert "**3 Oct 2026, 10:30**" in body
    assert files[server + "/zulu"][0]["date"] == "2026-10-03T10:30:00+00:00"


def test_rss_date_keeps_its_offset():
    feed = parse_feed('<rss version="2.0"><channel><item><title>a</title><link>https://a.example/a</link>'
                      "<pubDate>Sat, 03 Oct 2026 10:30:00 +0100</pubDate></item></channel></rss>",
                      "https://a.example/feed")
    assert feed.entries[0].date == "2026-10-03T10:30:00+01:00"


def rss_with_doctype(doctype, title="&a;"):
    return (f'<?xml version="1.0"?>\n{doctype}\n<rss version="2.0"><channel><title>T</title>'
            f"<item><title>{title}</title><link>/one</link><description>First entry body."
            f"</description></item>"
            f"<item><title>Two</title><link>/two</link><description>Second.</description></item>"
            f"</channel></rss>")


def test_harmless_internal_subset_still_parses(server, tmp_path):
    Handler.routes = {"/feed": (XML, rss_with_doctype('<!DOCTYPE rss [<!ENTITY a "AAA">]>'))}
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 0
    assert summary["feed"]["entries_written"] == 2
    files = files_by_url(config)
    # The entity is left unresolved, shown as written
    assert files[server + "/one"][0]["title"] == "&a;"


def test_external_entity_never_reads_a_local_file(server, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("MARKER-LOCAL-FILE-CONTENT")
    doctype = f'<!DOCTYPE rss [<!ENTITY a SYSTEM "file://{secret}">]>'
    Handler.routes = {"/feed": (XML, rss_with_doctype(doctype))}
    config, code, summary = run(server + "/feed", tmp_path / "out")
    assert code == 0 and summary["feed"]["entries_written"] == 2
    for md in (tmp_path / "out").rglob("*"):
        if md.is_file():
            assert "MARKER-LOCAL-FILE-CONTENT" not in md.read_text(errors="replace")


def test_billion_laughs_stays_small(server, tmp_path):
    lines = ['<!ENTITY lol "lol">']
    for i in range(1, 10):
        prev = "lol" if i == 1 else f"lol{i - 1}"
        lines.append(f'<!ENTITY lol{i} "' + f"&{prev};" * 10 + '">')
    doctype = "<!DOCTYPE rss [" + "".join(lines) + "]>"
    Handler.routes = {"/feed": (XML, rss_with_doctype(doctype, title="&lol9;"))}
    started = time.monotonic()
    config, code, summary = run(server + "/feed", tmp_path)
    assert time.monotonic() - started < 20
    assert code == 0 and summary["feed"]["entries_written"] == 2
    front, body, path = files_by_url(config)[server + "/one"]
    assert front["title"] == "&lol9;"
    assert path.stat().st_size < 5000


def test_remote_dtd_is_never_fetched(server, tmp_path):
    doctype = ('<!DOCTYPE rss SYSTEM "SERVER/evil.dtd" '
               '[<!ENTITY % ext SYSTEM "SERVER/param.dtd"> %ext;]>')
    Handler.routes = {"/feed": (XML, rss_with_doctype(doctype.replace("SERVER", server))),
                      "/evil.dtd": ("text/plain", '<!ENTITY a "FETCHED">'),
                      "/param.dtd": ("text/plain", '<!ENTITY a "FETCHED">')}
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 0
    assert "/evil.dtd" not in Handler.log and "/param.dtd" not in Handler.log
    assert files_by_url(config)[server + "/one"][0]["title"] == "&a;"


def test_feed_with_entries_that_cannot_be_read_fails(server, tmp_path):
    broken = ('<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
              "<![CDATA[<item><title>Lost</title><link>/lost</link></item>]]></channel></rss>")
    Handler.routes = {"/feed": (XML, broken)}
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 1
    assert summary["feed"]["error"] == "the feed has entries but none could be read"
    assert summary["failed"] == [{"url": server + "/feed",
                                  "reason": "the feed has entries but none could be read"}]


def test_empty_feed_is_not_an_error(server, tmp_path):
    Handler.routes = {"/feed": (XML, '<rss version="2.0"><channel><title>T</title></channel></rss>')}
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 0
    assert summary["feed"]["entries_in_feed"] == 0 and "error" not in summary["feed"]


def colliding(order):
    items = {"a": "<item><title>Plain</title><link>/post</link><description>One.</description></item>",
             "b": "<item><title>Slash</title><link>/post/</link><description>Two.</description></item>"}
    return ('<rss version="2.0"><channel><title>T</title>'
            + "".join(items[k] for k in order) + "</channel></rss>")


def test_colliding_names_are_stable_when_order_changes(server, tmp_path):
    Handler.routes = {"/feed": (XML, colliding("ab"))}
    config, _, _ = run(server + "/feed", tmp_path / "one")
    first = {u: p.name for u, (_f, _b, p) in files_by_url(config).items()}
    Handler.routes = {"/feed": (XML, colliding("ba"))}
    config, _, _ = run(server + "/feed", tmp_path / "two")
    second = {u: p.name for u, (_f, _b, p) in files_by_url(config).items()}
    assert first == second
    assert len(set(first.values())) == 2
    assert all(name.startswith("post-") and name.endswith(".md") for name in first.values())


def test_feed_option_only_written_when_set(tmp_path):
    plain = ScrapeConfig(url="https://a.example/", output_root=str(tmp_path))
    assert "feed" not in build_summary(plain, RunStats())["options"]
    flagged = ScrapeConfig(url="https://a.example/x", output_root=str(tmp_path), feed=True)
    assert build_summary(flagged, RunStats())["options"]["feed"] is True


@pytest.mark.parametrize("raw,expected", [
    ("20261103", "2026-11-03"),
    ("Tue, 03 Nov 2026", "2026-11-03"),
    ("3 Nov 2026", "2026-11-03"),
    ("2026-11-03", "2026-11-03"),
    ("20261340", ""),
    ("Tue, 31 Feb 2026", ""),
    ("2026-11-03T10:30:00+02:00", "2026-11-03T10:30:00+02:00"),
    ("Tue, 03 Nov 2026 10:30:00 +0000", "2026-11-03T10:30:00+00:00"),
])
def test_date_only_forms_stay_dates(raw, expected):
    feed = parse_feed(f'<rss version="2.0"><channel><item><title>a</title>'
                      f"<link>https://a.example/a</link><pubDate>{raw}</pubDate></item></channel></rss>",
                      "https://a.example/feed")
    assert feed.entries[0].date == expected


def test_basic_date_has_no_invented_time(server, tmp_path):
    Handler.routes = {"/feed": (XML, '<rss version="2.0"><channel><title>T</title><item>'
                                '<title>A</title><link>/a</link><pubDate>20261103</pubDate>'
                                "<description>Body.</description></item></channel></rss>")}
    config, code, summary = run(server + "/feed", tmp_path)
    front, body, _ = files_by_url(config)[server + "/a"]
    assert front["date"] == "2026-11-03"
    assert "**3 Nov 2026**" in body and "00:00" not in body


@pytest.mark.parametrize("xml,count", [
    ('<rss version="2.0"><channel><title>Bad</title>'
     "<item><title>One</title><description>No link.</description></item>"
     "<item><title>Two</title><description>No link either.</description></item>"
     "</channel></rss>", 2),
    ('<rss version="2.0"><channel><title>Cut</title><item><title>Truncated', 1),
])
def test_feed_where_no_entry_can_be_written_fails(server, tmp_path, xml, count):
    Handler.routes = {"/feed": (XML, xml)}
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 1
    reason = "no entry could be written (no link)"
    assert summary["feed"]["error"] == reason
    assert summary["failed"] == [{"url": server + "/feed", "reason": reason}]
    assert len(summary["feed"]["entries_skipped"]) == count


def test_partly_written_feed_succeeds_and_lists_skips(server, tmp_path):
    Handler.routes = {"/feed": (XML, '<rss version="2.0"><channel><title>Mixed</title>'
                                "<item><title>Good</title><link>/good</link><description>Fine."
                                "</description></item>"
                                "<item><title>Linkless</title><description>x</description></item>"
                                "</channel></rss>")}
    config, code, summary = run(server + "/feed", tmp_path)
    assert code == 0
    assert "error" not in summary["feed"]
    assert summary["feed"]["entries_written"] == 1
    assert summary["feed"]["entries_skipped"] == [{"title": "Linkless", "reason": "no link"}]
