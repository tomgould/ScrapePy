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
    add_zip_part, declare_huge_size,
    docx_with_tracked_changes, encrypt_pdf, make_docx, make_pdf, make_xlsx, pad_part,
    pdf_bomb, with_cached_formula_value, with_encrypted_flag, zip_bomb,
)

GOLDEN = Path(__file__).parent / "golden_documents"


def rendered(result) -> str:
    return (f"status: {result.status}\ntitle: {result.title}\ndate: {result.date}\n"
            f"pages: {result.pages}\nsheets: {result.sheets}\nrows: {result.rows}\n\n{result.text}\n")


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


def golden_layout_pdf():
    from .document_samples import layout_sample_pdf
    return layout_sample_pdf()


@pytest.mark.parametrize("name,kind,build", [
    ("pdf", "pdf", golden_pdf),
    ("pdf_layout", "pdf", golden_layout_pdf),
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
    assert result.reason == "unpacks to more than 128 MB (possible zip bomb)"


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
    # pypdf text (pages past the layout budget): soft line breaks joined,
    # a sentence ending well short of the longest line ends a paragraph,
    # list items start their own, a hyphen at a line end is kept
    text = ("Identity theft places a burden on its victims and\n"
            "presents a challenge. Do not\n"
            "send it to the IRS. The ef\ufb01cient well-\n"
            "known main-\n"
            "tenance.\n"
            "\u2022 First item that wraps over\n"
            "two lines\n"
            "# Not a heading")
    assert _clean_pdf_text(text) == (
        "Identity theft places a burden on its victims and presents a challenge. Do not send it to "
        "the IRS. The efficient well-known main-tenance.\n\n"
        "- First item that wraps over two lines # Not a heading")
    assert _clean_pdf_text("A short line.\nNext paragraph starts here and runs on for a while.") == (
        "A short line.\n\nNext paragraph starts here and runs on for a while.")
    assert _clean_pdf_text("# Title\n> quote") == "\\# Title > quote"
    assert _clean_pdf_text("A sentence ends here.\n# Then a line that starts with a hash and runs on") == (
        "A sentence ends here.\n\n\\# Then a line that starts with a hash and runs on")



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


# --- review fixes -------------------------------------------------------------

def test_pdf_bomb_fails_without_pdfminer_fallback(monkeypatch):
    from scrapepy.convert import documents as conv

    def no_fallback(*args):
        raise AssertionError("pdfminer.six must not run after a pypdf limit")
    monkeypatch.setattr(conv, "_pdf_pdfminer", no_fallback)
    result = convert_document(pdf_bomb(80), "pdf")
    assert (result.status, result.reason) == (
        "failed", "a compressed stream unpacks past the safety limit (possible PDF bomb)")


def test_pdf_bomb_on_a_page_without_text_is_not_handed_to_pdfminer(monkeypatch):
    from scrapepy.convert import documents as conv
    from .document_samples import pdf_bomb

    def no_fallback(*args):
        raise AssertionError("pdfminer.six must not run on a PDF bomb")
    monkeypatch.setattr(conv, "_pdf_pdfminer", no_fallback)
    # No fonts: pypdf's extract_text returns "" without unpacking the stream
    data = pdf_bomb(80).replace(b"/Resources << /Font << /F1 3 0 R >> >>",
                                b"/Resources <<" + b" " * 23 + b">>")     # same length, same offsets
    result = convert_document(data, "pdf")
    assert result.reason == "a compressed stream unpacks past the safety limit (possible PDF bomb)"


def test_pdfminer_respects_page_cap():
    from scrapepy.convert.documents import _pdf_pdfminer
    texts = _pdf_pdfminer(make_pdf([["one"], ["two"], ["three"]]), Limits(max_pages=2))
    assert len(texts) == 2 and "two" in texts[1]


def test_docx_xml_part_cap():
    data = pad_part(make_docx(), "word/document.xml", 2 * 1024 * 1024)
    result = convert_document(data, "docx", Limits(max_xml_part_bytes=1024 * 1024))
    assert result.reason == "a part unpacks to more than 1 MB (possible zip bomb)"
    assert convert_document(data, "docx").status == "converted"


def test_xlsx_sheets_are_streamed_so_not_part_capped():
    rows = [["n", "text"]] + [[i, "x" * 40] for i in range(30000)]
    data = make_xlsx({"Big": rows})
    result = convert_document(data, "xlsx", Limits(max_xml_part_bytes=1024 * 1024))
    assert result.status == "converted"
    shared = pad_part(data, "xl/styles.xml", 2 * 1024 * 1024)
    assert convert_document(shared, "xlsx", Limits(max_xml_part_bytes=1024 * 1024)).reason == \
        "a part unpacks to more than 1 MB (possible zip bomb)"


def test_zip_encrypted_flag_refused():
    assert convert_document(with_encrypted_flag(make_docx()), "docx").reason == \
        "encrypted (needs a password)"


def test_docx_tracked_insertions_and_nested_tables():
    result = convert_document(docx_with_tracked_changes(), "docx")
    assert result.text.startswith("Kept inserted words")
    assert "deleted words" not in result.text
    assert "| Outer cell | Nested cell text |" in result.text


def test_csv_utf16_with_bom():
    data = "name,city\nZo\u00eb,K\u00f8benhavn\n".encode("utf-16")
    result = convert_document(data, "csv")
    assert "| Zo\u00eb | K\u00f8benhavn |" in result.text


# --- child process --------------------------------------------------------------

def run_child(target, args, timeout=30, memory=0):
    import asyncio
    from scrapepy.processing.documents import run_in_child
    return asyncio.run(run_in_child(target, args, timeout, memory))


def test_child_returns_value():
    assert run_child("tests.document_samples:child_add", [2, 3]) == ("ok", 5)


def test_child_killed_on_timeout(tmp_path):
    import time
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    assert run_child("tests.document_samples:child_sleep", [str(pid_file)], timeout=3) == ("timeout", None)
    assert time.monotonic() - started < 15
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs POSIX resource limits")
def test_child_memory_limit():
    outcome, value = run_child("tests.document_samples:child_allocate", [2048], memory=512 * 1024 * 1024)
    assert outcome in ("error", "died")
    if outcome == "error":
        assert value == "too large to convert (memory limit reached)"
    assert run_child("tests.document_samples:child_allocate", [16], memory=512 * 1024 * 1024) == ("ok", 16 * 1024 * 1024)


def test_documents_empty_list_message_names_option_once(capsys):
    with pytest.raises(SystemExit):
        parse_args(["https://example.com/", "--documents", ","])
    err = capsys.readouterr().err.strip().splitlines()[-1]
    assert err.count("--documents") == 1
    assert "needs at least one type" in err


def test_child_never_reruns_the_callers_script(tmp_path):
    """A script without an `if __name__ == "__main__"` guard runs once:
    the child is a plain subprocess, not a multiprocessing spawn."""
    import subprocess
    import sys
    script = tmp_path / "caller.py"
    script.write_text(
        "import asyncio\n"
        "from scrapepy.processing.documents import run_in_child\n"
        "print('started', flush=True)\n"
        "print(asyncio.run(run_in_child('scrapepy.convert.documents:parse_pdf_date', "
        "['D:20250314'], 30, 0)))\n")
    root = str(Path(__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": root})
    assert out.stdout.splitlines() == ["started", "('ok', '2025-03-14')"]


def test_rows_scanned_are_capped():
    data = b"a\n" + b",\n" * 50 + b"late\n"
    result = convert_document(data, "csv", Limits(max_scan_rows=20))
    assert "late" not in result.text
    assert result.text.endswith("_Rows after row 20 are not read._")
    assert "late" in convert_document(data, "csv").text


# --- robustness follow-up -------------------------------------------------------

def test_docx_of_many_tiny_paragraphs_converts_in_seconds(tmp_path):
    import time
    from .document_samples import docx_many_paragraphs
    path = tmp_path / "many.docx"
    path.write_bytes(docx_many_paragraphs(470_000))      # 16 MB document.xml, under both caps
    started = time.monotonic()
    outcome, value = run_child("scrapepy.convert.documents:convert_path", [str(path), "docx"],
                               timeout=45)
    assert outcome == "ok", value
    assert value["status"] == "converted" and value["text"].startswith("x\n\nx")
    assert time.monotonic() - started < 45


def test_docx_style_ids_are_mapped_to_names():
    from .document_samples import docx_custom_styles
    # "MyHead" is named "heading 2": a heading, so it becomes the title
    # (the document has no title metadata); an unknown id is the default style
    result = convert_document(docx_custom_styles(), "docx")
    assert result.title == "Custom heading"
    assert result.text == "Unknown style\n\nPlain paragraph"


def test_child_output_on_stdout_does_not_spoil_the_result():
    assert run_child("tests.document_samples:child_print_and_return", [7]) == ("ok", 7)


def test_child_exit_without_result_is_described():
    assert run_child("tests.document_samples:child_print_and_exit", [3]) == (
        "died", "exited with code 3 without a result (boom); it also printed to stdout")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX signals")
def test_child_crash_is_described():
    assert run_child("tests.document_samples:child_segfault", []) == ("died", "crashed (SIGSEGV)")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX resource limits")
def test_child_cpu_limit_is_described():
    import asyncio
    from scrapepy.processing.documents import run_in_child
    assert asyncio.run(run_in_child("tests.document_samples:child_spin", [], 30, 0, cpu_seconds=1)) == (
        "died", "CPU time limit reached")


def test_child_gets_a_minimal_environment(monkeypatch):
    from scrapepy.processing.documents import CHILD_ENV_KEEP
    secrets = {"HTTP_PROXY": "http://proxy.invalid:8080", "https_proxy": "http://proxy.invalid:8080",
               "ALL_PROXY": "socks5://proxy.invalid", "NO_PROXY": "example.com",
               "AWS_SECRET_ACCESS_KEY": "not-a-real-key", "GITHUB_TOKEN": "not-a-real-token",
               "SCRAPER_PROXY": "http://proxy.invalid", "HOME": "/nowhere"}
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    outcome, keys = run_child("tests.document_samples:child_environ", [])
    assert outcome == "ok"
    assert not set(keys) & set(secrets)
    assert all(k in CHILD_ENV_KEEP or k.startswith("LC_") or k == "PYTHONPATH" for k in keys), keys
    assert "LC_ALL" in keys and "PYTHONPATH" in keys


def test_child_result_file_is_removed(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert run_child("tests.document_samples:child_add", [1, 1]) == ("ok", 2)
    assert run_child("tests.document_samples:child_print_and_exit", [3])[0] == "died"
    assert list(tmp_path.iterdir()) == []


def test_docx_list_levels():
    import docx, io
    document = docx.Document()
    document.add_paragraph("Top", style="List Bullet")
    buf = io.BytesIO()
    document.save(buf)
    from .document_samples import _replace_part
    nested = ('<w:p><w:pPr><w:numPr><w:ilvl w:val="2"/><w:numId w:val="1"/></w:numPr></w:pPr>'
              '<w:r><w:t>Deep</w:t></w:r></w:p>')
    data = _replace_part(buf.getvalue(), "word/document.xml",
                         lambda x: x.replace("</w:body>", nested + "</w:body>", 1)
                         if "<w:sectPr" not in x else x.replace("<w:sectPr", nested + "<w:sectPr", 1))
    assert convert_document(data, "docx").text == "- Top\n    - Deep"


FLOOD_PARENT = """
import asyncio, json, resource, sys
from scrapepy.processing.documents import run_in_child
# A first small run, so the baseline already includes everything imported
asyncio.run(run_in_child("tests.document_samples:child_flood", [1, False], 120, 0))
before_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
results = [asyncio.run(run_in_child("tests.document_samples:child_flood", [512, exit_], 120, 0))
           for exit_ in (False, True)]
after_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({"results": results, "growth_mb": (after_kb - before_kb) // 1024,
                  "peak_mb": after_kb // 1024}))
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX resource accounting")
def test_child_flooding_stdout_and_stderr_cannot_grow_the_parent(tmp_path):
    import json
    import subprocess
    import sys
    script = tmp_path / "flood_parent.py"
    script.write_text(FLOOD_PARENT)
    root = str(Path(__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300,
                         env={**os.environ, "PYTHONPATH": root}, cwd=root)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report["results"] == [
        ["ok", 512],
        ["died", "exited with code 5 without a result (last line of stderr); it also printed to stdout"],
    ]
    # 512 MB went through each pipe in each run; storing them grows the
    # parent by about 2 GB, reading them in bounded chunks by almost nothing
    assert report["growth_mb"] < 64, report


PARENT_THEN_KILLED = """
import asyncio, sys
from scrapepy.processing.documents import run_in_child
asyncio.run(run_in_child("tests.document_samples:child_sleep", [sys.argv[1]], 600, 0))
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX signals")
def test_child_exits_when_its_parent_is_killed(tmp_path):
    import signal
    import subprocess
    import sys
    import time
    script = tmp_path / "parent.py"
    script.write_text(PARENT_THEN_KILLED)
    pid_file = tmp_path / "child.pid"
    root = str(Path(__file__).resolve().parent.parent)
    parent = subprocess.Popen([sys.executable, str(script), str(pid_file)],
                              env={**os.environ, "PYTHONPATH": root}, cwd=root)
    try:
        deadline = time.monotonic() + 30
        while not pid_file.exists() or not pid_file.read_text():
            assert time.monotonic() < deadline, "child never started"
            time.sleep(0.1)
        child = int(pid_file.read_text())
        parent.send_signal(signal.SIGKILL)
        parent.wait(10)
        deadline = time.monotonic() + 10
        while True:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            # A child handed to init and already gone may linger as a zombie briefly
            try:
                with open(f"/proc/{child}/stat") as f:
                    if f.read().split()[2] == "Z":
                        break
            except OSError:
                break
            assert time.monotonic() < deadline, "child outlived its parent"
            time.sleep(0.2)
    finally:
        if parent.poll() is None:
            parent.kill()


# --- quality follow-up (real-document findings) ---------------------------------

@pytest.mark.parametrize("title,junk", [
    ("ch3199paga", True), ("ukpga_19990031_en", True), ("report.pdf", True),
    ("Microsoft Word - minutes.doc", True), ("Untitled", True), ("ab", True), ("", True), (None, True),
    ("12/2025", True), ("Budget", False), ("Publication 5027 (Rev. 5-2018)", False),
    ("Contracts (Rights of Third Parties) Act 1999", False), ("COVID-19 guidance", False),
    ("COVID-19", False), ("Annual-Report-2024", False), ("\u62a5\u544a", False), ("\u6982\u8981", False),
    ("\u62a5", True), ("A4", True), ("v2final", True),
])
def test_junk_title(title, junk):
    from scrapepy.convert.documents import _junk_title
    assert _junk_title(title) is junk


def test_pdf_junk_title_without_heading_is_dropped():
    # One font size: no heading to fall back on, so the collector uses the file name
    result = convert_document(make_pdf([["Plain text only"]], {"Title": "ch3199paga"}), "pdf")
    assert result.title is None


def test_pdf_good_metadata_title_is_kept():
    from .document_samples import make_layout_pdf
    data = make_layout_pdf([[(72, 720, 20, "Big Heading"), (72, 690, 11, "Body text here.")]],
                           {"Title": "Annual Report"})
    assert convert_document(data, "pdf").title == "Annual Report"


def test_docx_junk_metadata_title_falls_back_to_heading():
    assert convert_document(make_docx(title="ch3199paga"), "docx").title == "Welcome"


def test_long_csv_is_cut_into_sections_with_the_header_repeated():
    data = ("year,count,\n" + "".join(f"{1900 + i},{i},\n" for i in range(1, 101))).encode()
    text = convert_document(data, "csv").text
    assert [l for l in text.splitlines() if l.startswith("## ")] == [
        "## Rows 1 to 40", "## Rows 41 to 80", "## Rows 81 to 100"]
    assert text.count("| year | count |\n| --- | --- |") == 3
    assert "| 1941 | 41 |" in text.split("## Rows 41 to 80")[1].split("## Rows 81")[0]


def test_short_table_has_no_sections():
    data = ("n\n" + "".join(f"{i}\n" for i in range(1, 41))).encode()
    text = convert_document(data, "csv").text
    assert "## " not in text and text.count("| n |") == 1


def test_sections_keep_one_width_and_the_cap_note():
    rows = [["a", "b", "c"]] + [[str(i), "x", ""] for i in range(1, 9)] + [["9", "x", "late"]]
    data = "".join(",".join(r) + "\n" for r in rows).encode()
    text = convert_document(data, "csv", Limits(section_rows=4, max_rows=8)).text
    tables = [l for l in text.splitlines() if l.startswith("| a |")]
    assert tables == ["| a | b | c |"] * 2
    assert text.endswith("_Rows after the first 8 are not shown._")


def test_xlsx_sections_are_named_after_the_sheet():
    rows = [["n", "square"]] + [[i, i * i] for i in range(1, 51)]
    text = convert_document(make_xlsx({"Squares": rows}), "xlsx").text
    assert [l for l in text.splitlines() if l.startswith("#")] == [
        "## Squares", "### Squares: rows 1 to 40", "### Squares: rows 41 to 50"]


def test_rows_count_includes_rows_past_the_display_cap():
    rows = [["n"]] + [[i] for i in range(1, 11)] + [[None], [None]]
    result = convert_document(make_xlsx({"S": rows, "T": [["h"], ["v"]]}), "xlsx", Limits(max_rows=4))
    assert (result.rows, result.sheets, result.pages) == (11, 2, None)
    assert convert_document(b"h\n1\n2\n\n3\n", "csv").rows == 3


def test_xlsx_date_prefers_the_last_save():
    assert convert_document(make_xlsx({"S": [["a"], ["1"]]}), "xlsx").date == "2025-11-03T09:00:00+00:00"
    no_modified = make_xlsx({"S": [["a"], ["1"]]}, modified=None)
    assert convert_document(no_modified, "xlsx").date == "2025-06-02T12:00:00+00:00"


def test_layout_plan_is_counted_not_timed():
    from .document_samples import dense_page_pdf, raw_pdf, text_ops
    pages = [text_ops([(72, 700, 11, f"Page {n} text")]) for n in range(1, 6)]
    data = raw_pdf(pages)
    result = convert_document(data, "pdf", Limits(layout_max_pages=2))
    assert (result.layout_pages, result.fallback_pages) == (2, "3 to 5: page budget")
    assert "## Page 4\n\nPage 4 text" in result.text
    result = convert_document(data, "pdf", Limits(layout_total_pieces=3))
    assert (result.layout_pages, result.fallback_pages) == (3, "4 to 5: text budget")
    result = convert_document(data, "pdf", Limits(layout_total_chars=30))
    assert result.fallback_pages == "3 to 5: text budget"
    result = convert_document(dense_page_pdf(101), "pdf", Limits(layout_page_pieces=100))
    assert (result.layout_pages, result.fallback_pages) == (0, "1: dense page")
    assert "w1" in result.text
    assert convert_document(dense_page_pdf(100), "pdf", Limits(layout_page_pieces=100)).fallback_pages is None
    assert convert_document(data, "pdf", Limits(layout_page_chars=5)).fallback_pages == "1 to 5: dense page"
    # Many separate pieces: within the piece cap, but too many lines to order
    result = convert_document(dense_page_pdf(2000), "pdf")
    assert (result.layout_pages, result.fallback_pages) == (0, "1: complex page")
    assert convert_document(dense_page_pdf(40), "pdf").fallback_pages is None
    # The plan does not move with the time allowed
    slow, fast = (convert_document(data, "pdf", Limits(layout_seconds=s)) for s in (60, 6000))
    assert slow.text == fast.text and slow.fallback_pages is None


def test_oversized_page_box_is_not_laid_out():
    from .document_samples import raw_pdf, text_ops
    data = raw_pdf([text_ops([(72, 700, 11, "Big page")])], mediabox=b"[0 0 1000000000 1000000000]")
    result = convert_document(data, "pdf")
    assert (result.fallback_pages, result.status) == ("1: page box too large", "converted")


@pytest.mark.skipif(not hasattr(__import__("signal"), "setitimer"), reason="POSIX timer")
def test_time_caps_fall_back_to_pypdf_and_say_so():
    from .document_samples import raw_pdf, text_ops
    data = raw_pdf([text_ops([(72, 700, 11, f"Line {i} of the page") for i in range(40)])] * 2)
    result = convert_document(data, "pdf", Limits(layout_page_seconds=0.000001))
    assert (result.layout_pages, result.fallback_pages) == (0, "1 to 2: time cap")
    assert "Line 39 of the page" in result.text
    result = convert_document(data, "pdf", Limits(layout_seconds=-1))
    assert result.fallback_pages == "1 to 2: time cap"


def test_unmapped_glyphs_fall_back_and_never_reach_markdown():
    from .document_samples import UNMAPPED_FONT, raw_pdf, text_ops
    fonts = b"/F1 3 0 R /F2 20 0 R"
    mostly = raw_pdf([b"BT /F2 12 Tf 72 700 Td <0024004B00480024004B0048> Tj ET"],
                     UNMAPPED_FONT, fonts)
    result = convert_document(mostly, "pdf")
    assert result.fallback_pages == "1: no Unicode text"
    assert "(cid:" not in result.text
    few = raw_pdf([text_ops([(72, 700, 11, "Readable words fill most of this line of text")])
                   + b"\nBT /F2 12 Tf 72 680 Td <0024> Tj ET"], UNMAPPED_FONT, fonts)
    result = convert_document(few, "pdf")
    assert result.fallback_pages is None and "(cid:" not in result.text
    assert "Readable words fill most of this line of text" in result.text


def test_pdf_table_reads_row_by_row():
    from .document_samples import table_page_pdf
    assert convert_document(table_page_pdf(), "pdf").text == (
        "## Page 1\n\nQuarterly Sales Report\n\n| Region | Sales | Growth |\n| --- | --- | --- |\n"
        "| North | 1,200 | 4% |\n| South | 900 | 2% |\n| East | 1,500 | 7% |")


def test_heading_does_not_merge_into_smaller_text_below():
    from .document_samples import raw_pdf, text_ops
    data = raw_pdf([text_ops([(72, 700, 14, "Membership Figures"), (72, 686, 11, "Numbers rose this year.")])])
    assert convert_document(data, "pdf").text == "## Page 1\n\nMembership Figures\n\nNumbers rose this year."


def test_pdf_without_pdfminer_still_converts_with_joined_lines(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pdfminer", None)
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", None)
    from .document_samples import layout_sample_pdf
    result = convert_document(layout_sample_pdf(), "pdf")
    assert result.status == "converted"
    assert "Do not leave tools in the hall." in result.text


def test_default_column_cap_keeps_a_wide_table():
    header = ",".join(f"c{i}" for i in range(1, 61))
    row = ",".join(str(i) for i in range(1, 61))
    result = convert_document(f"{header}\n{row}\n".encode(), "csv")
    assert "| c60 |" in result.text and not result.notes
    wider = ",".join(f"c{i}" for i in range(1, 106))
    assert convert_document(f"{wider}\n{wider}\n".encode(), "csv").notes == [
        "Columns after the first 100 are not shown."]


def test_child_reason_has_no_control_characters():
    outcome, reason = run_child("tests.document_samples:child_stderr_controls", [4])
    assert (outcome, reason) == ("died", "exited with code 4 without a result ([31mboom with bell[0m)")


def test_child_exits_at_once_when_the_request_names_another_parent(tmp_path):
    """The parent died before the child started: the child's parent is
    already its reaper, which differs from the pid the request names."""
    import json
    import subprocess
    import sys
    pid_file = tmp_path / "child.pid"
    request = json.dumps({"target": "tests.document_samples:child_sleep", "args": [str(pid_file)],
                          "memory_bytes": 0, "cpu_seconds": 0,
                          "result_path": str(tmp_path / "reply.json"),
                          "parent_pid": os.getpid() + 1_000_000})
    root = str(Path(__file__).resolve().parent.parent)
    try:
        done = subprocess.run([sys.executable, "-m", "scrapepy.convert.document_child"],
                              input=request, capture_output=True, text=True, timeout=15,
                              env={**os.environ, "PYTHONPATH": root}, cwd=root)
    except subprocess.TimeoutExpired:
        pytest.fail("the child ran on although its parent was not the one named")
    assert done.returncode == 1
    assert not pid_file.exists()


def test_page_past_pypdf_limits_fails_as_a_bomb_like_before():
    """A page with more tokens than pypdf reads is planned out of layout
    (too dense) and then refused by pypdf: the reason stays the bomb reason."""
    from .document_samples import raw_pdf
    data = raw_pdf([b"BT /F1 6 Tf 72 700 Td " + b"(a) Tj " * 1_100_000 + b"ET"])
    result = convert_document(data, "pdf")
    assert (result.status, result.reason) == (
        "failed", "a compressed stream unpacks past the safety limit (possible PDF bomb)")
