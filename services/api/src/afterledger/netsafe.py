"""Which web addresses an agent may make AfterLedger fetch: public http(s) pages only.

A page an agent reads can name any address, and a model can be talked into asking for one, so every fetch is
checked here first, and again after each redirect: never this machine, the network it sits on, a cloud metadata
server, or a scheme other than http(s).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTS = frozenset({"localhost", "metadata", "metadata.google.internal", "metadata.goog"})
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".corp")


class UnsafeUrl(ValueError):
    pass


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
                or addr.is_unspecified or (isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT))


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def check_url(url: str, *, resolve: bool = True) -> str:
    """Returns the URL if it may be fetched; raises UnsafeUrl with the reason otherwise."""
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https"):
        raise UnsafeUrl(f"only http and https pages can be read, not {p.scheme or 'this'}")
    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeUrl("the address has no host")
    if p.username or p.password:
        raise UnsafeUrl("addresses with a user name or password are not read")
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeUrl(f"{host} is not a public site")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not _public(str(literal)):
            raise UnsafeUrl(f"{host} is not a public address")
        return url
    if resolve:
        try:
            infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        except OSError:
            return url        # cannot resolve here (an offline run, or behind a proxy): the fetch itself will fail
        for info in infos:
            ip = str(info[4][0]).split("%")[0]
            if not _public(ip):
                raise UnsafeUrl(f"{host} points to a private address")
    return url


def allowed(url: str, *, resolve: bool = True) -> tuple[bool, str]:
    try:
        check_url(url, resolve=resolve)
        return True, ""
    except UnsafeUrl as e:
        return False, str(e)
