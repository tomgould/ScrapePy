"""Which file each URL is written to.

Different URLs can map to the same file name (/about and /about/, or
/news/123/slug and /news/slug-123). A page never replaces another URL's
file. Each URL gets its natural path unless that path is already assigned
to a different URL, in this run or in an earlier one; then it gets the
natural name with "-" and 8 hex characters of a hash of the URL inserted
before the extension. The Markdown file and HTML copy of one page carry the
same suffix. Assignments are stored in the incremental cache and reused on
every later run, so a URL keeps its file whatever the crawl order.
"""

import hashlib
import os

import yaml

# "file" is a document's downloaded original (--documents)
KINDS = ("md", "html", "file")


def url_suffix(url: str, length: int = 8) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]


# Longest file or folder name most file systems allow, in bytes
MAX_NAME_BYTES = 255


def _cut_utf8(text: str, limit: int) -> str:
    """text cut to at most limit UTF-8 bytes, never inside a character."""
    return text.encode("utf-8")[:max(limit, 0)].decode("utf-8", "ignore")


def _split_ext(name: str) -> tuple[str, str]:
    stem, ext = os.path.splitext(name)
    if len(ext.encode("utf-8")) > 16:     # not a real extension
        return name, ""
    return stem, ext


def _fit_name(stem: str, ext: str, tag: str | None) -> str:
    """stem + ("-" + tag) + ext, with the stem cut so the whole name fits in
    MAX_NAME_BYTES."""
    extra = (len(tag) + 1 if tag else 0) + len(ext.encode("utf-8"))
    stem = _cut_utf8(stem, MAX_NAME_BYTES - extra)
    return f"{stem}-{tag}{ext}" if tag else f"{stem}{ext}"


def fit_path(path: str) -> str:
    """path with every file or folder name that is longer than the file
    system allows shortened: its stem is cut and a short hash of the full
    name is added, so two long names stay apart and the result is the same
    in every run. Names that fit are left alone."""
    parts = path.split(os.sep)
    out = []
    for part in parts:
        if len(part.encode("utf-8")) <= MAX_NAME_BYTES:
            out.append(part)
            continue
        stem, ext = _split_ext(part)
        tag = hashlib.sha256(part.encode("utf-8")).hexdigest()[:8]
        out.append(_fit_name(stem, ext, tag))
    return os.sep.join(out)


def suffixed_path(path: str, url: str, length: int = 8) -> str:
    """path with -<hash of url> inserted before the extension, the stem cut
    when needed so the name still fits in MAX_NAME_BYTES."""
    folder, name = os.path.split(path)
    stem, ext = _split_ext(name)
    return fit_path(os.path.join(folder, _fit_name(stem, ext, url_suffix(url, length))))


class FileNames:
    """URL -> assigned files. Each assignment is a dict with "md", "html"
    and, for a document, "file" (assigned paths), "natural_md",
    "natural_html" and "natural_file" (the paths it would have had), and
    "hash" (the body hash of the Markdown written for a page)."""

    def __init__(self, stored: dict[str, dict] | None = None):
        self.by_url: dict[str, dict] = {u: dict(a) for u, a in (stored or {}).items()}
        self.taken: dict[str, str] = {}
        for url, a in self.by_url.items():
            for kind in KINDS:
                if a.get(kind):
                    self.taken[a[kind]] = url

    def assign(self, url: str, natural: dict[str, str | None]) -> dict:
        """Assign files for the kinds in natural ({"md": path or None,
        "html": path or None}). A URL keeps any file it already has."""
        a = self.by_url.setdefault(url, {})
        # A natural name too long for the file system is shortened first
        natural = {k: (fit_path(v) if v else v) for k, v in natural.items()}
        missing = [k for k in KINDS if natural.get(k) and not a.get(k)]
        suffix = self.is_renamed(url) or any(
            self.taken.get(natural[k], url) != url for k in missing
        )
        for kind in missing:
            path = natural[kind]
            if suffix:
                path = suffixed_path(natural[kind], url)
                if self.taken.get(path, url) != url:
                    path = suffixed_path(natural[kind], url, 16)
            a[kind] = path
            a["natural_" + kind] = natural[kind]
            self.taken[path] = url
        return a

    def is_renamed(self, url: str) -> bool:
        a = self.by_url.get(url, {})
        return any(a.get(k) and a[k] != a.get("natural_" + k) for k in KINDS)

    def renamed_entry(self, url: str) -> tuple[str, str] | None:
        """(file, natural_file) for a renamed URL, preferring the Markdown."""
        a = self.by_url.get(url, {})
        for kind in KINDS:
            if a.get(kind) and a[kind] != a.get("natural_" + kind):
                return a[kind], a["natural_" + kind]
        return None


def _read_frontmatter(path: str) -> dict | None:
    """The YAML frontmatter of a Markdown file, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            first = f.readline()
            if first.strip() != "---":
                return None
            lines = []
            for line in f:
                if line.strip() == "---":
                    break
                lines.append(line)
                if len(lines) > 200:
                    return None
        data = yaml.safe_load("".join(lines))
        return data if isinstance(data, dict) else None
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def assignments_from_disk(markdown_dir: str) -> dict[str, dict]:
    """Rebuild URL -> Markdown file from the files' own frontmatter, for a
    run with no stored assignments (no cache, a deleted cache, or a cache
    that holds none), so URLs keep the files they already have. Generated
    listing pages (with an "articles" count) are skipped. When one URL has
    several files, its unsuffixed one wins, else the first by name."""
    found: dict[str, dict] = {}
    if not markdown_dir or not os.path.isdir(markdown_dir):
        return found
    for d, _, files in os.walk(markdown_dir):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.abspath(os.path.join(d, name))
            front = _read_frontmatter(path)
            url = front.get("url") if front else None
            # A file without a usable url (hand edited, broken) is unowned
            if (not isinstance(url, str) or not url.startswith(("http://", "https://"))
                    or "articles" in front):
                continue
            tail = f"-{url_suffix(url)}.md"
            natural = path[: -len(tail)] + ".md" if path.endswith(tail) else path
            current = found.get(url)
            if current and (current["md"] == current["natural_md"]
                            or (natural != path and current["md"] < path)):
                continue
            found[url] = {"md": path, "natural_md": natural,
                          "hash": front.get("content_hash")}
    return found
