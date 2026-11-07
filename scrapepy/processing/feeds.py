"""RSS 2.0 and Atom feeds read as content: one FeedEntry per item or entry.

Discovery (processing/discovery.py) reads feeds only for their dates; this
module keeps each entry's title, link, date, author and body so feed mode
can write one Markdown file per entry.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html.entities import name2codepoint
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..convert.html_prep import safe_urljoin
from ..convert.recipe import clean_text

# Entries whose body has fewer words than this, or that end like a cut-off
# excerpt, are counted as teasers in the run summary.
TEASER_MAX_WORDS = 100

_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_RE_NAMED_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_RE_XML_ENCODING = re.compile(rb"""^\s*<\?xml[^>]*encoding=["']([A-Za-z0-9._-]+)["']""")
_RE_URL_PATH_FEED = re.compile(
    r"(?:^|/)(?:rss|feed|feeds|atom)(?:\.xml|\.rss|\.atom)?/?$"
    r"|(?:^|/)(?:rss|atom|feed|index)\.xml$|\.(?:rss|atom)$",
    re.IGNORECASE,
)
_RE_TEASER_END = re.compile(
    r"(?:…|\.\.\.|\[…\]|\[\.\.\.\]|read more|continue reading|"
    r"read the full (?:post|article|story|recipe)|the post .{1,200} appeared first on .{1,200})"
    r"[\s\W]*$",
    re.IGNORECASE,
)


@dataclass
class FeedEntry:
    title: str = ""
    link: str = ""          # absolute URL, or "" when the entry has none
    date: str = ""         # ISO 8601 as precise as published: date only, naive or with offset
    author: str = ""
    body_html: str = ""
    body_source: str = ""   # "content", "summary" or "" (no body)


@dataclass
class Feed:
    kind: str               # "rss" or "atom"
    title: str = ""
    entries: list[FeedEntry] = field(default_factory=list)


def looks_like_feed_url(url: str) -> bool:
    """Whether a start URL names a feed by its path (/feed, /rss.xml, ...)
    or its query (?feed=rss2, ?format=atom)."""
    parsed = urlparse(url)
    if _RE_URL_PATH_FEED.search(parsed.path or ""):
        return not parsed.path.lower().endswith("sitemap.xml")
    query = parsed.query.lower()
    return bool(re.search(r"(?:^|&)(?:feed|format|type)=(?:rss2?|atom)(?:&|$)", query))


def feed_kind(text: str) -> str | None:
    """"rss" or "atom" when the start of the text is a feed document."""
    head = text[:2000].lower()
    if "<rss" in head or "<rdf:rdf" in head or "<channel" in head:
        return "rss"
    if re.search(r"<feed[\s>]", head):
        return "atom"
    return None


def decode_feed(data: bytes, http_encoding: str | None = None) -> str:
    """Bytes to text, by the XML declaration, then the HTTP charset, then UTF-8."""
    m = _RE_XML_ENCODING.match(data[:200])
    for encoding in ((m.group(1).decode("ascii") if m else None), http_encoding, "utf-8"):
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


_RE_DOCTYPE = re.compile(r"<!DOCTYPE\b[^\[>]*(?:\[.*?\]\s*)?>", re.IGNORECASE | re.DOTALL)
_RE_ENTITY_DECL = re.compile(r"<!ENTITY\b[^>]*>", re.IGNORECASE)


def neutralise_doctype(text: str) -> str:
    """Remove the DOCTYPE and any internal subset, so no entity is ever
    declared: nothing is expanded (no billion laughs), no DTD or other URL
    is fetched and no local file is read. Entities the document then uses
    stay unresolved and show as written."""
    text = _RE_DOCTYPE.sub("", text)
    # A stray declaration outside a DOCTYPE is removed too
    return _RE_ENTITY_DECL.sub("", text)


def _fix_entities(text: str) -> str:
    """HTML named entities (&nbsp;, &eacute;) are not defined in XML and make
    the parser drop text; turn them into numeric references first. Any other
    unknown entity is escaped, so it shows as written instead of breaking
    the parse."""
    def sub(m):
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        code = name2codepoint.get(name)
        return f"&#{code};" if code else f"&amp;{name};"
    return _RE_NAMED_ENTITY.sub(sub, text)


def _text(tag) -> str:
    """Plain text of an element: entities decoded, any HTML tags removed."""
    if tag is None:
        return ""
    return clean_text(tag.get_text())


def _raw(tag) -> str:
    """An element's text as written, for URLs and dates."""
    return tag.get_text().strip() if tag is not None else ""


def _first(parent, *names):
    for name in names:
        tag = parent.find(name, recursive=False)
        if tag is not None:
            return tag
    return None


def _rss_author(item) -> str:
    tag = _first(item, "dc:creator", "creator", "author")
    text = _text(tag)
    # RSS <author> is "email (Name)"; keep the name
    m = re.match(r"^\S+@\S+\s*\((.+)\)$", text)
    if m:
        return m.group(1).strip()
    if re.fullmatch(r"\S+@\S+", text):
        return ""
    return text


def _atom_body(tag) -> str:
    """HTML for an Atom content or summary element, by its type."""
    if tag is None:
        return ""
    kind = (tag.get("type") or "text").lower()
    if kind == "xhtml":
        return tag.decode_contents()
    text = tag.get_text()
    if kind in ("html", "text/html"):
        return text
    return "".join(f"<p>{html_lib.escape(p.strip())}</p>"
                   for p in re.split(r"\n\s*\n", text) if p.strip())


def _base_of(tag, fallback: str) -> str:
    """xml:base of the tag or its nearest ancestor, resolved against fallback."""
    bases = []
    node = tag
    while node is not None and getattr(node, "attrs", None) is not None:
        base = node.attrs.get("xml:base")
        if base:
            bases.append(base)
        node = node.parent
    result = fallback
    for base in reversed(bases):
        result = safe_urljoin(result, base.strip()) or result
    return result


def _date(text: str) -> str:
    """ISO 8601 for a feed date, no more precise than published: a date
    stays a date, a time without an offset stays without one. "" when the
    value cannot be read."""
    text = (text or "").strip()
    if not text:
        return ""
    # Date only: 2026-11-03, 20261103, "Tue, 03 Nov 2026", "3 Nov 2026"
    if re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", text):
        digits = text.replace("-", "")
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
        except ValueError:
            return ""
    m = re.fullmatch(r"(?:[A-Za-z]{3,9},?\s+)?(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2).title()} {m.group(3)}",
                                     "%d %b %Y").date().isoformat()
        except ValueError:
            return ""
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).isoformat()   # RSS (RFC 822) dates
    except (TypeError, ValueError, IndexError):
        return ""


def _is_http(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https") and bool(urlparse(url).netloc)


def parse_feed(text: str, feed_url: str) -> Feed | None:
    """Parse RSS 2.0 or Atom text. None when it is neither."""
    kind = feed_kind(text)
    if kind is None:
        return None
    soup = BeautifulSoup(_fix_entities(neutralise_doctype(text)), "lxml-xml")
    if kind == "rss":
        return _parse_rss_entries(soup, feed_url)
    return _parse_atom_entries(soup, feed_url)


def _parse_rss_entries(soup, feed_url: str) -> Feed:
    channel = soup.find("channel")
    feed = Feed(kind="rss", title=_text(_first(channel, "title")) if channel else "")
    for item in soup.find_all("item"):
        entry = FeedEntry(title=_text(_first(item, "title")))
        base = _base_of(item, feed_url)
        link = _raw(_first(item, "link"))
        if not link:
            guid = _first(item, "guid")
            if guid is not None and (guid.get("isPermaLink") or "true").lower() != "false":
                link = _raw(guid)
        if link:
            joined = safe_urljoin(base, link)
            entry.link = joined if joined and _is_http(joined) else ""
        entry.date = _date(_raw(_first(item, "pubDate"))) or _date(
            _raw(_first(item, "dc:date", "date")))
        entry.author = _rss_author(item)
        content = _first(item, "content:encoded", "encoded")
        summary = _first(item, "description")
        if content is not None and content.get_text().strip():
            entry.body_html, entry.body_source = content.get_text(), "content"
        elif summary is not None and summary.get_text().strip():
            entry.body_html, entry.body_source = summary.get_text(), "summary"
        feed.entries.append(entry)
    return feed


def _parse_atom_entries(soup, feed_url: str) -> Feed:
    root = soup.find("feed")
    feed = Feed(kind="atom", title=_text(_first(root, "title")) if root else "")
    for item in soup.find_all("entry"):
        entry = FeedEntry(title=_text(_first(item, "title")))
        base = _base_of(item, feed_url)
        links = item.find_all("link", recursive=False)
        chosen = next((l for l in links if (l.get("rel") or "alternate") == "alternate"
                       and l.get("href")), None)
        href = (chosen.get("href") or "").strip() if chosen else ""
        if not href:
            entry_id = _raw(_first(item, "id"))
            href = entry_id if _is_http(entry_id) else ""
        if href:
            joined = safe_urljoin(_base_of(chosen, base) if chosen else base, href)
            entry.link = joined if joined and _is_http(joined) else ""
        entry.date = _date(_raw(_first(item, "published"))) or _date(
            _raw(_first(item, "updated")))
        author = _first(item, "author")
        if author is None and root is not None:
            author = _first(root, "author")
        entry.author = _text(_first(author, "name")) if author is not None else ""
        content = _first(item, "content")
        summary = _first(item, "summary")
        if content is not None and not content.get("src") and _atom_body(content).strip():
            entry.body_html, entry.body_source = _atom_body(content), "content"
        elif summary is not None and _atom_body(summary).strip():
            entry.body_html, entry.body_source = _atom_body(summary), "summary"
        feed.entries.append(entry)
    return feed


def is_teaser(entry: FeedEntry, word_count: int) -> bool:
    """A body that is only an excerpt: no body, fewer than TEASER_MAX_WORDS
    words, or text that ends like a cut-off excerpt ("...", "Read more")."""
    if not entry.body_source:
        return True
    if word_count < TEASER_MAX_WORDS:
        return True
    text = BeautifulSoup(entry.body_html, "html.parser").get_text(" ")
    return bool(_RE_TEASER_END.search(text.strip()))
