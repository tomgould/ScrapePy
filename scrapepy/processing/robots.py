"""robots.txt rules and per-host request pacing.

The policy is parsed with the standard library's urllib.robotparser. A
missing or unreachable robots.txt means every URL is allowed.
"""

import asyncio
import math
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

# Token used to match robots.txt groups when no --user-agent is given.
# "*" matches only the catch-all group, which is what a generic client gets.
DEFAULT_ROBOTS_AGENT = "*"

_RE_FRACTIONAL_DELAY = re.compile(r"^(\s*crawl-delay\s*:\s*)(\d*\.\d+)", re.IGNORECASE)


def _round_up_fractional_delays(lines: list[str]) -> list[str]:
    """urllib.robotparser ignores non-integer Crawl-delay values; round them up."""
    def _fix(line: str) -> str:
        m = _RE_FRACTIONAL_DELAY.match(line)
        if not m:
            return line
        return m.group(1) + str(math.ceil(float(m.group(2)))) + line[m.end():]
    return [_fix(line) for line in lines]


class RobotsPolicy:
    """Decides whether a URL may be fetched, and the minimum delay between requests."""

    def __init__(
        self,
        parser: RobotFileParser | None,
        user_agent: str = DEFAULT_ROBOTS_AGENT,
        base_url: str = "",
        enforced: bool = True,
    ):
        self._parser = parser
        self.user_agent = user_agent or DEFAULT_ROBOTS_AGENT
        self.base_url = base_url
        self.enforced = enforced

    @classmethod
    def from_text(
        cls,
        text: str | None,
        user_agent: str | None = None,
        base_url: str = "",
        enforced: bool = True,
    ) -> "RobotsPolicy":
        """Build a policy from robots.txt content. None means no robots.txt."""
        if text is None:
            return cls(None, user_agent or DEFAULT_ROBOTS_AGENT, base_url, enforced)
        parser = RobotFileParser()
        parser.parse(_round_up_fractional_delays(text.splitlines()))
        return cls(parser, user_agent or DEFAULT_ROBOTS_AGENT, base_url, enforced)

    @classmethod
    def allow_all(cls, base_url: str = "") -> "RobotsPolicy":
        return cls(None, DEFAULT_ROBOTS_AGENT, base_url, enforced=False)

    @property
    def has_rules(self) -> bool:
        return self._parser is not None

    def can_fetch(self, url_or_path: str) -> bool:
        """True when the URL may be fetched. Relative paths resolve against base_url."""
        if not self.enforced or self._parser is None:
            return True
        url = url_or_path
        if not urlparse(url).scheme:
            url = urljoin(self.base_url + "/", url_or_path)
        return self._parser.can_fetch(self.user_agent, url)

    @property
    def crawl_delay(self) -> float | None:
        """Crawl-delay for our user agent group, or None when not set."""
        if self._parser is None:
            return None
        delay = self._parser.crawl_delay(self.user_agent)
        if delay is None:
            return None
        try:
            return max(0.0, float(delay))
        except (TypeError, ValueError):
            return None

    def min_delay(self, wait_time: float) -> float:
        """Minimum gap between requests to the host.

        The configured wait time is the floor. A robots.txt Crawl-delay raises
        it, unless robots.txt rules are being ignored.
        """
        delay = wait_time
        if self.enforced and self.crawl_delay is not None:
            delay = max(delay, self.crawl_delay)
        return delay


class HostRateLimiter:
    """Spaces request start times per host by at least min_interval seconds.

    Safe to share across concurrent tasks: each caller reserves the next free
    slot under a lock, then sleeps outside it.
    """

    def __init__(self, min_interval: float, clock=time.monotonic, sleep=asyncio.sleep):
        self.min_interval = max(0.0, float(min_interval))
        self._clock = clock
        self._sleep = sleep
        self._next_slot: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def reserve(self, host: str) -> float:
        """Reserve the next slot for host and return how long to wait for it."""
        now = self._clock()
        slot = max(now, self._next_slot.get(host, now))
        self._next_slot[host] = slot + self.min_interval
        return slot - now

    async def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        async with self._lock:
            delay = self.reserve(host)
        if delay > 0:
            await self._sleep(delay)


def robots_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/robots.txt"
