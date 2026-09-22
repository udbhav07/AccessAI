import os

import requests
from flask import Flask, render_template, request

from src import runstore, verifier
from src.nethttp import BlockedURL
from src.webScraper import Scraper, UnsupportedContent

app = Flask(__name__, template_folder="templates")


@app.route("/", methods=["POST", "GET"])
def index():
    if request.method != "POST":
        return render_template("index.html")

    url = (request.form.get("website_link") or "").strip()
    if not url:
        return render_template("index.html", error="Please enter a URL.")

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
        {"url": result.url, "modified_ids": sorted(result.modified_ids)},
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
            error="Verification could not be completed.",
        )

    return render_template(
        "index.html",
        output=after_html,
        run_id=run_id,
        report=report.as_dict(),
    )


if __name__ == "__main__":
    # Werkzeug's debugger is an interactive console -- shipping it enabled
    # is remote code execution. Opt in locally with FLASK_DEBUG=1.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(debug=debug)
