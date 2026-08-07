from flask import Flask, render_template, request

from src import runstore, verifier
from src.webScraper import Scraper

app = Flask(__name__, template_folder="templates")


@app.route("/", methods=["POST", "GET"])
def index():
    if request.method == "POST":
        url = request.form.get("website_link")
        result = Scraper().scrape_url(url)

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

    return render_template("index.html")


@app.route("/verify", methods=["POST"])
def verify():
    """Compare the remediated HTML against the original page.

    The real operation is *this generated HTML vs. that original URL* -- one
    side is not a URL at all, which is why the old two-URL screenshot tool
    could never be the right thing to point at.
    """
    run = runstore.load_run(request.form.get("run_id", ""))
    if run is None:
        return render_template(
            "index.html",
            error="That result has expired. Please run the scan again.",
        )

    before_html, after_html, meta = run
    report = verifier.run_checks(
        meta.get("url", ""), before_html, after_html, meta.get("modified_ids", [])
    )

    return render_template(
        "index.html",
        output=after_html,
        run_id=request.form.get("run_id", ""),
        report=report.as_dict(),
    )


if __name__ == "__main__":
    app.run(debug=True)
