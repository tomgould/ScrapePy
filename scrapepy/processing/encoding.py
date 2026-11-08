"""Decoding response bodies without ever failing on a bad charset.

The order: a UTF-16 or UTF-32 byte order mark; the encoding the response
reports (the Content-Type charset, or UTF-8 when the header names none);
the charset a <meta> tag declares; UTF-8. When none of those decodes the
body, a body that is mostly valid UTF-8 (more valid non-ASCII characters
than bad bytes) is read as UTF-8 with the bad bytes replaced, and any other
as Windows-1252, falling back to UTF-8 with replacement. A body that
decodes with the response's encoding gives exactly the text it always gave,
unless it starts with a UTF-16 or UTF-32 byte order mark.
"""

import codecs
import re

_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.IGNORECASE)
# The HTML standard reads a <meta charset> only in the first 1024 bytes;
# a little more allows for long comments before it
_META_WINDOW = 4096
REPLACED = "utf-8 (undecodable bytes replaced)"


def meta_charset(body: bytes) -> str | None:
    """The charset a <meta> tag near the start of the body declares, or None."""
    m = _META_CHARSET.search(body[:_META_WINDOW])
    return m.group(1).decode("ascii", "ignore") if m else None


def _codec(name: str | None) -> str | None:
    """The codec's canonical name, or None for a name Python does not know."""
    if not name:
        return None
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


# Longest first: the UTF-32 LE mark starts with the UTF-16 LE one
_BOMS = ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))


def decode_body(body: bytes, declared: str | None) -> tuple[str, str, str]:
    """(text, encoding used, encoding first tried) for a response body.
    Never raises. The first encoding tried is declared (the response's
    encoding), or UTF-8 when it is empty or a name Python does not know."""
    first = _codec(declared) or "utf-8"
    for bom, name in _BOMS:
        if body.startswith(bom):
            try:
                return body.decode(name), name, first
            except UnicodeError:
                break
    tried = []
    for name in (first, _codec(meta_charset(body)), "utf-8"):
        if not name or name in tried:
            continue
        tried.append(name)
        try:
            return body.decode(name), name, first
        except (UnicodeError, LookupError):
            continue
    # Mostly UTF-8 with a few stray bytes: keep it UTF-8
    replaced = body.decode("utf-8", errors="replace")
    bad = replaced.count("\ufffd")
    good = sum(1 for ch in replaced if ord(ch) > 0x7F) - bad
    if good > bad:
        return replaced, REPLACED, first
    try:
        return body.decode("cp1252"), "cp1252", first
    except UnicodeError:
        return replaced, REPLACED, first
