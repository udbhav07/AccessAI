"""Tests for the Flask routes.

The scraper is replaced per test, so nothing here reaches the network or
starts a browser.
"""

import os

import pytest
import requests
from bs4 import BeautifulSoup

import app as webapp
from src import runstore
from src.nethttp import BlockedURL
from src.webScraper import ScrapeResult, UnsupportedContent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE_HTML = "<html><body><p data-aai-id='1'>hi</p></body></html>"
SAMPLE_ISSUES = [
    {"type": "alt", "source": "image", "target": "cat.png",
     "old": "", "new": "a sleeping cat", "ids": ["1"]},
    {"type": "contrast", "source": "inline", "target": "h2",
     "old": "#00f", "new": "#8888ff", "ratio_before": 2.1, "ratio_after": 4.9,
     "ids": ["2"]},
    {"type": "contrast-skipped", "source": "unresolvable", "target": "p",
     "old": None, "new": None, "ratio_before": None, "ratio_after": None,
     "reason": "text sits on a non-uniform background", "ids": []},
]


@pytest.fixture
def client(monkeypatch):
    webapp.app.config["TESTING"] = True
    monkeypatch.setattr(webapp, "_scan_log", type(webapp._scan_log)(
        webapp._scan_log.default_factory))
    return webapp.app.test_client()


@pytest.fixture
def scraper(monkeypatch):
    """Stand in for Scraper. Set `.raises` to fail, `.result` to succeed."""
    class FakeScraper:
        raises = None
        result = ScrapeResult(
            url="http://example.com/",
            soup=BeautifulSoup(SAMPLE_HTML, "html.parser"),
            issues=SAMPLE_ISSUES,
            html_before=SAMPLE_HTML,
            modified_ids={"1", "2"},
        )

        def scrape_url(self, url):
            if FakeScraper.raises is not None:
                raise FakeScraper.raises
            return FakeScraper.result

    monkeypatch.setattr(webapp, "Scraper", FakeScraper)
    return FakeScraper


def scan(client, url="http://example.com/"):
    return client.post("/", data={"website_link": url})


# --- the landing page -------------------------------------------------------

def test_get_renders_the_form(client):
    page = client.get("/")
    assert page.status_code == 200
    assert b'name="website_link"' in page.data
    assert b"Output:" not in page.data


# --- security headers -------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
])
def test_security_headers(client, header, expected):
    assert client.get("/").headers.get(header) == expected


def test_the_csp_is_sent_and_is_restrictive(client):
    csp = client.get("/").headers.get("Content-Security-Policy", "")
    assert csp
    assert "'unsafe-inline'" not in csp.split("style-src")[0], \
        "inline script must stay blocked"
    assert "base-uri 'none'" in csp, "a scraped <base> must not retarget us"
    assert "form-action 'self'" in csp, "the preview must not post elsewhere"


def test_the_csp_allows_what_the_template_loads(client):
    """If these drift apart the page silently loses its styling."""
    landing = client.get("/").data.decode()
    csp = client.get("/").headers.get("Content-Security-Policy", "")
    origins = {line.split('"')[1].split("/")[2]
               for line in landing.splitlines()
               if 'src="https://' in line or 'href="https://' in line}
    for origin in origins:
        assert origin in csp, f"the template loads from {origin}"


def test_the_download_route_keeps_nosniff(client):
    assert "nosniff" in client.get("/download/deadbeef").headers.get(
        "X-Content-Type-Options", "")


# --- the missing-key banner -------------------------------------------------

def test_the_banner_appears_with_no_key(client, monkeypatch):
    monkeypatch.setattr(webapp.gemini, "ai_enabled", lambda: False)
    assert b"No API key configured" in client.get("/").data


def test_the_banner_is_gone_once_a_key_is_set(client, monkeypatch):
    monkeypatch.setattr(webapp.gemini, "ai_enabled", lambda: True)
    assert b"No API key configured" not in client.get("/").data


# --- a failed scrape reaches the user, not a 500 ----------------------------

@pytest.mark.parametrize("exc,expected", [
    (BlockedURL("localhost resolves to a non-public address"), b"can&#39;t be scanned"),
    (UnsupportedContent("that URL returned application/pdf, not an HTML page"),
     b"application/pdf"),
    (requests.RequestException("connection refused"), b"Could not reach that site"),
    (RuntimeError("boom"), b"Something went wrong"),
])
def test_a_failure_is_explained(client, scraper, exc, expected):
    scraper.raises = exc
    response = scan(client)
    assert response.status_code == 200
    assert expected in response.data


def test_an_internal_error_does_not_leak_its_message(client, scraper):
    scraper.raises = RuntimeError("boom")
    assert b"boom" not in scan(client).data


@pytest.mark.parametrize("data", [{"website_link": "   "}, {}])
def test_an_empty_url_is_caught_before_anything_is_fetched(client, data):
    assert b"Please enter a URL." in client.post("/", data=data).data


# --- a successful scrape ----------------------------------------------------

def test_a_good_scrape_renders_and_offers_the_next_steps(client, scraper):
    scraper.raises = None
    response = scan(client)
    assert response.status_code == 200
    assert b"<iframe" in response.data
    assert b'id="verify-btn"' in response.data
    assert b'id="download-btn"' in response.data


def test_the_issue_list_is_rendered(client, scraper):
    scraper.raises = None
    body = scan(client).data

    assert b"2 changes applied" in body, "abstentions must not count as changes"
    assert b"a sleeping cat" in body
    assert b"#00f" in body and b"#8888ff" in body
    assert b"2.1:1" in body and b"4.9:1" in body
    assert b"1 left alone" in body
    assert b"non-uniform background" in body


def _newest_run():
    runs = os.path.join(ROOT, "runs")
    return max(os.listdir(runs),
               key=lambda d: os.path.getmtime(os.path.join(runs, d)))


def test_the_issue_list_survives_into_verify(client, scraper, monkeypatch):
    scraper.raises = None
    scan(client)
    run_id = _newest_run()

    class Report:
        def as_dict(self):
            return {"verdict": "PASS", "error": None, "checks": []}

    monkeypatch.setattr(webapp.verifier, "run_checks", lambda *a, **k: Report())
    response = client.post("/verify", data={"run_id": run_id})
    assert b"a sleeping cat" in response.data


# --- downloading ------------------------------------------------------------

def test_download_serves_an_attachment_named_after_the_host(client, scraper):
    scraper.raises = None
    scan(client)
    response = client.get(f"/download/{_newest_run()}")

    assert response.status_code == 200
    disposition = response.headers.get("Content-Disposition", "")
    assert "attachment" in disposition
    assert "example.com-accessible.html" in disposition


def test_download_strips_the_verifier_stamps_by_default(client, scraper):
    scraper.raises = None
    scan(client)
    run_id = _newest_run()

    assert b"data-aai-id" not in client.get(f"/download/{run_id}").data
    assert b"data-aai-id" in client.get(f"/download/{run_id}?stamped=1").data


@pytest.mark.parametrize("run_id", ["deadbeef", "..%2f..%2fetc"])
def test_download_refuses_an_unknown_or_escaping_run(client, run_id):
    assert client.get(f"/download/{run_id}").status_code == 404


# --- rate limiting ----------------------------------------------------------

def test_scans_are_capped_per_caller(client, scraper):
    scraper.raises = None
    responses = [scan(client) for _ in range(webapp.SCANS_PER_HOUR + 3)]
    limited = [r for r in responses if b"more than" in r.data]

    assert len(responses) - len(limited) == webapp.SCANS_PER_HOUR
    assert len(limited) == 3
    assert all(r.status_code == 200 for r in responses), "a message, not an error page"


def test_one_caller_does_not_spend_another_callers_allowance(client, scraper):
    scraper.raises = None
    for _ in range(webapp.SCANS_PER_HOUR + 1):
        scan(client)

    other = client.post("/", data={"website_link": "http://example.com/"},
                        environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert b"more than" not in other.data


# --- /verify ----------------------------------------------------------------

@pytest.mark.parametrize("data", [
    {"run_id": "deadbeef"},
    {"run_id": "../../etc"},
    {},
])
def test_verify_on_a_bogus_run_says_it_expired(client, data):
    assert b"expired" in client.post("/verify", data=data).data


def test_verify_surfaces_a_crash_without_losing_the_result(client, monkeypatch):
    run_id = runstore.new_run_id()
    runstore.save_run(run_id, "<p data-aai-id='1'>before</p>",
                      "<p data-aai-id='1'>after</p>",
                      {"url": "http://example.com/", "modified_ids": []})

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(webapp.verifier, "run_checks", boom)
    response = client.post("/verify", data={"run_id": run_id})

    assert response.status_code == 200
    assert b"could not be completed" in response.data
    assert b"after" in response.data, "the scan result should not be thrown away"
