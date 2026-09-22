import collections
import os
import threading
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, abort, render_template, request

from src import gemini, runstore, verifier
from src.nethttp import BlockedURL
from src.webScraper import Scraper, UnsupportedContent, strip_ids

app = Flask(__name__, template_folder="templates")

# Whatever the last burst left behind sits there until someone scans again,
# because the sweep only used to run on write. Clear it once on the way up.
runstore.sweep()

# A scan is expensive in a way a visitor cannot see: dozens of Gemini calls,
# two full page copies on disk, and a browser launch if they verify. Without a
# ceiling, one person with a loop empties the API budget and fills the disk.
# Deliberately in-process and per-worker -- a shared store would be the right
# answer, and is not worth a Redis dependency at this size.
SCANS_PER_HOUR = 20
_scan_log = collections.defaultdict(collections.deque)
_scan_lock = threading.Lock()


def _over_rate_limit(client_ip):
    cutoff = time.time() - 3600
    with _scan_lock:
        seen = _scan_log[client_ip]
        while seen and seen[0] < cutoff:
            seen.popleft()
        if len(seen) >= SCANS_PER_HOUR:
            return True
        seen.append(time.time())
        return False


@app.after_request
def security_headers(response):
    """Headers the app should have been sending all along.

    The CSP pairs with the empty `sandbox` on the output iframe: the preview
    is third-party markup rendered inside our page, so it must not be able to
    run anything.

    Checked in Chromium: because the frame is sandboxed into an opaque origin,
    this policy does not reach inside it -- the scraped page's own stylesheets
    and images still load and the preview renders as the site looks. Worth
    re-checking if anyone tightens this, because a policy that *did* inherit
    would leave every preview unstyled, which is most of the point of it.

    The jsdelivr entries are for the Bootstrap bundle the template pulls in;
    style-src needs 'unsafe-inline' only because Bootstrap components set
    inline styles at runtime.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "frame-src data:; "
        "base-uri 'none'; "
        "form-action 'self'",
    )
    return response


@app.context_processor
def template_defaults():
    """Tell every page whether the model is actually reachable.

    Without a key the fixers fall back to deterministic choices, which look
    exactly like model output on screen. The user should not have to guess
    which one they are reading.
    """
    return {"ai_enabled": gemini.ai_enabled()}


@app.route("/", methods=["POST", "GET"])
def index():
    if request.method != "POST":
        return render_template("index.html")

    url = (request.form.get("website_link") or "").strip()
    if not url:
        return render_template("index.html", error="Please enter a URL.")

    if _over_rate_limit(request.remote_addr or "unknown"):
        return render_template(
            "index.html",
            error=f"That is more than {SCANS_PER_HOUR} scans in an hour. "
                  "Please try again later.",
        )

    try:
        result = Scraper().scrape_url(url)
    except BlockedURL as exc:
        return render_template("index.html", error=f"That URL can't be scanned: {exc}")
    except UnsupportedContent as exc:
        return render_template("index.html", error=str(exc))
    except requests.RequestException as exc:
        return render_template("index.html", error=f"Could not reach that site: {exc}")
    except Exception:
        # Last resort. The traceback goes to the log, never to the page: an
        # internal error message tells a visitor about paths and versions they
        # have no business knowing.
        app.logger.exception("scrape failed for %s", url)
        return render_template(
            "index.html", error="Something went wrong scanning that page."
        )

    # Asset URLs were absolutised and identity stamps applied inside the
    # scraper, before the before-snapshot was taken -- so the two versions
    # differ only by the fixes themselves.
    html = str(result.soup)

    run_id = runstore.new_run_id()
    runstore.save_run(
        run_id,
        result.html_before,
        html,
        {
            "url": result.url,
            "modified_ids": sorted(result.modified_ids),
            # Kept so /verify can show the same list again -- rebuilding it
            # would mean a second scrape, and Gemini would answer differently.
            "issues": result.issues,
        },
    )

    return render_template(
        "index.html", output=html, run_id=run_id, issues=result.issues
    )


@app.route("/verify", methods=["POST"])
def verify():
    """Compare the remediated HTML against the original page.

    The real operation is *this generated HTML vs. that original URL* -- one
    side is not a URL at all, which is why the old two-URL screenshot tool
    could never be the right thing to point at.
    """
    run_id = request.form.get("run_id", "")
    run = runstore.load_run(run_id)
    if run is None:
        return render_template(
            "index.html",
            error="That result has expired. Please run the scan again.",
        )

    before_html, after_html, meta = run
    issues = meta.get("issues", [])
    try:
        report = verifier.run_checks(
            meta.get("url", ""), before_html, after_html, meta.get("modified_ids", [])
        )
    except Exception:
        # run_checks handles a browser that will not start on its own; this is
        # for everything else. The scan result is still worth showing, so it
        # goes back with the error rather than being thrown away.
        app.logger.exception("verification failed for run %s", run_id)
        return render_template(
            "index.html",
            output=after_html,
            run_id=run_id,
            issues=issues,
            error="Verification could not be completed.",
        )

    return render_template(
        "index.html",
        output=after_html,
        run_id=run_id,
        issues=issues,
        report=report.as_dict(),
    )


@app.route("/download/<run_id>")
def download(run_id):
    """Hand back the remediated page as a file.

    Two forms. The default strips the `data-aai-*` stamps, because those exist
    only so the verifier can match old to new and have no business in a page
    anyone deploys. `?stamped=1` keeps them, so a result can be fed back
    through verification later.
    """
    run = runstore.load_run(run_id)      # validates the id; traversal is refused
    if run is None:
        abort(404)

    _, after_html, meta = run
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
            "X-Content-Type-Options": "nosniff",
        },
    )


if __name__ == "__main__":
    # Werkzeug's debugger is an interactive console -- shipping it enabled
    # is remote code execution. Opt in locally with FLASK_DEBUG=1.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(debug=debug)
