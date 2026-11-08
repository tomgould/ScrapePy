"""Convert downloaded documents (PDF, DOCX, XLSX, CSV) to Markdown text.

Documents are untrusted input. Nothing in them is executed or followed:
links inside a document are never fetched, XLSX formulas are read as the
values Excel last cached, and macros are ignored. Zip-based formats (DOCX,
XLSX) are checked before they are opened: an archive that would unpack to
more than Limits.max_unzipped_bytes, or with an XML part the library parses
whole that is larger than Limits.max_xml_part_bytes, is refused. A PDF
stream that pypdf stops at its decompression limit fails the document (no
fallback to a parser without limits). Every page, row, column and character
count is capped, control characters are removed from all extracted text,
and any error a library raises becomes a "failed" result with a short
reason instead of an exception.

The crawl runs each conversion in a child process (document_child.py) with
an address-space and CPU limit, killed when it runs too long, so a document
that defeats these checks cannot exhaust the machine or outlive its time.

The libraries are optional (pip install "scrapepy[documents]") and are
imported only when a document of their type is converted: pypdf for PDF,
with pdfminer.six as a fallback when pypdf cannot read a file or finds no
text, python-docx for DOCX and openpyxl for XLSX. CSV uses the standard
library.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from .to_markdown import clean_text_value

MB = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    """Caps applied while converting one document."""

    max_pages: int = 2000              # PDF pages read
    max_chars: int = 2_000_000         # characters of Markdown body kept
    max_rows: int = 2000               # data rows per table (XLSX sheet, CSV, DOCX table)
    max_columns: int = 100             # columns per table
    max_scan_rows: int = 100_000       # rows read per table, empty ones included
    section_rows: int = 40             # a long table is cut into sections of this many rows
    layout_seconds: float = 30.0       # PDF pages laid out with pdfminer.six for this long, then pypdf
    max_unzipped_bytes: int = 128 * MB  # DOCX and XLSX: total unpacked size
    max_xml_part_bytes: int = 16 * MB   # one XML part parsed whole (all of DOCX; XLSX except sheets)
    max_zip_entries: int = 10000       # DOCX and XLSX: parts in the archive


DEFAULT_LIMITS = Limits()


@dataclass
class Converted:
    """The result of converting one document.

    status is "converted" (text holds the Markdown body below the title),
    "no_text" (the document was read but holds no text, reason says which
    kind) or "failed" (reason says why)."""

    status: str
    reason: str | None = None
    title: str | None = None
    date: str | None = None
    pages: int | None = None           # PDF pages
    sheets: int | None = None          # XLSX sheets
    rows: int | None = None            # XLSX and CSV: data rows read (headers not counted)
    text: str = ""
    notes: list[str] = field(default_factory=list)


class DocumentError(Exception):
    """A document that cannot be converted, with a reason for the run summary."""


def convert_document(data: bytes, kind: str, limits: Limits | None = None) -> Converted:
    """Convert one document of the given kind (pdf, docx, xlsx or csv).
    Never raises for a bad document: the result says why it failed."""
    limits = limits or DEFAULT_LIMITS
    try:
        if kind == "pdf":
            out = _convert_pdf(data, limits)
        elif kind == "docx":
            out = _convert_docx(data, limits)
        elif kind == "xlsx":
            out = _convert_xlsx(data, limits)
        elif kind == "csv":
            out = _convert_csv(data, limits)
        else:
            return Converted("failed", reason=f"unsupported document type: {kind}")
    except DocumentError as e:
        return Converted("failed", reason=str(e))
    except MemoryError:
        return Converted("failed", reason="too large to convert")
    except RecursionError:
        return Converted("failed", reason=f"corrupt or unreadable {kind.upper()} file (nesting too deep)")
    except Exception as e:  # any library error on a malformed file
        detail = clean_text_value(str(e)).strip().replace("\n", " ")[:160]
        reason = f"corrupt or unreadable {kind.upper()} file ({type(e).__name__}"
        reason += f": {detail})" if detail else ")"
        return Converted("failed", reason=reason)
    out.title = _clean_title(out.title)
    if out.status == "converted":
        text = _cap_chars(clean(out.text).strip(), limits.max_chars, out.notes)
        if out.notes:
            text += "\n\n" + "\n\n".join(f"_{n}_" for n in out.notes)
        out.text = text
    return out


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st", "­": None,
})
_RE_MD_LINE_START = re.compile(r"^(\s*)([#>])", re.MULTILINE)


def clean(text: str) -> str:
    """text without control characters (tab and newline kept) and with
    Windows and old Mac line ends made plain newlines."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return clean_text_value(text)


def _escape_line_starts(text: str) -> str:
    """Escape a # or > at the start of a line, so document text never
    becomes a heading or a quote in the Markdown."""
    return _RE_MD_LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text)


def _clean_title(title) -> str | None:
    if title is None:
        return None
    text = " ".join(clean(str(title)).split())
    return text[:300] or None


def _cap_chars(text: str, limit: int, notes: list[str]) -> str:
    """text cut to at most limit characters, at a line end when there is
    one, with a note saying it was cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if "\n" in cut:
        cut = cut.rsplit("\n", 1)[0]
    notes.append(f"Text after the first {limit:,} characters is not shown.")
    return cut.rstrip()


def _iso(value) -> str | None:
    """ISO 8601 for a datetime from document metadata. Times without a
    zone are UTC, as the OOXML formats store them."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.replace(microsecond=0).isoformat()
    return None


_RE_PDF_DATE = re.compile(
    r"^D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*(Z|[+-]\d{2}'?\d{2}?'?)?")


def parse_pdf_date(raw) -> str | None:
    """A PDF date string (D:YYYYMMDDHHmmSS+HH'mm') as ISO 8601: a date
    alone when it has no time, a time with its offset when it has one."""
    if not raw:
        return None
    m = _RE_PDF_DATE.match(str(raw).strip())
    if not m:
        return None
    year, month, day, hour, minute, second, zone = m.groups()
    try:
        d = date(int(year), int(month or 1), int(day or 1))
        if hour is None:
            return d.isoformat()
        t = time(int(hour), int(minute or 0), int(second or 0))
    except ValueError:
        return None
    text = f"{d.isoformat()}T{t.isoformat()}"
    if zone:
        if zone == "Z":
            return text + "+00:00"
        digits = re.sub(r"\D", "", zone)
        if len(digits) >= 2:
            return f"{text}{zone[0]}{digits[:2]}:{(digits[2:4] or '00')}"
    return text


def _cell_text(value) -> str:
    """A spreadsheet or CSV value as one line of table text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return " ".join(clean(str(value)).split())


def table_width(rows: list[list[str]]) -> int:
    """The number of columns once columns empty in every row at the right are dropped."""
    width = max((len(r) for r in rows), default=0)
    while width and all(len(r) < width or not str(r[width - 1]).strip() for r in rows):
        width -= 1
    return width


def markdown_table(rows: list[list[str]], width: int | None = None) -> str:
    """A GitHub-style table: the first row is the header. Columns empty in
    every row at the right are dropped (or the table is cut or padded to
    width); | in a cell is escaped."""
    rows = [[" ".join(clean(c).split()).replace("|", "\\|") for c in r] for r in rows]
    if width is None:
        width = table_width(rows)
    if not width:
        return ""
    rows = [(r + [""] * width)[:width] for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _quiet(*names: str) -> None:
    """Silence the warnings pdf libraries log for every odd object in a file."""
    for name in names:
        logging.getLogger(name).setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _convert_pdf(data: bytes, limits: Limits) -> Converted:
    """Pages are laid out with pdfminer.six (paragraphs rebuilt from its
    text lines, in its reading order) until Limits.layout_seconds is spent,
    and read with pypdf after that, so a long PDF keeps a bounded time.
    pypdf opens the file first (encryption, metadata, page count) and every
    page's content stream is unpacked through its limits before pdfminer,
    which has none, sees the file."""
    if b"%PDF-" not in data[:1024]:
        raise DocumentError("not a PDF file")
    _quiet("pypdf", "pdfminer")
    reader = title = created = None
    count = None
    try:
        reader, title, created, count = _pdf_open(data)
    except DocumentError:
        raise
    except Exception as e:
        if _is_limit_error(e):
            raise DocumentError(_PDF_BOMB) from None
        reader = None          # pypdf cannot read it: pdfminer alone
    wanted = min(count, limits.max_pages) if reader is not None else limits.max_pages
    if reader is not None:
        _check_pdf_streams(reader, wanted)
    try:
        layout = _pdf_layout(data, limits, wanted)
    except Exception:
        if reader is None:
            raise
        layout = None
    layout = layout or []
    if reader is None:
        if not layout:
            raise DocumentError("corrupt or unreadable PDF file")
        count = wanted = len(layout)
    texts = [text for text, _ in layout]
    for index in range(wanted):
        if index < len(texts) and texts[index].strip():
            continue
        raw = (reader.pages[index].extract_text() or "") if reader is not None else ""
        text = _clean_pdf_text(raw)
        if index < len(texts):
            texts[index] = text
        else:
            texts.append(text)
    if _junk_title(title):
        heading = layout[0][1] if layout else None
        title = heading if not _junk_title(heading) else None
    out = Converted("converted", title=title, date=created, pages=count)
    if not any(t.strip() for t in texts):
        out.status, out.reason = "no_text", "no text layer"
        return out
    parts = [f"## Page {number}\n\n{text}" for number, text in enumerate(texts, start=1) if text]
    if count is not None and count > len(texts):
        out.notes.append(f"Pages after the first {len(texts):,} are not converted.")
    out.text = "\n\n".join(parts)
    return out


def _is_limit_error(error: Exception) -> bool:
    try:
        from pypdf.errors import LimitReachedError
    except ImportError:
        return False
    return isinstance(error, LimitReachedError)


_PDF_BOMB = "a compressed stream unpacks past the safety limit (possible PDF bomb)"


def _check_pdf_streams(reader, pages: int) -> None:
    """Unpack each page's content stream through pypdf's limits; a stream
    over them fails the document. extract_text can skip a page unread, so
    this is what stands between pdfminer.six and a bomb."""
    for index in range(pages):
        try:
            contents = reader.pages[index].get_contents()
            if contents is not None:
                contents.get_data()
        except Exception as e:
            if _is_limit_error(e):
                raise DocumentError(_PDF_BOMB) from None


def _pdf_open(data: bytes):
    """(reader, metadata title, date, page count) from pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception:
            opened = 0
        if not opened:
            raise DocumentError("encrypted (needs a password)")
    meta = reader.metadata or {}
    title = meta.get("/Title")
    created = parse_pdf_date(meta.get("/CreationDate")) or parse_pdf_date(meta.get("/ModDate"))
    return reader, (str(title) if title else None), created, len(reader.pages)


# A line that starts a list item: a bullet, "1." or "1)", or "(a)"
_RE_LIST_START = re.compile(r"^(?:[\u2022\u25cf\u25aa\u25e6\u2023\u2043\u2013*-]\s|\d{1,3}[.)]\s|\([a-z0-9]{1,4}\)\s)")
_RE_BULLET = re.compile(r"^[\u2022\u25cf\u25aa\u25e6\u2023\u2043]\s*")
_RE_TERMINAL = re.compile(r"[.!?:;]['\")\u2019\u201d]?$")


def _join_lines(left: str, right: str) -> str:
    """Two lines of one paragraph: a word broken by a hyphen at the line
    end keeps its hyphen and loses the space (no dictionary can tell
    "main-tenance" from "well-known", so nothing is lost)."""
    if len(left) > 1 and left.endswith("-") and left[-2].isalpha() and right[:1].islower():
        return left + right
    return left + " " + right


def _paragraph_start(text: str) -> str:
    """A paragraph's first line, with a bullet sign made a Markdown "- "."""
    return _RE_BULLET.sub("- ", text, count=1)


def _pdf_layout(data: bytes, limits: Limits, pages: int):
    """[(page text, heading or None)] from pdfminer.six for the first pages,
    stopping once Limits.layout_seconds is spent; None when pdfminer is not
    installed."""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LAParams
    except ImportError:
        return None
    import time

    out = []
    started = time.monotonic()
    for layout in extract_pages(io.BytesIO(data), laparams=LAParams(), maxpages=pages):
        out.append(_layout_page(layout))
        if time.monotonic() - started > limits.layout_seconds:
            break
    return out


def _pdf_pdfminer(data: bytes, limits: Limits) -> list[str] | None:
    """Per-page text from pdfminer.six, or None when it is not installed."""
    layout = _pdf_layout(data, limits, limits.max_pages)
    return None if layout is None else [text for text, _ in layout]


def _layout_page(layout) -> tuple[str, str | None]:
    """A page's paragraphs and its heading (the first paragraph set clearly
    larger than the page's usual text), from pdfminer's text lines. A line
    joins the paragraph above it when it sits just below, at about the same
    size, in the same column, and does not start a list item."""
    from pdfminer.layout import LTTextBox, LTTextLine

    lines = []
    for box in layout:
        if not isinstance(box, LTTextBox):
            continue
        for line in box:
            if not isinstance(line, LTTextLine):
                continue
            text = " ".join(clean(line.get_text().translate(_LIGATURES)).split())
            if text:
                lines.append((text, line.x0, line.x1, line.y0, line.y1))
    paragraphs: list[list] = []     # [text, x0, x1, y0, y1, first line height]
    for text, x0, x1, y0, y1 in lines:
        height = y1 - y0
        if paragraphs and not _RE_LIST_START.match(text):
            para = paragraphs[-1]
            p_height = para[4] - para[3]
            tall = max(height, p_height) or 1.0
            if (abs(height - p_height) <= 0.25 * tall
                    and -0.5 * tall <= para[3] - y1 <= 0.9 * tall
                    and x0 < para[2] and x1 > para[1]):
                para[0] = _join_lines(para[0], text)
                para[1:5] = [min(para[1], x0), max(para[2], x1), y0, y1]
                continue
        paragraphs.append([_paragraph_start(text), x0, x1, y0, y1, height])
    heading = None
    heights = sorted(p[5] for p in paragraphs)
    if heights:
        usual = heights[len(heights) // 2]
        for para in paragraphs:
            if para[5] >= 1.25 * usual and re.search(r"[^\W\d_]{2}", para[0]):
                heading = para[0][:300]
                break
    text = "\n\n".join(_escape_line_starts(p[0]) for p in paragraphs)
    return text, heading


def _clean_pdf_text(text: str) -> str:
    """pypdf page text (pages past the layout budget) as paragraphs: lines
    joined unless the next starts a list item or the line ends a sentence
    well short of the page's longest line, which is how a paragraph ends;
    ligatures spelled out, hyphens at line ends kept, line starts escaped."""
    text = clean(text.translate(_LIGATURES))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    width = max((len(line) for line in lines), default=0)
    paragraphs: list[str] = []
    last = ""
    for line in lines:
        if not line:
            last = ""
            continue
        if paragraphs and last and not _RE_LIST_START.match(line) and not (
                _RE_TERMINAL.search(last) and len(last) < 0.75 * width):
            paragraphs[-1] = _join_lines(paragraphs[-1], line)
        else:
            paragraphs.append(_paragraph_start(line))
        last = line
    return "\n\n".join(_escape_line_starts(p) for p in paragraphs)


_RE_FILE_LIKE = re.compile(r"\.(?:docx?|pdf|indd|qxd|qxp|rtf|odt|xlsx?|pptx?|tmp|tex|dvi|ps)$", re.IGNORECASE)
_PLACEHOLDER_TITLES = {"untitled", "document", "title", "presentation", "unknown", "none", "null"}


def _junk_title(title: str | None) -> bool:
    """Whether a metadata title is not a title: empty, no word of three
    letters, a single token with digits, underscores, dots or slashes (a
    code or a file name, such as "ch3199paga"), a file name, or a
    placeholder such as "Untitled"."""
    if not title:
        return True
    text = " ".join(str(title).split())
    if not re.search(r"[^\W\d_]{3}", text):
        return True
    if " " not in text and re.search(r"[\d_./\\]", text):
        return True
    if _RE_FILE_LIKE.search(text):
        return True
    return text.lower() in _PLACEHOLDER_TITLES


# ---------------------------------------------------------------------------
# Zip-based formats
# ---------------------------------------------------------------------------

def check_zip(data: bytes, kind: str, limits: Limits) -> None:
    """Refuse a DOCX or XLSX file before any library opens it: not a zip
    archive, encrypted, too many parts, or more unpacked bytes than the cap
    (a zip bomb). zipfile never unpacks more than a part declares, so the
    declared sizes bound the work."""
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise DocumentError(f"not a {kind.upper()} file (password-protected or old binary format)")
    if data[:4] != b"PK\x03\x04":
        raise DocumentError(f"not a {kind.upper()} file")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        raise DocumentError(f"corrupt {kind.upper()} file (not a readable zip archive)") from None
    if len(infos) > limits.max_zip_entries:
        raise DocumentError(f"too many parts in the archive ({len(infos):,})")
    if any(info.flag_bits & 0x1 for info in infos):
        raise DocumentError("encrypted (needs a password)")
    unpacked = sum(info.file_size for info in infos)
    if unpacked > limits.max_unzipped_bytes:
        raise DocumentError(
            f"unpacks to more than {limits.max_unzipped_bytes // MB} MB (possible zip bomb)")
    for info in infos:
        name = info.filename.lower()
        streamed = kind == "xlsx" and name.startswith("xl/worksheets/")   # read row by row
        if name.endswith((".xml", ".rels")) and not streamed \
                and info.file_size > limits.max_xml_part_bytes:
            raise DocumentError(f"a part unpacks to more than {limits.max_xml_part_bytes // MB} MB "
                                "(possible zip bomb)")


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _docx_blocks(body):
    """Paragraph and table elements of a document body in order, including
    those inside content controls."""
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else ""
        if tag in ("p", "tbl"):
            yield tag, child
        elif tag == "sdt":
            for content in child.iterchildren():
                if isinstance(content.tag, str) and content.tag.endswith("}sdtContent"):
                    yield from _docx_blocks(content)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(element) -> str:
    """All visible text under element: runs, hyperlinks, tracked-change
    insertions and nested tables included (deleted text is stored as
    w:delText and left out), tabs and breaks as spaces, paragraphs
    separated by a space."""
    parts: list[str] = []
    for node in element.iter(f"{_W}t", f"{_W}tab", f"{_W}br", f"{_W}cr", f"{_W}p"):
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        else:
            parts.append(" ")
    return " ".join(clean("".join(parts)).split())


def _docx_style_names(document) -> tuple[dict[str, str], str]:
    """styleId -> style name (as Word shows it: "Heading 1") and the default
    paragraph style's name, read once from the styles part. Looking the
    style up per paragraph through python-docx costs about a millisecond
    each, which a document of many tiny paragraphs turns into minutes."""
    from docx.styles import BabelFish

    names: dict[str, str] = {}
    default = ""
    try:
        styles = document.styles.element
    except Exception:
        return names, default
    for style in styles.iterchildren(f"{_W}style"):
        name_el = style.find(f"{_W}name")
        name = BabelFish.internal2ui((name_el.get(f"{_W}val") if name_el is not None else "") or "")
        style_id = style.get(f"{_W}styleId")
        if style_id:
            names[style_id] = name
        if style.get(f"{_W}type") == "paragraph" and style.get(f"{_W}default") in ("1", "true", "on"):
            default = name
    return names, default


def _convert_docx(data: bytes, limits: Limits) -> Converted:
    check_zip(data, "docx", limits)
    import docx
    from docx.table import Table

    document = docx.Document(io.BytesIO(data))
    core = document.core_properties
    meta_title = _clean_title(core.title)
    if _junk_title(meta_title):
        meta_title = None
    created = _iso(core.created) or _iso(core.modified)

    style_names, default_style = _docx_style_names(document)
    blocks: list[tuple[str, int, str]] = []    # (kind, level, text)
    for tag, element in _docx_blocks(document.element.body):
        if tag == "tbl":
            table = Table(element, document)
            rows = []
            for row in table.rows:
                if len(rows) > limits.max_rows:
                    break
                rows.append([_docx_text(cell._tc) for cell in row.cells][:limits.max_columns])
            text = markdown_table(rows[:limits.max_rows + 1])
            if text:
                if len(rows) > limits.max_rows:
                    text += f"\n\n_Rows after the first {limits.max_rows:,} are not shown._"
                blocks.append(("table", 0, text))
            continue
        text = _docx_text(element)
        if not text:
            continue
        # Read from the XML: no python-docx Paragraph or style lookup per paragraph
        ppr = element.find(f"{_W}pPr")
        style_el = ppr.find(f"{_W}pStyle") if ppr is not None else None
        style_id = style_el.get(f"{_W}val") if style_el is not None else None
        style = (style_names.get(style_id, default_style) if style_id else default_style) or ""
        num_pr = ppr.find(f"{_W}numPr") if ppr is not None else None
        heading = re.fullmatch(r"Heading (\d)", style)
        numbered = num_pr is not None
        if style == "Title":
            blocks.append(("title", 0, text))
        elif heading:
            blocks.append(("heading", int(heading.group(1)), text))
        elif style.startswith("List") or numbered:
            level = 0
            ilvl = num_pr.find(f"{_W}ilvl") if numbered else None
            if ilvl is not None:
                try:
                    level = min(int(ilvl.get(f"{_W}val")), 8)
                except (TypeError, ValueError):
                    level = 0
            marker = "1." if "Number" in style else "-"
            blocks.append(("item", level, f"{marker} {_escape_line_starts(text)}"))
        else:
            blocks.append(("para", 0, _escape_line_starts(text)))

    title = meta_title
    used = None
    if not title:
        for index, (kind, level, text) in enumerate(blocks):
            if kind == "title" or kind == "heading":
                title, used = text, index
                break
    parts: list[str] = []
    previous = None
    for index, (kind, level, text) in enumerate(blocks):
        if index == used:
            continue
        if kind == "item":
            line = "  " * level + text
            marker = text.split(" ", 1)[0]
            if previous == ("item", marker):
                parts[-1] += "\n" + line
            else:
                parts.append(line)
            previous = ("item", marker)
            continue
        elif kind in ("title", "heading"):
            parts.append("#" * min((level or 1) + 1, 6) + " " + text)
        else:
            parts.append(text)
        previous = kind
    out = Converted("converted", title=title, date=created)
    if not parts:
        out.status, out.reason = "no_text", "no text"
        return out
    out.text = "\n\n".join(parts)
    return out


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def table_sections(rows: list[list[str]], truncated: bool, limits: Limits, level: int,
                   label: str = "") -> str:
    """A table as Markdown. One with more than limits.section_rows data rows
    is cut into sections of that many, each under its own heading ("Rows 41
    to 80", after label when given) and with the header row repeated, so a
    chunk taken from anywhere in it still says what its columns are."""
    width = table_width(rows)
    if not width:
        return ""
    header, data = rows[0], rows[1:]
    size = max(limits.section_rows, 1)
    if len(data) <= size:
        text = markdown_table(rows, width)
    else:
        parts = []
        for start in range(0, len(data), size):
            chunk = data[start:start + size]
            name = f"Rows {start + 1:,} to {start + len(chunk):,}"
            heading = f"{label}: {name.lower()}" if label else name
            parts.append(f"{'#' * level} {_escape_line_starts(heading)}\n\n"
                         + markdown_table([header] + chunk, width))
        text = "\n\n".join(parts)
    if truncated:
        text += f"\n\n_Rows after the first {limits.max_rows:,} are not shown._"
    return text


def _table_rows(rows_iter, limits: Limits, notes: list[str],
                label: str = "") -> tuple[list[list[str]], bool, int]:
    """Non-empty rows, at most a header and limits.max_rows data rows,
    whether rows were left out, and how many data rows were read in all
    (rows past the display cap are counted, not kept). Columns beyond
    limits.max_columns are cut, and reading stops after
    limits.max_scan_rows rows (a sheet with a far away cell yields a
    million empty rows), each with a note."""
    rows: list[list[str]] = []
    truncated = False
    wide = False
    counted = 0
    for scanned, raw in enumerate(rows_iter):
        if scanned >= limits.max_scan_rows:
            where = f" of {label}" if label else ""
            notes.append(f"Rows after row {limits.max_scan_rows:,}{where} are not read.")
            break
        raw = list(raw or ())
        if len(raw) > limits.max_columns:
            wide = wide or any(v not in (None, "") for v in raw[limits.max_columns:])
            raw = raw[:limits.max_columns]
        if len(rows) > limits.max_rows:
            # Past the display cap: only count the rows that hold something
            if any(v not in (None, "") for v in raw):
                truncated = True
                counted += 1
            continue
        cells = [_cell_text(v) for v in raw]
        if not any(cells):
            continue
        rows.append(cells)
        counted += 1
    if wide:
        where = f" in {label}" if label else ""
        notes.append(f"Columns after the first {limits.max_columns}{where} are not shown.")
    return rows, truncated, max(counted - 1, 0)


def _core_dates(data: bytes) -> tuple[str | None, str | None]:
    """(modified, created) as ISO 8601 from an OOXML file's docProps/core.xml,
    each None when the file does not state it (openpyxl would put the
    current time in a missing one)."""
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read("docProps/core.xml"))
    except (KeyError, zipfile.BadZipFile, ET.ParseError, ValueError, OSError):
        return None, None
    found = {}
    for name in ("modified", "created"):
        node = root.find(f"{{http://purl.org/dc/terms/}}{name}")
        text = (node.text or "").strip() if node is not None else ""
        try:
            found[name] = _iso(datetime.fromisoformat(text.replace("Z", "+00:00"))) if text else None
        except ValueError:
            found[name] = None
    return found["modified"], found["created"]


def _convert_xlsx(data: bytes, limits: Limits) -> Converted:
    check_zip(data, "xlsx", limits)
    import openpyxl

    # read_only streams the sheets; data_only gives each formula's cached
    # value (formulas are never calculated); keep_vba=False drops macros
    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True,
                                      keep_vba=False, keep_links=False)
    try:
        props = workbook.properties
        title = props.title if props is not None else None
        # A workbook is updated in place, so its last save says more than
        # when it was first made; both come from the file itself
        modified, first_made = _core_dates(data)
        created = modified or first_made
        sheets = list(workbook.worksheets)
        parts: list[str] = []
        notes: list[str] = []
        total_rows = 0
        for sheet in sheets:
            if not hasattr(sheet, "iter_rows"):
                continue
            if hasattr(sheet, "reset_dimensions"):
                # A sheet's stated size is not trusted: read the cells it has
                sheet.reset_dimensions()
            name = " ".join(clean(str(sheet.title)).split())
            rows, truncated, counted = _table_rows(sheet.iter_rows(values_only=True), limits, notes,
                                                   f"sheet {name}")
            table = table_sections(rows, truncated, limits, 3, name or "Sheet")
            if not table:
                continue
            total_rows += counted
            parts.append(f"## {_escape_line_starts(name) or 'Sheet'}\n\n{table}")
    finally:
        workbook.close()
    out = Converted("converted", title=None if _junk_title(title) else title, date=created,
                    sheets=len(sheets), rows=total_rows)
    if not parts:
        out.status, out.reason = "no_text", "no data"
        return out
    out.text = "\n\n".join(parts)
    out.notes.extend(notes)
    return out


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def decode_text(data: bytes) -> str:
    """Bytes of a text file as a string: UTF-8 (a byte order mark is
    dropped), UTF-16 with a byte order mark, else Windows-1252."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data[:4096]:
        raise DocumentError("not a CSV file (binary data)")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _convert_csv(data: bytes, limits: Limits) -> Converted:
    text = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    notes: list[str] = []
    try:
        rows, truncated, counted = _table_rows(csv.reader(io.StringIO(text), dialect), limits, notes)
    except csv.Error as e:
        raise DocumentError(f"corrupt CSV file ({e})") from None
    table = table_sections(rows, truncated, limits, 2)
    out = Converted("converted", rows=counted)
    if not table:
        out.status, out.reason, out.rows = "no_text", "no data", None
        return out
    out.text = table
    out.notes.extend(notes)
    return out


# ---------------------------------------------------------------------------
# Child process
# ---------------------------------------------------------------------------

def convert_path(path: str, kind: str, limits: dict | None = None) -> dict:
    """convert_document on a saved file, with limits and result as plain
    dicts (the child process sends them as JSON)."""
    import dataclasses

    with open(path, "rb") as f:
        data = f.read()
    return dataclasses.asdict(convert_document(data, kind, Limits(**limits) if limits else None))
