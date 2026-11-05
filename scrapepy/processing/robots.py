"""robots.txt rules (RFC 9309) and per-host request pacing.

Matching follows RFC 9309, the robots exclusion protocol:

- The group for our user agent is chosen by product token. A group naming
  our token (case-insensitive prefix, longest name wins) replaces the
  catch-all "*" group. Several groups for the same agent are merged.
- Within the group, the rule with the longest path pattern that matches
  wins. On a tie, Allow wins. No matching rule means allowed.
- "*" in a pattern matches any run of characters and a trailing "$"
  anchors the end. Paths are case-sensitive.
- Percent-encoding is normalised on both the pattern and the URL, so
  "/caf%C3%A9" and "/café" are the same path.
- An empty Disallow allows everything. /robots.txt itself is always allowed.

What a missing file means is decided by the HTTP status when it is fetched:
see policy_from_response().
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse

# Token used to choose a robots.txt group when no --user-agent is given.
# "*" selects only the catch-all group, which is what a generic client gets.
DEFAULT_ROBOTS_AGENT = "*"

_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_RE_PCT = re.compile(r"%([0-9A-Fa-f]{2})")
# Characters left as they are when percent-encoding a path or pattern
_SAFE = "/?=&;:@!$'()*+,%-._~[]#"


def normalise_path(value: str) -> str:
    """Percent-encode non-ASCII, decode encoded unreserved characters, uppercase hex."""
    encoded = quote(value, safe=_SAFE)

    def _fix(m: re.Match) -> str:
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()

    return _RE_PCT.sub(_fix, encoded)


def product_token(user_agent: str | None) -> str:
    """The product token of a user agent string, lowercased: "MyBot/1.0 (+url)" -> "mybot"."""
    if not user_agent:
        return DEFAULT_ROBOTS_AGENT
    token = user_agent.strip().split("/", 1)[0].split(" ", 1)[0].strip().lower()
    return token or DEFAULT_ROBOTS_AGENT


@dataclass
class Rule:
    allow: bool
    pattern: str
    _regex: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        pattern = normalise_path(self.pattern)
        anchored = pattern.endswith("$")
        if anchored:
            pattern = pattern[:-1]
        self.pattern = pattern + ("$" if anchored else "")
        body = ".*".join(re.escape(part) for part in pattern.split("*"))
        self._regex = re.compile(body + ("\\Z" if anchored else ""), re.DOTALL)

    @property
    def length(self) -> int:
        return len(self.pattern)

    def matches(self, path: str) -> bool:
        return self._regex.match(path) is not None


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


def parse_robots(text: str) -> list[Group]:
    """Parse robots.txt into groups. Rules before any User-agent line are ignored."""
    groups: list[Group] = []
    current: Group | None = None
    last_was_agent = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if current is None or not last_was_agent:
                current = Group()
                groups.append(current)
            if value:
                current.agents.append(value.lower())
            last_was_agent = True
            continue

        last_was_agent = False
        if current is None:
            continue
        if key in ("allow", "disallow"):
            if value:  # An empty rule matches nothing
                current.rules.append(Rule(allow=(key == "allow"), pattern=value))
        elif key == "crawl-delay" and current.crawl_delay is None:
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0 and delay == delay and delay != float("inf"):
                current.crawl_delay = delay
    return groups


def select_groups(groups: list[Group], token: str) -> list[Group]:
    """Groups that apply to our product token: the longest matching name, else "*"."""
    best_len = -1
    best: list[Group] = []
    if token != DEFAULT_ROBOTS_AGENT:
        for group in groups:
            for agent in group.agents:
                if agent != "*" and token.startswith(agent):
                    if len(agent) > best_len:
                        best_len, best = len(agent), [group]
                    elif len(agent) == best_len and group not in best:
                        best.append(group)
    if best:
        return best
    return [g for g in groups if "*" in g.agents]


def decide(rules: list[Rule], path: str) -> bool:
    """Longest matching pattern wins; Allow wins a tie; no match allows."""
    best: Rule | None = None
    for rule in rules:
        if not rule.matches(path):
            continue
        if (best is None or rule.length > best.length
                or (rule.length == best.length and rule.allow and not best.allow)):
            best = rule
    return best is None or best.allow


class RobotsPolicy:
    """Decides whether a URL may be fetched, and the minimum delay between requests."""

    def __init__(
        self,
        rules: list[Rule] | None,
        crawl_delay: float | None = None,
        user_agent: str = DEFAULT_ROBOTS_AGENT,
        base_url: str = "",
        enforced: bool = True,
        disallow_all: bool = False,
        note: str = "",
    ):
        self._rules = rules
        self._crawl_delay = crawl_delay
        self.user_agent = user_agent or DEFAULT_ROBOTS_AGENT
        self.base_url = base_url
        self.enforced = enforced
        self.disallow_all = disallow_all
        # Why the policy is what it is, for a one-line message (empty when parsed normally)
        self.note = note

    @classmethod
    def from_text(
        cls,
        text: str | None,
        user_agent: str | None = None,
        base_url: str = "",
        enforced: bool = True,
    ) -> "RobotsPolicy":
        """Build a policy from robots.txt content. None means there is no robots.txt."""
        agent = user_agent or DEFAULT_ROBOTS_AGENT
        if text is None:
            return cls(None, None, agent, base_url, enforced)
        groups = select_groups(parse_robots(text), product_token(user_agent))
        rules = [rule for g in groups for rule in g.rules]
        delays = [g.crawl_delay for g in groups if g.crawl_delay is not None]
        return cls(rules, delays[0] if delays else None, agent, base_url, enforced)

    @classmethod
    def blocked(cls, note: str, user_agent: str | None = None, base_url: str = "",
                enforced: bool = True) -> "RobotsPolicy":
        """Disallow everything, for a robots.txt that could not be read (5xx, unreachable)."""
        return cls(None, None, user_agent or DEFAULT_ROBOTS_AGENT, base_url, enforced,
                   disallow_all=True, note=note)

    @classmethod
    def allow_all(cls, base_url: str = "") -> "RobotsPolicy":
        return cls(None, None, DEFAULT_ROBOTS_AGENT, base_url, enforced=False)

    @property
    def has_rules(self) -> bool:
        return self._rules is not None or self.disallow_all

    def can_fetch(self, url_or_path: str) -> bool:
        """True when the URL may be fetched. Relative paths resolve against base_url."""
        if not self.enforced:
            return True
        parsed = urlparse(url_or_path if urlparse(url_or_path).scheme
                          else urljoin(self.base_url + "/", url_or_path))
        path = parsed.path or "/"
        if path == "/robots.txt":
            return True
        if self.disallow_all:
            return False
        if not self._rules:
            return True
        if parsed.query:
            path += "?" + parsed.query
        return decide(self._rules, normalise_path(path))

    @property
    def crawl_delay(self) -> float | None:
        """Crawl-delay of the group that applies to us, or None when not set."""
        return self._crawl_delay

    def min_delay(self, wait_time: float) -> float:
        """Minimum gap between requests to the host.

        The configured wait time is the floor. A robots.txt Crawl-delay raises
        it, unless robots.txt rules are being ignored.
        """
        delay = wait_time
        if self.enforced and self._crawl_delay is not None:
            delay = max(delay, self._crawl_delay)
        return delay


def policy_from_response(
    status: int,
    text: str | None,
    user_agent: str | None = None,
    base_url: str = "",
    enforced: bool = True,
) -> RobotsPolicy:
    """Policy for a robots.txt fetch result, following RFC 9309 section 2.3.1.

    2xx: parse the file. 4xx (404 and the like): no restrictions.
    5xx, other statuses or unreachable (status 0): disallow everything.
    """
    if 200 <= status < 300:
        return RobotsPolicy.from_text(text or "", user_agent, base_url, enforced)
    if 400 <= status < 500:
        return RobotsPolicy.from_text(None, user_agent, base_url, enforced)
    reason = f"HTTP {status}" if status else "unreachable"
    return RobotsPolicy.blocked(
        f"robots.txt could not be read ({reason}), so the site is treated as disallowed",
        user_agent, base_url, enforced,
    )


class HostRateLimiter:
    """Spaces request start times per host.

    Each caller reserves the next free slot under a lock, then sleeps outside
    it, so concurrent tasks are spaced too. One limiter can serve callers with
    different gaps (see with_interval); the gap is measured from the previous
    request to the same host, whoever made it.
    """

    def __init__(self, min_interval: float, clock=time.monotonic, sleep=asyncio.sleep):
        self.min_interval = max(0.0, float(min_interval))
        self._clock = clock
        self._sleep = sleep
        self._last_start: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def reserve(self, host: str, interval: float | None = None) -> float:
        """Reserve the next slot for host and return how long to wait for it."""
        gap = self.min_interval if interval is None else max(0.0, float(interval))
        now = self._clock()
        last = self._last_start.get(host)
        slot = now if last is None else max(now, last + gap)
        self._last_start[host] = slot
        return slot - now

    async def wait(self, url: str, interval: float | None = None) -> None:
        host = urlparse(url).netloc
        async with self._lock:
            delay = self.reserve(host, interval)
        if delay > 0:
            await self._sleep(delay)

    def with_interval(self, interval: float) -> "_LimiterView":
        """A view that shares this limiter's state but uses a different gap."""
        return _LimiterView(self, interval)


class _LimiterView:
    def __init__(self, limiter: HostRateLimiter, interval: float):
        self._limiter = limiter
        self.min_interval = interval

    async def wait(self, url: str) -> None:
        await self._limiter.wait(url, self.min_interval)


def robots_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/robots.txt"
