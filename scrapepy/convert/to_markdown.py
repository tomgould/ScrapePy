"""Convert scraped HTML files to clean Markdown.

Handles standard HTML, API docs,
Swagger UI, Adobe RoboHelp, and MadCap Flare documentation.
"""

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from .recipe import find_recipes, merge_recipe_markdown, recipe_frontmatter, strip_recipe_text
from .html_prep import (
    absolutize_links,
    canonical_url,
    clean_title,
    code_language,
    dominant_container,
    expand_truncated_link_text,
    first_h1,
    fix_block_links,
    insert_after_h1,
    normalize_headings,
    same_site,
)
import markdownify
import yaml

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False


def _format_visible_date(date_str: str) -> str:
    """Format an ISO date/datetime string for display in article body.

    Returns "27 Feb 2026, 10:30" for datetimes with time,
    or "27 Feb 2026" for date-only values.
    """
    from datetime import datetime as _dt
    try:
        dt = _dt.fromisoformat(date_str)
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and "T" not in date_str:
            return dt.strftime("%-d %b %Y")
        return dt.strftime("%-d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return date_str


_JUNK_SECTION_RE = re.compile(
    r'^#{2,6}\s+(?:'
    r'Most Read|Subscribe\b|Sign up\b|In this article|More (?:News|Stories)|'
    r'Shopping Best Buys?|More About|Related (?:Articles?|Stories|Topics)|'
    r'Trending|Popular|Recommended|You May (?:Also )?Like|'
    r'Also Read|Don\'t Miss|Latest\s+(?:News|Stories|Articles)|'
    r"Editor'?s? Picks?|Sponsored|Top Stories|What to Read Next|"
    r'Promoted Stories|Around the Web|From the Web|More from\b|'
    r'Around (?:Sky|the Site|the BBC)|'
    r'Comments?\s*(?:\d+\s*)?$|Add (?:your |a )?comment|'
    r'Join (?:the |our )(?:conversation|discussion|commenting)|'
    r'Take (?:my|our|the) quiz\b'
    r').*?(?=^#{1,3}\s|\Z)',
    re.MULTILINE | re.IGNORECASE | re.DOTALL
)

# Inline "Read More" blocks: heading + link-only lines (not greedy to EOF)
_READ_MORE_RE = re.compile(
    r'^#{2,3}\s+Read More\s*$\n+'
    r'(?:\s*\[.+?\]\(.+?\).*$\n*)+',
    re.MULTILINE | re.IGNORECASE
)

_JUNK_CLASS_KEYWORDS = (
    'advert', 'ad-slot', 'ad-container', 'ad-wrapper', 'dfp-ad',
    'google-ad', 'gpt-ad', 'ad-unit', 'ad-banner', 'ad-break',
    'newsletter', 'signup', 'sign-up', 'subscribe', 'email-capture',
    'most-read', 'most_read', 'mostread', 'trending', 'top-stories',
    'popular-stories', 'recommended', 'related-articles', 'related-stories',
    'more-stories', 'more-news', 'commercial-slot', 'commercial-container',
    'commercial-wrapper', 'sponsorship',
    'social-share', 'share-buttons', 'social-links',
    'cookie-banner', 'cookie-consent', 'consent-banner',
    'puff', 'dms-puff', 'rotator',
)


# Button labels that are page furniture, not content, even on a button that
# opens or closes something (aria-expanded, aria-controls)
_CHROME_BUTTON_LABELS = frozenset({
    "menu", "main menu", "open menu", "close menu", "toggle menu", "navigation",
    "toggle navigation", "open navigation", "close", "open", "dismiss", "search",
    "submit", "send", "go", "ok", "cancel", "share", "more", "show more",
    "show less", "read more", "see more", "see all", "view all", "load more",
    "expand", "collapse", "expand all", "collapse all", "filter", "filters",
    "sort", "sort by", "options", "settings", "language", "account",
    "my account", "sign in", "log in", "login", "sign up", "register", "cart",
    "basket", "back", "next", "previous", "prev", "play", "pause", "mute",
    "unmute", "copy", "print", "accept", "reject", "accept all", "reject all",
    # Menu and Close in German, French, Spanish, Italian and Dutch
    "menü", "menú", "menù", "schließen", "schliessen", "fermer", "cerrar",
    "chiudi", "sluiten",
})
# Furniture labels by pattern, matched against the whole label so that
# content such as "Next steps after your application" or "Our cookie
# recipes" is kept: share, load or show more, next and previous, slide and
# page counters ("Slide 1 of 3", "3 / 5"), cookie settings and search
_CHROME_BUTTON_RE = re.compile(
    r"(?:share(?:\s+(?:on\s+\w+|this(?:\s+(?:article|page|post|story))?|via\s+\w+))?"
    r"|load\s+more(?:\s+\w+)?"
    r"|(?:show|see|view)\s+(?:\d+\s+)?(?:more|less|all)(?:\s+\w+)?"
    r"|(?:go\s+to\s+)?(?:the\s+)?(?:next|previous|prev)(?:\s+(?:page|slide|item))?"
    r"|(?:page|slide|item|image|photo)\s+\d+(?:\s*(?:of|/)\s*\d+)?"
    r"|\d+\s*(?:of|/)\s*\d+"
    r"|(?:manage\s+)?cookies?(?:\s+(?:settings|preferences|consent|policy))?"
    r"|(?:advanced\s+|open\s+|close\s+|toggle\s+)?search)",
    re.IGNORECASE,
)
_PAGER_CONTAINER_HINT = "pag"
# A <select> with more options than this is a picker (countries, years),
# not a list worth indexing
_MAX_SELECT_OPTIONS = 50
_PICKER_SELECT_HINTS = ("sort", "filter", "lang", "locale", "country", "currency",
                        "per-page", "perpage", "per_page", "search", "jump")
# A select of only numbers is a quantity picker when its name, id or class
# says so; otherwise its numbers can be content (sizes 8, 10, 12, or years)
_QUANTITY_SELECT_HINTS = ("qty", "quantity", "amount", "count")
_PLACEHOLDER_OPTION_RE = re.compile(r"^(?:-+|select|choose|please|pick)\b", re.I)


def _button_label_is_content(btn, text: str) -> bool:
    """Whether a button's text belongs in the Markdown.

    Kept: questions, and labels of buttons that open a panel (accordion and
    FAQ toggles, which carry aria-expanded or aria-controls), unless the label
    is common page furniture ("Menu", "Close", "Share on Facebook", "Load
    more", pager numbers, "Slide 1 of 3"), the button opens a popup menu, or
    the panel it opens is mostly links. Everything else ("Submit", "Add to
    basket") is dropped.
    """
    if not any(c.isalnum() for c in text):
        return False
    if text.endswith("?"):
        return True
    if btn.has_attr("aria-haspopup"):
        return False
    toggles = btn.has_attr("aria-expanded") or btn.has_attr("aria-controls") or any(
        btn.get(attr) == "collapse" for attr in ("data-toggle", "data-bs-toggle"))
    if not toggles:
        return False
    label = text.rstrip(".:")
    if label.lower() in _CHROME_BUTTON_LABELS or _CHROME_BUTTON_RE.fullmatch(label):
        return False
    if label.isdigit() and _in_pager(btn):
        return False
    return not _opens_link_menu(btn)


def _in_pager(btn) -> bool:
    """Whether a button sits in navigation or pagination: a nav element,
    role="navigation", or a class or aria-label containing "pag"."""
    for parent in btn.parents:
        if parent.name == "nav" or parent.get("role") == "navigation":
            return True
        hints = " ".join(parent.get("class", []) + [parent.get("aria-label", "")]).lower()
        if _PAGER_CONTAINER_HINT in hints:
            return True
    return False


def _opens_link_menu(btn) -> bool:
    """Whether the panel a toggle opens is a menu: three or more links that
    hold most of its text (a mega-menu "Products" or "Shop by category").
    The panel is the element named by aria-controls, else the next sibling."""
    panel = None
    target = btn.get("aria-controls")
    if target:
        root = btn
        while root.parent is not None:
            root = root.parent
        panel = root.find(id=target)
    if panel is None:
        panel = btn.find_next_sibling()
    if panel is None:
        return False
    links = panel.find_all("a")
    if len(links) < 3:
        return False
    total = len(panel.get_text(" ", strip=True).split())
    linked = sum(len(a.get_text(" ", strip=True).split()) for a in links)
    if total == 0 or linked < 0.6 * total:
        return False
    # An answer that says something in its own words ("We ship to these
    # countries.") is content even when a list of links follows
    return not _has_sentence(panel, links)


_RE_SENTENCE = re.compile(r"[^.!?]+[.!?]")


def _has_sentence(panel, links) -> bool:
    """Whether the panel's text outside its links holds a sentence of four or
    more words ending in a full stop, question or exclamation mark."""
    linked_text = {id(s) for a in links for s in a.find_all(string=True)}
    own = " ".join(
        str(t) for t in panel.find_all(string=True) if id(t) not in linked_text
    )
    return any(len(m.group().split()) >= 4 for m in _RE_SENTENCE.finditer(own))


def _replace_form_controls(soup) -> None:
    """Keep the text of form controls that carries page content, drop the rest.

    A button inside a heading is unwrapped, so accordion headers stay headings.
    Other buttons keep their text as a paragraph when it is content (see
    _button_label_is_content). A <select> becomes a list of its options,
    without placeholders, unless it is a long picker, a sort, filter or
    language switcher, or a quantity picker (only numbers, and named qty,
    quantity, amount or count). A <textarea> with text becomes a paragraph.
    Inputs and iframes are removed.
    """
    for tag in soup.find_all(["input", "iframe"]):
        tag.decompose()
    for btn in soup.find_all("button"):
        if btn.parent is None:
            continue
        text = " ".join(btn.get_text(" ", strip=True).split())
        if text and btn.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"]):
            btn.unwrap()
        elif _button_label_is_content(btn, text):
            btn.clear()
            btn.append(text)
            btn.name = "p"
            btn.attrs = {}
        else:
            btn.decompose()
    for sel in soup.find_all("select"):
        if sel.parent is None:
            continue
        hints = " ".join([sel.get("name", ""), sel.get("id", "")] + sel.get("class", [])).lower()
        options = []
        for opt in sel.find_all("option"):
            label = " ".join(opt.get_text(" ", strip=True).split())
            if not label or opt.has_attr("disabled") or opt.get("value") == "" \
                    or _PLACEHOLDER_OPTION_RE.match(label):
                continue
            options.append(label)
        if not options or len(options) > _MAX_SELECT_OPTIONS \
                or any(h in hints for h in _PICKER_SELECT_HINTS) \
                or (all(label.isdigit() for label in options)
                    and any(h in hints for h in _QUANTITY_SELECT_HINTS)):
            sel.decompose()
            continue
        ul = soup.new_tag("ul")
        for label in options:
            li = soup.new_tag("li")
            li.string = label
            ul.append(li)
        sel.replace_with(ul)
    for area in soup.find_all("textarea"):
        text = area.get_text().strip()
        if not text:
            area.decompose()
            continue
        para = soup.new_tag("p")
        para.string = " ".join(text.split())
        area.replace_with(para)


# Frontmatter keys in output order. Every Markdown file uses this set.
FRONTMATTER_FIELDS = (
    "title", "url", "canonical_url", "date", "date_source", "page_type",
    "author", "recipe_category", "recipe_cuisine", "servings", "total_time", "keywords",
    "word_count", "description", "domain",
    "articles", "sub_sections",
    "scraped_at", "content_hash",
)


_RE_UNSAFE_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")


def clean_text_value(value: str) -> str:
    """Remove characters that YAML cannot represent (C0/C1 controls except tab
    and newlines, lone surrogates, non-characters)."""
    return _RE_UNSAFE_CHARS.sub("", value)


def build_frontmatter(metadata: dict) -> str:
    """YAML frontmatter ("---" ... "---") for metadata, safe for yaml.safe_load."""
    data = {}
    for key in FRONTMATTER_FIELDS:
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = [clean_text_value(str(v)) for v in value if v is not None]
            if value:
                data[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            value = clean_text_value(str(value))
        data[key] = value
    if not data:
        return "---\n---\n"
    body = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False,
        width=2 ** 31 - 1,
    )
    return "---\n" + body + "---\n"


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def content_hash(body: str) -> str:
    """sha256 of the Markdown body (everything after the frontmatter)."""
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


_RE_BACKTICK_RUN = re.compile(r"`+")
_RE_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class _FenceSafeConverter(markdownify.MarkdownConverter):
    """markdownify, with code fences longer than any backtick run inside the
    code (minimum three), so code that shows a fence cannot close its own."""

    def convert_pre(self, el, text, *args, **kwargs):
        out = super().convert_pre(el, text, *args, **kwargs)
        longest = max((len(r) for r in _RE_BACKTICK_RUN.findall(text or "")), default=0)
        if longest < 3 or "```" not in out:
            return out
        fence = "`" * (longest + 1)
        start, end = out.index("```"), out.rindex("```")
        return out[:start] + fence + out[start + 3:end] + fence + out[end + 3:]


class HTMLToMarkdown:
    def __init__(self, input_dir, output_dir=None, flatten=False, base_url=None, domain=None,
                 absolute_links=False):
        self.input_dir = Path(input_dir)
        self.flatten = flatten
        self.base_url = base_url
        self.domain = domain
        # Markdown-only mode: links and images point at absolute live URLs
        self.absolute_links = absolute_links

        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            # Standalone use: <root>/ScrapedSites/<site> -> <root>/MarkdownSites/<site>
            from ..config import derive_output_dirs
            root = self.input_dir.parent.parent
            self.output_dir = Path(derive_output_dirs(str(root), self.input_dir.name)[1])

        self.converted = 0
        self.failed = 0
        self.skipped = 0
        # Path of the file the most recent convert_file() call wrote, or None
        self.last_output_path: str | None = None
        # Whether that file already existed before it was written
        self.last_output_existed = False
        # One page, one file: a page whose Markdown body matches one already
        # written in this run (/ and /index.html, say) is not written again.
        self.dedupe = True
        self._written_hashes: dict[str, str] = {}
        self.last_duplicate_of: str | None = None
        # One file, one URL: the first URL to write a file in this run keeps
        # it. A later URL mapping to the same file (/about and /about.html)
        # is skipped, and last_collision says which file and which URL kept it.
        self._written_paths: dict[str, str] = {}
        self.last_collision: tuple[str, str] | None = None
        # Why the most recent convert_file() call failed, or None
        self.last_error: str | None = None

    def _prepare_html(self, html: str, metadata: dict) -> str:
        """Unwrap block links, read the canonical URL, and in markdown-only
        mode make links absolute.

        metadata["url"] stays the address the page was fetched from, so two
        pages never share a document ID. A same-site canonical that differs
        from it is recorded in metadata["canonical_url"]."""
        soup = BeautifulSoup(html, "lxml")
        fix_block_links(soup)
        expand_truncated_link_text(soup)
        page_url = metadata.get("url")
        if page_url:
            canonical = canonical_url(soup, page_url)
            if canonical and canonical != page_url and same_site(canonical, page_url):
                metadata["canonical_url"] = canonical
            if self.absolute_links:
                absolutize_links(soup, page_url)
        return str(soup)

    @staticmethod
    def _md(html_text: str, **extra) -> str:
        """markdownify with the house options: ATX headings, "-" bullets,
        fenced code blocks labelled with the language the HTML gives."""
        return _FenceSafeConverter(
            heading_style="ATX", bullets="-",
            code_language_callback=code_language, **extra,
        ).convert(html_text)

    def is_asset_directory(self, rel_path):
        first_part = rel_path.parts[0] if rel_path.parts else ""
        return first_part in {"css", "js", "images", "fonts"}

    def is_error_page(self, soup, html):
        from ..processing.errors import is_error_page_html, is_error_page_content
        # Quick raw HTML check
        if is_error_page_html(html):
            return True
        # Rendered content check
        title = soup.find("title")
        title_text = title.get_text() if title else ""
        h1 = soup.find("h1")
        h1_text = h1.get_text() if h1 else ""
        body_text = soup.get_text()[:2000]
        return is_error_page_content(title_text, h1_text, body_text)

    def unwrap_layout_tables(self, soup):
        for table in soup.find_all("table"):
            table_class = " ".join(table.get("class", []))
            is_layout = (
                "Table-NoLines" in table_class
                or "layout" in table_class.lower()
                or len([td for td in table.find_all("td") if td.get_text(strip=True)])
                    < len(table.find_all("td")) * 0.5
            )
            if is_layout:
                table.unwrap()

        for tag_name in ("tbody", "tr", "td", "th", "colgroup", "col"):
            for tag in soup.find_all(tag_name):
                tag.unwrap()

    def extract_api_docs_content(self, soup):
        for tag in soup.find_all(["script", "style", "noscript", "svg", "link"]):
            tag.decompose()

        for selector in ("sidebar", "nav", "menu", "toc"):
            for tag in soup.find_all(class_=lambda x: x and selector in str(x).lower()):
                if not tag.find(class_=lambda x: x and "content" in str(x).lower() if x else False):
                    tag.decompose()

        md_parts = []

        title = ""
        title_el = soup.find("h1") or soup.find(
            class_=lambda x: x and "title" in str(x).lower() if x else False
        )
        if title_el:
            title = title_el.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        if title:
            md_parts.append(f"# {title}\n")

        main_content = (
            soup.find("main")
            or soup.find("article")
            or soup.find(class_=lambda x: x and "content" in str(x).lower() if x else False)
        )
        if not main_content:
            main_content = soup.find("body")

        if main_content:
            endpoint_blocks = main_content.find_all(
                class_=lambda x: x and (
                    "endpoint" in str(x).lower()
                    or "api-method" in str(x).lower()
                    or "operation" in str(x).lower()
                ) if x else False
            )

            if endpoint_blocks:
                for block in endpoint_blocks:
                    method_el = block.find(
                        class_=lambda x: x and ("method" in str(x).lower() or "http" in str(x).lower()) if x else False
                    )
                    method = method_el.get_text(strip=True).upper() if method_el else ""

                    path_el = block.find(
                        class_=lambda x: x and ("path" in str(x).lower() or "url" in str(x).lower() or "endpoint" in str(x).lower()) if x else False
                    )
                    path = path_el.get_text(strip=True) if path_el else ""

                    if method or path:
                        md_parts.append(f"\n### `{method}` {path}\n")

                    desc_el = block.find(class_=lambda x: x and "description" in str(x).lower() if x else False)
                    if desc_el:
                        md_parts.append(f"{desc_el.get_text(strip=True)}\n")

                    params_section = block.find(class_=lambda x: x and "param" in str(x).lower() if x else False)
                    if params_section:
                        params = params_section.find_all(
                            ["tr", "li", "div"],
                            class_=lambda x: x and "param" in str(x).lower() if x else True,
                        )
                        if params:
                            md_parts.append("\n**Parameters:**\n")
                            for param in params:
                                param_text = param.get_text(strip=True)
                                if param_text:
                                    md_parts.append(f"- {param_text}\n")

                    response_section = block.find(class_=lambda x: x and "response" in str(x).lower() if x else False)
                    if response_section:
                        md_parts.append("\n**Response:**\n")
                        md_parts.append(f"{response_section.get_text(strip=True)}\n")
            else:
                md = self._md(str(main_content))
                if title and first_h1(md) is None:
                    md = f"# {clean_title(title)}\n\n{md}"
                return md

        result = "\n".join(md_parts)
        return result if len(result.strip()) > 100 else None

    def extract_swagger_content(self, soup):
        for tag in soup.find_all(["script", "style", "noscript", "svg", "link"]):
            tag.decompose()

        for selector in ("topbar", "auth-wrapper", "information-container-wrapper"):
            for tag in soup.find_all(class_=lambda x: x and selector in x):
                tag.decompose()

        for tag in soup.find_all(["button", "input", "select"]):
            tag.decompose()

        for tag in soup.find_all(class_=lambda x: x and ("try-out" in str(x) or "execute" in str(x))):
            tag.decompose()

        body = soup.find("body")
        if not body:
            return None

        title = ""
        info_title = soup.find(class_="title") or soup.find("h1") or soup.find("h2")
        if info_title:
            title = info_title.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True).replace("Swagger UI", "").strip(" -")

        md_parts = []
        if title:
            md_parts.append(f"# {title}\n")

        info_section = soup.find(class_="info") or soup.find(class_="information-container")
        if info_section:
            desc = info_section.find(class_="description") or info_section.find("p")
            if desc:
                md_parts.append(desc.get_text(strip=True) + "\n")

        opblocks = soup.find_all(class_="opblock")
        current_tag = None

        for opblock in opblocks:
            summary = opblock.find(class_="opblock-summary")
            if summary:
                method_el = summary.find(class_="opblock-summary-method")
                path_el = (
                    summary.find(class_="opblock-summary-path")
                    or summary.find(class_="opblock-summary-path__deprecated")
                )

                method = method_el.get_text(strip=True) if method_el else ""
                path = path_el.get_text(strip=True) if path_el else ""

                tag_el = opblock.find_previous(class_="opblock-tag")
                if tag_el:
                    tag_name = tag_el.get_text(strip=True)
                    if tag_name != current_tag:
                        current_tag = tag_name
                        md_parts.append(f"\n## {tag_name}\n")

                md_parts.append(f"\n### `{method}` {path}\n")

                desc_el = summary.find(class_="opblock-summary-description")
                if desc_el:
                    md_parts.append(f"\n{desc_el.get_text(strip=True)}\n")

            body_el = opblock.find(class_="opblock-body")
            if body_el:
                params_section = body_el.find(class_="parameters")
                if params_section:
                    md_parts.append("\n**Parameters:**\n")
                    for param in params_section.find_all(class_="parameter"):
                        name_el = param.find(class_="parameter__name")
                        type_el = param.find(class_="parameter__type")
                        desc_el = param.find(class_="parameter__description") or param.find(class_="markdown")

                        name = name_el.get_text(strip=True) if name_el else ""
                        ptype = type_el.get_text(strip=True) if type_el else ""
                        desc = desc_el.get_text(strip=True) if desc_el else ""
                        required = param.find(class_="parameter__required")
                        req_str = " *(required)*" if required else ""
                        md_parts.append(f"- `{name}` ({ptype}){req_str}: {desc}\n")

                responses_section = body_el.find(class_="responses")
                if responses_section:
                    md_parts.append("\n**Responses:**\n")
                    for resp in responses_section.find_all(class_="response"):
                        code_el = resp.find(class_="response-col_status")
                        desc_el = resp.find(class_="response-col_description")
                        code = code_el.get_text(strip=True) if code_el else ""
                        desc = ""
                        if desc_el:
                            desc_text = desc_el.find(class_="markdown") or desc_el.find("p")
                            if desc_text:
                                desc = desc_text.get_text(strip=True)
                        md_parts.append(f"- `{code}`: {desc}\n")

        models_section = soup.find(class_="models")
        if models_section:
            md_parts.append("\n## Schemas\n")
            for model in models_section.find_all(class_="model-container"):
                name_el = model.find(class_="model-title")
                if name_el:
                    md_parts.append(f"\n### {name_el.get_text(strip=True)}\n")
                for prop in model.find_all(class_="property"):
                    prop_text = prop.get_text(strip=True)
                    if prop_text:
                        md_parts.append(f"- {prop_text}\n")

        result = "\n".join(md_parts)
        return result if len(result.strip()) > 100 else None

    def extract_robohelp_content(self, soup):
        for tag in soup.find_all(["script", "style", "noscript", "colgroup"]):
            tag.decompose()

        for selector in ("topic-header", "topic-header-shadow", "breadcrumbs", "minitoc"):
            for tag in soup.find_all(class_=selector):
                tag.decompose()

        for tag in soup.find_all(id=lambda x: x and x.startswith("rh-")):
            tag.decompose()

        for a in soup.find_all("a"):
            if a.get("onclick"):
                del a["onclick"]

        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        body = soup.find("body")
        if not body:
            return None

        for p in body.find_all("p", class_="NormalFooter"):
            parent_table = p.find_parent("table")
            if parent_table:
                parent_table.decompose()

        for element in body.find_all(["p", "div"]):
            text = element.get_text(strip=True).lower()
            if "associated press" in text and ("copyright" in text or "all rights reserved" in text):
                parent_table = element.find_parent("table")
                if parent_table:
                    parent_table.decompose()
                else:
                    element.decompose()

        self.unwrap_layout_tables(body)

        for p in body.find_all("p"):
            text = p.get_text(strip=True)
            if not text or text == "\xa0":
                p.decompose()

        for img in body.find_all("img"):
            src = img.get("src", "")
            if "icon" in src.lower():
                img.decompose()

        md = self._md(str(body), strip=["img"])

        if title and first_h1(md) is None:
            md = f"# {clean_title(title)}\n\n{md}"

        return md

    def _remove_junk_elements(self, soup):
        """Remove non-content HTML elements before content extraction."""
        # Structural non-content
        for tag in soup.find_all(["script", "style", "noscript", "svg", "link"]):
            tag.decompose()
        for tag in soup.find_all(["nav", "header", "footer"]):
            tag.decompose()
        for tag in soup.find_all(attrs={"role": "navigation"}):
            tag.decompose()
        # Form controls: keep accordion questions, option lists and textarea
        # text; drop "Submit", "Menu", "Add to basket" and the like
        _replace_form_controls(soup)
        for tag in soup.find_all(id=lambda x: x and x in ("access", "branding", "colophon")):
            tag.decompose()

        # Sidebars and widget areas
        for tag in soup.find_all(["aside", "section"]):
            classes = " ".join(tag.get("class", []))
            tag_id = tag.get("id", "")
            if any(kw in classes.lower() or kw in tag_id.lower()
                   for kw in ("sidebar", "widget", "secondary", "nav", "menu")):
                tag.decompose()

        # Ad, newsletter, trending, and related containers by CSS class/id
        for tag in list(soup.find_all(True)):
            if not tag.parent:
                continue  # Already decomposed with a parent element
            classes = " ".join(tag.get("class", []))
            tag_id = tag.get("id", "")
            combined = (classes + " " + tag_id).lower()
            if any(kw in combined for kw in _JUNK_CLASS_KEYWORDS):
                tag.decompose()

    def extract_standard_content(self, soup):
        """Extract content from standard sites (WordPress, blogs, general CMS)."""
        # The page's H1 can sit in a <header>, which is removed as junk
        page_h1 = soup.find("h1")
        page_h1 = page_h1.get_text(" ", strip=True) if page_h1 else ""
        self._remove_junk_elements(soup)

        # Try precise content selectors (most specific first)
        content = None
        selectors = [
            ".entry-content",           # WordPress
            ".post-content",            # Common blog theme
            ".article-text",            # Daily Mail
            ".article-body",            # Various CMS
            "article .content",         # Generic article
            "[role='main'] article",    # ARIA main > article
            "#content article",         # ID content > article
            "article",                  # Bare article tag
            "[role='main']",            # ARIA main
            "main",                     # HTML5 main
            "#main",                    # ID main (styled-components sites)
            "#content",                 # ID content
            ".content",                 # Class content
        ]
        for sel in selectors:
            content = soup.select_one(sel)
            if content and len(content.get_text(strip=True).split()) > 50:
                break
        else:
            # No known container: take the element that holds most of the
            # text (skips header rows, sidebars and footers on listing pages)
            content = dominant_container(soup)
            if content is None or len(content.get_text(strip=True).split()) <= 50:
                return None

        # Extract title
        title = ""
        h1 = content.find("h1") or soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        elif page_h1:
            title = page_h1
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        # Convert to markdown
        md = self._md(str(content))

        if title and first_h1(md) is None:
            md = f"# {clean_title(title)}\n\n{md}"

        return md

    def extract_content(self, html, filepath):
        soup = BeautifulSoup(html, "lxml")

        if self.is_error_page(soup, html):
            return None

        # Detect API documentation platforms
        is_readme = (
            soup.find(class_=lambda x: x and "readme" in str(x).lower())
            or soup.find("meta", attrs={"name": "readme-deploy"})
            or soup.find(class_=lambda x: x and "api-" in str(x).lower() and "content" in str(x).lower())
        )
        has_api_content = (
            soup.find(class_=lambda x: x and ("endpoint" in str(x).lower() or "api-method" in str(x).lower()))
            or soup.find("code", class_=lambda x: x and "http" in str(x).lower() if x else False)
        )
        if is_readme or has_api_content:
            result = self.extract_api_docs_content(soup)
            if result and len(result.strip()) > 100:
                return result

        # Detect Swagger UI
        is_swagger = (
            soup.find(class_="swagger-ui")
            or soup.find(class_="opblock")
            or ("swagger" in html.lower()[:5000] and soup.find(class_="info"))
        )
        if is_swagger:
            result = self.extract_swagger_content(soup)
            if result and len(result.strip()) > 100:
                return result

        # Detect RoboHelp/MadCap
        generator = soup.find("meta", attrs={"name": "generator"})
        is_robohelp = generator and (
            "RoboHelp" in generator.get("content", "") or "MadCap" in generator.get("content", "")
        )
        has_rh_structure = soup.find(class_="topic-header") or soup.find(
            id=lambda x: x and x.startswith("rh-")
        )
        if is_robohelp or has_rh_structure:
            result = self.extract_robohelp_content(soup)
            if result and len(result.strip()) > 100:
                return result

        # Standard site extraction (WordPress, blogs, CMS)
        # Re-parse only if soup was mutated by earlier extractors
        if is_readme or has_api_content or is_swagger or is_robohelp or has_rh_structure:
            standard_soup = BeautifulSoup(html, "lxml")
        else:
            standard_soup = soup
        result = self.extract_standard_content(standard_soup)
        if result and len(result.strip()) > 100:
            return result

        # Fallback: trafilatura
        if HAS_TRAFILATURA:
            try:
                extracted = trafilatura.extract(
                    html,
                    include_images=True,
                    include_links=True,
                    include_tables=True,
                    include_formatting=True,
                    output_format="markdown",
                )
                if extracted and len(extracted.strip()) > 100:
                    return extracted
            except (ImportError, MemoryError):
                raise
            except Exception:
                pass

        # Fallback: generic
        try:
            for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
                tag.decompose()

            main_content = soup.select_one("main, article, [role='main'], #main, .content, #content, body")
            if main_content:
                title = ""
                if soup.find("title"):
                    title = soup.find("title").get_text().strip()
                if soup.find("h1"):
                    title = soup.find("h1").get_text().strip()

                md = self._md(str(main_content))
                if title and first_h1(md) is None:
                    md = f"# {clean_title(title)}\n\n{md}"

                if len(md.strip()) > 100:
                    return md
        except (ImportError, MemoryError):
            raise
        except Exception:
            pass

        return None

    def clean_markdown(self, md):
        if not md:
            return None

        # ===== INLINE JUNK PATTERNS =====

        # Advertisement markers (common ad-slot style)
        md = re.sub(r'^\s*Article continues below\s*(?:ADVERTISEMENT)?\s*$',
                     '', md, flags=re.MULTILINE)
        # Hearst/general "Advertisement - Continue Reading Below"
        md = re.sub(r'^\s*Advertisement\s*[-\u2013\u2014]\s*Continue Reading Below\s*$',
                     '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Standalone "Advertisement" text
        md = re.sub(r'^\s*Advertisement\s*$', '', md, flags=re.MULTILINE)

        # Google preferred source badges (multiple formats)
        md = re.sub(
            r'\[!\[Add\s+.+?as a preferred source\]\([^)]*\)\]\([^)]*\)',
            '', md, flags=re.IGNORECASE)
        # Google preferred source full-line blocks (Express style)
        md = re.sub(
            r'^\[.*?(?:Preferred Source|preferred source|More of Our News on Google).*?'
            r'!\[.*?preferred source.*?\]\([^)]*\)\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Relative date strings (standalone lines)
        md = re.sub(
            r'^\s*(?:just now|yesterday|'
            r'(?:Updated\s+)?(?:\d+|an?|one)\s+'
            r'(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago'
            r')\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Affiliate link disclaimers
        md = re.sub(
            r'^This article contains affiliate links.*?\[Learn more\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter form artifacts
        md = re.sub(r'^\s*Leave this field blank\s*$', '', md, flags=re.MULTILINE)

        # Standalone "Sign up" / "Subscribe" / "Follow" buttons
        md = re.sub(r'^\s*(?:Sign up|Subscribe|Follow)\s*$', '', md, flags=re.MULTILINE)

        # Topic/author follow widget text (Verge, Vox Media)
        md = re.sub(
            r'^.*?(?:Posts from this (?:topic|author) will be added to your|'
            r'daily email digest and your homepage feed).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*\[See All (?:by )?.+?\]\([^)]*\)\s*$',
                     '', md, flags=re.MULTILINE)

        # "Follow topics and authors" footer blocks
        md = re.sub(
            r'^\*\*Follow topics and authors\*\*.*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Advertiser content blocks
        md = re.sub(
            r'^Advertiser Content.*$(?:\s*\n.*?(?:Sponsor|native ad|thumbnail).*$)*',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Footer action links (HuffPost, general)
        md = re.sub(
            r'^\s*(?:Go To Homepage|Suggest a correction|Submit a tip)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Share/view comment counts (Daily Mail, etc.)
        md = re.sub(r'^\s*\*?\*?\d+\*?\*?\s+shares?\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*\[?\d+\s+View\s+comments?\]?\(?\#?[^)]*\)?\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*Share what you think\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*(?:Submit Comment|Enter your comment|Clear)\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)

        # "Related" / "Related Topics" label followed by topic tag links
        md = re.sub(
            r'^\s*[-*]?\s*Related(?:\s+Topics?)?\s*$\n+(?:\s*[-*]?\s*\[.+?\]\(.+?\)\s*$\n*)+',
            '', md, flags=re.MULTILINE)

        # Fox News Flash / video promo blocks (image link + heading + description)
        md = re.sub(
            r'^\[?!\[.*?(?:Fox News Flash|top headlines).*?\]\([^)]*\).*?\]\([^)]*\)\s*\n+'
            r'(?:#{2,4}\s+\[.*?(?:Fox News Flash|top headlines).*?\]\([^)]*\)\s*\n+'
            r'.*?(?:Check out what\'s clicking|FoxNews\.com).*$\n*)?',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter consent/email preference text
        md = re.sub(
            r'^.*?(?:I would like to be emailed about|'
            r'For more information see our \[Privacy).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter/edition embedded images
        md = re.sub(
            r'^!\[.*?(?:newsletter|edition|subscribe|sign.?up).*?\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter description lines between image and "Sign up"
        md = re.sub(
            r'^(?:Get the latest|News from |Sign up to our|'
            r'The best .+ direct to your inbox|'
            r'Stay up to date with|Never miss).+(?:inbox|newsletter|updates).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Social share icon/link blocks (GB News: image + Facebook/Twitter/E-mail links)
        md = re.sub(
            r'^\s*!\[desktop-(?:share|like|comment)\]\([^)]*\).*$',
            '', md, flags=re.MULTILINE)
        md = re.sub(
            r'^\s*\[Facebook\]\(https://www\.facebook\.com/sharer[^)]*\)\s+'
            r'\[Twitter\]\(https://twitter\.com/intent[^)]*\)\s+'
            r'\[E-mail\]\(mailto:[^)]*\)\s*$',
            '', md, flags=re.MULTILINE)

        # Byline blocks: "By" + author link on separate lines
        md = re.sub(
            r'^By\s*\n+\[([^\]]+)\]\([^)]*\)\s*,?\s*$',
            r'By \1', md, flags=re.MULTILINE)

        # Published date/time in body (redundant with frontmatter)
        md = re.sub(
            r'^\s*Published:\s+\d{1,2}/\d{1,2}/\d{4}\s*$',
            '', md, flags=re.MULTILINE)
        md = re.sub(r'^\s*-\s+\d{1,2}:\d{2}\s*$', '', md, flags=re.MULTILINE)

        # Relative timestamps with optional comments link (Standard: "2 minutes ago[COMMENTS](#...)")
        md = re.sub(
            r'^\s*\d+\s+(?:seconds?|minutes?|hours?|days?)\s+ago'
            r'(?:\s*\[COMMENTS\]\([^)]*\))?\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter embed images (Standard: "![The Weekender](../images/newsletter_*_embed_*.png)")
        md = re.sub(
            r'^\s*!\[[^\]]*\]\([^)]*newsletter[^)]*embed[^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Rating/score widget text (Pitchfork)
        md = re.sub(
            r'^\s*(?:No score yet, be the first to add\.|Reader Score)\s*$',
            '', md, flags=re.MULTILINE)
        md = re.sub(r'^\s*0\.0\s*$', '', md, flags=re.MULTILINE)

        # Standalone "Latest" heading (GB News uses this before article body)
        md = re.sub(r'^#{2,3}\s+Latest\s*$', '', md, flags=re.MULTILINE)

        # "Most Read" sidebar blocks (non-heading, followed by list)
        md = re.sub(
            r'^Most Read\s+\w+\s*$\n+'
            r'(?:\s*[-*]?\s*\[.+?\]\(.+?\).*$\n*)+',
            '', md, flags=re.MULTILINE)

        # UI element text (gallery viewers, bookmark popovers, close buttons, video players)
        md = re.sub(
            r'^\s*(?:open image in gallery|close popover|close panel|'
            r'Removed from bookmarks|Video Player Placeholder|'
            r'View \d+ Images?|Save this story|'
            r'Skip to main content|Scroll (?:Left|Right)|'
            r'Please use Chrome browser for a more accessible video player)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Close button links after title (Fox News)
        md = re.sub(r'^\s*\[close\]\(#[^)]*\)\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Concatenated "NEW" banner (Fox News "NEWYou can now listen to...")
        md = re.sub(r'^NEW\s*You can now listen to .+? articles!?\s*$',
                     '', md, flags=re.MULTILINE)
        # App download CTAs
        md = re.sub(
            r'^\s*\[?\*?\*?CLICK HERE TO (?:DOWNLOAD|GET) THE .+? APP\*?\*?\]?\(?[^)]*\)?\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Icon text artifacts (Hearst "Caret Right/Left", "Chevron" etc.)
        md = re.sub(r'\s*Caret (?:Right|Left|Down|Up)\s*', ' ', md)
        md = re.sub(r'^\s*Chevron\s*$', '', md, flags=re.MULTILINE)
        # Concatenated follow/unfollow button text (BBC Sport)
        md = re.sub(r'Follow\w*(?:Follow|Unfollow)\w*', '', md)
        # Comments links (standalone)
        md = re.sub(r'^\s*\[Comments?\]\(#[^)]*\)\s*$', '', md, flags=re.MULTILINE)
        # Zero-width characters on standalone lines
        md = re.sub(r'^\s*[\u200b\u200c\u200d\ufeff]+\s*$', '', md, flags=re.MULTILINE)
        # Bookmark popover heading (Independent)
        md = re.sub(r'^#{2,3}\s+Bookmark popover\s*$', '', md, flags=re.MULTILINE)
        # "In full" related article blocks (Independent bulletin)
        md = re.sub(
            r'^In full\s*$\n+(?:\s*[-*]\s*\[.+?\]\(.+?\).*$\n*)*',
            '', md, flags=re.MULTILINE)

        # Social follow promos (WhatsApp, Telegram, etc.)
        md = re.sub(
            r'^\[?!\[Follow .+? on WhatsApp\]\([^)]*\)\s*'
            r'(?:Follow .+? on WhatsApp[^\]]*\]\([^)]*\))?\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Placeholder/logo images (SVGs, brand logos, hero video posters)
        md = re.sub(
            r'^!\[(?:Video Player Placeholder|Hero video poster|logo)[^\]]*\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # "View image in fullscreen" links (Guardian), standalone or inline
        md = re.sub(r'\[View image in fullscreen\]\(#[^)]*\)',
                     '', md, flags=re.IGNORECASE)

        # "Prefer X on Google" links (Guardian)
        md = re.sub(r'^\s*\[Prefer .+? on Google\]\([^)]*\)\s*$',
                     '', md, flags=re.MULTILINE)

        # "Reuse this content" links
        md = re.sub(r'^\s*\[Reuse this content\]\([^)]*\)\s*$',
                     '', md, flags=re.MULTILINE)

        # Share mailto links: [Share](mailto:...) or [e-mail](mailto:...)
        md = re.sub(r'^\s*[-*]?\s*\[(?:Share|e-?mail)\]\(mailto:[^)]*\)\s*$',
                     '', md, flags=re.MULTILINE | re.IGNORECASE)

        # "Supported by" sponsor blocks (Guardian): "Supported by" + sponsor image + about link
        md = re.sub(
            r'^Supported by\s*\n+(?:\s*\[?!\[.*?\]\([^)]*\)\]?\([^)]*\)\s*\n*)*'
            r'(?:\s*\[About this content\]\([^)]*\)\s*$)?',
            '', md, flags=re.MULTILINE)

        # Duplicate image captions: alt text repeated as standalone line
        md = re.sub(
            r'(!\[([^\]]+)\]\([^)]+\))\s*\n+\2\s*$',
            r'\1', md, flags=re.MULTILINE)
        # Duplicate caption lines after image (caption\n\ncaption -> caption)
        md = re.sub(
            r'(!\[[^\]]*\]\([^)]+\)\s*\n+)(.+(?:Photograph|Photo|Image|Illustration|Credit|Getty|Reuters|AP|PA|AFP|Alamy|Shutterstock|iStock).+)\s*\n+\2',
            r'\1\2', md, flags=re.IGNORECASE)

        # "Explore more on these topics" + topic link list (Guardian)
        md = re.sub(
            r'^Explore more on these topics\s*$'
            r'(?:\s*\n\s*[-*]\s*\[.+?\]\(.+?\)\s*$)*',
            '', md, flags=re.MULTILINE)

        # ===== SECTION-LEVEL JUNK =====

        # Small heading + thumbnail link lists (Hearst: "Related Story", "For More on That...")
        md = re.sub(
            r'^#{4,6}\s+(?:Related Stor(?:y|ies)|For More on That\.{0,3}|'
            r'Key Points?)\s*$\n+'
            r'(?:\s*[-*]?\s*\[?!\[.*?\]\([^)]*\).*$\n*)*',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Linked heading + thumbnail grid (Hearst related content sections)
        # Pattern: ## [Section Title](link) followed by one or more image+text
        # link blocks. A linked heading on its own is content and is kept.
        md = re.sub(
            r'^#{2,3}\s+\[.+?\]\(.+?\)\s*$\n+'
            r'(?:\s*\[?!\[.*?\]\(https?://[^)]*\)[\s\S]*?\]\([^)]*\)\s*$\n*)+',
            '', md, flags=re.MULTILINE)

        # Inline "Read More" blocks (heading + link lines only, not greedy)
        md = _READ_MORE_RE.sub('', md)

        # Remove entire junk sections (heading to next heading or EOF)
        md = _JUNK_SECTION_RE.sub('', md)

        # "MORE ABOUT" topic tag blocks (not a heading, uppercase text + links)
        md = re.sub(
            r'^MORE ABOUT\s*$(?:\s*\n\s*(?:\[.+?\]\(.+?\)|[A-Z][\w\s]*)\s*$)*',
            '', md, flags=re.MULTILINE)

        # ===== BLOCK PATTERNS =====

        # Inline event promotion blocks (TechCrunch): "Techcrunch event" ... "REGISTER NOW"
        md = re.sub(
            r'^Techcrunch event\s*$.*?(?:^\[REGISTER NOW\]\([^)]*\)\s*$)',
            '', md, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)

        # Previous/Next navigation blocks (broken multi-line links)
        md = re.sub(
            r'\[#{1,3}\s*(?:Previous|Next)\b[\s\S]*?\.(?:html|md)\)',
            '', md)

        # Shopping/ecommerce recommendation blocks (to end of string)
        md = re.sub(
            r'^\[Shopping\s+Best\s+Buys?\]\([^)]*\)[\s\S]*$',
            '', md, flags=re.MULTILINE)

        # Breadcrumb/category link lines (adjacent linked labels with "View all posts" titles)
        md = re.sub(
            r'^\s*(?:\[[\w\s&/]+\]\([^)]*(?:"View all [^"]*")?\)\s*){2,}\s*$',
            '', md, flags=re.MULTILINE)

        # Comment moderation disclaimers
        md = re.sub(
            r'^.*(?:comments below have not been moderated|'
            r'views expressed .* do not necessarily reflect|'
            r'By posting your comment you agree).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Comment sort tabs (Newest/Oldest/Best rated/Worst rated)
        md = re.sub(
            r'^\s*[-*]\s*\[(?:Newest|Oldest|Best rated|Worst rated)\]\(#\)\s*$',
            '', md, flags=re.MULTILINE)
        # Standalone "View all" links
        md = re.sub(r'^\s*View all\s*$', '', md, flags=re.MULTILINE)

        # ===== STRUCTURAL FIXES =====

        # Strip site name suffixes from H1: "# Title | Site.com" -> "# Title"
        # Match domain-style suffixes
        md = re.sub(
            r'^(# .+?)\s+[|\u2013\u2014-]\s+[\w][\w\s]*?'
            r'\.(?:com|co\.uk|net|org|io|uk|us|info|news)\s*$',
            r'\1', md, flags=re.MULTILINE)
        # Match brand name suffixes (short text after separator, max 40 chars)
        md = re.sub(
            r'^(# .{20,}?)\s+[|\u2013\u2014-]\s+(?:The\s+)?[\w][\w\s]{1,35}$',
            r'\1', md, flags=re.MULTILINE)

        # Duplicate H1: keep first, remove subsequent identical ones
        lines = md.split('\n')
        first_h1 = None
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('# ') and not stripped.startswith('## '):
                h1_text = stripped[2:].strip()
                if first_h1 is None:
                    first_h1 = h1_text
                    cleaned.append(line)
                elif h1_text == first_h1:
                    continue
                else:
                    cleaned.append(line)
            else:
                cleaned.append(line)
        md = '\n'.join(cleaned)

        # Duplicate consecutive images (same URL appearing twice)
        md = re.sub(
            r'(!\[[^\]]*\]\(([^)]+)\))\s*\n+!\[[^\]]*\]\(\2\)',
            r'\1', md)

        # Fix multi-line markdown links
        def _fix_multiline_link(m):
            text = re.sub(r'\s*\n\s*', ' ', m.group(1))
            return f'[{text}]({m.group(2)})'
        md = re.sub(r'\[([^\]]*\n[^\]]*)\]\(([^)]+)\)', _fix_multiline_link, md)

        # ===== STANDARD CLEANUP =====

        # Empty table rows
        md = re.sub(r'^\|(?:\s*\|)+\s*$', '', md, flags=re.MULTILINE)
        # Broken/empty links
        md = re.sub(r'\[([^\]]*)\]\(\s*\)', r'\1', md)
        # Empty headings
        md = re.sub(r'^#{1,6}\s*$', '', md, flags=re.MULTILINE)
        # RoboHelp cruft
        md = re.sub(r'\[?Click here to see this page in full context\]?\(?[^)]*\)?', '', md)
        # Copyright/footer
        md = re.sub(r'^©\s*\d{4}.*$', '', md, flags=re.MULTILINE)
        md = re.sub(r'^Copyright\s+\d{4}\s+\S+\.\s*All rights reserved\.?\s*$',
                     '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^All rights reserved\.?\s*$', '', md, flags=re.MULTILINE)
        md = re.sub(r'^\d+ queries\..*seconds\.\s*$', '', md, flags=re.MULTILINE)

        # Collapse blank lines and strip
        md = re.sub(r'\n{3,}', '\n\n', md)
        md = '\n'.join(line.rstrip() for line in md.split('\n'))
        md = md.strip()

        return md if len(md) > 50 else None

    def promote_bold_headings(self, md):
        """Convert standalone bold lines that act as section headings to ## headings."""
        lines = md.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            # Line is purely bold text (no other content), min 3 chars inside
            m = re.match(r'^\*\*(.{3,})\*\*$', stripped)
            if m:
                heading_text = m.group(1)
                # Don't promote if it looks like inline emphasis (contains sentences)
                if len(heading_text) < 80 and '.' not in heading_text[:-1]:
                    result.append(f'## {heading_text}')
                    continue
            result.append(line)
        return '\n'.join(result)

    def convert_links(self, md, filepath):
        current_filename = filepath.name.replace(".html", ".md").replace(".htm", ".md")

        def replace_link(match):
            text = match.group(1)
            url = match.group(2)
            # A link title ("[text](url "Title")") is not part of the URL
            title = ""
            m = re.match(r'^(\S+)(\s+"[^"]*")$', url)
            if m:
                url, title = m.group(1), m.group(2)

            # Absolute URLs of any scheme (http, mailto, javascript, data,
            # tel...) and in-page anchors are never rewritten
            if url.startswith("#") or _RE_URL_SCHEME.match(url):
                return match.group(0)

            if url.endswith(".html"):
                url = url[:-5] + ".md"
            elif url.endswith(".htm"):
                url = url[:-4] + ".md"
            elif url.endswith("/"):
                url = (url.rstrip("/") or "index") + ".md"
            elif "." not in url.split("/")[-1]:
                url += ".md"

            clean_url = url.lstrip("./")
            if clean_url in ("index.md", "_index.md", current_filename):
                return text

            return f"[{text}]({url}{title})"

        return re.sub(r'\[([^\]]*)\]\(([^)]+)\)', replace_link, md)

    def get_output_path(self, input_path):
        rel_path = input_path.relative_to(self.input_dir)
        if self.is_asset_directory(rel_path):
            return None
        if self.flatten:
            output_path = self.output_dir / rel_path.name
        else:
            output_path = self.output_dir / rel_path
        return output_path.with_suffix(".md")

    @staticmethod
    def _build_frontmatter(metadata: dict) -> str:
        """Build the YAML frontmatter block from a metadata dict.

        Keys come out in FRONTMATTER_FIELDS order; None values are left out.
        Values are written by yaml.safe_dump, so yaml.safe_load always gives
        back the same strings and ints, whatever the text contains. Control
        characters and lone surrogates, which YAML cannot carry, are removed.
        """
        return build_frontmatter(metadata)

    @staticmethod
    def _compute_word_count(md: str) -> int:
        """Count words in markdown, stripping syntax markers."""
        text = md
        # Strip image/link syntax: ![alt](url) -> alt, [text](url) -> text
        text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
        text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
        # Strip heading markers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Strip bold/italic markers
        text = re.sub(r'\*{1,3}|_{1,3}', '', text)
        # Strip frontmatter if present
        text = re.sub(r'^---\n.*?\n---\n', '', text, count=1, flags=re.DOTALL)
        return len(text.split())

    @staticmethod
    def _extract_description(html: str, soup=None) -> str | None:
        """Extract meta description from HTML. Reuses soup if provided."""
        if soup is None:
            soup = BeautifulSoup(html[:10000], "lxml")
        for attr in ({"name": "description"}, {"property": "og:description"}):
            tag = soup.find("meta", attrs=attr)
            if tag and tag.get("content"):
                desc = tag["content"].strip()
                if desc:
                    return desc
        return None

    def _extract_metadata_from_html(self, html: str, filepath: Path) -> dict:
        """Extract metadata from HTML for batch mode (no crawl-time metadata)."""
        from ..processing.dates import get_best_date
        from ..processing.classifier import classify_page

        metadata: dict = {}

        # Reconstruct URL from base_url + relative file path
        if self.base_url:
            rel = filepath.relative_to(self.input_dir)
            url_path = "/" + str(rel).replace("\\", "/")
            # Strip .html/.htm extension for URL
            if url_path.endswith(".html"):
                url_path = url_path[:-5]
            elif url_path.endswith(".htm"):
                url_path = url_path[:-4]
            if url_path.endswith("/index"):
                url_path = url_path[:-6] or "/"
            metadata["url"] = self.base_url + url_path

        if self.domain:
            metadata["domain"] = self.domain

        # Date extraction
        date_result = get_best_date(url_path if self.base_url else "", html)
        if date_result:
            metadata["date"] = date_result.date.isoformat()
            metadata["date_source"] = date_result.source.name

        # Page type
        page_type = classify_page(url_path if self.base_url else "", html)
        metadata["page_type"] = page_type.value

        # Description
        desc = self._extract_description(html)
        if desc:
            metadata["description"] = desc

        return metadata

    def convert_file(self, filepath, metadata=None, output_path=None, html=None,
                     claimed_by=None):
        """Convert one page. With html given, filepath is only used for naming
        and is never read (markdown-only mode has no HTML on disk).

        claimed_by is the URL that already owns this page's file in this run,
        when the caller knows one (the crawl does, for pages it saw unchanged):
        the page is then not written unless it is a duplicate of a file."""
        self.last_output_path = None
        self.last_output_existed = False
        self.last_duplicate_of = None
        self.last_collision = None
        self.last_error = None
        filepath = Path(filepath)
        explicit_output = output_path is not None
        if output_path:
            output_path = Path(output_path)
        else:
            output_path = self.get_output_path(filepath)
            if not output_path:
                return False

        try:
            if html is None:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    html = f.read()

            # Skip RSS/Atom XML feeds
            if html.lstrip().startswith("<?xml") or "<rss" in html[:500] or "<feed" in html[:500]:
                print(f"  Skipped (XML/RSS feed): {filepath.name}")
                self.skipped += 1
                return False

            # Skip XML manifests and non-HTML content
            if html.lstrip().startswith("<manifest") or html.lstrip().startswith("<wlw"):
                print(f"  Skipped (XML manifest): {filepath.name}")
                self.skipped += 1
                return False

            # Skip known low-value WordPress paths
            rel_path = filepath.relative_to(self.input_dir)
            rel_str = str(rel_path)
            wp_skip_patterns = ("/feed", "/author/", "/category/", "/tag/", "/wp-includes/", "/wp-json/")
            if any(pat in "/" + rel_str for pat in wp_skip_patterns):
                print(f"  Skipped (WordPress boilerplate): {filepath.name}")
                self.skipped += 1
                return False

            if "<title>404" in html or "<title>403" in html or "NoSuchKey" in html:
                print(f"  Skipped (error page): {filepath.name}")
                self.skipped += 1
                return False

            # Build metadata if not provided (batch mode)
            if metadata is None:
                metadata = self._extract_metadata_from_html(html, filepath)

            # Extract description from HTML if not already in metadata
            if "description" not in metadata:
                desc = self._extract_description(html)
                if desc:
                    metadata["description"] = desc

            # Section pages become _index.md in their own directory
            # (but not topic/tag/author pages which happen to look like sections)
            url_for_check = metadata.get("url", "")
            from urllib.parse import urlparse
            url_path_for_check = urlparse(url_for_check).path if url_for_check else ""
            from ..processing.urls import is_non_section_path
            if (not explicit_output
                    and metadata.get("page_type") == "section"
                    and not is_non_section_path(url_path_for_check)):
                # Redirect: news.md -> news/_index.md
                stem = output_path.stem  # e.g. "news"
                output_path = output_path.parent / stem / "_index.md"

            html = self._prepare_html(html, metadata)
            # schema.org Recipe data becomes a structured block (convert/recipe.py)
            recipes = find_recipes(html)
            if recipes:
                html = strip_recipe_text(html, recipes)
                metadata.update(recipe_frontmatter(recipes))
            md = self.extract_content(html, filepath)
            if not md and not recipes:
                print(f"  Skipped (no content/error page): {filepath.name}")
                self.skipped += 1
                return False

            # Promote bold lines to headings first so junk section patterns
            # (which match ## headings) can catch promoted junk headings
            md = self.promote_bold_headings(md or "")

            md = self.clean_markdown(md) or ("" if recipes else None)
            if md is None:
                print(f"  Skipped (content too short): {filepath.name}")
                self.skipped += 1
                return False

            # Ensure page title is always present as # Heading
            if first_h1(md) is None:
                title_soup = BeautifulSoup(html[:5000], "lxml")
                title = ""
                h1 = title_soup.find("h1")
                if h1:
                    title = h1.get_text(" ", strip=True)
                elif title_soup.find("title"):
                    title = title_soup.find("title").get_text(" ", strip=True)
                if title:
                    md = f"# {clean_title(title)}\n\n{md}"

            # One H1, no skipped heading levels
            md = normalize_headings(md)
            if recipes:
                md = normalize_headings(merge_recipe_markdown(md, recipes))

            # Capture title from first # Heading for metadata
            title_text = first_h1(md)
            if title_text and "title" not in metadata:
                metadata["title"] = title_text

            md = self.convert_links(md, filepath)

            # Compute word count, skip list/section pages with no real content
            word_count = self._compute_word_count(md)
            if word_count < 50 and not recipes:
                print(f"  Skipped (list/section page, {word_count} words): {filepath.name}")
                self.skipped += 1
                return False

            metadata["word_count"] = word_count

            # Insert visible date/time after the title heading (not on listing
            # pages, whose date is usually just the server's Last-Modified)
            date_str = metadata.get("date")
            if date_str and date_str != "null" and metadata.get("page_type") != "section":
                visible_date = _format_visible_date(date_str)
                md = insert_after_h1(md, f"**{visible_date}**")

            metadata.setdefault("scraped_at", utc_now_iso())
            metadata["content_hash"] = content_hash(md)
            first = self._written_hashes.get(metadata["content_hash"])
            owner = claimed_by or self._written_paths.get(str(output_path))
            owned_by_other = bool(owner) and owner != metadata.get("url")
            if self.dedupe and first and (first != str(output_path) or owned_by_other):
                print(f"  Skipped (same content as {Path(first).name}): {filepath.name}")
                self.skipped += 1
                self.last_duplicate_of = first
                return False
            if owned_by_other:
                print(f"  Skipped (file already written for {owner}): {filepath.name}")
                self.skipped += 1
                self.last_collision = (str(output_path), owner)
                return False
            frontmatter = self._build_frontmatter(metadata)
            md = frontmatter + "\n" + md

            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.last_output_existed = output_path.exists()
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md)

            print(f"  Converted: {filepath.name}")
            self.converted += 1
            self.last_output_path = str(output_path)
            self._written_hashes.setdefault(metadata["content_hash"], str(output_path))
            if metadata.get("url"):
                self._written_paths.setdefault(str(output_path), metadata["url"])
            return True

        except Exception as e:
            print(f"  Error converting {filepath.name}: {type(e).__name__}: {e}")
            self.failed += 1
            self.last_error = f"{type(e).__name__}: {e}"
            return False

    def copy_assets(self):
        """Copy asset directories (images, media) to the markdown output directory."""
        asset_dirs = ("images", "media")
        copied = 0
        for dirname in asset_dirs:
            src = self.input_dir / dirname
            if src.is_dir():
                dst = self.output_dir / dirname
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
                print(f"  Copied {dirname}/ ({file_count} files)")
                copied += file_count
        return copied

    def convert_all(self):
        print(f"Input:  {self.input_dir}")
        print(f"Output: {self.output_dir}")
        if self.flatten:
            print("Mode:   Flattened")
        print()

        from itertools import chain
        filtered = sorted(
            f for f in chain(self.input_dir.rglob("*.html"), self.input_dir.rglob("*.htm"))
            if not self.is_asset_directory(f.relative_to(self.input_dir))
        )

        print(f"Found {len(filtered)} HTML files")
        print()

        for filepath in filtered:
            self.convert_file(filepath)

        # Copy asset directories so image/css/font references work
        asset_count = self.copy_assets()

        print()
        print("Done!")
        print(f"  Converted: {self.converted}")
        print(f"  Skipped:   {self.skipped}")
        print(f"  Failed:    {self.failed}")
        if asset_count:
            print(f"  Assets:    {asset_count} files copied")
        print(f"Output: {self.output_dir}")


def generate_section_index(
    section_label: str,
    section_url: str,
    domain: str,
    articles: list[dict],
    sub_sections: list[dict],
    scraped_at: str | None = None,
) -> str:
    """Generate a section index.md with article listings and sub-section links.

    Args:
        section_label: Section name (e.g., "Sport")
        section_url: Full URL of the section page
        domain: Site domain
        articles: List of dicts with keys: title, filename, date, word_count,
                  description, thumbnail (path or None)
        sub_sections: List of dicts with keys: label, dir_name

    Returns:
        Complete markdown string with YAML frontmatter
    """
    metadata = {
        "title": section_label,
        "url": section_url,
        "page_type": "section",
        "domain": domain,
        "articles": len(articles),
    }
    if sub_sections:
        metadata["sub_sections"] = len(sub_sections)

    parts = [f"# {section_label}", ""]

    # Sub-sections listed before articles
    if sub_sections:
        parts.append("## Sub-sections")
        parts.append("")
        for sub in sub_sections:
            parts.append(f"- [{sub['label']}]({sub['dir_name']}/_index.md)")
        parts.append("")

    # Articles listing
    if articles:
        parts.append("## Articles")
        parts.append("")
        for article in articles:
            # Heading link
            parts.append(f"### [{article['title']}]({article['filename']})")

            # Optional thumbnail
            thumbnail = article.get("thumbnail")
            if thumbnail:
                if not thumbnail.startswith(("images/", "http://", "https://")):
                    thumbnail = f"images/{thumbnail}"
                parts.append(f"![]({thumbnail})")

            # Metadata line: date and word count
            meta_parts = []
            if article.get("date"):
                meta_parts.append(_format_visible_date(article["date"]))
            if article.get("word_count"):
                meta_parts.append(f"{article['word_count']:,} words")
            if meta_parts:
                parts.append(" | ".join(meta_parts))

            # Optional description
            if article.get("description"):
                parts.append("")
                parts.append(article["description"])

            parts.append("")

    body = "\n".join(parts)
    metadata["scraped_at"] = scraped_at or utc_now_iso()
    metadata["content_hash"] = content_hash(body)
    frontmatter = HTMLToMarkdown._build_frontmatter(metadata)
    return frontmatter + "\n" + body
