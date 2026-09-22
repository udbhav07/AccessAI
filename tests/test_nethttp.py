"""Tests for the guarded fetch helper.

No network: the address checks are exercised against literal IPs and a stubbed
resolver, and the redirect walk against a stubbed requests.get. Run with:

    python tests/test_nethttp.py
"""

import os
import sys

# Tests live in tests/ but import the package from the project root, so put the
# root on sys.path before anything else. Works no matter where you run from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import nethttp                                          # noqa: E402
from src.nethttp import BlockedURL                               # noqa: E402

PASSED, FAILED = 0, 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}   {detail}")


def blocked(url):
    """True if assert_fetchable refuses `url`."""
    try:
        nethttp.assert_fetchable(url)
        return False
    except BlockedURL:
        return True


# Resolve every hostname to a public address unless a test says otherwise, so
# the scheme and redirect tests do not depend on DNS.
_resolve_map = {}


def fake_getaddrinfo(host, port):
    addr = _resolve_map.get(host, "93.184.216.34")      # example.com
    return [(2, 1, 6, "", (addr, 0))]


nethttp.socket.getaddrinfo = fake_getaddrinfo


# ---------------------------------------------------------------------------
print("\n[1] schemes")

check("http allowed", not blocked("http://example.com/"))
check("https allowed", not blocked("https://example.com/"))
check("file: refused", blocked("file:///etc/passwd"))
check("gopher: refused", blocked("gopher://example.com/"))
check("ftp: refused", blocked("ftp://example.com/"))
check("data: refused", blocked("data:text/html,hi"))
check("scheme-less refused", blocked("example.com/page"))
check("empty refused", blocked(""))
check("None refused", blocked(None))
check("no host refused", blocked("http:///nohost"))

print("\n[2] address ranges -- the SSRF targets")

for label, host, addr in [
    ("loopback by name", "localhost", "127.0.0.1"),
    ("loopback by literal", "127.0.0.1", "127.0.0.1"),
    ("cloud metadata", "metadata.internal", "169.254.169.254"),
    ("private 10/8", "internal.corp", "10.0.0.1"),
    ("private 172.16/12", "internal.corp", "172.16.5.4"),
    ("private 192.168/16", "router.lan", "192.168.1.1"),
    ("unspecified", "nowhere", "0.0.0.0"),
    ("ipv6 loopback", "localhost6", "::1"),
]:
    _resolve_map[host] = addr
    check(f"{label} refused", blocked(f"http://{host}/"), addr)

_resolve_map["example.com"] = "93.184.216.34"
check("a public address is allowed", not blocked("http://example.com/"))

print("\n[3] every resolved address is checked, not just the first")


def multi_homed(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("127.0.0.1", 0))]


_real_getaddrinfo = nethttp.socket.getaddrinfo
nethttp.socket.getaddrinfo = multi_homed
check("public first, loopback second is still refused",
      blocked("http://sneaky.example/"))
nethttp.socket.getaddrinfo = _real_getaddrinfo

print("\n[4] unresolvable hosts")


def failing_resolver(host, port):
    raise nethttp.socket.gaierror("no such host")


nethttp.socket.getaddrinfo = failing_resolver
check("a host that will not resolve is refused",
      blocked("http://does-not-exist.invalid/"))
nethttp.socket.getaddrinfo = _real_getaddrinfo


print("\n[5] redirects are re-checked at every hop")


class FakeResponse:
    def __init__(self, body=b"ok", status=200, location=None, url="http://example.com/"):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.url = url
        self._body = body
        self._content = None        # nethttp.get fills this in, as on a real response
        self.closed = False

    @property
    def content(self):
        return self._content

    @property
    def is_redirect(self):
        return 300 <= self.status_code < 400 and "location" in self.headers

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


_requested = []
_chain = {}


def fake_get(url, **kwargs):
    _requested.append((url, kwargs))
    return _chain.get(url, FakeResponse(url=url))


nethttp.requests.get = fake_get

_requested.clear()
_chain = {
    "http://example.com/start": FakeResponse(
        status=302, location="http://evil.example/step2", url="http://example.com/start"),
    "http://evil.example/step2": FakeResponse(
        status=302, location="http://localhost/admin", url="http://evil.example/step2"),
}
_resolve_map["evil.example"] = "93.184.216.34"
_resolve_map["localhost"] = "127.0.0.1"

try:
    nethttp.get("http://example.com/start")
    _refused = False
except BlockedURL:
    _refused = True
check("a redirect into loopback is refused", _refused)
check("...and the loopback host was never requested",
      not any("localhost" in u for u, _ in _requested), _requested)

_requested.clear()
_chain = {
    "http://example.com/start": FakeResponse(
        status=302, location="/landed", url="http://example.com/start"),
}
result = nethttp.get("http://example.com/start")
check("a relative Location is resolved against the current URL",
      _requested[-1][0] == "http://example.com/landed", _requested)
check("the final body is returned", result.content == b"ok", result.content)

print("\n[6] timeout and size cap")

_requested.clear()
_chain = {}
nethttp.get("http://example.com/")
check("a timeout is always passed to requests",
      _requested[0][1].get("timeout") == nethttp.DEFAULT_TIMEOUT,
      _requested[0][1])
check("redirects are not followed by requests itself",
      _requested[0][1].get("allow_redirects") is False, _requested[0][1])

_chain = {"http://example.com/big": FakeResponse(body=b"x" * 5000,
                                                 url="http://example.com/big")}
try:
    nethttp.get("http://example.com/big", max_bytes=1000)
    _capped = False
except BlockedURL:
    _capped = True
check("an oversized body is refused", _capped)
check("...and the connection is closed", _chain["http://example.com/big"].closed)

_chain = {"http://example.com/ok": FakeResponse(body=b"x" * 500,
                                                url="http://example.com/ok")}
check("a body under the cap comes back whole",
      nethttp.get("http://example.com/ok", max_bytes=1000).content == b"x" * 500)

print("\n" + "=" * 62)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 62)
sys.exit(1 if FAILED else 0)
