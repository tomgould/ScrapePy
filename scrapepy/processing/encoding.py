"""Decoding response bodies without ever failing on a bad charset.

The order: the encoding the response reports (the Content-Type charset, or
UTF-8 when the header names none); then the charset a <meta> tag declares;
then UTF-8 and Windows-1252; and last UTF-8 with undecodable bytes replaced. A body that decodes with the
first choice gives exactly the text it always gave, so only pages that could
not be decoded before are affected.
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


def decode_body(body: bytes, declared: str | None) -> tuple[str, str, str]:
    """(text, encoding used, encoding first tried) for a response body.
    Never raises. The first encoding tried is declared (the response's
    encoding), or UTF-8 when it is empty or a name Python does not know."""
    first = _codec(declared) or "utf-8"
    tried = []
    for name in (first, _codec(meta_charset(body)), "utf-8", "cp1252"):
        if not name or name in tried:
            continue
        tried.append(name)
        try:
            return body.decode(name), name, first
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace"), REPLACED, first
