"""Tests for processing/encoding.py and processing/rendering.py."""

import codecs

import pytest

from scrapepy.processing.encoding import REPLACED, decode_body, meta_charset
from scrapepy.processing.rendering import looks_unrendered


class TestDecodeBody:
    def test_declared_encoding_that_works_is_used(self):
        assert decode_body("café".encode("utf-8"), "utf-8") == ("café", "utf-8", "utf-8")

    def test_declared_latin1_is_used_as_before(self):
        # Every byte is valid ISO-8859-1, so the declared charset always wins
        assert decode_body(b"caf\xc3\xa9", "ISO-8859-1")[1:] == ("iso8859-1", "iso8859-1")

    def test_wrong_declared_utf8_falls_back_to_cp1252(self):
        text, used, first = decode_body("café –".encode("cp1252"), "utf-8")
        assert (text, used, first) == ("café –", "cp1252", "utf-8")

    def test_meta_charset_is_tried_before_cp1252(self):
        body = '<meta charset="iso-8859-15">€'.encode("iso-8859-15")
        assert decode_body(body, "utf-8")[1] == "iso8859-15"

    def test_unknown_declared_charset_starts_with_utf8(self):
        assert decode_body("é".encode("utf-8"), "no-such-charset") == ("é", "utf-8", "utf-8")

    def test_empty_declared(self):
        assert decode_body(b"abc", None) == ("abc", "utf-8", "utf-8")

    def test_undecodable_bytes_are_replaced(self):
        # 0x81 is undefined in Windows-1252 and invalid in UTF-8
        text, used, _ = decode_body(b"a\x81b", "utf-8")
        assert used == REPLACED and text == "a�b"

    def test_never_raises_on_odd_meta(self):
        assert decode_body(b'<meta charset="\xff\xfe">\x81', "utf-8")[1] == REPLACED

    @pytest.mark.parametrize("body,name", [
        (b'<meta charset="windows-1252">', "windows-1252"),
        (b"<META http-equiv='Content-Type' content='text/html; charset=ISO-8859-1'>", "ISO-8859-1"),
        (b"<p>no meta</p>", None),
    ])
    def test_meta_charset(self, body, name):
        assert meta_charset(body) == name


class TestDecodeBodyHardCases:
    def test_meta_charset_undefined_never_raises(self):
        # "undefined" is a codec Python knows that raises on every decode
        text, used, _ = decode_body(b'<meta charset="undefined">caf\xe9', "utf-8")
        assert used == "cp1252" and text.endswith("caf\u00e9")

    def test_utf8_is_tried_before_cp1252_when_the_declared_charset_is_wrong(self):
        assert decode_body("caf\u00e9".encode("utf-8"), "ascii") == ("caf\u00e9", "utf-8", "ascii")

    def test_one_stray_byte_keeps_a_utf8_page_utf8(self):
        lines = ["Gr\u00fc\u00dfe aus K\u00f6ln, \u00e7a va tr\u00e8s bien"] * 40
        body = "\n".join(lines).encode("utf-8") + b"\n\xe9"
        text, used, _ = decode_body(body, "utf-8")
        assert used == REPLACED
        assert text.splitlines()[:40] == lines

    def test_mostly_latin1_page_is_cp1252(self):
        body = ("caf\u00e9 " * 40).encode("cp1252")
        assert decode_body(body, "utf-8")[1] == "cp1252"

    @pytest.mark.parametrize("codec,name", [("utf-16", "utf-16"), ("utf-16-be", "utf-16"),
                                            ("utf-32", "utf-32")])
    def test_byte_order_mark_wins(self, codec, name):
        body = "<html>caf\u00e9</html>".encode(codec)
        if codec == "utf-16-be":
            body = codecs.BOM_UTF16_BE + body
        assert decode_body(body, None)[:2] == ("<html>caf\u00e9</html>", name)

    def test_meta_window(self):
        late = b"<!--" + b"x" * 3000 + b'--><meta charset="iso-8859-15">'
        assert meta_charset(late) == "iso-8859-15"
        too_late = b"<!--" + b"x" * 5000 + b'--><meta charset="iso-8859-15">'
        assert meta_charset(too_late) is None


class TestLooksUnrendered:
    def test_empty_shell_with_script(self):
        assert looks_unrendered('<html><body><div id="root"></div>'
                                '<script src="/app.js"></script></body></html>') is True

    def test_noscript_asking_for_javascript(self):
        html = ("<html><body><header>" + "Menu item " * 20 + "</header><noscript>Please enable "
                "JavaScript.</noscript><script>boot()</script></body></html>")
        assert looks_unrendered(html) is True

    def test_page_with_text_and_scripts(self):
        html = "<html><body><p>" + "Real words here. " * 30 + "</p><script>x()</script></body></html>"
        assert looks_unrendered(html) is False

    def test_short_page_without_scripts(self):
        assert looks_unrendered("<html><body><p>Short.</p></body></html>") is False

    def test_text_inside_scripts_does_not_count(self):
        html = "<html><body><script>" + "var a = 'words words';" * 100 + "</script></body></html>"
        assert looks_unrendered(html) is True

    def test_thresholds(self):
        script = "<script>boot()</script>"
        assert looks_unrendered(f"<html><body><p>{'a' * 199}</p>{script}</body></html>") is True
        assert looks_unrendered(f"<html><body><p>{'a' * 200}</p>{script}</body></html>") is False
        noscript = "<noscript>Enable JavaScript</noscript>"
        assert looks_unrendered(f"<html><body><p>{'a' * 499}</p>{noscript}</body></html>") is True
        assert looks_unrendered(f"<html><body><p>{'a' * 500}</p>{noscript}</body></html>") is False
