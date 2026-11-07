"""Machine-readable summary of a crawl, written as run_summary.json.

The schema is documented in README.md. Bump SCHEMA_VERSION on any change
that removes or renames a field; adding a field does not need a bump.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import ScrapeConfig

SUMMARY_FILENAME = "run_summary.json"
SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_NOTHING_FETCHED = 1
EXIT_BAD_ARGS = 2
EXIT_INTERRUPTED = 130   # SIGINT (Ctrl+C)
EXIT_TERMINATED = 143    # SIGTERM


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RunStats:
    """Counters and lists collected while a crawl runs."""

    started_at: str = field(default_factory=utc_now_iso)
    fetched: int = 0
    new: list[str] = field(default_factory=list)          # page URLs
    changed: list[str] = field(default_factory=list)      # page URLs
    unchanged: list[str] = field(default_factory=list)    # page URLs
    new_markdown: list[str] = field(default_factory=list)
    changed_markdown: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)      # {"url", "reason"}
    blocked_by_robots: list[str] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)  # {"url", "same_as"}
    renamed: list[dict] = field(default_factory=list)  # {"url", "file", "natural_file"}
    skipped_non_html: list[dict] = field(default_factory=list)  # {"url", "content_type"}
    skipped_old: int = 0
    interrupted: bool = False
    interrupted_by: str | None = None    # "SIGINT" or "SIGTERM"
    feed: dict | None = None             # feed mode only, written as "feed"
    targeting: dict | None = None        # targeted crawls only, written as "targeting"

    def record_saved(self, url: str, existed_before: bool, markdown_path: str | None):
        """Record a page that was fetched and written because it is new or changed."""
        if existed_before:
            self.changed.append(url)
            if markdown_path:
                self.changed_markdown.append(markdown_path)
        else:
            self.new.append(url)
            if markdown_path:
                self.new_markdown.append(markdown_path)

    def record_failed(self, url: str, reason: str):
        self.failed.append({"url": url, "reason": reason})

    def record_duplicate(self, url: str, same_as_path: str):
        """A page whose Markdown matched a file already written in this run."""
        self.duplicates.append({"url": url, "same_as": same_as_path})

    def record_renamed(self, url: str, file_path: str, natural_path: str) -> None:
        """A page written to a hash-suffixed name because another URL has
        its natural path."""
        self.renamed.append({"url": url, "file": file_path, "natural_file": natural_path})

    def record_non_html(self, url: str, content_type: str) -> None:
        """A response that was fetched but not converted because it is not HTML."""
        self.skipped_non_html.append({"url": url, "content_type": content_type})

    def record_blocked(self, url: str):
        if url not in self.blocked_by_robots:
            self.blocked_by_robots.append(url)


def options_used(config: ScrapeConfig) -> dict:
    """The options that shaped this run. Credentials are never included.
    "feed" is written only when --feed was given."""
    options = {
        "markdown": config.markdown,
        "stay_in_path": config.stay_in_path,
        "path_wildcard": config.path_wildcard,
        "js_render": config.js_render,
        "include_assets": config.include_assets,
        "incremental": config.incremental,
        "index_pages": config.index_pages,
        "markdown_only": config.markdown_only,
        "wait_seconds": config.wait_time,
        "concurrent": config.concurrent,
        "user_agent": config.user_agent,
        "obey_robots": config.obey_robots,
        "ignore_robots": not config.obey_robots,
        "recent": config.recent,
        "since": config.since,
        "min_articles": config.min_articles,
        "max_articles": config.max_articles,
        "auth_used": bool(config.auth),
        "proxy_used": bool(config.proxy),
    }
    if config.feed:
        options["feed"] = True
    # Targeted-crawl options appear only when set
    for key, value in (("include", list(config.include)), ("exclude", list(config.exclude)),
                       ("max_depth", config.max_depth), ("max_pages", config.max_pages),
                       ("urls_file", bool(config.urls_file)), ("start_urls", len(config.start_urls)),
                       ("only_recipes", config.only_recipes), ("site_name", config.site_name),
                       ("follow_next", config.follow_next)):
        if value is None or value is False or value == [] or (value == 0 and key != "max_depth"):
            continue
        options[key] = value
    return options


def relative_to_root(path: str, output_root: str) -> str:
    """Path relative to the --output-dir root, with forward slashes."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(output_root or "."))
    return rel.replace(os.sep, "/")


def run_status(stats: RunStats, in_progress: bool = False) -> str:
    if in_progress:
        return "running"
    return "interrupted" if stats.interrupted else "complete"


def build_summary(
    config: ScrapeConfig,
    stats: RunStats,
    ended_at: str | None = None,
    in_progress: bool = False,
) -> dict:
    """The run_summary.json content. in_progress=True gives the placeholder
    written when a crawl starts: status "running", no end time, no exit code."""
    root = config.output_root or "."
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": run_status(stats, in_progress),
        "start_url": config.url,
        "started_at": stats.started_at,
        "ended_at": None if in_progress else (ended_at or utc_now_iso()),
        "output_dir": os.path.abspath(root),
        "html_dir": relative_to_root(config.output_dir, root),
        "markdown_dir": relative_to_root(config.markdown_dir, root) if config.markdown else None,
        "options": options_used(config),
        "counts": {
            "fetched": stats.fetched,
            "new": len(stats.new),
            "changed": len(stats.changed),
            "unchanged": len(stats.unchanged),
            "failed": len(stats.failed),
            "blocked_by_robots": len(stats.blocked_by_robots),
            "duplicates": len(stats.duplicates),
            "renamed": len(stats.renamed),
            "skipped_non_html": len(stats.skipped_non_html),
            "skipped_old": stats.skipped_old,
        },
        "new_markdown": [relative_to_root(p, root) for p in stats.new_markdown],
        "changed_markdown": [relative_to_root(p, root) for p in stats.changed_markdown],
        "unchanged_urls": list(stats.unchanged),
        "failed": list(stats.failed),
        "blocked_by_robots": list(stats.blocked_by_robots),
        "duplicates": [
            {"url": d["url"], "same_as": relative_to_root(d["same_as"], root)}
            for d in stats.duplicates
        ],
        "skipped_non_html": list(stats.skipped_non_html),
        "renamed": [
            {"url": r["url"], "file": relative_to_root(r["file"], root),
             "natural_file": relative_to_root(r["natural_file"], root)}
            for r in stats.renamed
        ],
        "interrupted": stats.interrupted,
        "interrupted_by": stats.interrupted_by,
        "exit_code": None if in_progress else exit_code_for(stats),
    }
    if stats.targeting is not None:
        summary["targeting"] = {k: (list(v) if isinstance(v, list) else v)
                                for k, v in stats.targeting.items()}
    if stats.feed is not None:
        summary["feed"] = {k: (list(v) if isinstance(v, list) else v)
                           for k, v in stats.feed.items()}
    return summary


def write_summary(config: ScrapeConfig, summary: dict) -> str:
    """Write the summary atomically: a temp file in the same folder, then os.replace."""
    path = os.path.join(config.output_dir, SUMMARY_FILENAME)
    os.makedirs(config.output_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".run_summary.", suffix=".tmp", dir=config.output_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def exit_code_for(stats: RunStats) -> int:
    """0 when at least one page was fetched without failing, 1 otherwise.

    An interrupted run exits 130 for Ctrl+C (SIGINT) or 143 for SIGTERM.
    """
    if stats.interrupted:
        return EXIT_TERMINATED if stats.interrupted_by == "SIGTERM" else EXIT_INTERRUPTED
    if stats.fetched == 0 or len(stats.failed) >= stats.fetched:
        return EXIT_NOTHING_FETCHED
    return EXIT_OK
