"""Spotting pages that need a browser to show their content."""

from bs4 import BeautifulSoup

# Visible text below which a page with scripts counts as an empty shell
SHELL_TEXT_CHARS = 200
# A <noscript> asking for JavaScript allows a little more text (a header)
NOSCRIPT_TEXT_CHARS = 500


def looks_unrendered(html: str) -> bool:
    """Whether an HTTP-fetched page looks like a JavaScript shell: it has
    scripts and almost no visible text, or a <noscript> that asks for
    JavaScript and little text. Such a page may show its content when
    fetched again with -j."""
    lower = html[:200000].lower()
    if "<script" not in lower and "<noscript" not in lower:
        return False
    soup = BeautifulSoup(html, "lxml")
    has_script = soup.find("script") is not None
    asks_for_js = any("javascript" in n.get_text().lower() for n in soup.find_all("noscript"))
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    root = soup.body or soup
    text_len = len(root.get_text(" ", strip=True))
    return (has_script and text_len < SHELL_TEXT_CHARS) or (
        asks_for_js and text_len < NOSCRIPT_TEXT_CHARS)
