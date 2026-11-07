"""Document conversion (scrapepy/convert/documents.py) and the --documents
helpers, on small synthetic documents from tests/document_samples.py.

Golden files in tests/golden_documents/ hold the expected Markdown for each
type; they were written from this converter's output and checked by hand.
"""

import argparse
import os
from datetime import datetime
from pathlib import Path

import pytest

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.convert.documents import (
    Limits, convert_document, markdown_table, parse_pdf_date, _clean_pdf_text,
)
from scrapepy.output.writer import UnsafePathError, write_within
from scrapepy.processing.documents import (
    _canonical, _same_site, document_type, parse_document_types, safe_relpath,
)

from .document_samples import (
    add_zip_part, declare_huge_size, encrypt_pdf, make_docx, make_pdf, make_xlsx,
    with_cached_formula_value, zip_bomb,
)

GOLDEN = Path(__file__).parent / "golden_documents"


def rendered(result) -> str:
    return (f"status: {result.status}\ntitle: {result.title}\ndate: {result.date}\n"
            f"pages: {result.pages}\n\n{result.text}\n")


def golden_pdf():
    return make_pdf([["Annual report 2025", "Total members: 42", "# not a heading", "(draft)"],
                     [], ["Second page text"]],
                    {"Title": "Garden Club Annual Report", "CreationDate": "D:20250314093000+01'00'"})


def golden_xlsx():
    data = make_xlsx({"Rota": [["Plot", "Holder", "Size", "Due"],
                               ["A1", "Sam", 2.0, datetime(2025, 7, 1)],
                               ["A2", "Kim", 3.5, None], [None, None, None, None],
                               ["A3", True, None, None]],
                      "Empty": [], "Notes | misc": [["Note"], ["Bring | share"]]})
    return with_cached_formula_value(data, "C3", "C2*2", "7")


@pytest.mark.parametrize("name,kind,build", [
    ("pdf", "pdf", golden_pdf),
    ("docx", "docx", make_docx),
    ("xlsx", "xlsx", golden_xlsx),
    ("csv", "csv", lambda: "name;count;note\r\napples;3;ripe\r\npears;5;\"a|b\"\r\n".encode("cp1252")),
])
def test_golden(name, kind, build):
    assert rendered(convert_document(build(), kind)) == (GOLDEN / f"{name}.md").read_text(encoding="utf-8")


def test_pdf_without_text_layer():
    result = convert_document(make_pdf([[], []]), "pdf")
    assert (result.status, result.reason, result.pages) == ("no_text", "no text layer", 2)


def test_encrypted_pdf_fails_cleanly():
    result = convert_document(encrypt_pdf(golden_pdf()), "pdf")
    assert (result.status, result.reason) == ("failed", "encrypted (needs a password)")


def test_pdf_with_empty_user_password_is_read():
    result = convert_document(encrypt_pdf(make_pdf([["Open to all"]]), password=""), "pdf")
    assert result.status == "converted" and "Open to all" in result.text


@pytest.mark.parametrize("data,kind", [
    (b"%PDF-1.4\nnot a pdf at all\n", "pdf"),
    (b"just text", "pdf"),
    (make_docx()[:1500], "docx"),
    (make_xlsx({"S": [["a"]]})[:900], "xlsx"),
    (b"PK\x03\x04garbage", "docx"),
    (make_xlsx({"S": [["a"]]}), "docx"),       # a valid zip that is not a Word file
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "xlsx"),
    (b"a,b\n\x00\x01\x02", "csv"),
])
def test_corrupt_files_fail_without_raising(data, kind):
    result = convert_document(data, kind)
    assert result.status == "failed"
    assert result.reason


def test_zip_bomb_refused_before_unpacking():
    result = convert_document(zip_bomb("xlsx", unpacked_mb=3), "xlsx", Limits(max_unzipped_bytes=2 * 1024 * 1024))
    assert result.reason == "unpacks to more than 2 MB (possible zip bomb)"
    assert convert_document(zip_bomb("xlsx", unpacked_mb=1), "xlsx",
                            Limits(max_unzipped_bytes=2 * 1024 * 1024)).reason != result.reason


def test_crafted_sizes_refused_at_default_cap():
    result = convert_document(declare_huge_size(make_docx()), "docx")
    assert result.reason == "unpacks to more than 256 MB (possible zip bomb)"


def test_too_many_zip_parts_refused():
    data = make_docx()
    result = convert_document(data, "docx", Limits(max_zip_entries=5))
    assert result.status == "failed" and result.reason.startswith("too many parts")


def test_macros_are_ignored():
    data = add_zip_part(make_xlsx({"S": [["a", "b"], ["1", "2"]]}), "xl/vbaProject.bin", b"\x00macro")
    result = convert_document(data, "xlsx")
    assert result.status == "converted" and "| 1 | 2 |" in result.text


def test_xlsx_row_cap_and_note():
    rows = [["n", "square"]] + [[i, i * i] for i in range(1, 11)]
    result = convert_document(make_xlsx({"Numbers": rows}), "xlsx", Limits(max_rows=4))
    lines = result.text.splitlines()
    assert lines[-3] == "| 4 | 16 |"
    assert lines[-1] == "_Rows after the first 4 are not shown._"
    assert "| 5 | 25 |" not in result.text


def test_row_cap_exact_fit_has_no_note():
    rows = [["n"]] + [[i] for i in range(1, 5)]
    result = convert_document(make_xlsx({"S": rows}), "xlsx", Limits(max_rows=4))
    assert "not shown" not in result.text and "| 4 |" in result.text


def test_csv_row_cap():
    data = ("n\n" + "".join(f"{i}\n" for i in range(1, 2002))).encode()
    result = convert_document(data, "csv")
    assert "| 2000 |" in result.text and "| 2001 |" not in result.text
    assert result.text.endswith("_Rows after the first 2,000 are not shown._")


def test_column_cap():
    result = convert_document(b"a,b,c,d\n1,2,3,4\n", "csv", Limits(max_columns=2))
    assert result.text.splitlines()[0] == "| a | b |"
    assert result.text.endswith("_Columns after the first 2 are not shown._")


def test_character_cap():
    lines = [f"Line {i} of a long document" for i in range(40)]
    result = convert_document(make_pdf([lines]), "pdf", Limits(max_chars=200))
    body, note = result.text.rsplit("\n\n", 1)
    assert len(body) <= 200
    assert note == "_Text after the first 200 characters is not shown._"


def test_pdf_page_cap():
    result = convert_document(make_pdf([["one"], ["two"], ["three"]]), "pdf", Limits(max_pages=2))
    assert "## Page 2" in result.text and "three" not in result.text
    assert result.pages == 3
    assert result.text.endswith("_Pages after the first 2 are not converted._")


def test_control_characters_removed():
    result = convert_document(b"name\nbell\x07here\nesc\x1b[31mred\n", "csv")
    assert "\x07" not in result.text and "\x1b" not in result.text
    assert "| bellhere |" in result.text


def test_pdf_text_clean_up():
    assert _clean_pdf_text("efﬁcient workﬂow\nmain-\ntenance\nWell-\nKnown\n\n\n\nend  ") == (
        "efficient workflow\nmaintenance\nWell-\nKnown\n\nend")
    assert _clean_pdf_text("# Title\n> quote") == "\\# Title\n\\> quote"


def test_docx_without_text():
    import docx, io
    buf = io.BytesIO()
    docx.Document().save(buf)
    result = convert_document(buf.getvalue(), "docx")
    assert (result.status, result.reason) == ("no_text", "no text")


def test_docx_title_from_first_heading_is_not_repeated():
    result = convert_document(make_docx(title=None), "docx")
    assert result.title == "Welcome"
    assert result.text.startswith("Members meet")


def test_markdown_table_escaping_and_trailing_columns():
    assert markdown_table([["a", "b|c", ""], ["1\n2", "", ""]]) == (
        "| a | b\\|c |\n| --- | --- |\n| 1 2 |  |")
    assert markdown_table([["", ""]]) == ""


@pytest.mark.parametrize("raw,iso", [
    ("D:20250314", "2025-03-14"),
    ("D:2025", "2025-01-01"),
    ("D:20250314093000Z", "2025-03-14T09:30:00+00:00"),
    ("D:20250314093000+01'00'", "2025-03-14T09:30:00+01:00"),
    ("D:20250314093000-0530", "2025-03-14T09:30:00-05:30"),
    ("20250314093000", "2025-03-14T09:30:00"),
    ("D:20251399", None),
    ("yesterday", None),
    (None, None),
])
def test_parse_pdf_date(raw, iso):
    assert parse_pdf_date(raw) == iso


# --- detection, names and paths ---------------------------------------------

def cfg(**kw):
    return ScrapeConfig(url="https://example.com/", **kw)


def test_document_type_only_with_documents():
    off = cfg()
    on = cfg(documents=["pdf", "docx"])
    assert document_type("/a/report.pdf", off) is None
    assert document_type("/a/report.PDF", on) == "pdf"
    assert document_type("https://example.com/a/b.docx?x=1#top", on) == "docx"
    assert document_type("/a/b.doc", on) == "doc"           # old format of a listed type
    assert document_type("/a/b.xls", on) is None            # xlsx not listed
    assert document_type("/a/b.xlsx", on) is None
    assert document_type("/a/pdf", on) is None


def test_parse_document_types():
    assert parse_document_types(" PDF,csv,pdf, .docx ") == ["pdf", "csv", "docx"]
    with pytest.raises(ValueError, match="unknown type 'txt'"):
        parse_document_types("pdf,txt")
    with pytest.raises(ValueError, match="old binary format"):
        parse_document_types("doc")
    with pytest.raises(ValueError, match="at least one"):
        parse_document_types(" , ")


def test_safe_relpath():
    assert safe_relpath("/reports/2025/annual.pdf") == os.path.join("reports", "2025", "annual.pdf")
    assert safe_relpath("/a/b:c?.pdf") == os.path.join("a", "b_c_.pdf")
    assert safe_relpath("/../../etc/passwd.pdf") == os.path.join("etc", "passwd.pdf")
    assert safe_relpath("/a/%2e%2e%2Fb.pdf") == os.path.join("a", "%2e%2e%2Fb.pdf")


def test_same_site():
    c = ScrapeConfig(url="https://Example.com:8443/start")
    assert _same_site("https://example.com:8443/x.pdf", c)
    assert not _same_site("https://example.com/x.pdf", c)
    assert not _same_site("http://example.com:8443/x.pdf", c)
    assert not _same_site("https://evil.com:8443/x.pdf", c)
    assert not _same_site("https://user@example.com:8443/x.pdf", c)
    assert not _same_site("https://example.com:bad/x.pdf", c)


def test_canonical_from_link_header():
    c = cfg()
    url = "https://example.com/files/r.pdf?v=2"
    assert _canonical({"Link": '<https://example.com/files/r.pdf>; rel="canonical"'}, url, c) == \
        "https://example.com/files/r.pdf"
    assert _canonical({"link": '</other.pdf>; rel=canonical'}, url, c) == "https://example.com/other.pdf"
    assert _canonical({"Link": '<https://cdn.example.net/r.pdf>; rel="canonical"'}, url, c) is None
    assert _canonical({"Link": '<https://example.com/next>; rel="next"'}, url, c) is None
    assert _canonical({}, url, c) is None


def test_write_within(tmp_path):
    root = tmp_path / "root"
    write_within(str(root / "a" / "b.bin"), b"\x00\x01", str(root))
    assert (root / "a" / "b.bin").read_bytes() == b"\x00\x01"
    write_within(str(root / "a" / "c.md"), "café", str(root))
    assert (root / "a" / "c.md").read_text(encoding="utf-8") == "café"
    with pytest.raises(UnsafePathError, match="outside"):
        write_within(str(tmp_path / "elsewhere.bin"), b"x", str(root))
    with pytest.raises(UnsafePathError, match="outside"):
        write_within(str(root / ".." / "escape.bin"), b"x", str(root))
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "link")
    with pytest.raises(UnsafePathError, match="symbolic link"):
        write_within(str(root / "link" / "x.bin"), b"x", str(root))
    os.symlink(outside / "target.bin", root / "file-link.bin")
    with pytest.raises(UnsafePathError, match="symbolic link"):
        write_within(str(root / "file-link.bin"), b"x", str(root))
    assert list(outside.iterdir()) == []


# --- command line -------------------------------------------------------------

def test_cli_documents_options():
    config = parse_args(["https://example.com/", "--documents", "pdf,CSV"])
    assert config.documents == ["pdf", "csv"] and config.max_document_mb == 25
    config = parse_args(["https://example.com/", "--documents", "xlsx", "--max-document-mb", "200"])
    assert config.max_document_mb == 200
    assert parse_args(["https://example.com/"]).documents == []
    assert config.files_dir.endswith(os.path.join("files", "example_com"))


@pytest.mark.parametrize("argv", [
    ["--documents", "txt"],
    ["--documents", "doc"],
    ["--documents", "pdf", "--max-document-mb", "0"],
    ["--documents", "pdf", "--max-document-mb", "201"],
    ["--max-document-mb", "10"],
    ["--documents", "pdf", "--no-markdown"],
    ["--documents", "pdf", "-1"],
    ["--documents", "pdf", "--feed"],
    ["--documents", "pdf", "--swagger"],
])
def test_cli_documents_errors(argv):
    with pytest.raises(SystemExit) as e:
        parse_args(["https://example.com/"] + argv)
    assert e.value.code == 2
