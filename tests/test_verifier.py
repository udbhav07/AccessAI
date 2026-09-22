"""Tests for the remediation verifier.

Runs a real headless browser but stubs Gemini, so it is deterministic and
free. Includes deliberate-break cases -- a verifier that never fails isn't a
verifier.

    python test_verifier.py
"""

import subprocess
import time
import types

import os
import sys

# Tests live in tests/ but import the package from the project root, so put the
# root on sys.path before anything else. Works no matter where you run from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


_stub = types.ModuleType("gemini")
_stub.suggest_text_color = lambda fg, bg: None      # force the deterministic path
_stub.getAlt = lambda src: "a stubbed description"
_stub.getLabel = lambda inp, label="": "Stub Label"
sys.modules["src.gemini"] = _stub
sys.modules["gemini"] = _stub

from bs4 import BeautifulSoup                                      # noqa: E402
from src import nethttp, runstore, verifier, webScraper            # noqa: E402
from src.webScraper import Scraper, stamp_ids, strip_ids           # noqa: E402

PORT = 8791
# Served from the project root, not templates/: the fixture's images live at
# ../DemoImages, so serving templates/ would 404 them -- and a broken image
# renders at 16px without alt text but expands to fit it once alt is added,
# which is a real (but fixture-induced) layout change.
BASE = f"http://localhost:{PORT}/templates"

# The fixture server is on localhost, which is exactly what nethttp refuses.
# Opt in for the duration of the suite.
os.environ[nethttp.ALLOW_PRIVATE_ENV] = "1"
PASSED, FAILED = 0, 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}   {detail}")


def stamped(html):
    soup = BeautifulSoup(html, "html.parser")
    stamp_ids(soup)
    return soup


def verify(before_soup, after_soup, modified_ids=()):
    return verifier.run_checks(
        f"{BASE}/experiment.html", str(before_soup), str(after_soup), modified_ids
    )


def named(report, name):
    return next(c for c in report.checks if c.name == name)


# ---------------------------------------------------------------------------
print("\n[1] run store")

rid = runstore.new_run_id()
runstore.save_run(rid, "<p>before</p>", "<p>after</p>",
                  {"url": "http://x/", "modified_ids": ["3", "7"]})
loaded = runstore.load_run(rid)
check("round-trips a run", loaded is not None and loaded[0] == "<p>before</p>")
check("meta survives", loaded[2]["modified_ids"] == ["3", "7"], loaded[2])
check("unknown id returns None", runstore.load_run("deadbeef") is None)
check("empty id returns None", runstore.load_run("") is None)
check("path traversal refused on load", runstore.load_run("../../etc") is None)
try:
    runstore.save_run("../evil", "a", "b", {})
    _refused = False
except ValueError:
    _refused = True
check("path traversal refused on save", _refused)

print("\n[2] identity stamping")

soup = stamped("<html><body><div><p>hi</p></div></body></html>")
ids = [el.get("data-aai-id") for el in soup.find_all(True)]
check("every element stamped", all(i is not None for i in ids), ids)
check("ids are unique", len(set(ids)) == len(ids))
strip_ids(soup)
check("strip_ids removes them",
      all(el.get("data-aai-id") is None for el in soup.find_all(True)))

print("\n[3] helpers")

S = verifier.ElementSnapshot
snaps = [
    S("1", None, False, "DIV", 0, 0, 10, 10, "rgb(0,0,0)", "rgb(255,255,255)", 16, 400, True, False),
    S("2", "1", False, "P", 0, 0, 10, 10, "rgb(0,0,0)", "rgb(255,255,255)", 16, 400, True, True),
    S(None, "2", True, "LABEL", 0, 0, 5, 5, "rgb(0,0,0)", "rgb(255,255,255)", 16, 400, True, True),
]
check("insertion ancestors walk up the whole chain",
      verifier._insertion_ancestors(snaps) == {"1", "2"},
      verifier._insertion_ancestors(snaps))
check("descendants of a modified id are excluded",
      verifier._with_descendants({"1"}, snaps) == {"1", "2"},
      verifier._with_descendants({"1"}, snaps))

big = S("9", None, False, "H1", 0, 0, 9, 9, "", "", 30, 400, True, True)
bold = S("9", None, False, "H1", 0, 0, 9, 9, "", "", 19, 700, True, True)
small = S("9", None, False, "P", 0, 0, 9, 9, "", "", 16, 400, True, True)
check("large text uses the 3:1 threshold", verifier._threshold_for(big) == 3.0)
check("bold 19px uses 3:1", verifier._threshold_for(bold) == 3.0)
check("normal text uses 4.5:1", verifier._threshold_for(small) == 4.5)

print("\n[4] live capture on the demo fixture (real browser)")

server = subprocess.Popen(
    [sys.executable, "-m", "http.server", str(PORT)],
    cwd=ROOT,          # serve the project root: the fixture's images are at ../DemoImages
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(2)

try:
    result = Scraper().scrape_url(f"{BASE}/experiment.html")
    after_html = str(result.soup)
    report = verifier.run_checks(
        result.url, result.html_before, after_html, result.modified_ids
    )

    print("\n" + report.as_text() + "\n")

    check("capture succeeded", report.error is None, report.error)
    check("all seven checks ran", len(report.checks) == 7,
          [c.name for c in report.checks])

    cov = named(report, "Coverage")
    check("coverage: alt 0/4 -> 4/4", "alt 0/4 -> 4/4" in cov.summary, cov.summary)
    # inp1 and inp3 already carry non-empty labels; inp2's is whitespace-only.
    # Coverage measures presence, not correctness -- "wrong labeled" on a Gmail
    # field counts as covered. The plan's expected 1/4 was simply miscounted.
    check("coverage: labels 2/4 -> 4/4", "labels 2/4 -> 4/4" in cov.summary, cov.summary)
    check("coverage passes", cov.passed)

    check("visibility passes (nothing lost)", named(report, "Visibility").passed,
          named(report, "Visibility").summary)
    check("layout passes (insertion ancestors exempt from height)",
          named(report, "Layout").passed, named(report, "Layout").summary)
    check("colour passes (no unintended changes)", named(report, "Colour").passed,
          named(report, "Colour").summary)
    check("contrast passes now that COLOR_FIX_PLAN landed",
          named(report, "Contrast").passed, named(report, "Contrast").summary)
    # line-2 declares a black background inline and takes its text colour from
    # demostyles.css, which the inline pass cannot read -- so the fixer
    # abstains. That belongs on the leftover list, not in the verdict.
    rem = named(report, "Remaining")
    check("what the fixer could not reach is listed, not failed",
          rem.passed and rem.details, rem.summary)
    check("...and it is advisory, so the verdict stays PASS",
          rem.tier == "advisory" and report.verdict == "PASS", report.verdict)
    check("pixels within tolerance", named(report, "Pixels").passed,
          named(report, "Pixels").summary)
    check("overall verdict is PASS", report.verdict == "PASS", report.verdict)

    print("\n[4a] non-HTML responses are refused")

    try:
        Scraper().scrape_url(f"{BASE}/demo_theme.css")
        _refused_css = False
    except webScraper.UnsupportedContent:
        _refused_css = True
    check("a stylesheet URL is not parsed as a page", _refused_css)

    try:
        Scraper().scrape_url(f"{BASE}/does-not-exist.html")
        _refused_404 = False
    except Exception:
        _refused_404 = True
    check("a 404 is not scraped as if it were the page", _refused_404)

    check("an html fixture is still accepted",
          Scraper().scrape_url(f"{BASE}/experiment.html").soup is not None)

    print("\n[4b] a failed getAlt leaves the image alone")

    # webScraper does `from .gemini import getAlt`, so the name is bound in
    # that module -- swapping it on the stub module would have no effect.
    _real_getAlt = webScraper.getAlt
    webScraper.getAlt = lambda src: None          # every call fails
    try:
        dud = Scraper().scrape_url(f"{BASE}/experiment.html")
    finally:
        webScraper.getAlt = _real_getAlt

    alt_written = [i for i in dud.soup.find_all("img") if i.get("alt")]
    check("no alt attribute is written when the call fails",
          alt_written == [], [i.get("alt") for i in alt_written])
    cov = verifier.check_coverage(
        BeautifulSoup(dud.html_before, "html.parser"),
        BeautifulSoup(str(dud.soup), "html.parser"))
    check("coverage reports 0/4, not a placeholder 4/4",
          "alt 0/4 -> 0/4" in cov.summary, cov.summary)
    check("the skip is reported rather than silent",
          any(i.get("source") == "skipped" for i in dud.issues),
          [i.get("source") for i in dud.issues])

    print("\n[5] deliberate breaks — a verifier that never fails isn't a verifier")

    base_html = result.html_before

    # B: hide an element
    broken = stamped(base_html)
    broken.find("h1")["style"] = "display:none"
    r = verify(stamped(base_html), broken)
    check("B fails when an element is hidden", not named(r, "Visibility").passed,
          named(r, "Visibility").summary)
    check("...and the verdict is BROKEN", r.verdict == "BROKEN", r.verdict)

    # B: remove an element outright
    broken = stamped(base_html)
    broken.find("h1").decompose()
    r = verify(stamped(base_html), broken)
    check("B fails when an element is removed", not named(r, "Visibility").passed)

    # A: change a width
    broken = stamped(base_html)
    broken.find("h1")["style"] = "width:80px;display:block"
    r = verify(stamped(base_html), broken)
    check("A fails when a width changes", not named(r, "Layout").passed,
          named(r, "Layout").summary)

    # D: recolour something not in modified_ids
    broken = stamped(base_html)
    target = broken.find("h1")
    target["style"] = "color:#c0ffee"
    r = verify(stamped(base_html), broken, modified_ids=[])
    check("D fails on an unattributed colour change", not named(r, "Colour").passed,
          named(r, "Colour").summary)

    # D: same change, but declared -> must pass
    r = verify(stamped(base_html), broken, modified_ids=[target.get("data-aai-id")])
    check("D passes when the change is declared", named(r, "Colour").passed,
          named(r, "Colour").summary)

    # C: introduce a contrast failure
    broken = stamped(base_html)
    broken.find("h1")["style"] = "color:#777;background:#888"
    r = verify(stamped(base_html), broken, modified_ids=[])
    check("C fails on a contrast regression", not named(r, "Contrast").passed,
          named(r, "Contrast").summary)
    # A regression is ours whether we aimed at that element or not, so it
    # still fails even with nothing declared as modified.
    check("...even though nothing was declared modified",
          any("got worse" in d for d in named(r, "Contrast").details),
          named(r, "Contrast").details)

    # C: a pre-existing failure we never claimed is not our failure
    pre_existing = stamped(
        '<html><body><p id="bad" style="color:#777;background:#888">dim</p>'
        '</body></html>')
    r = verify(stamped(str(pre_existing)), pre_existing, modified_ids=[])
    check("an untouched pre-existing failure does not fail the run",
          named(r, "Contrast").passed, named(r, "Contrast").summary)
    check("...it is reported as remaining work instead",
          named(r, "Remaining").details, named(r, "Remaining").summary)
    check("...so the verdict is PASS, not INCOMPLETE", r.verdict == "PASS", r.verdict)

    # C: a failure we DID claim to fix is still a failure
    claimed = stamped(
        '<html><body><p style="color:#777;background:#888">dim</p></body></html>')
    target_id = claimed.find("p").get("data-aai-id")
    r = verify(stamped(str(claimed)), claimed, modified_ids=[target_id])
    check("a claimed element that still fails is reported",
          not named(r, "Contrast").passed, named(r, "Contrast").summary)

    # E: strip an alt attribute
    before_soup = stamped(base_html)
    for img in before_soup.find_all("img"):
        img["alt"] = "described"
    after_soup = stamped(str(before_soup))
    del after_soup.find("img")["alt"]
    r = verify(before_soup, after_soup)
    check("E fails when alt coverage drops", not named(r, "Coverage").passed,
          named(r, "Coverage").summary)

    print("\n[5b] the scraped page's scripts must not run")

    hostile = stamped(
        '<html><body><h1>kept</h1>'
        '<script>document.body.innerHTML = "";</script>'
        '</body></html>'
    )
    r = verify(stamped(str(hostile)), hostile)
    check("a script that empties the body does not run",
          named(r, "Visibility").passed, named(r, "Visibility").summary)
    check("...so the verdict is not dictated by the page",
          r.verdict == "PASS", r.verdict)

    print("\n[6] verdict tiering")

    def rep(**kw):
        r = verifier.Report()
        r.checks = [
            verifier.CheckResult("Layout", kw.get("layout", True), "", [], "blocking"),
            verifier.CheckResult("Contrast", kw.get("contrast", True), "", [], "objective"),
            verifier.CheckResult("Pixels", kw.get("pixels", True), "", [], "advisory"),
        ]
        return r

    check("all pass -> PASS", rep().verdict == "PASS")
    check("advisory only -> REVIEW", rep(pixels=False).verdict == "REVIEW")
    check("objective -> INCOMPLETE", rep(contrast=False).verdict == "INCOMPLETE")
    check("blocking -> BROKEN", rep(layout=False).verdict == "BROKEN")
    check("blocking outranks objective",
          rep(layout=False, contrast=False).verdict == "BROKEN")
    check("no blended score: one good check can't mask a fatal one",
          rep(layout=False, contrast=True, pixels=True).verdict == "BROKEN")

    print("\n[7] graceful degradation")

    real_capture = verifier.capture
    verifier.capture = lambda *a, **k: (_ for _ in ()).throw(
        verifier.CaptureError("no browser"))
    r = verify(stamped(base_html), stamped(base_html))
    verifier.capture = real_capture
    check("capture failure yields ERROR, not a false PASS", r.verdict == "ERROR", r.verdict)
    check("...but coverage still reported", any(c.name == "Coverage" for c in r.checks))
    check("...and the error is surfaced", bool(r.error), r.error)

finally:
    server.terminate()
    server.wait(timeout=5)

print("\n" + "=" * 62)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 62)
sys.exit(1 if FAILED else 0)
