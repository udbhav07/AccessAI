"""Tests for the Flask routes.

Gemini is stubbed and the scraper is replaced per test, so nothing here
touches the network or starts a browser. Run with:

    python tests/test_app.py
"""

import types

import os
import sys

# Tests live in tests/ but import the package from the project root, so put the
# root on sys.path before anything else. Works no matter where you run from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_stub = types.ModuleType("gemini")
_stub.suggest_text_color = lambda fg, bg: None
_stub.getAlt = lambda src: "a stubbed description"
_stub.getLabel = lambda inp, label="": "Stub Label"
sys.modules["src.gemini"] = _stub
sys.modules["gemini"] = _stub

import requests                                                  # noqa: E402

import app as webapp                                             # noqa: E402
from src import runstore                                         # noqa: E402
from src.nethttp import BlockedURL                               # noqa: E402
from src.webScraper import ScrapeResult, UnsupportedContent      # noqa: E402

PASSED, FAILED = 0, 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}   {detail}")


webapp.app.config["TESTING"] = True
client = webapp.app.test_client()


class FakeScraper:
    """Stands in for Scraper. `raises` is thrown instead of returning."""

    raises = None
    result = None

    def scrape_url(self, url):
        if FakeScraper.raises is not None:
            raise FakeScraper.raises
        return FakeScraper.result


webapp.Scraper = FakeScraper


def post(url="http://example.com/"):
    FakeScraper.raises = None
    return client.post("/", data={"website_link": url})


def failing(exc):
    FakeScraper.raises = exc
    return client.post("/", data={"website_link": "http://example.com/"})


# ---------------------------------------------------------------------------
print("\n[1] GET / renders the form")

page = client.get("/")
check("200 on the landing page", page.status_code == 200, page.status_code)
check("the url field is there", b'name="website_link"' in page.data)
check("no output section yet", b"Output:" not in page.data)

print("\n[2] a scrape that fails reaches the user as a message, not a 500")

for label, exc, expected in [
    ("a blocked url", BlockedURL("localhost resolves to a non-public address"),
     b"can&#39;t be scanned"),
    ("a non-html response", UnsupportedContent("that URL returned application/pdf, "
                                               "not an HTML page"), b"application/pdf"),
    ("an unreachable host", requests.RequestException("connection refused"),
     b"Could not reach that site"),
    ("an unexpected error", RuntimeError("boom"), b"Something went wrong"),
]:
    response = failing(exc)
    check(f"{label} returns 200", response.status_code == 200, response.status_code)
    check(f"{label} explains itself", expected in response.data,
          response.data[-300:])

check("an internal error does not leak its message",
      b"boom" not in failing(RuntimeError("boom")).data)

print("\n[3] an empty url is caught before anything is fetched")

response = client.post("/", data={"website_link": "   "})
check("blank input asks for a URL", b"Please enter a URL." in response.data)
response = client.post("/", data={})
check("a missing field does the same", b"Please enter a URL." in response.data)

print("\n[4] a successful scrape renders and stores the run")

from bs4 import BeautifulSoup                                    # noqa: E402

soup = BeautifulSoup("<html><body><p data-aai-id='1'>hi</p></body></html>",
                     "html.parser")
FakeScraper.result = ScrapeResult(
    url="http://example.com/",
    soup=soup,
    issues=[{"type": "alt", "source": "image", "target": "a.png",
             "old": "", "new": "a cat", "ids": ["1"]}],
    html_before="<html><body><p data-aai-id='1'>hi</p></body></html>",
    modified_ids={"1"},
)
response = post()
check("200 on a good scrape", response.status_code == 200, response.status_code)
check("the output iframe is rendered", b"<iframe" in response.data)
check("the verify button appears", b'id="verify-btn"' in response.data)

print("\n[4b] the issue list is actually rendered")

FakeScraper.result = ScrapeResult(
    url="http://example.com/",
    soup=soup,
    issues=[
        {"type": "alt", "source": "image", "target": "cat.png",
         "old": "", "new": "a sleeping cat", "ids": ["1"]},
        {"type": "contrast", "source": "inline", "target": "h2",
         "old": "#00f", "new": "#8888ff", "ratio_before": 2.1,
         "ratio_after": 4.9, "ids": ["2"]},
        {"type": "contrast-skipped", "source": "unresolvable", "target": "p",
         "old": None, "new": None, "ratio_before": None, "ratio_after": None,
         "reason": "text sits on a non-uniform background", "ids": []},
    ],
    html_before="<html><body><p data-aai-id='1'>hi</p></body></html>",
    modified_ids={"1", "2"},
)
response = post()
body = response.data
check("the count reflects the changes only", b"2 changes applied" in body,
      body[body.find(b"applied") - 40:body.find(b"applied") + 10])
check("an alt fix is listed", b"a sleeping cat" in body)
check("a contrast fix shows its old colour", b"#00f" in body)
check("...and its new one", b"#8888ff" in body)
check("...and the ratio it moved between",
      b"2.1:1" in body and b"4.9:1" in body)
check("an abstention is not counted as a change",
      b"1 left alone" in body, body[-400:])
check("...and its reason is given", b"non-uniform background" in body)

print("\n[4c] a run's issues survive into /verify")

# The scrape above wrote a run; pick up the newest one rather than reaching
# into the response for an id the page only carries in a hidden field.
_runs_dir = os.path.join(ROOT, "runs")
rid_issues = max(os.listdir(_runs_dir),
                 key=lambda d: os.path.getmtime(os.path.join(_runs_dir, d)))

_real = webapp.verifier.run_checks


class _Rep:
    def as_dict(self):
        return {"verdict": "PASS", "error": None, "checks": []}


webapp.verifier.run_checks = lambda *a, **k: _Rep()
response = client.post("/verify", data={"run_id": rid_issues})
webapp.verifier.run_checks = _real
check("the same change list is shown again after verifying",
      b"a sleeping cat" in response.data, response.data[-300:])

print("\n[5] /verify on an expired or bogus run")

response = client.post("/verify", data={"run_id": "deadbeef"})
check("an unknown run says so", b"expired" in response.data, response.data[-200:])
response = client.post("/verify", data={"run_id": "../../etc"})
check("a traversal attempt is refused the same way", b"expired" in response.data)
response = client.post("/verify", data={})
check("a missing run id is refused", b"expired" in response.data)

print("\n[6] /verify surfaces a verification failure without losing the result")

rid = runstore.new_run_id()
runstore.save_run(rid, "<p data-aai-id='1'>before</p>", "<p data-aai-id='1'>after</p>",
                  {"url": "http://example.com/", "modified_ids": []})

_real_run_checks = webapp.verifier.run_checks
webapp.verifier.run_checks = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
response = client.post("/verify", data={"run_id": rid})
webapp.verifier.run_checks = _real_run_checks

check("a crash in run_checks is not a 500", response.status_code == 200,
      response.status_code)
check("the user is told", b"could not be completed" in response.data)
check("the scan result is not thrown away", b"after" in response.data)

print("\n" + "=" * 62)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 62)
sys.exit(1 if FAILED else 0)
