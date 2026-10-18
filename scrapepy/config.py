"""Configuration dataclass for ScrapePy."""

import base64
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
    concurrent: int = 10
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

    # Derived fields (set in __post_init__)
    cutoff_date: datetime | None = field(init=False, default=None)
    base_url: str = field(init=False, default="")
    domain: str = field(init=False, default="")
    starting_path: str = field(init=False, default="")
    starting_hash: str = field(init=False, default="")
    output_dir: str = field(init=False, default="")
    output_dir_name: str = field(init=False, default="")

    def __post_init__(self):
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
            self.output_dir_name = "instagram"
            self.output_dir = "./ScrapedSites/instagram"
            return

        # Generate output directory name
        dir_name = self.domain.removeprefix("www.").replace(".", "_")
        if self.stay_in_path and self.starting_path:
            path_suffix = self.starting_path.lstrip("/").replace("/", "_")
            if path_suffix:
                dir_name += "_" + path_suffix
        self.output_dir_name = dir_name
        self.output_dir = f"./ScrapedSites/{dir_name}"

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
