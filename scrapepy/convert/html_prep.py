"""HTML clean-up before conversion and Markdown clean-up after it.

These are the pieces that decide how well the Markdown indexes: one H1,
no skipped heading levels, headings that are not wrapped in links, absolute
links in markdown-only mode, fenced code with its language, and the page's
canonical URL.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "section", "article",
               "ul", "ol", "li", "table", "blockquote", "figure", "header", "footer"}
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "#")
_RE_LANG_CLASS = re.compile(r"^(?:language|lang|highlight|brush)-?:?([A-Za-z0-9_+#.-]+)$")
# A heading, optionally inside a list item ("- #### Card title")
_RE_HEADING = re.compile(r"^(\s*(?:[-*+]|\d{1,9}[.)])\s+)?(#{1,6})(\s+.*)$")
_RE_FENCE = re.compile(r"^\s*(```|~~~)")


def clean_title(text: str | None) -> str:
    """Collapse whitespace (titles often carry newlines and indentation)."""
    return " ".join((text or "").split())


def canonical_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """The page's <link rel="canonical">, made absolute, when it is an http(s) URL."""
    for link in soup.find_all("link", href=True):
        rels = link.get("rel") or []
        if isinstance(rels, str):
            rels = rels.split()
        if "canonical" in (r.lower() for r in rels):
            url = urljoin(page_url, link["href"].strip())
            if urlparse(url).scheme in ("http", "https") and urlparse(url).netloc:
                return url.split("#", 1)[0]
    return None


_DEFAULT_PORTS = {"http": 80, "https": 443}


def same_site(a: str, b: str) -> bool:
    """Same scheme, host and port (a default port matches no port).

    A leading www. on the host is ignored.
    """
    def origin(u):
        parsed = urlparse(u)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower().removeprefix("www.")
        try:
            port = parsed.port or _DEFAULT_PORTS.get(scheme)
        except ValueError:
            return None
        return (scheme, host, port) if host else None
    oa = origin(a)
    return oa is not None and oa == origin(b)


def absolutize_links(soup: BeautifulSoup, page_url: str) -> None:
    """Rewrite a[href] and img[src] to absolute URLs, honouring <base href>."""
    base_tag = soup.find("base", href=True)
    base = urljoin(page_url, base_tag["href"]) if base_tag else page_url
    for tag, attr in (("a", "href"), ("img", "src")):
        for el in soup.find_all(tag, **{attr: True}):
            value = el[attr].strip()
            if not value or value.startswith(_SKIP_SCHEMES):
                continue
            el[attr] = urljoin(base, value)


def fix_block_links(soup: BeautifulSoup) -> None:
    """Unwrap <a> elements that wrap block content (card layouts).

    Markdown cannot put a heading inside a link, so "[## Title text...](url)"
    came out. The link moves onto the first heading (or paragraph) inside.
    """
    for a in list(soup.find_all("a", href=True)):
        if not any(isinstance(d, Tag) and d.name in _BLOCK_TAGS for d in a.descendants):
            continue
        target = a.find(_HEADINGS) or a.find("p")
        href = a["href"]
        if target is not None and not target.find("a"):
            inner = soup.new_tag("a", href=href)
            for child in list(target.contents):
                inner.append(child.extract())
            target.append(inner)
        a.unwrap()


_RE_TRUNCATED = re.compile(r"(\.\.\.|\u2026)\s*$")


def expand_truncated_link_text(soup: BeautifulSoup) -> None:
    """Replace "A Light in the ..." with the full title="A Light in the Attic".

    Listing pages often cut link text short and keep the full name in the
    title attribute; the full text is what people search for.
    """
    for a in soup.find_all("a", title=True):
        text = a.get_text(" ", strip=True)
        full = " ".join(a["title"].split())
        if not text or not full or a.find(True):
            continue
        if _RE_TRUNCATED.search(text):
            stem = _RE_TRUNCATED.sub("", text).strip()
            if full.startswith(stem) and len(full) > len(stem):
                a.string = full


def code_language(el) -> str | None:
    """markdownify code_language_callback: read language-xxx style classes."""
    candidates = [el] + [c for c in el.find_all("code", limit=1)]
    for node in candidates:
        for cls in node.get("class") or []:
            m = _RE_LANG_CLASS.match(cls)
            if m and m.group(1).lower() not in ("none", "plaintext", "text", "nohighlight"):
                return m.group(1).lower()
        lang = node.get("data-lang") or node.get("data-language")
        if lang:
            return lang.strip().lower()
    return None


def normalize_headings(md: str) -> str:
    """One H1 (the first heading) and no skipped levels, outside code fences.

    The first heading becomes H1. Any later H1 becomes H2. Each heading goes
    at most one level deeper than the one before it, so H1 then H4 becomes
    H1 then H2. Headings at the start of a list item count too.
    """
    out = []
    in_fence = False
    prev = 0
    for line in md.split("\n"):
        if _RE_FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        m = None if in_fence else _RE_HEADING.match(line)
        if not m:
            out.append(line)
            continue
        if prev == 0:
            level = 1
        else:
            level = max(2, min(len(m.group(2)), prev + 1))
        prev = level
        out.append((m.group(1) or "") + "#" * level + m.group(3))
    return "\n".join(out)


def dominant_container(soup: BeautifulSoup, min_share: float = 0.6):
    """Descend from <body> into the child holding most of the text.

    Stops when no single child holds at least min_share of the words, which
    is where the repeated items of a listing (or the paragraphs of an
    article) sit side by side. Used when no known content selector matches.
    """
    node = soup.body or soup
    total = len(node.get_text(" ", strip=True).split())
    if not total:
        return None
    while True:
        children = [c for c in node.find_all(recursive=False) if isinstance(c, Tag)]
        if not children:
            return node
        counts = [(len(c.get_text(" ", strip=True).split()), c) for c in children]
        words, best = max(counts, key=lambda x: x[0])
        node_words = sum(w for w, _ in counts) or 1
        if words < min_share * node_words:
            return node
        node = best
