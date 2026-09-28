"""API tests through Flask's test client: every endpoint, error codes, rate limits, CORS,
security headers and the health check. The scraper and verifier are faked.
"""

import pytest
import requests
from bs4 import BeautifulSoup

from accessai import create_app
from accessai.api import routes
from accessai.config import Config
from accessai.core import browser, runstore
from accessai.core.nethttp import BlockedURL
from accessai.core.scraper import ScrapeResult, UnsupportedContent

FRONTEND = "http://localhost:5173"
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
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path))
    config = Config(cors_origins=[FRONTEND], scans_per_hour=5,
                    runs_dir=str(tmp_path))
    app = create_app(config)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def scraper(monkeypatch):
    """Stand in for Scraper. Set `.raises` to fail; succeeds by default."""
    class FakeScraper:
        raises = None
        urls = []

        def scrape_url(self, url):
            FakeScraper.urls.append(url)
            if FakeScraper.raises is not None:
                raise FakeScraper.raises
            return ScrapeResult(
                url="http://example.com/",
                soup=BeautifulSoup(SAMPLE_HTML, "html.parser"),
                issues=SAMPLE_ISSUES,
                html_before=SAMPLE_HTML,
                modified_ids={"1", "2"},
            )

    FakeScraper.urls = []
    monkeypatch.setattr(routes, "Scraper", FakeScraper)
    return FakeScraper


@pytest.fixture
def fake_report(monkeypatch):
    class Report:
        def as_dict(self):
            return {"verdict": "PASS", "error": None, "checks": [
                {"name": "Layout", "passed": True, "summary": "ok",
                 "details": [], "tier": "blocking"}]}

    monkeypatch.setattr(routes.verifier, "run_checks", lambda *a, **k: Report())


def scan(client, url="http://example.com/", **kwargs):
    return client.post("/api/scans", json={"url": url}, **kwargs)


def error_code(response):
    return response.get_json()["error"]["code"]


@pytest.fixture
def browser_installed(monkeypatch):
    monkeypatch.setattr(browser, "is_available", lambda: True)


def test_health(client, browser_installed):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok", "checks": {"storage": True, "browser": True, "ai": False}}


def test_health_is_degraded_without_a_browser(client, monkeypatch):
    monkeypatch.setattr(browser, "is_available", lambda: False)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


def test_health_is_degraded_when_results_cant_be_saved(client, browser_installed, monkeypatch):
    monkeypatch.setattr(runstore, "is_writable", lambda: False)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.get_json()["checks"]["storage"] is False


def test_health_is_ok_without_ai(client, browser_installed):
    """AI is optional: the fallbacks still produce a result."""
    assert client.get("/api/health").status_code == 200


def test_config_reports_what_the_frontend_needs(client, monkeypatch):
    monkeypatch.setattr(routes.gemini, "ai_enabled", lambda: False)
    body = client.get("/api/config").get_json()
    assert body == {"ai_enabled": False, "scans_per_hour": 5,
                    "result_ttl_minutes": runstore.MAX_AGE_SECONDS // 60}


def test_config_says_when_the_model_is_available(client, monkeypatch):
    monkeypatch.setattr(routes.gemini, "ai_enabled", lambda: True)
    assert client.get("/api/config").get_json()["ai_enabled"] is True


def test_an_unknown_route_is_a_json_404(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_a_wrong_method_is_a_json_405(client):
    response = client.get("/api/scans")
    assert response.status_code == 405
    assert error_code(response) == "method_not_allowed"


@pytest.mark.parametrize("header,expected", [
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
])
def test_security_headers(client, header, expected):
    assert client.get("/api/health").headers.get(header) == expected


def test_the_frontend_origin_is_allowed(client):
    response = client.get("/api/health", headers={"Origin": FRONTEND})
    assert response.headers.get("Access-Control-Allow-Origin") == FRONTEND


def test_a_preflight_from_the_frontend_succeeds(client):
    response = client.options("/api/scans", headers={
        "Origin": FRONTEND,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    })
    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") == FRONTEND


def test_another_origin_is_not_allowed(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize("payload", [{"url": "   "}, {}, None, ["not", "an", "object"]])
def test_an_empty_url_is_caught_before_anything_is_fetched(client, scraper, payload):
    response = client.post("/api/scans", json=payload)
    assert response.status_code == 400
    assert error_code(response) == "missing_url"
    assert scraper.urls == []


@pytest.mark.parametrize("typed,fetched", [
    ("example.com", "https://example.com"),
    ("example.com:8080/page", "https://example.com:8080/page"),
    ("  http://example.com/  ", "http://example.com/"),
])
def test_what_people_type_is_normalised(client, scraper, typed, fetched):
    assert scan(client, typed).status_code == 201
    assert scraper.urls == [fetched]


@pytest.mark.parametrize("url", ["ftp://example.com/", "javascript:alert(1)", "mailto:a@b.c", "http://"])
def test_a_non_web_url_is_refused(client, scraper, url):
    response = scan(client, url)
    assert response.status_code == 400
    assert error_code(response) == "invalid_url"
    assert scraper.urls == []


@pytest.mark.parametrize("exc,status,code,expected", [
    (BlockedURL("localhost resolves to a non-public address"), 422, "blocked_url",
     "can't be scanned"),
    (UnsupportedContent("that URL returned application/pdf, not an HTML page"), 422,
     "unsupported_content", "That URL returned application/pdf"),
    (requests.ConnectionError("connection refused"), 502, "unreachable",
     "Could not reach that site"),
    (requests.Timeout("slow"), 502, "unreachable", "too long"),
    (RuntimeError("boom"), 500, "internal_error", "Something went wrong"),
])
def test_a_failure_is_explained(client, scraper, exc, status, code, expected):
    scraper.raises = exc
    response = scan(client)
    assert response.status_code == status
    assert error_code(response) == code
    assert expected in response.get_json()["error"]["message"]


def test_an_internal_error_does_not_leak_its_message(client, scraper):
    scraper.raises = RuntimeError("secret path /srv/app")
    assert b"secret path" not in scan(client).data


def test_a_good_scan_returns_the_result(client, scraper):
    response = scan(client)
    assert response.status_code == 201
    body = response.get_json()

    assert body["id"].isalnum()
    assert body["url"] == "http://example.com/"
    assert "data-aai-id" in body["html"]
    assert body["issues"] == SAMPLE_ISSUES
    assert body["report"] is None
    assert body["expires_at"] - body["created_at"] == runstore.MAX_AGE_SECONDS


def test_a_scan_can_be_fetched_again(client, scraper):
    created = scan(client).get_json()
    fetched = client.get(f"/api/scans/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.get_json() == created


def test_an_unknown_scan_explains_itself(client):
    response = client.get("/api/scans/deadbeef")
    assert response.status_code == 404
    assert error_code(response) == "scan_expired"
    assert "minutes" in response.get_json()["error"]["message"]


@pytest.mark.parametrize("method,path", [
    ("get", "/api/scans/..%2f..%2fetc"),
    ("post", "/api/scans/..%2f..%2fetc/verification"),
    ("get", "/api/scans/x.y"),
])
def test_an_escaping_scan_id_goes_nowhere(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert response.is_json


def test_verification_returns_the_report(client, scraper, fake_report):
    run_id = scan(client).get_json()["id"]
    response = client.post(f"/api/scans/{run_id}/verification")
    assert response.status_code == 200
    assert response.get_json()["report"]["verdict"] == "PASS"


def test_the_report_is_kept_with_the_scan(client, scraper, fake_report):
    run_id = scan(client).get_json()["id"]
    client.post(f"/api/scans/{run_id}/verification")
    body = client.get(f"/api/scans/{run_id}").get_json()
    assert body["report"]["verdict"] == "PASS"
    assert body["issues"] == SAMPLE_ISSUES


def test_verifying_a_bogus_scan_explains_itself(client):
    response = client.post("/api/scans/deadbeef/verification")
    assert response.status_code == 404
    assert "no longer available" in response.get_json()["error"]["message"]


def test_a_verifier_crash_is_reported_and_the_scan_survives(client, scraper, monkeypatch):
    run_id = scan(client).get_json()["id"]

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(routes.verifier, "run_checks", boom)
    response = client.post(f"/api/scans/{run_id}/verification")

    assert response.status_code == 500
    assert error_code(response) == "verification_failed"
    assert b"boom" not in response.data
    assert client.get(f"/api/scans/{run_id}").status_code == 200


def test_download_serves_an_attachment_named_after_the_host(client, scraper):
    run_id = scan(client).get_json()["id"]
    response = client.get(f"/api/scans/{run_id}/download")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    disposition = response.headers.get("Content-Disposition", "")
    assert "attachment" in disposition
    assert "example.com-accessible.html" in disposition


def test_download_is_sandboxed_and_not_sniffed(client, scraper):
    run_id = scan(client).get_json()["id"]
    response = client.get(f"/api/scans/{run_id}/download")
    assert response.headers.get("Content-Security-Policy") == "sandbox"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_download_strips_the_verifier_stamps_by_default(client, scraper):
    run_id = scan(client).get_json()["id"]
    assert b"data-aai-id" not in client.get(f"/api/scans/{run_id}/download").data
    assert b"data-aai-id" in client.get(f"/api/scans/{run_id}/download?stamped=1").data


@pytest.mark.parametrize("run_id", ["deadbeef", "..%2f..%2fetc"])
def test_download_refuses_an_unknown_or_escaping_run(client, run_id):
    assert client.get(f"/api/scans/{run_id}/download").status_code == 404


def test_scans_are_capped_per_caller(client, scraper):
    responses = [scan(client) for _ in range(5 + 3)]
    limited = [r for r in responses if r.status_code == 429]

    assert len(limited) == 3
    assert all(error_code(r) == "rate_limited" for r in limited)
    assert all(int(r.headers["Retry-After"]) > 0 for r in limited)


def test_one_caller_does_not_spend_another_callers_allowance(client, scraper):
    for _ in range(5 + 1):
        scan(client)
    other = scan(client, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert other.status_code == 201


def test_a_rejected_url_does_not_use_up_the_allowance(client, scraper):
    for _ in range(10):
        client.post("/api/scans", json={"url": ""})
    assert scan(client).status_code == 201


def test_behind_a_proxy_the_real_client_address_is_used(tmp_path, scraper, monkeypatch):
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path))
    app = create_app(Config(scans_per_hour=1, trusted_proxies=1, runs_dir=str(tmp_path)))
    client = app.test_client()

    first = scan(client, headers={"X-Forwarded-For": "198.51.100.1"})
    second = scan(client, headers={"X-Forwarded-For": "198.51.100.2"})
    assert first.status_code == second.status_code == 201, \
        "two visitors behind one proxy must not share a bucket"


def test_verifications_are_capped_per_caller(tmp_path, scraper, fake_report, monkeypatch):
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path))
    app = create_app(Config(verifications_per_hour=2, runs_dir=str(tmp_path)))
    client = app.test_client()
    run_id = scan(client).get_json()["id"]

    codes = [client.post(f"/api/scans/{run_id}/verification").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_an_old_result_is_gone_even_before_the_sweep(client, scraper, monkeypatch):
    run_id = scan(client).get_json()["id"]
    monkeypatch.setattr(runstore.time, "time",
                        lambda: 10**10)  # far past created_at + MAX_AGE_SECONDS
    assert client.get(f"/api/scans/{run_id}").status_code == 404


def test_the_frontend_can_read_the_download_filename(client, scraper):
    run_id = scan(client).get_json()["id"]
    response = client.get(f"/api/scans/{run_id}/download", headers={"Origin": FRONTEND})
    assert "Content-Disposition" in response.headers.get("Access-Control-Expose-Headers", "")


def test_the_ai_fallback_flag_is_returned_and_kept(client, scraper, monkeypatch):
    def scrape_url(self, url):
        return ScrapeResult("http://example.com/", BeautifulSoup(SAMPLE_HTML, "html.parser"),
                            SAMPLE_ISSUES, SAMPLE_HTML, {"1"}, [], ai_fallback=True)

    monkeypatch.setattr(scraper, "scrape_url", scrape_url)
    body = scan(client).get_json()
    assert body["ai_fallback"] is True
    assert client.get(f"/api/scans/{body['id']}").get_json()["ai_fallback"] is True


def test_a_clean_scan_has_no_warnings(client, scraper):
    body = scan(client).get_json()
    assert body["warnings"] == []
    assert body["ai_fallback"] is False
