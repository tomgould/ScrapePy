"""Error page detection for pages that answered 200 (soft 404s and the like).

A response with status 400 or above is rejected by its status before these
checks run. A 200 page is an error page only when its title or its first
H1 says so, or when it is a short storage-service error document
(NoSuchKey, AccessDenied). A title or H1 says so when:

- it is short (under 12 words) and holds 404, 403 or 410 as a whole token,
  in any language ("Fehler 404", "404 Страница не найдена", "404エラー");
- it is short and holds 500, 502 or 503 as a whole token, alone or with a
  word for "error" in one of several languages ("Erreur 500", "Ошибка 502");
- or it holds an English error phrase ("404 Not Found", "Page not found",
  "Internal Server Error"), or is nothing but "Error" or "Not Found".

Body text and code samples are never read for this, so a tutorial that
prints 404 in an example, a long title that mentions 404, or a chapter
called "Errors and Exceptions" is kept.
"""

import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# A storage-service (S3 style) error document is a few hundred bytes of XML
_STORAGE_ERROR_MAX = 2000
_STORAGE_MARKERS = ("NoSuchKey", "AccessDenied", "The specified key does not exist")

_CODES = r"(?:400|401|403|404|410|429|500|502|503|504)"
_ERROR_HEADING_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    # A heading that is only the code: "404", "HTTP 404", "Error 404"
    rf"^\W*(?:(?:http|error)\W*)?{_CODES}\W*$",
    # "404 Not Found", "HTTP 403: Forbidden", "Example | Error 500"
    rf"\b{_CODES}\W*(?:error|not\s+found|forbidden|gone|unauthori[sz]ed)\b",
    rf"\berror\W*{_CODES}\b",
    r"\b(?:internal\s+server\s+error|service\s+(?:temporarily\s+)?unavailable|"
    r"bad\s+gateway|gateway\s+time-?out)\b",
    # "Page not found", "File not found - Example"
    r"\b(?:page|file|document|resource|url)\s+(?:was\s+)?not\s+found\b",
    r"\b(?:page|file)\s+(?:cannot|can't|can’t|could\s+not)\s+be\s+found\b",
    r"\bpage\s+(?:does\s+not|doesn't|doesn’t)\s+exist\b",
    # A heading that is nothing but the error
    r"^\W*(?:not\s+found|access\s+denied|forbidden|unauthori[sz]ed|error|"
    r"(?:internal\s+)?server\s+error|nosuchkey)\W*$",
))


# A heading under this many words can be an error page by its status code alone
SHORT_HEADING_WORDS = 12
# A status code as a whole token: no letter, digit or dot of the Latin
# alphabet next to it (CJK text can follow a code directly: "404エラー")
_TOKEN = r"(?<![0-9A-Za-z.]){}(?![0-9A-Za-z.])"
_CLIENT_CODE_RE = re.compile(_TOKEN.format(r"(?:404|403|410)"))
_SERVER_CODE_RE = re.compile(_TOKEN.format(r"(?:500|502|503)"))
_SERVER_CODE_ALONE_RE = re.compile(r"^\W*(?:http\W*)?(?:500|502|503)\W*$", re.IGNORECASE)
# "Error" in several languages, for 5xx codes, which also appear in
# ordinary titles ("Fortune 500")
_ERROR_WORD_RE = re.compile(
    r"error|fehler|erreur|errore|erro|ошибка|помилка|エラー|错误|錯誤|오류|خطأ|שגיאה|"
    r"hata|błąd|fout|chyba|virhe|\bfel\b|feil|hiba|σφάλμα|lỗi|kesalahan|ข้อผิดพลาด",
    re.IGNORECASE)


def is_error_heading(text: str) -> bool:
    """Whether a title or H1 text names an error page."""
    text = (text or "").strip()
    if not text:
        return False
    if len(text.split()) < SHORT_HEADING_WORDS:
        if _CLIENT_CODE_RE.search(text):
            return True
        if _SERVER_CODE_RE.search(text) and (
                _SERVER_CODE_ALONE_RE.match(text) or _ERROR_WORD_RE.search(text)):
            return True
    return any(r.search(text) for r in _ERROR_HEADING_RES)


def is_storage_error_document(html: str) -> bool:
    """A short storage-service error document (S3 NoSuchKey and the like)."""
    return len(html) < _STORAGE_ERROR_MAX and any(m in html for m in _STORAGE_MARKERS)


def is_error_page_html(html: str) -> bool:
    """Quick check on raw HTML: a storage error document, or a <title> or
    first <h1> that names an error."""
    if not html:
        return False
    if is_storage_error_document(html):
        return True
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html[:20000], "lxml")
    title = soup.find("title")
    h1 = soup.find("h1")
    return is_error_page_content(title.get_text() if title else "",
                                 h1.get_text() if h1 else "", "")


def is_error_page_content(title: str, h1_text: str, body_text: str = "") -> bool:
    """Whether the title or H1 names an error page. body_text is not read:
    it is accepted so callers can pass what they have."""
    return is_error_heading(title) or is_error_heading(h1_text)


def is_cloudflare_challenge(html: str) -> bool:
    """Detect Cloudflare challenge pages."""
    return "Verifying you are human" in html or "Just a moment" in html
