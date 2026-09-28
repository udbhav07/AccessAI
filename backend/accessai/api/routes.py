"""The API endpoints. They validate input, rate-limit scans and verifications, call core
and turn its failures into JSON errors.

    GET  /api/health           storage, browser and AI status; 503 if degraded
    GET  /api/config
    POST /api/scans            {"url"}  fetch a page, fix it, store the run
    GET  /api/scans/<id>
    POST /api/scans/<id>/verification   render before/after and compare
    GET  /api/scans/<id>/download       fixed page as an .html file

No CSRF protection: there are no sessions or cookies.
"""

import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Response, current_app, jsonify, request

from ..core import browser, gemini, runstore, verifier
from ..core.nethttp import BlockedURL
from ..core.scraper import Scraper, UnsupportedContent, strip_ids
from . import api
from .errors import ApiError

MAX_URL_LENGTH = 2048
# Matches "mailto:" but not "localhost:8000" (digits after the colon are a port).
_HAS_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)")


def _settings():
    return current_app.config["ACCESSAI"]


def _check_rate_limit(limiter, limit, action):
    wait = current_app.extensions[limiter].hit(request.remote_addr or "unknown")
    if wait:
        raise ApiError(
            429, "rate_limited",
            f"That is more than {limit} {action} in an hour. Please try again later.",
            headers={"Retry-After": str(wait)},
        )


def _normalise_url(raw):
    url = (raw or "").strip()
    if not url:
        raise ApiError(400, "missing_url", "Please enter a URL.")
    if len(url) > MAX_URL_LENGTH:
        raise ApiError(400, "invalid_url", "That URL is too long.")
    if not _HAS_SCHEME.match(url):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ApiError(400, "invalid_url", "Please enter a web address starting "
                                           "with http:// or https://.")
    return url


def _load_run_or_404(run_id):
    run = runstore.load_run(run_id)  # also validates the id
    if run is None:
        minutes = runstore.MAX_AGE_SECONDS // 60
        raise ApiError(404, "scan_expired",
                       f"That result is no longer available -- results are kept "
                       f"for {minutes} minutes. Please run the scan again.")
    return run


def _scan_payload(run_id, after_html, meta):
    created = meta.get("created_at", time.time())
    return {
        "id": run_id,
        "url": meta.get("url", ""),
        "html": after_html,
        "issues": meta.get("issues", []),
        "warnings": meta.get("warnings", []),
        "ai_fallback": meta.get("ai_fallback", False),
        "report": meta.get("report"),
        "created_at": created,
        "expires_at": created + runstore.MAX_AGE_SECONDS,
    }


@api.get("/health")
def health():
    """503 when scans can't work properly. AI is reported but optional."""
    checks = {
        "storage": runstore.is_writable(),
        "browser": browser.is_available(),
        "ai": gemini.ai_enabled(),
    }
    healthy = checks["storage"] and checks["browser"]
    return {"status": "ok" if healthy else "degraded", "checks": checks}, 200 if healthy else 503


@api.get("/config")
def config():
    return {
        "ai_enabled": gemini.ai_enabled(),
        "scans_per_hour": _settings().scans_per_hour,
        "result_ttl_minutes": runstore.MAX_AGE_SECONDS // 60,
    }


@api.post("/scans")
def create_scan():
    body = request.get_json(silent=True) or {}
    url = _normalise_url(body.get("url") if isinstance(body, dict) else None)

    _check_rate_limit("scan_limiter", _settings().scans_per_hour, "scans")

    try:
        result = Scraper().scrape_url(url)
    except BlockedURL as exc:
        raise ApiError(422, "blocked_url", f"That URL can't be scanned: {exc}")
    except UnsupportedContent as exc:
        raise ApiError(422, "unsupported_content", _sentence(str(exc)))
    except requests.RequestException as exc:
        current_app.logger.info("fetch failed for %s: %s", url, exc)
        raise ApiError(502, "unreachable",
                       f"Could not reach that site: {_describe_fetch_error(exc)}")

    after_html = str(result.soup)
    meta = {
        "url": result.url,
        "modified_ids": sorted(result.modified_ids),
        "issues": result.issues,
        "warnings": list(result.warnings),
        "ai_fallback": result.ai_fallback,
        "created_at": time.time(),
    }
    run_id = runstore.new_run_id()
    runstore.save_run(run_id, result.html_before, after_html, meta)

    return jsonify(_scan_payload(run_id, after_html, meta)), 201


@api.get("/scans/<run_id>")
def get_scan(run_id):
    _, after_html, meta = _load_run_or_404(run_id)
    return _scan_payload(run_id, after_html, meta)


@api.post("/scans/<run_id>/verification")
def verify_scan(run_id):
    before_html, after_html, meta = _load_run_or_404(run_id)
    _check_rate_limit("verify_limiter", _settings().verifications_per_hour, "verifications")
    try:
        report = verifier.run_checks(
            meta.get("url", ""), before_html, after_html, meta.get("modified_ids", [])
        ).as_dict()
    except Exception:
        current_app.logger.exception("verification failed for run %s", run_id)
        raise ApiError(500, "verification_failed", "Verification could not be completed.")

    runstore.update_meta(run_id, report=report)
    return {"report": report}


@api.get("/scans/<run_id>/download")
def download_scan(run_id):
    """Strips the data-aai-* stamps unless ?stamped=1."""
    _, after_html, meta = _load_run_or_404(run_id)
    if request.args.get("stamped") not in ("1", "true", "yes"):
        soup = BeautifulSoup(after_html, "html.parser")
        strip_ids(soup)
        after_html = str(soup)

    host = urlparse(meta.get("url", "")).netloc or "page"
    safe = "".join(c for c in host if c.isalnum() or c in "-.") or "page"
    return Response(
        after_html,
        mimetype="text/html",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}-accessible.html"',
            # Scraped third-party HTML: don't let it run if opened inline.
            "Content-Security-Policy": "sandbox",
        },
    )


def _sentence(text):
    return text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")


def _describe_fetch_error(exc):
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"it answered with HTTP {exc.response.status_code}."
    if isinstance(exc, requests.Timeout):
        return "it took too long to answer."
    if isinstance(exc, requests.ConnectionError):
        return "the connection failed. Check the address and try again."
    return "the request failed."
