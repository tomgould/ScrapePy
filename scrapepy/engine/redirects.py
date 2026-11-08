"""--refuse-private-redirects: refuse redirects into private networks.

A redirect target is refused when its host is, or resolves to, an address
in one of PRIVATE_NETWORKS: loopback, "this host" (0.0.0.0/8, ::), link-local
(169.254.0.0/16, which holds cloud metadata services at 169.254.169.254, and
fe80::/10), RFC 1918 private ranges, IPv6 unique local addresses (fc00::/7)
and carrier-grade NAT (100.64.0.0/10). An IPv4-mapped IPv6 address
(::ffff:127.0.0.1) is judged by its IPv4 address. A hostname is resolved
with socket.getaddrinfo() and refused if any address it resolves to is in
those ranges; a name that does not resolve is left to the fetch, which fails.

Only redirect targets are checked: the start URL is the user's choice and is
fetched whatever its address, and a redirect to the start URL's own host and
port is followed without a check. A redirect to a scheme other than http or
https is refused too.

Limits: the address is resolved for the check and again by the HTTP client,
so a name whose DNS answer changes between the two (DNS rebinding) is not
caught; with --proxy the proxy resolves the name, not this machine. Browser
fetches (-j, -1, --swagger, -d, --instagram) follow redirects inside the
browser and are not checked.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8",        # "this host"
    "10.0.0.0/8",       # RFC 1918
    "100.64.0.0/10",    # carrier-grade NAT
    "127.0.0.0/8",      # loopback
    "169.254.0.0/16",   # link-local, cloud metadata
    "172.16.0.0/12",    # RFC 1918
    "192.168.0.0/16",   # RFC 1918
    "::/128",           # unspecified
    "::1/128",          # loopback
    "fc00::/7",         # unique local
    "fe80::/10",        # link-local
))

# Hops followed when ScrapePy follows redirects itself
MAX_REDIRECTS = 10
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SCHEMES = ("http", "https")


class RedirectRefused(Exception):
    """A redirect that --refuse-private-redirects does not follow."""


def is_private_address(address: str) -> bool:
    """True for an IP address in PRIVATE_NETWORKS. Not an address: False."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in PRIVATE_NETWORKS if net.version == ip.version)


def _host_port(parts) -> tuple[str, int]:
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    return (parts.hostname or "").lower(), port


def private_redirect_reason(target: str, site_url: str = "", resolve=None) -> str | None:
    """Why a redirect to target is refused, or None when it may be followed.

    site_url is the start URL: a target on the same host and port passes.
    resolve defaults to socket.getaddrinfo (it takes host and port).
    """
    try:
        parts = urlsplit(target)
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        return f"redirect to an invalid URL refused ({target[:200]})"
    if parts.scheme.lower() not in _SCHEMES:
        return f"redirect to a non-HTTP URL refused ({parts.scheme or 'no scheme'})"
    host, port = _host_port(parts)
    if not host:
        return "redirect without a host refused"
    if site_url:
        try:
            if _host_port(urlsplit(site_url)) == (host, port):
                return None
        except ValueError:
            pass
    if is_private_address(host):
        return f"redirect to a private address refused ({host})"
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return None  # a public address literal
    except ValueError:
        pass
    try:
        infos = (resolve or socket.getaddrinfo)(host, port)
    except (OSError, UnicodeError):
        return None  # does not resolve: the fetch itself fails
    for info in infos:
        address = info[4][0]
        if is_private_address(str(address)):
            return f"redirect to a private address refused ({host} resolves to {address})"
    return None


def check_redirect(current: str, location: str, site_url: str, resolve=None) -> str:
    """The absolute target of a redirect, or RedirectRefused if it is refused."""
    target = urljoin(current, location)
    reason = private_redirect_reason(target, site_url, resolve)
    if reason:
        raise RedirectRefused(reason)
    return target


def header_value(headers, name: str) -> str:
    """A response header, case-insensitively, or ''."""
    for key, value in dict(headers or {}).items():
        if key.lower() == name:
            return value
    return ""
