"""Documents linked from crawled pages (--documents pdf,docx,xlsx,csv).

Without --documents nothing here runs and links to these files are treated
as before. With it, a link on the crawl's own site to a listed type goes
through the crawl queue like a page link, so the same scope, --include,
--exclude, --max-depth, --max-pages and (with --obey-robots) robots.txt
rules apply, and is then handled here instead of being fetched as a page:

- it is downloaded with the asset client, after the run's single
  HostRateLimiter has spaced it from the previous request to the host,
  with a size cap (--max-document-mb). Redirects are followed only to the
  crawl's own scheme, host and port (and, with --obey-robots, to paths
  robots.txt allows), at most five times, each hop paced; a redirect
  anywhere else is refused;
- the original is written under files/<site>/ (beside ScrapedSites and
  MarkdownSites), and the converted Markdown under MarkdownSites/<site>/
  as the URL path plus ".md" (reports/2025.pdf.md), both named through the
  run's FileNames so a document never takes another URL's file;
- the incremental cache keeps each document's ETag, Last-Modified and
  sha256, so an unchanged document is reported unchanged and not rewritten.

.doc and .xls links (the old binary formats of docx and xlsx) are recorded
as skipped and never fetched. Links inside documents are never followed.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import posixpath
import re
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urljoin, urlsplit

from ..config import DOCUMENT_TYPES, ScrapeConfig
from ..engine.fetcher import create_asset_client, download_document
from ..output.naming import fit_path
from ..output.run_summary import relative_to_root
from ..output.writer import UnsafePathError, write_within

# Old binary formats: recorded as skipped when their modern type is listed
LEGACY_TYPES = {"doc": "docx", "xls": "xlsx"}

# The library each type needs (import name), from the "documents" extra
REQUIRED_MODULES = {"pdf": "pypdf", "docx": "docx", "xlsx": "openpyxl"}

INSTALL_HINT = 'pip install "scrapepy[documents]"'

# How many unvisited document URLs the run summary lists
UNVISITED_SAMPLE = 20

MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

# Each conversion runs in a child process that is killed after this long
# and cannot use more memory or CPU time than this
CONVERT_TIMEOUT_SECONDS = 120
CONVERT_MEMORY_BYTES = 1024 * 1024 * 1024

# A document download (all redirects included) that takes longer is abandoned
DOWNLOAD_DEADLINE_SECONDS = 300

_RE_EXTENSION = re.compile(r"\.([A-Za-z0-9]{1,5})$")
_RE_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"|?*\\]')
_RE_LINK_CANONICAL = re.compile(r'<([^>]+)>\s*;[^,]*?\brel\s*=\s*"?canonical\b', re.IGNORECASE)


def parse_document_types(value: str) -> list[str]:
    """The --documents list: lowercase, duplicates removed, in the order
    given. Raises ValueError naming an unknown type (the message has no
    option name: argparse adds it)."""
    types: list[str] = []
    for raw in value.split(","):
        name = raw.strip().lower().lstrip(".")
        if not name:
            continue
        if name not in DOCUMENT_TYPES:
            known = ", ".join(DOCUMENT_TYPES)
            if name in LEGACY_TYPES:
                raise ValueError(f"{name} is an old binary format and is not converted "
                                 f"(.{name} links are recorded as skipped when "
                                 f"{LEGACY_TYPES[name]} is listed); choose from {known}")
            raise ValueError(f"unknown type {name!r}; choose from {known}")
        if name not in types:
            types.append(name)
    if not types:
        raise ValueError("needs at least one type: " + ", ".join(DOCUMENT_TYPES))
    return types


def missing_libraries(types: list[str]) -> str | None:
    """A one-line message when a listed type's library is not installed."""
    import importlib

    missing = []
    for kind in types:
        module = REQUIRED_MODULES.get(kind)
        if not module:
            continue
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(kind)
    if not missing:
        return None
    return (f"--documents {','.join(missing)} needs the optional document libraries: "
            f"{INSTALL_HINT}")


def document_type(link: str, config: ScrapeConfig) -> str | None:
    """The document type of a link or path ("pdf", ..., or "doc"/"xls" for
    an old format whose modern type is listed), or None: always None
    without --documents."""
    if not config.documents or not link:
        return None
    m = _RE_EXTENSION.search(link.split("#", 1)[0].split("?", 1)[0])
    if not m:
        return None
    ext = m.group(1).lower()
    if ext in config.documents:
        return ext
    if LEGACY_TYPES.get(ext) in config.documents:
        return ext
    return None


def safe_relpath(url_path: str) -> str:
    """A URL path as a relative file path: percent-escapes kept as written
    (so no escape can add a folder), and characters file systems refuse
    replaced with '_'."""
    path = posixpath.normpath("/" + url_path.lstrip("/")).lstrip("/")
    parts = [_RE_UNSAFE_NAME_CHARS.sub("_", p) for p in path.split("/") if p not in ("", ".", "..")]
    return os.path.join(*parts) if parts else "index"


# The only variables the conversion child is given: enough to find
# programs and pick a locale and a temporary folder. Proxy settings,
# credentials and everything else in the crawl's environment stay out.
CHILD_ENV_KEEP = ("PATH", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP",
                  "SYSTEMROOT", "WINDIR", "COMSPEC")    # the last three: Windows needs them


def child_environment() -> dict[str, str]:
    """The minimal environment for the conversion child (CHILD_ENV_KEEP and
    LC_*), plus a PYTHONPATH that finds scrapepy as this process does."""
    import sys

    env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_KEEP or k.startswith("LC_")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))
    return env


# The most of the child's stderr kept for a failure reason; stdout is only
# counted. Neither is stored in full, since the address-space limit does not
# bound what a child writes into a pipe.
CHILD_STDERR_TAIL_BYTES = 4096


async def _drain(stream, keep: int) -> tuple[int, bytes]:
    """Read a pipe to its end: the number of bytes and the last keep bytes."""
    total = 0
    tail = b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return total, tail
        total += len(chunk)
        if keep:
            tail = (tail + chunk)[-keep:]


def _describe_end(returncode: int | None, printed: bool, stderr: bytes) -> str:
    """What happened to a child that left no reply."""
    import signal

    if returncode is not None and returncode < 0:
        sig = -returncode
        names = {getattr(signal, "SIGXCPU", None): "CPU time limit reached",
                 signal.SIGKILL: "killed (SIGKILL), usually the memory limit",
                 getattr(signal, "SIGSEGV", None): "crashed (SIGSEGV)"}
        try:
            what = names.get(sig) or f"killed by {signal.Signals(sig).name}"
        except ValueError:
            what = f"killed by signal {sig}"
    else:
        what = f"exited with code {returncode} without a result"
        last = stderr.decode("utf-8", "replace").strip().splitlines()
        if last:
            # The child's own text: control characters (escape sequences,
            # BEL, NUL) are removed before it reaches a terminal or a file
            from ..convert.to_markdown import clean_text_value
            detail = " ".join(clean_text_value(last[-1]).split())[:160]
            if detail:
                what += f" ({detail})"
    if printed:
        what += "; it also printed to stdout"
    return what


async def run_in_child(target: str, args: list, timeout: float, memory_bytes: int,
                       cpu_seconds: int | None = None):
    """Run target ("module:function") with args (JSON values) in a child
    Python process with an address-space limit of memory_bytes and a CPU
    limit (cpu_seconds, by default just over timeout). Returns ("ok", value),
    ("error", text), ("timeout", None) or ("died", what happened). The reply
    comes back through a file, not stdout, so a library that prints cannot
    spoil it; stdout is only counted and only the last few KB of stderr are
    kept, so a child that floods them cannot grow this process. The reply
    file is made with mkstemp (private to this user) in the temporary
    folder and reopened by name in the child, so that folder must be
    private or sticky, as /tmp is. The child gets a minimal environment (child_environment), is
    killed on timeout and when the caller is cancelled, so its memory is
    always given back, and is a plain subprocess, so the caller's own script
    is never re-run."""
    import json
    import sys
    import tempfile

    fd, result_path = tempfile.mkstemp(prefix="scrapepy-convert-", suffix=".json")
    os.close(fd)
    request = json.dumps({
        "target": target, "args": list(args), "memory_bytes": memory_bytes,
        "cpu_seconds": cpu_seconds if cpu_seconds is not None else int(timeout) + 2,
        "result_path": result_path,
        "parent_pid": os.getpid(),
    }).encode()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "scrapepy.convert.document_child",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=child_environment(),
        )
        async def run():
            proc.stdin.write(request)
            try:
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            proc.stdin.close()
            out, err = await asyncio.gather(_drain(proc.stdout, 0),
                                            _drain(proc.stderr, CHILD_STDERR_TAIL_BYTES))
            await proc.wait()
            return out, err
        try:
            (stdout_bytes, _), (_, stderr_tail) = await asyncio.wait_for(run(), timeout)
        except asyncio.TimeoutError:
            return "timeout", None
        try:
            with open(result_path, encoding="utf-8") as f:
                outcome, value = json.loads(f.read())
        except (OSError, ValueError, UnicodeDecodeError):
            return "died", _describe_end(proc.returncode, stdout_bytes > 0, stderr_tail)
        return outcome, value
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.shield(proc.wait())
            except BaseException:
                pass
        try:
            os.unlink(result_path)
        except OSError:
            pass


def _same_site(url: str, config: ScrapeConfig) -> bool:
    """Whether url has the crawl's scheme, host and port and no user name."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    host = (parts.hostname or "").lower()
    label = f"{parts.scheme.lower()}://{host}{f':{port}' if port else ''}"
    return label == config.base_url.lower()


def _header(headers: dict, name: str) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value)
    return ""


def _magic_ok(kind: str, data: bytes) -> bool:
    """Whether the bytes look like the type the URL names."""
    if kind == "pdf":
        return b"%PDF-" in data[:1024]
    if kind in ("docx", "xlsx"):
        return data[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")
    return True     # CSV: checked while decoding


class DocumentCollector:
    """Fetches, saves and converts the documents a crawl reaches."""

    def __init__(self, config: ScrapeConfig, *, robots, limiter, cache, names, stats, limits=None):
        from ..convert import documents as convert

        self.config = config
        self.robots = robots
        self.limiter = limiter
        self.cache = cache
        self.names = names
        self.stats = stats
        self.limits = limits or convert.DEFAULT_LIMITS
        self.client = None
        self.linked_from: dict[str, str] = {}
        self.found: set[str] = set()
        self.handled = 0
        self.fetched = 0
        self.converted = 0
        self.unchanged = 0
        self.skipped: list[dict] = []
        self.failed: list[dict] = []
        self.unvisited: list[str] = []

    async def open(self) -> None:
        self.client = create_asset_client(self.config)
        await self.client.__aenter__()

    async def close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            await client.aclose()

    # -- discovery ---------------------------------------------------------

    def kind(self, link: str) -> str | None:
        return document_type(link, self.config)

    def note_link(self, url_path: str, page_url: str) -> None:
        """Remember the first page that linked a document."""
        self.linked_from.setdefault(url_path, page_url)

    def record_blocked(self, url: str) -> None:
        """A document robots.txt disallows (also in blocked_by_robots)."""
        if url not in self.found:
            self.found.add(url)
            self.skipped.append({"url": url, "reason": "blocked by robots.txt"})

    # -- bookkeeping -------------------------------------------------------

    def _skip(self, url: str, reason: str) -> None:
        self.skipped.append({"url": url, "reason": reason})

    def _fail(self, url: str, reason: str, url_path: str | None = None) -> None:
        print(f"  Document failed ({reason}): {url}")
        self.failed.append({"url": url, "reason": reason})
        self.stats.record_failed(url, reason)
        if self.cache and url_path:
            self.cache.remove_document(url_path)

    def _unchanged(self, url: str, why: str) -> None:
        print(f"  Unchanged ({why}): {url}")
        self.unchanged += 1
        renamed = self.names.renamed_entry(url)
        if renamed:
            self.stats.record_renamed(url, renamed[0], renamed[1])
        self.stats.unchanged.append(url)

    def note_unvisited(self, urls) -> None:
        """Document URLs still queued when the crawl stopped (--max-pages,
        say), so a caller can tell a widened budget would reach them."""
        for url in urls:
            if url not in self.found and url not in self.unvisited:
                self.unvisited.append(url)

    def summary(self) -> dict:
        root = self.config.output_root or "."
        return {
            "types": list(self.config.documents),
            "files_dir": relative_to_root(self.config.files_dir, root),
            "found": len(self.found),
            "fetched": self.fetched,
            "converted": self.converted,
            "unchanged": self.unchanged,
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "found_unvisited": len(self.unvisited),
            "found_unvisited_sample": self.unvisited[:UNVISITED_SAMPLE],
        }

    # -- fetching ----------------------------------------------------------

    async def _get(self, url: str, headers: dict | None):
        """Download url, following redirects only within the crawl's site
        and robots.txt rules, within DOWNLOAD_DEADLINE_SECONDS in all (the
        per-read timeout alone never ends a slow trickle)."""
        try:
            return await asyncio.wait_for(self._get_hops(url, headers), DOWNLOAD_DEADLINE_SECONDS)
        except asyncio.TimeoutError:
            return 0, None, {}, f"download took longer than {DOWNLOAD_DEADLINE_SECONDS} seconds"

    async def _get_hops(self, url: str, headers: dict | None):
        """Each request waits on the run's limiter."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await self.limiter.wait(current)
            status, data, resp_headers, error = await download_document(
                current, self.config, self.client, headers=headers,
                max_bytes=self.config.max_document_mb * 1024 * 1024,
            )
            if status not in _REDIRECT_STATUSES:
                return status, data, resp_headers, error
            location = _header(resp_headers, "location")
            if not location:
                return 0, None, resp_headers, "redirect without a Location header"
            target = urljoin(current, location).split("#", 1)[0]
            if not _same_site(target, self.config):
                host = urlsplit(target).hostname or "an unknown host"
                return 0, None, resp_headers, f"redirect to another site refused ({host})"
            if not self.robots.can_fetch(target):
                return 0, None, resp_headers, "redirect target blocked by robots.txt"
            current = target
        return 0, None, {}, f"more than {MAX_REDIRECTS} redirects"

    def _natural_paths(self, url_path: str) -> dict[str, str]:
        rel = safe_relpath(url_path)
        return {
            "md": os.path.abspath(os.path.join(self.config.markdown_dir, rel + ".md")),
            "file": os.path.abspath(os.path.join(self.config.files_dir, rel)),
        }

    async def handle(self, url_path: str, full_url: str, kind: str) -> None:
        """Fetch, save and convert one document taken off the crawl queue."""
        self.handled += 1
        self.found.add(full_url)
        if kind in LEGACY_TYPES:
            print(f"Skipped document (old .{kind} format, not converted): {full_url}")
            self._skip(full_url, f"old binary format (.{kind}), not converted")
            return
        print(f"Fetching document: {full_url}")
        known = self.names.by_url.get(full_url, {})
        entry = self.cache.get_document(url_path) if self.cache else None
        have_files = bool(entry) and _exists(known.get("file")) and (
            not entry.get("converted") or _exists(known.get("md")))
        headers = self.cache.get_document_headers(url_path) if have_files else None

        status, data, resp_headers, error = await self._get(full_url, headers or None)
        self.stats.fetched += 1
        self.fetched += 1
        if status == 304 and have_files:
            self._unchanged(full_url, "304")
            return
        if error:
            self._fail(full_url, error, url_path)
            return
        if status != 200 or data is None:
            self._fail(full_url, f"HTTP {status}", url_path)
            return
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if have_files and entry.get("sha256") == digest:
            self._unchanged(full_url, "same content")
            return
        if not _magic_ok(kind, data):
            ctype = _header(resp_headers, "content-type").split(";", 1)[0].strip() or "no type"
            self._fail(full_url, f"not a {kind.upper()} file (server sent {ctype})", url_path)
            return

        natural = self._natural_paths(url_path)
        existed = bool(entry) or _exists(known.get("md"))
        a = self.names.assign(full_url, natural)
        try:
            # Checked from files/ down, so a link at files/<site> is refused too
            write_within(a["file"], data, os.path.dirname(self.config.files_dir))
        except UnsafePathError as e:
            self._fail(full_url, str(e), url_path)
            return
        except OSError as e:
            self._fail(full_url, f"could not save the file ({e.strerror or e})", url_path)
            return

        import dataclasses

        from ..convert.documents import Converted
        outcome, value = await run_in_child(
            "scrapepy.convert.documents:convert_path",
            [a["file"], kind, dataclasses.asdict(self.limits)],
            CONVERT_TIMEOUT_SECONDS, CONVERT_MEMORY_BYTES)
        if outcome != "ok":
            reason = {
                "timeout": f"conversion took longer than {CONVERT_TIMEOUT_SECONDS} seconds",
                "died": f"conversion stopped: {value}",
            }.get(outcome) or f"conversion failed ({value})"
            self._fail(full_url, reason, url_path)
            return
        result = Converted(**value)
        if result.status == "failed":
            self._fail(full_url, result.reason or "conversion failed", url_path)
            return
        for kind_name in ("md", "file"):
            if a.get(kind_name) and a[kind_name] != fit_path(natural[kind_name]):
                self.stats.record_renamed(full_url, a[kind_name], fit_path(natural[kind_name]))
                break
        if result.status == "no_text":
            print(f"  Saved without Markdown ({result.reason}): {full_url}")
            self._skip(full_url, result.reason or "no text")
            if self.cache:
                self.cache.update_document(url_path, resp_headers, digest, converted=False)
            return

        text = self._markdown(url_path, full_url, kind, result, resp_headers)
        try:
            write_within(a["md"], text, os.path.dirname(self.config.markdown_dir))
        except UnsafePathError as e:
            self._fail(full_url, str(e), url_path)
            return
        except OSError as e:
            self._fail(full_url, f"could not write the Markdown ({e.strerror or e})", url_path)
            return
        print(f"  Converted document: {os.path.relpath(a['md'], self.config.markdown_dir)}")
        self.converted += 1
        self.stats.record_saved(full_url, existed, a["md"])
        if self.cache:
            self.cache.update_document(url_path, resp_headers, digest, converted=True)

    def _markdown(self, url_path: str, full_url: str, kind: str, result, headers: dict) -> str:
        from ..convert.to_markdown import (
            HTMLToMarkdown, build_frontmatter, content_hash, utc_now_iso,
        )

        title = result.title or _file_title(url_path)
        body = f"# {title}\n\n{result.text}\n"
        date, date_source = result.date, "DOCUMENT_METADATA"
        if not date:
            date, date_source = _http_date(headers), "HTTP_HEADER"
        metadata = {
            "title": title,
            "url": full_url,
            "canonical_url": _canonical(headers, full_url, self.config),
            "date": date,
            "date_source": date_source if date else None,
            "page_type": "document",
            "word_count": HTMLToMarkdown._compute_word_count(body),
            "domain": self.config.domain,
            "file_type": kind,
            "pages": result.pages,
            "layout_pages": result.layout_pages,
            "fallback_pages": result.fallback_pages,
            "sheets": result.sheets,
            "rows": result.rows,
            "linked_from": self.linked_from.get(url_path),
            "scraped_at": utc_now_iso(),
            "content_hash": content_hash(body),
        }
        return build_frontmatter(metadata) + "\n" + body


def _exists(path: str | None) -> bool:
    return bool(path) and os.path.isfile(path) and not os.path.islink(path)


def _file_title(url_path: str) -> str:
    """The document's file name, decoded, as a last-resort title."""
    name = unquote(url_path.rstrip("/").rsplit("/", 1)[-1])
    return " ".join(name.split()) or "Document"


def _http_date(headers: dict) -> str | None:
    """The response's Last-Modified as ISO 8601, or None."""
    raw = _header(headers, "last-modified")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, IndexError):
        return None


def _canonical(headers: dict, url: str, config: ScrapeConfig) -> str | None:
    """A canonical address from a Link response header, when it is on the
    crawl's site and differs from url."""
    m = _RE_LINK_CANONICAL.search(_header(headers, "link"))
    if not m:
        return None
    target = urljoin(url, m.group(1).strip())
    if target != url and _same_site(target, config):
        return target
    return None
