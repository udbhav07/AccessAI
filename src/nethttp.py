"""The only place in the codebase allowed to fetch a user-influenced URL.

Four different call sites reach the network with a URL the user (or a page the
user pointed us at) chose: the page fetch, every linked stylesheet, every image
handed to the alt-text generator, and the verifier's base-URL navigation. Each
one of them needs the same scheme check, the same private-address refusal, the
same timeout and the same size cap -- so they all come through here rather than
re-deriving it four times and getting it wrong three.

Known gap: a host that answers the resolution check with a public address and
then answers the connection with a private one (DNS rebinding) still gets
through. Closing that means pinning the resolved address into the connection
with a custom HTTPAdapter; until then this stops the direct hit, which is the
case that matters for a deployment anyone can type a URL into.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_TIMEOUT = (5, 15)       # (connect, read) -- a host that accepts and
                                # never answers must not pin a worker forever
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5


class BlockedURL(ValueError):
    """The URL resolves somewhere we refuse to fetch from."""


def _resolves_public(host):
    """Refuse a host that resolves to anything but a public address.

    Every address the name resolves to is checked, not just the first: a name
    with both a public and a loopback record would otherwise pass here and
    connect to the loopback one.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve {host}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
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
    """Fetch, with the guard applied to the URL *and* to every redirect hop.

    Redirects are followed by hand rather than by requests, because
    `allow_redirects=True` would check only the first URL -- a public host that
    302s to http://169.254.169.254/ would sail straight past a one-shot check.
    """
    assert_fetchable(url)
    response = requests.get(url, timeout=timeout, allow_redirects=False,
                            stream=True, **kwargs)

    hops = 0
    while response.is_redirect and hops < MAX_REDIRECTS:
        location = response.headers.get("location")
        if not location:
            break
        target = urljoin(response.url, location)
        assert_fetchable(target)                   # re-check every hop
        response.close()
        response = requests.get(target, timeout=timeout, allow_redirects=False,
                                stream=True, **kwargs)
        hops += 1

    body = b""
    for chunk in response.iter_content(65536):
        body += chunk
        if len(body) > max_bytes:
            response.close()
            raise BlockedURL(f"response exceeded {max_bytes} bytes")

    # Streaming was only needed to enforce the cap; hand back a response whose
    # .text and .content behave exactly like a normal one.
    response._content = body
    response._content_consumed = True
    return response
