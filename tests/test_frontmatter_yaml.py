"""Frontmatter must parse with yaml.safe_load and give back exactly what was written."""

import pytest
import yaml

from scrapepy.convert.to_markdown import (
    FRONTMATTER_FIELDS,
    HTMLToMarkdown,
    build_frontmatter,
    clean_text_value,
    generate_section_index,
)

TEXT_FIELDS = ("title", "url", "date", "date_source", "page_type", "description",
               "domain", "scraped_at", "content_hash")

ADVERSARIAL = [
    "yes", "no", "Yes", "NO", "on", "off", "y", "n", "true", "false", "True", "FALSE",
    "null", "Null", "~", "", " ", "123", "0x1F", "0o17", "1e3", "-1", "+1", ".5", "1_000",
    "3.14", ".inf", "-.inf", ".nan", "2026-02-20", "2026-02-20T10:30:00+00:00", "12:30:45",
    "- list item", "-", "--- not a document", "...", "# comment", "#", "key: value",
    "a: b: c", ":", "::", "?", "? question", "!tag", "!!str x", "&anchor", "*alias", "%YAML",
    "@at", "`backtick`", "|", ">", "|-", ">+", "{a: 1}", "[1, 2]", "{", "]",
    "'single'", '"double"', "it's", 'say "hi"', "back\\slash", "\\n not a newline",
    "tab\there", "\ttab first", "trailing tab\t", "line\nbreak", "\nleading newline",
    "trailing newline\n", "two\n\nparagraphs", "carriage\rreturn", "crlf\r\nline",
    "  leading spaces", "trailing spaces  ", "x" * 5000, "word " * 2000,
    "café", "日本語のタイトル", "emoji 🎉 title", "Ελληνικά", "עברית", "\u00a0nbsp",
    "zero\u200bwidth", "\u2028line sep", "mixed: 'quotes' and \"more\" # hash",
    "https://example.com/a?b=c&d=e#frag", "C:\\path\\file", "<html>", "&amp;",
]


def parse(fm: str) -> dict:
    assert fm.startswith("---\n") and fm.endswith("---\n")
    return yaml.safe_load(fm[4:-4]) or {}


@pytest.mark.parametrize("value", ADVERSARIAL)
@pytest.mark.parametrize("field", TEXT_FIELDS)
def test_text_field_round_trips(field, value):
    assert parse(build_frontmatter({field: value})) == {field: value}


def test_all_fields_together_round_trip():
    meta = {f: ADVERSARIAL[i] for i, f in enumerate(TEXT_FIELDS)}
    meta.update(word_count=1234, articles=5, sub_sections=0)
    assert parse(build_frontmatter(meta)) == meta


def test_ints_stay_ints():
    data = parse(build_frontmatter({"word_count": 0, "articles": 12}))
    assert data == {"word_count": 0, "articles": 12}
    assert all(type(v) is int for v in data.values())


def test_stable_key_order():
    meta = {f: "v" for f in reversed(FRONTMATTER_FIELDS)}
    lines = build_frontmatter(meta).splitlines()[1:-1]
    assert [l.split(":")[0] for l in lines] == list(FRONTMATTER_FIELDS)


def test_none_values_left_out():
    assert parse(build_frontmatter({"title": "T", "date": None})) == {"title": "T"}


def test_unknown_keys_ignored():
    assert parse(build_frontmatter({"title": "T", "secret": "x"})) == {"title": "T"}


@pytest.mark.parametrize("value,expected", [
    ("nul\x00byte", "nulbyte"),
    ("bell\x07", "bell"),
    ("esc\x1b[0m", "esc[0m"),
    ("del\x7f", "del"),
    ("c1\x85next", "c1next"),  # NEL would load as a line break
    ("c1\x9bcsi", "c1csi"),
    ("lone \ud800 surrogate", "lone  surrogate"),
    ("non\ufffechar", "nonchar"),
])
def test_unrepresentable_characters_removed(value, expected):
    assert clean_text_value(value) == expected
    fm = build_frontmatter({"title": value})
    fm.encode("utf-8")  # must be writable as UTF-8
    assert parse(fm) == {"title": expected}


def test_written_file_round_trips(tmp_path):
    """Through convert_file, including the title taken from the page."""
    input_dir = tmp_path / "ScrapedSites" / "site"
    input_dir.mkdir(parents=True)
    page = input_dir / "page.html"
    title = 'yes: "Quoted" # not a comment'
    body = "Plenty of readable words appear here so the page passes the length check. " * 5
    page.write_text(f"<html><head><title>x</title></head><body><article><h1>{title}</h1>"
                    f"<p>{body}</p></article></body></html>", encoding="utf-8")
    conv = HTMLToMarkdown(input_dir, output_dir=tmp_path / "md")
    meta = {"url": "https://example.com/page", "description": "null", "page_type": "article"}
    assert conv.convert_file(page, metadata=meta)
    text = open(conv.last_output_path, encoding="utf-8").read()
    data = yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))
    assert data["title"] == title
    assert data["description"] == "null"
    assert data["url"] == "https://example.com/page"


def test_section_index_round_trips():
    md = generate_section_index(
        section_label="no", section_url="https://example.com/no", domain="example.com",
        articles=[], sub_sections=[], scraped_at="2026-01-01T00:00:00+00:00",
    )
    data = yaml.safe_load(md.split("\n---\n", 1)[0].removeprefix("---\n"))
    assert data["title"] == "no"
    assert data["scraped_at"] == "2026-01-01T00:00:00+00:00"
