"""Small synthetic documents for the --documents tests.

Every document here is generated: PDFs by a tiny writer below (Helvetica
text, one content stream per page), DOCX with python-docx, XLSX with
openpyxl. No real document is committed.
"""

import io
import zipfile
from datetime import datetime


def _pdf_string(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def make_pdf(pages: list[list[str]], info: dict[str, str] | None = None) -> bytes:
    """A PDF with one page per list of lines (an empty list gives a page
    with no text layer) and an optional Info dictionary."""
    count = len(pages)
    font_id = 3
    first_page_id = 4
    info_id = first_page_id + 2 * count
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (b"<< /Type /Pages /Count %d /Kids [" % count
            + b" ".join(b"%d 0 R" % (first_page_id + 2 * i) for i in range(count)) + b"] >>"),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, lines in enumerate(pages):
        page_id = first_page_id + 2 * i
        content_id = page_id + 1
        if lines:
            ops = b"BT /F1 12 Tf 14 TL 72 750 Td " + b" ".join(
                b"(" + _pdf_string(line) + b") Tj T*" for line in lines) + b" ET"
        else:
            ops = b"0 0 m 100 100 l S"      # a drawing, no text
        objects[page_id] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                            % (font_id, content_id))
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream"
    if info:
        objects[info_id] = b"<< " + b" ".join(
            b"/" + k.encode() + b" (" + _pdf_string(v) + b")" for k, v in info.items()) + b" >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        out += b"%010d 00000 n \n" % offsets[number] if number in offsets else b"0000000000 65535 f \n"
    trailer = b"<< /Size %d /Root 1 0 R" % size
    if info:
        trailer += b" /Info %d 0 R" % info_id
    out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def encrypt_pdf(data: bytes, password: str = "secret") -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=password, owner_password=password + "-owner", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_docx(title: str | None = "Garden Club Handbook", created: datetime | None = None) -> bytes:
    import docx

    document = docx.Document()
    props = document.core_properties
    props.title = title or ""
    props.created = created or datetime(2025, 3, 14, 9, 30, 0)
    props.modified = datetime(2025, 4, 1, 8, 0, 0)
    document.add_heading("Welcome", level=1)
    document.add_paragraph("Members meet on the first Saturday of each month.")
    document.add_heading("What to bring", level=2)
    document.add_paragraph("Gloves", style="List Bullet")
    document.add_paragraph("A trowel", style="List Bullet")
    document.add_paragraph("Water the seedlings", style="List Number")
    document.add_paragraph("# Not a heading, just text")
    table = document.add_table(rows=3, cols=2)
    for r, row in enumerate([("Plot", "Holder"), ("A1", "Sam | Lee"), ("A2", "Kim")]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def make_xlsx(sheets: dict[str, list[list]], title: str | None = "Plot rota",
              created: datetime | None = None, formula: bool = False,
              modified: str | None = "2025-11-03T09:00:00Z") -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    if formula:
        workbook.worksheets[0]["D2"] = "=B2*2"
    workbook.properties.title = title
    workbook.properties.created = created or datetime(2025, 6, 2, 12, 0, 0)
    buf = io.BytesIO()
    workbook.save(buf)
    # openpyxl stamps the save time as the modified date: set it afterwards
    import re as _re
    if modified is None:
        drop = lambda x: _re.sub(r"<dcterms:modified[^>]*>[^<]*</dcterms:modified>", "", x)
        return _replace_part(buf.getvalue(), "docProps/core.xml", drop)
    stamp = lambda x: _re.sub(r"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                              lambda m: m.group(1) + modified + m.group(2), x)
    return _replace_part(buf.getvalue(), "docProps/core.xml", stamp)


def with_cached_formula_value(xlsx: bytes, cell: str, formula: str, cached: str) -> bytes:
    """The workbook with cell (in the first sheet) set to a formula and a
    cached value, as Excel saves it."""
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                marker = f'<c r="{cell}"'
                start = text.index(marker)
                end = text.index("</c>", start) + 4
                text = text[:start] + (f'<c r="{cell}"><f>{formula}</f><v>{cached}</v></c>') + text[end:]
                data = text.encode("utf-8")
            dst.writestr(info, data)
    return out.getvalue()


def add_zip_part(data: bytes, name: str, content: bytes) -> bytes:
    """The zip archive with one more part (for a macro part, say)."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr(name, content)
    return out.getvalue()


def zip_bomb(kind: str = "docx", unpacked_mb: int = 3) -> bytes:
    """A zip that starts like an Office file and unpacks to unpacked_mb of zeros."""
    out = io.BytesIO()
    part = "word/document.xml" if kind == "docx" else "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(part, b"\0" * (unpacked_mb * 1024 * 1024))
    return out.getvalue()


def declare_huge_size(data: bytes, size: int = 4 * 1024 ** 3 - 1) -> bytes:
    """The zip with every central directory entry claiming size bytes
    unpacked, as a crafted bomb would."""
    out = bytearray(data)
    pos = 0
    while True:
        pos = out.find(b"PK\x01\x02", pos)
        if pos < 0:
            return bytes(out)
        out[pos + 24:pos + 28] = size.to_bytes(4, "little")
        pos += 4


def pdf_bomb(unpacked_mb: int = 80) -> bytes:
    """A one-page PDF whose content stream is Flate-compressed zeros that
    unpack to unpacked_mb (beyond pypdf's 75 MB stream limit at 80)."""
    import zlib

    packed = zlib.compress(b"0" * (unpacked_mb * 1024 * 1024), 9)
    data = make_pdf([["placeholder"]])
    start = data.index(b"4 0 obj")
    content = data.index(b"5 0 obj")
    end = data.index(b"endobj\n", content) + 7
    body = (b"5 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(packed)
            + packed + b"\nendstream\nendobj\n")
    rebuilt = data[:content] + body
    # Rewrite the cross-reference table for the new offsets
    import re as _re
    head = rebuilt
    offsets = {int(m.group(1)): m.start() for m in _re.finditer(rb"(\d+) 0 obj\n", head)}
    size = max(offsets) + 1
    xref = len(head)
    out = bytearray(head)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for n in range(1, size):
        out += b"%010d 00000 n \n" % offsets[n]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    assert start < content
    return bytes(out)


def docx_with_tracked_changes() -> bytes:
    """A DOCX with a tracked insertion, a tracked deletion and a nested table."""
    import docx

    document = docx.Document()
    document.add_paragraph("Base text.")
    outer = document.add_table(rows=1, cols=2)
    outer.cell(0, 0).text = "Outer cell"
    inner = outer.cell(0, 1).add_table(rows=1, cols=1)
    inner.cell(0, 0).text = "Nested cell text"
    buf = io.BytesIO()
    document.save(buf)
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    tracked = ('<w:p><w:r><w:t xml:space="preserve">Kept </w:t></w:r>'
               '<w:ins w:id="1" w:author="A" w:date="2025-01-01T00:00:00Z"><w:r><w:t>inserted words</w:t></w:r></w:ins>'
               '<w:del w:id="2" w:author="A" w:date="2025-01-01T00:00:00Z"><w:r><w:delText>deleted words</w:delText></w:r></w:del>'
               '</w:p>')
    return _replace_part(buf.getvalue(), "word/document.xml",
                         lambda x: x.replace("<w:body>", "<w:body>" + tracked, 1))


def _replace_part(data: bytes, name: str, change) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            part = src.read(info.filename)
            if info.filename == name:
                part = change(part.decode("utf-8")).encode("utf-8")
            dst.writestr(info, part)
    return out.getvalue()


def pad_part(data: bytes, name: str, extra_bytes: int) -> bytes:
    """The archive with an XML comment of extra_bytes added to one part."""
    comment = "<!--" + " " * extra_bytes + "-->"
    return _replace_part(data, name, lambda x: x.replace("?>", "?>" + comment, 1)
                         if "?>" in x else comment + x)


def with_encrypted_flag(data: bytes) -> bytes:
    """The archive with the 'encrypted' flag set on its first part, in the
    central directory (zipfile clears the flag when writing)."""
    out = bytearray(data)
    pos = out.find(b"PK\x01\x02")
    flags = int.from_bytes(out[pos + 8:pos + 10], "little") | 0x1
    out[pos + 8:pos + 10] = flags.to_bytes(2, "little")
    return bytes(out)


# Targets for the child-process tests (run in a spawned process)

def child_sleep(pid_path: str) -> None:
    import os, time
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(600)


def child_allocate(mb: int) -> int:
    block = bytearray(mb * 1024 * 1024)
    return len(block)


def child_add(a: int, b: int) -> int:
    return a + b


def docx_many_paragraphs(count: int = 470_000) -> bytes:
    """A small DOCX whose document.xml holds count one-letter paragraphs
    (about 16 MB unpacked at 470,000, just under the part cap), the shape
    that made python-docx's style lookup per paragraph slow."""
    import docx

    buf = io.BytesIO()
    docx.Document().save(buf)
    para = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
    return _replace_part(buf.getvalue(), "word/document.xml",
                         lambda x: x.replace("<w:body>", "<w:body>" + para * count, 1))


def docx_custom_styles() -> bytes:
    """A DOCX with a style whose id differs from its name ("MyHead" named
    "heading 2"), a paragraph with an unknown style id, and one with none."""
    import docx

    buf = io.BytesIO()
    docx.Document().save(buf)
    style = ('<w:style w:type="paragraph" w:styleId="MyHead"><w:name w:val="heading 2"/>'
             '<w:basedOn w:val="Normal"/></w:style>')
    data = _replace_part(buf.getvalue(), "word/styles.xml",
                         lambda x: x.replace("</w:styles>", style + "</w:styles>", 1))
    body = ('<w:p><w:pPr><w:pStyle w:val="MyHead"/></w:pPr><w:r><w:t>Custom heading</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:pStyle w:val="NoSuchStyle"/></w:pPr><w:r><w:t>Unknown style</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Plain paragraph</w:t></w:r></w:p>')
    return _replace_part(data, "word/document.xml",
                         lambda x: x.replace("<w:body>", "<w:body>" + body, 1))


def child_print_and_return(value: int) -> int:
    print("chatty library output", flush=True)
    return value


def child_print_and_exit(code: int) -> None:
    import os, sys
    print("chatty library output", flush=True)
    sys.stderr.write("boom\n")
    sys.stderr.flush()
    os._exit(code)


def child_segfault() -> None:
    import os, signal
    os.kill(os.getpid(), signal.SIGSEGV)


def child_spin() -> None:
    while True:
        pass


def child_environ() -> list:
    import os
    return sorted(os.environ)


def child_flood(mb: int, then_exit: bool = False) -> int:
    """Write mb MB to stdout and to stderr, then return (or exit with no result)."""
    import os
    block = b"x" * (1024 * 1024)
    for _ in range(mb):
        os.write(1, block)
        os.write(2, block)
    os.write(2, b"\nlast line of stderr\n")
    if then_exit:
        os._exit(5)
    return mb


def make_layout_pdf(pages: list[list[tuple[float, float, float, str]]],
                    info: dict[str, str] | None = None) -> bytes:
    """A PDF with text placed exactly: each page is a list of (x, y, size,
    text) in points from the bottom left, in Helvetica."""
    count = len(pages)
    first_page_id = 4
    info_id = first_page_id + 2 * count
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (b"<< /Type /Pages /Count %d /Kids [" % count
            + b" ".join(b"%d 0 R" % (first_page_id + 2 * i) for i in range(count)) + b"] >>"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    for i, items in enumerate(pages):
        page_id = first_page_id + 2 * i
        ops = b"\n".join(b"BT /F1 %g Tf %g %g Td (%s) Tj ET" % (size, x, y, _pdf_string(text))
                         for x, y, size, text in items)
        objects[page_id] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (page_id + 1))
        objects[page_id + 1] = b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream"
    if info:
        objects[info_id] = b"<< " + b" ".join(
            b"/" + k.encode() + b" (" + _pdf_string(v) + b")" for k, v in info.items()) + b" >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        out += b"%010d 00000 n \n" % offsets[number] if number in offsets else b"0000000000 65535 f \n"
    trailer = b"<< /Size %d /Root 1 0 R" % size
    if info:
        trailer += b" /Info %d 0 R" % info_id
    out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def layout_sample_pdf() -> bytes:
    """A page laid out like a leaflet: a large heading, a paragraph wrapped
    over three lines (one ending in a hyphen), a bulleted list whose second
    item wraps, a second paragraph after a gap, and a side column; plus a
    junk metadata title."""
    body = [
        (72, 720, 20, "Garden Club Leaflet"),
        (72, 690, 11, "Members meet on the first Saturday of"),
        (72, 676, 11, "each month in the community hall, where the well-"),
        (72, 662, 11, "known plant swap takes place."),
        (72, 636, 11, "\x95 Bring gloves"),
        (72, 622, 11, "\x95 Bring a trowel and a pot for"),
        (84, 608, 11, "seedlings"),
        (72, 580, 11, "Do not"),
        (72, 566, 11, "leave tools in the hall."),
        (430, 690, 9, "Side note:"),
        (430, 679, 9, "the hall opens"),
        (430, 668, 9, "at nine."),
    ]
    return make_layout_pdf([body], {"Title": "ch3199paga", "CreationDate": "D:20250314"})
