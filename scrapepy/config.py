"""Configuration dataclass for ScrapePy."""

import base64
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse


def _parse_recent(value: str) -> datetime:
    """Parse a human-friendly duration like '2w', '1d', '6h' into a cutoff datetime.

    Supported suffixes: h (hours), d (days), w (weeks).
    """
    m = re.fullmatch(r'(\d+)\s*([hdw])', value.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid --recent value: '{value}'. "
            "Use a number + suffix: e.g. '2w' (weeks), '3d' (days), '6h' (hours)"
        )
    amount = int(m.group(1))
    unit = m.group(2)
    if unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "d":
        delta = timedelta(days=amount)
    elif unit == "w":
        delta = timedelta(weeks=amount)
    else:
        raise ValueError(f"Unknown time unit: '{unit}'")
    return datetime.now(timezone.utc) - delta


def _parse_since(value: str) -> datetime:
    """Parse an absolute date like '2026-02-11' into a timezone-aware datetime."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    raise ValueError(
        f"Invalid --since value: '{value}'. Use ISO format: YYYY-MM-DD"
    )


DEFAULT_CONCURRENCY = 4
HTML_ROOT_NAME = "ScrapedSites"
MARKDOWN_ROOT_NAME = "MarkdownSites"


def derive_output_dirs(output_root: str, site_dir_name: str) -> tuple[str, str]:
    """Return (html_dir, markdown_dir) for a site under output_root.

    This is the single place the ScrapedSites and MarkdownSites paths are built.
    """
    root = output_root or "."
    return (
        os.path.join(root, HTML_ROOT_NAME, site_dir_name),
        os.path.join(root, MARKDOWN_ROOT_NAME, site_dir_name),
    )


FILES_ROOT_NAME = "files"


def derive_files_dir(output_root: str, site_dir_name: str) -> str:
    """The folder for a site's downloaded documents (--documents), beside
    ScrapedSites and MarkdownSites so a Markdown indexer never reads them."""
    return os.path.join(output_root or ".", FILES_ROOT_NAME, site_dir_name)


DOCUMENT_TYPES = ("pdf", "docx", "xlsx", "csv")
DEFAULT_MAX_DOCUMENT_MB = 25
MAX_DOCUMENT_MB_LIMIT = 200


_RE_SITE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def validate_site_name(name: str) -> str:
    """A --site-name usable as one folder name, or ValueError."""
    name = (name or "").strip()
    if not _RE_SITE_NAME.fullmatch(name):
        raise ValueError("--site-name must be one folder name: letters, digits, '.', '_' "
                         "or '-', starting with a letter or digit")
    return name


@dataclass
class ScrapeConfig:
    url: str
    markdown: bool = True
    stay_in_path: bool = False
    path_wildcard: bool = False
    js_render: bool = False
    single_page: bool = False
    swagger: bool = False
    docs: bool = False
    include_assets: bool = True
    incremental: bool = True
    wait: float | None = None
    concurrent: int = DEFAULT_CONCURRENCY
    proxy: str | None = None
    headless: bool = False
    verbose: bool = False
    instagram: bool = False
    accounts_file: str | None = None
    ig_login: bool = False
    ig_max_posts: int = 0
    ig_update_meta: bool = False
    recent: str | None = None
    since: str | None = None
    min_articles: int = 0
    max_articles: int = 0
    audit: bool = False
    auth: str | None = None
    user_agent: str | None = None
    obey_robots: bool = False
    output_root: str = "."
    index_pages: bool = True
    markdown_only: bool = False
    feed: bool = False
    # Targeted crawling (all off by default)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    max_depth: int | None = None
    max_pages: int = 0
    urls_file: str | None = None
    start_urls: list[str] = field(default_factory=list)
    only_recipes: bool = False
    site_name: str | None = None
    follow_next: bool = False
    # Documents (off by default): linked files of these types are downloaded
    # and converted to Markdown
    documents: list[str] = field(default_factory=list)
    max_document_mb: int = DEFAULT_MAX_DOCUMENT_MB
    # Network safety: sitemap hosts besides the site's own whose sitemaps
    # (from robots.txt or a sitemap index) are fetched; refusing redirects
    # into private networks; the browser's Cloudflare challenge solver
    allow_sitemap_hosts: list[str] = field(default_factory=list)
    refuse_private_redirects: bool = False
    solve_cloudflare: bool = True

    # Derived fields (set in __post_init__)
    cutoff_date: datetime | None = field(init=False, default=None)
    base_url: str = field(init=False, default="")
    domain: str = field(init=False, default="")
    starting_path: str = field(init=False, default="")
    starting_hash: str = field(init=False, default="")
    output_dir: str = field(init=False, default="")
    markdown_dir: str = field(init=False, default="")
    files_dir: str = field(init=False, default="")
    output_dir_name: str = field(init=False, default="")

    def __post_init__(self):
        if self.start_urls and not self.url:
            self.url = self.start_urls[0]
        if self.site_name is not None:
            self.site_name = validate_site_name(self.site_name)
        if self.markdown_only:
            # Markdown only: no HTML mirror, no asset downloads
            self.markdown = True
            self.include_assets = False

        parsed = urlparse(self.url)

        # Extract credentials from URL (user:pass@host) if --auth not set
        if parsed.username and not self.auth:
            password = parsed.password or ""
            self.auth = f"{parsed.username}:{password}"

        # Use hostname (without credentials) for domain/base_url
        self.domain = parsed.hostname or parsed.netloc
        if parsed.port:
            self.domain = f"{self.domain}:{parsed.port}"
        self.base_url = f"{parsed.scheme}://{self.domain}"

        # Rewrite URL to strip embedded credentials
        if parsed.username:
            self.url = f"{self.base_url}{parsed.path}"
            if parsed.query:
                self.url += f"?{parsed.query}"
            if parsed.fragment:
                self.url += f"#{parsed.fragment}"

        self.starting_path = parsed.path.rstrip("/") or ""
        self.starting_hash = parsed.fragment or ""

        # Compute cutoff date from --recent or --since
        if self.recent:
            self.cutoff_date = _parse_recent(self.recent)
        elif self.since:
            self.cutoff_date = _parse_since(self.since)

        if self.instagram:
            dir_name = "instagram"
        else:
            dir_name = self.domain.removeprefix("www.").replace(".", "_")
            if self.site_name:
                dir_name = self.site_name
            elif self.stay_in_path and self.starting_path:
                path_suffix = self.starting_path.lstrip("/").replace("/", "_")
                if path_suffix:
                    dir_name += "_" + path_suffix
        self.output_dir_name = dir_name
        # Resolved once, here: every path the run builds, compares or stores
        # is then absolute, whatever form -o took ("data", "./data", "~/data")
        if self.proxy:
            from .proxy import validate_proxy
            self.proxy = validate_proxy(self.proxy)
        self.output_root = os.path.abspath(os.path.expanduser(self.output_root or "."))
        self.output_dir, self.markdown_dir = derive_output_dirs(self.output_root, dir_name)
        self.files_dir = derive_files_dir(self.output_root, dir_name)

    @property
    def wait_time(self) -> float:
        if self.wait is not None:
            return self.wait
        if self.instagram:
            return 5.0
        if self.js_render or self.single_page or self.swagger or self.docs:
            return 5.0
        return 3.0

    @property
    def needs_browser(self) -> bool:
        return self.js_render or self.single_page or self.swagger or self.docs or self.instagram

    @property
    def has_date_filter(self) -> bool:
        return self.cutoff_date is not None

    @property
    def auth_header(self) -> dict[str, str]:
        """Build HTTP Basic Auth header dict. Empty dict when no auth configured."""
        if not self.auth:
            return {}
        token = base64.b64encode(self.auth.encode()).decode()
        return {"Authorization": f"Basic {token}"}
