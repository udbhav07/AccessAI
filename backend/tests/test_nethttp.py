"""Tests for the SSRF guard: blocked addresses and schemes, redirects, size and time limits."""

import types

import pytest

from accessai.core import nethttp
from accessai.core.nethttp import BlockedURL


@pytest.fixture
def resolver(monkeypatch):
    """Point hostnames at chosen addresses. Anything unlisted is public."""
    mapping = {}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (mapping.get(host, "93.184.216.34"), 0))]

    monkeypatch.setattr(nethttp.socket, "getaddrinfo", fake_getaddrinfo)
    return mapping


def blocked(url):
    try:
        nethttp.assert_fetchable(url)
        return False
    except BlockedURL:
        return True


@pytest.mark.parametrize("url", ["http://example.com/", "https://example.com/"])
def test_http_schemes_allowed(resolver, url):
    assert not blocked(url)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/",
    "data:text/html,hi",
    "example.com/page",
    "http:///nohost",
    "",
    None,
])
def test_other_schemes_refused(resolver, url):
    assert blocked(url)


@pytest.mark.parametrize("host,address", [
    ("localhost", "127.0.0.1"),
    ("127.0.0.1", "127.0.0.1"),
    ("metadata.internal", "169.254.169.254"),  # cloud metadata
    ("internal.corp", "10.0.0.1"),
    ("internal2.corp", "172.16.5.4"),
    ("router.lan", "192.168.1.1"),
    ("nowhere", "0.0.0.0"),
    ("localhost6", "::1"),
])
def test_private_addresses_refused(resolver, host, address):
    resolver[host] = address
    assert blocked(f"http://{host}/")


def test_public_address_allowed(resolver):
    resolver["example.com"] = "93.184.216.34"
    assert not blocked("http://example.com/")


def test_every_resolved_address_is_checked(monkeypatch):
    monkeypatch.setattr(nethttp.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 0)),
        (2, 1, 6, "", ("127.0.0.1", 0)),
    ])
    assert blocked("http://sneaky.example/")


def test_unresolvable_host_refused(monkeypatch):
    def boom(*args, **kwargs):
        raise nethttp.socket.gaierror("no such host")

    monkeypatch.setattr(nethttp.socket, "getaddrinfo", boom)
    assert blocked("http://does-not-exist.invalid/")


def test_opt_in_allows_private_hosts(resolver, monkeypatch):
    resolver["localhost"] = "127.0.0.1"
    monkeypatch.setenv(nethttp.ALLOW_PRIVATE_ENV, "1")
    assert not blocked("http://localhost:8791/x")
    assert blocked("file:///etc/passwd")


def test_without_opt_in_private_hosts_refused(resolver, monkeypatch):
    resolver["localhost"] = "127.0.0.1"
    monkeypatch.delenv(nethttp.ALLOW_PRIVATE_ENV, raising=False)
    assert blocked("http://localhost:8791/x")


class FakeResponse:
    def __init__(self, body=b"ok", status=200, location=None,
                 url="http://example.com/"):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.url = url
        self._body = body
        self._content = None  # set by nethttp.get
        self.closed = False

    @property
    def content(self):
        return self._content

    @property
    def is_redirect(self):
        return 300 <= self.status_code < 400 and "location" in self.headers

    def iter_content(self, chunk_size):
        size = chunk_size or 1000
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


@pytest.fixture
def http(monkeypatch, resolver):
    """Serve canned responses and record what was requested."""
    chain, requested = {}, []

    def fake_get(url, **kwargs):
        requested.append((url, kwargs))
        return chain.get(url, FakeResponse(url=url))

    monkeypatch.setattr(nethttp.requests, "get", fake_get)
    monkeypatch.delenv(nethttp.ALLOW_PRIVATE_ENV, raising=False)
    return types.SimpleNamespace(chain=chain, requested=requested,
                                 resolver=resolver)


def test_redirect_into_loopback_is_refused(http):
    http.resolver["localhost"] = "127.0.0.1"
    http.chain.update({
        "http://example.com/start": FakeResponse(
            status=302, location="http://evil.example/step2",
            url="http://example.com/start"),
        "http://evil.example/step2": FakeResponse(
            status=302, location="http://localhost/admin",
            url="http://evil.example/step2"),
    })

    with pytest.raises(BlockedURL):
        nethttp.get("http://example.com/start")

    assert not any("localhost" in url for url, _ in http.requested), \
        "the loopback host should never have been requested"


def test_relative_location_resolved_against_current_url(http):
    http.chain["http://example.com/start"] = FakeResponse(
        status=302, location="/landed", url="http://example.com/start")

    result = nethttp.get("http://example.com/start")

    assert http.requested[-1][0] == "http://example.com/landed"
    assert result.content == b"ok"


def test_a_timeout_is_always_passed(http):
    nethttp.get("http://example.com/")
    assert http.requested[0][1].get("timeout") == nethttp.DEFAULT_TIMEOUT


def test_requests_does_not_follow_redirects_itself(http):
    nethttp.get("http://example.com/")
    assert http.requested[0][1].get("allow_redirects") is False


def test_oversized_body_is_refused_and_closed(http):
    response = FakeResponse(body=b"x" * 5000, url="http://example.com/big")
    http.chain["http://example.com/big"] = response

    with pytest.raises(BlockedURL):
        nethttp.get("http://example.com/big", max_bytes=1000)

    assert response.closed


def test_body_under_the_cap_comes_back_whole(http):
    http.chain["http://example.com/ok"] = FakeResponse(
        body=b"x" * 500, url="http://example.com/ok")

    assert nethttp.get("http://example.com/ok", max_bytes=1000).content == b"x" * 500


@pytest.mark.parametrize("address", ["100.64.0.1", "192.0.0.1", "0.0.0.0"])
def test_other_non_public_ranges_refused(resolver, address):
    resolver["sneaky.example"] = address
    assert blocked("http://sneaky.example/")


def test_a_slow_download_is_cut_off(http, monkeypatch):
    clock = iter([0, 10, 20, 40])
    monkeypatch.setattr(nethttp.time, "monotonic", lambda: next(clock))
    response = FakeResponse(body=b"x" * 3000, url="http://example.com/slow")
    http.chain["http://example.com/slow"] = response

    with pytest.raises(nethttp.requests.Timeout):
        nethttp.get("http://example.com/slow")
    assert response.closed


def test_requests_identify_themselves(http):
    nethttp.get("http://example.com/")
    for _, kwargs in http.requested:
        assert kwargs["headers"]["User-Agent"].startswith("AccessAI/")
