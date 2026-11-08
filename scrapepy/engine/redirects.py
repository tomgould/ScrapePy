"""--refuse-private-redirects: refuse redirects into private networks.

A redirect target is refused when its host is, or resolves to, an address
that is not public unicast: loopback, "this host" (0.0.0.0/8, ::), link-local
(169.254.0.0/16, which holds cloud metadata services at 169.254.169.254, and
fe80::/10), RFC 1918 private ranges, IPv6 unique local addresses (fc00::/7),
carrier-grade NAT (100.64.0.0/10), and whatever else Python's ipaddress
module calls private, reserved or multicast (198.18.0.0/15, 240.0.0.0/4,
224.0.0.0/4, ff00::/8 and others). An IPv4 address carried inside an IPv6
one (IPv4-mapped ::ffff:127.0.0.1, IPv4-compatible ::127.0.0.1, NAT64
64:ff9b::/96, 6to4 2002::/16) is judged by its IPv4 address, and localhost
and any name under .localhost are refused by name. A hostname is resolved
with socket.getaddrinfo() and refused if any address it resolves to is in
those ranges; a name that does not resolve is left to the fetch, which fails.

Only redirect targets are checked: the start URL is the user's choice and is
fetched whatever its address, and a redirect to the start URL's own host and
port is followed without a check. A redirect to a scheme other than http or
https is refused too.

Limits: the address is resolved for the check and again by the HTTP client,
so a name whose DNS answer changes between the two (DNS rebinding) is not
caught; with --proxy the proxy resolves the name, not this machine. Browser
fetches (-j, -1, --swagger, -d, --instagram) are not checked: neither the
browser's redirects nor the images, scripts, icons and other files the
browser loads while it renders a page.
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
    # Also caught by ipaddress; listed so every Python version agrees
    "198.18.0.0/15",    # benchmarking
    "224.0.0.0/4",      # multicast
    "240.0.0.0/4",      # reserved
    "ff00::/8",         # multicast
))

# Hops followed when ScrapePy follows redirects itself
MAX_REDIRECTS = 10
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SCHEMES = ("http", "https")


class RedirectRefused(Exception):
    """A redirect that --refuse-private-redirects does not follow."""


# Addresses that carry an IPv4 address inside an IPv6 one
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def embedded_ipv4(ip):
    """The IPv4 address inside an IPv4-mapped (::ffff:a.b.c.d), IPv4-compatible
    (::a.b.c.d), NAT64 (64:ff9b::/96) or 6to4 (2002::/16) address, else ip."""
    if ip.version != 6:
        return ip
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64 or (int(ip) >> 32 == 0 and int(ip) > 1):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def is_private_address(address: str) -> bool:
    """True for an address that is not a public unicast one: in
    PRIVATE_NETWORKS, or private, reserved, multicast, loopback, link-local
    or unspecified by Python's ipaddress module (which covers 198.18.0.0/15,
    240.0.0.0/4, 224.0.0.0/4, ff00::/8 and more). An IPv4 address carried
    inside an IPv6 one is judged as IPv4. Not an address: False."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    ip = embedded_ipv4(ip)
    if (ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_loopback
            or ip.is_link_local or ip.is_unspecified):
        return True
    return any(ip in net for net in PRIVATE_NETWORKS if net.version == ip.version)


def is_local_name(host: str) -> bool:
    """localhost and any name under it: curl and browsers send them to
    loopback whether or not DNS knows the name."""
    host = host.lower().rstrip(".")
    return host == "localhost" or host.endswith(".localhost")


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
    if is_local_name(host):
        return f"redirect to a local host name refused ({host})"
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
