"""SSRF-guarded HTTP GET for every user-influenced URL: public addresses only, every
redirect re-checked, size and time capped.

Not covered: DNS rebinding between the address check and the connection.
"""

import ipaddress
import os
import socket
import time
from urllib.parse import urljoin, urlparse

import requests

ALLOWED_SCHEMES = {"http", "https"}
# For tests and local fixture servers only; it disables the SSRF check.
ALLOW_PRIVATE_ENV = "ACCESSAI_ALLOW_PRIVATE_HOSTS"
DEFAULT_TIMEOUT = (5, 15)       # (connect, read)
TOTAL_TIMEOUT = 30              # whole download, so a slow drip can't hold a worker
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5
# Many sites (e.g. Wikipedia) refuse the default python-requests identifier.
HEADERS = {
    "User-Agent": "AccessAI/1.0 (accessibility checker; +https://github.com/RushiVivek/AccessAI-AG33)",
    "Accept": "text/html,application/xhtml+xml,text/css,image/*;q=0.9,*/*;q=0.8",
}


class BlockedURL(ValueError):
    """The URL points somewhere we refuse to fetch from."""


def private_hosts_allowed():
    return os.environ.get(ALLOW_PRIVATE_ENV, "").lower() in ("1", "true", "yes")


def _resolves_public(host):
    # Check every resolved address, not just the first.
    if private_hosts_allowed():
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve {host}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL(f"{host} resolves to a non-public address ({ip})")


def assert_fetchable(url):
    """Raise BlockedURL unless `url` is a public http(s) address."""
    parts = urlparse(url or "")
    if parts.scheme not in ALLOWED_SCHEMES:
        raise BlockedURL(f"scheme {parts.scheme or '(none)'!r} is not allowed")
    if not parts.hostname:
        raise BlockedURL("no host in URL")
    _resolves_public(parts.hostname)


def get(url, *, timeout=DEFAULT_TIMEOUT, max_bytes=MAX_BYTES, **kwargs):
    """Guarded GET. Redirects are followed by hand so each hop is checked too."""
    assert_fetchable(url)
    kwargs.setdefault("headers", HEADERS)
    response = requests.get(url, timeout=timeout, allow_redirects=False,
                            stream=True, **kwargs)

    hops = 0
    while response.is_redirect and hops < MAX_REDIRECTS:
        location = response.headers.get("location")
        if not location:
            break
        target = urljoin(response.url, location)
        assert_fetchable(target)
        response.close()
        response = requests.get(target, timeout=timeout, allow_redirects=False,
                                stream=True, **kwargs)
        hops += 1

    if response.is_redirect:
        response.close()
        raise requests.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")

    deadline = time.monotonic() + TOTAL_TIMEOUT
    body = b""
    for chunk in response.iter_content(chunk_size=None):
        body += chunk
        if len(body) > max_bytes:
            response.close()
            raise BlockedURL(f"response exceeded {max_bytes} bytes")
        if time.monotonic() > deadline:
            response.close()
            raise requests.Timeout(f"download took longer than {TOTAL_TIMEOUT}s")

    # Streamed only to enforce the cap; make .text/.content work as usual.
    response._content = body
    response._content_consumed = True
    return response
