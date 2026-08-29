"""Offline tests for the colour-contrast remediation.

Gemini is stubbed out, so the suite is deterministic, free, and needs no API
key or network. Run with:  python test_colors.py
"""

import types

import os
import sys

# Tests live in tests/ but import the package from the project root, so put the
# root on sys.path before anything else. Works no matter where you run from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# --- stub gemini before webColorss imports it -------------------------------
_stub = types.ModuleType("gemini")
_stub.suggest_calls = []
_stub.suggest_reply = None          # what the fake model "returns"


def _suggest_text_color(fg, bg):
    _stub.suggest_calls.append((fg, bg))
    return _stub.suggest_reply


_stub.suggest_text_color = _suggest_text_color
# webColorss does `from .gemini import ...`, which resolves to the
# absolute name src.gemini -- so that is the key to stub.
sys.modules["src.gemini"] = _stub
sys.modules["gemini"] = _stub

from bs4 import BeautifulSoup                                   # noqa: E402
from src import webColorss as wc                                # noqa: E402

PASSED, FAILED = 0, 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}   {detail}")


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def ratio_of(el_style_color, bg):
    return wc.check_contrast(el_style_color, bg)


# ---------------------------------------------------------------------------
print("\n[1] colour parsing — named colours were the hidden blocker")

check("named colour resolves", wc.resolve_color("cadetblue") == (95, 158, 160),
      wc.resolve_color("cadetblue"))
check("short hex resolves", wc.resolve_color("#00f") == (0, 0, 255))
check("rgb() resolves", wc.resolve_color("rgb(255, 0, 0)") == (255, 0, 0))
check("!important stripped", wc.resolve_color("black !important") == (0, 0, 0))
check("transparent -> None", wc.resolve_color("transparent") is None)
check("inherit -> None", wc.resolve_color("inherit") is None)
check("currentColor -> None", wc.resolve_color("currentColor") is None)
check("zero-alpha rgba -> None", wc.resolve_color("rgba(0,0,0,0)") is None)
check("garbage -> None", wc.resolve_color("no-repeat") is None)
check("None -> None", wc.resolve_color(None) is None)

print("\n[2] contrast maths")

check("black on white is 21:1", abs(wc.check_contrast("black", "white") - 21.0) < 0.01,
      wc.check_contrast("black", "white"))
check("identical colours are 1:1", abs(wc.check_contrast("#123456", "#123456") - 1.0) < 0.01)
aqua_cadet = wc.check_contrast("cadetblue", "aqua")
check("demo #ab pair is ~2.4:1 (was 1.0 before the fix)",
      2.3 < aqua_cadet < 2.5, f"got {aqua_cadet}")
check("unresolvable returns None (not a fake 0.5)",
      wc.check_contrast("transparent", "white") is None)

print("\n[3] deterministic fallback always clears 4.5:1")

worst = 99.0
for r in range(0, 256, 15):
    for g in range(0, 256, 15):
        for b in range(0, 256, 15):
            bg = (r, g, b)
            fixed = wc._deterministic_color((128, 128, 128), bg, wc.WCAG_AA_NORMAL)
            got = wc.check_contrast(fixed, wc._to_hex(bg))
            worst = min(worst, got)
check("every background reaches >= 4.5:1", worst >= wc.WCAG_AA_NORMAL,
      f"worst was {worst:.3f}")
print(f"        (worst case across 5,832 backgrounds: {worst:.2f}:1)")

print("\n[4] ensure_contrast — verify the model, then fall back")

wc.reset_budget()
_stub.suggest_calls.clear()
_stub.suggest_reply = "#ffffff"                    # a good suggestion
got = wc.ensure_contrast("#00f", "#000")
check("accepts a passing suggestion", got == "#ffffff", got)

wc.reset_budget()
_stub.suggest_reply = "#0000ee"                    # still fails on black
got = wc.ensure_contrast("#00f", "#000")
check("rejects a failing suggestion", got != "#0000ee", got)
check("...and the fallback passes", wc.check_contrast(got, "#000") >= 4.5)

wc.reset_budget()
_stub.suggest_reply = "not a colour at all"
got = wc.ensure_contrast("#00f", "#000")
check("survives an unparseable suggestion", wc.check_contrast(got, "#000") >= 4.5)

wc.reset_budget()
_stub.suggest_reply = None                         # API failure
got = wc.ensure_contrast("#00f", "#000")
check("survives a total API failure", wc.check_contrast(got, "#000") >= 4.5)

wc.reset_budget()
_stub.suggest_reply = "#ffffff"
_stub.suggest_calls.clear()
for _ in range(30):
    wc.ensure_contrast("#00f", "#000")
check("cache collapses 30 identical pairs to 1 call",
      len(_stub.suggest_calls) == 1, f"{len(_stub.suggest_calls)} calls")

wc.reset_budget()
_stub.suggest_calls.clear()
for i in range(40):                                 # 40 distinct pairs
    wc.ensure_contrast(f"#0000{i:02x}", "#000")
check(f"call budget caps at {wc.MAX_GEMINI_CALLS}",
      len(_stub.suggest_calls) == wc.MAX_GEMINI_CALLS, f"{len(_stub.suggest_calls)} calls")

print("\n[5] inline styles")

wc.reset_budget()
_stub.suggest_reply = None                          # force deterministic everywhere
report = []
s = soup_of('<h2 style="background-color: #000; color: #00f; padding: 2em">line-1</h2>')
wc.fix_inline_styles(s, report)
h2 = s.find("h2")
check("blue-on-black was fixed", "#00f" not in h2["style"].lower(), h2["style"])
check("...to a passing colour",
      wc.check_contrast(wc.cssutils.parseStyle(h2["style"]).getPropertyValue("color"),
                        "#000") >= 4.5)
check("padding survived (whole-attribute bug fixed)", "padding" in h2["style"], h2["style"])
check("report has one entry", len(report) == 1, report)

report = []
s = soup_of('<h2 style="background-color: black">line-2</h2>')
wc.fix_inline_styles(s, report)
h2 = s.find("h2")
colour = wc.cssutils.parseStyle(h2["style"]).getPropertyValue("color")
check("line-2 (bg only, inherited black text) was fixed", bool(colour), h2["style"])
check("...to a passing colour", wc.check_contrast(colour, "black") >= 4.5)

report = []
s = soup_of('<div style="background:black"><h2 style="color:#111">deep</h2></div>')
wc.fix_inline_styles(s, report)
colour = wc.cssutils.parseStyle(s.find("h2")["style"]).getPropertyValue("color")
check("ancestor background walk works", wc.check_contrast(colour, "black") >= 4.5, colour)
check("`background:` shorthand was understood", len(report) >= 1)

report = []
s = soup_of('<p style="color:#000; background:#fff">fine</p>')
wc.fix_inline_styles(s, report)
check("passing pair is left alone", report == [], report)

report = []
s = soup_of('<p style="padding:4px">no colours</p>')
wc.fix_inline_styles(s, report)
check("element with no colour declarations is skipped", report == [])

print("\n[6] presentational attributes")

report = []
s = soup_of('<body bgcolor="#000000" text="#0000ff"><p>hi</p></body>')
wc.fix_presentational_attributes(s, report)
body = s.find("body")
check("body text= fixed against bgcolor=", wc.check_contrast(body["text"], "#000000") >= 4.5,
      body["text"])
check("bgcolor left untouched", body["bgcolor"] == "#000000")
check("no inline style was introduced (cascade preserved)",
      body.get("style") is None, body.get("style"))

report = []
s = soup_of('<body bgcolor="black"><font color="#222">dim</font></body>')
wc.fix_presentational_attributes(s, report)
check("<font color> fixed against ancestor bgcolor",
      wc.check_contrast(s.find("font")["color"], "black") >= 4.5, s.find("font")["color"])

report = []
s = soup_of('<body bgcolor="white" text="black"></body>')
wc.fix_presentational_attributes(s, report)
check("passing attribute pair left alone", report == [], report)

print("\n[7] <style> blocks")

wc.reset_budget()
report = []
css = "#ab{background-color:aqua;color:cadetblue}\n.hero{background:url('img/x.png') no-repeat}"
s = soup_of(f"<style>{css}</style>")
wc.fix_style_blocks(s, "https://example.com/assets/page.html", report)
out = s.find("style").string
check("aqua/cadetblue rule was fixed", "cadetblue" not in out, out)
check("report names the selector",
      any(e["target"] == "#ab" for e in report), report)
check("ratio_before ~2.4 recorded",
      any(2.3 < (e["ratio_before"] or 0) < 2.5 for e in report), report)
check("ratio_after clears 4.5",
      all((e["ratio_after"] or 0) >= 4.5 for e in report), report)
check("url() absolutised against the page",
      "https://example.com/assets/img/x.png" in out, out)
check("selector '>' not HTML-escaped", "&gt;" not in out)

print("\n[8] external <link> stylesheets")


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        pass


fetched = []


def fake_get(url, timeout=None):
    fetched.append(url)
    if url.endswith("bad.css"):
        return FakeResponse("#ab{background-color:aqua;color:cadetblue}"
                            ".h{background:url('img/hero.png')}")
    if url.endswith("good.css"):
        return FakeResponse("p{color:#000;background-color:#fff}")
    raise wc.requests.RequestException("404")


wc.requests.get = fake_get

wc.reset_budget()
report, fetched[:] = [], []
s = soup_of('<link rel="stylesheet" href="css/bad.css">'
            '<link rel="stylesheet" href="css/good.css">'
            '<link rel="stylesheet" href="css/missing.css">'
            '<link rel="icon" href="favicon.ico">')
wc.fix_linked_stylesheets(s, "https://example.com/page.html", report)

check("per-sheet URL used for the fetch",
      "https://example.com/css/bad.css" in fetched, fetched)
check("non-stylesheet <link rel=icon> skipped",
      not any("favicon" in u for u in fetched), fetched)
check("modified sheet was inlined",
      s.find("style") is not None and "cadetblue" not in s.find("style").string)
check("url() resolved against the SHEET, not the page",
      "https://example.com/css/img/hero.png" in s.find("style").string,
      s.find("style").string)
check("unmodified sheet stays a <link>",
      any(l.get("href", "").endswith("good.css") for l in s.find_all("link")),
      [l.get("href") for l in s.find_all("link")])
check("unreachable sheet leaves its <link> intact",
      any(l.get("href", "").endswith("missing.css") for l in s.find_all("link")))
check("<link rel=icon> untouched",
      any(l.get("href") == "favicon.ico" for l in s.find_all("link")))

print("\n[9] ChangeColor end-to-end on the demo fixture")

wc.reset_budget()


def fixture_get(url, timeout=None):
    return FakeResponse("#ab{background-color:aqua;color:cadetblue}")


wc.requests.get = fixture_get

demo = """<html><head>
<link rel="stylesheet" href="demostyles.css">
</head><body>
<div><h2 style="background-color: #000; color: #00f;">line-1</h2></div>
<div><h2 style="background-color: black;">line-2</h2></div>
<div><h2 id="ab">line-3</h2></div>
<div><h2 id="abc">line-4</h2></div>
</body></html>"""

s = soup_of(demo)
issues = wc.ChangeColor("http://localhost:8000/experiment.html", s)
sources = {e["source"] for e in issues}

check("returned a real report (was always [] before)", len(issues) >= 3, len(issues))
check("inline failures detected", "inline" in sources, sources)
check("stylesheet failure detected", "stylesheet" in sources, sources)
check("every emitted colour passes 4.5:1",
      all((e["ratio_after"] or 0) >= 4.5 for e in issues),
      [(e["target"], e["ratio_after"]) for e in issues])
check("every fix was a genuine improvement",
      all(e["ratio_after"] > e["ratio_before"] for e in issues))
check("#ab (2.4:1, stylesheet-only) is now fixed — the case the old code could never see",
      any(e["target"] == "#ab" for e in issues), [e["target"] for e in issues])


print("")
print("[10] the four-source fixture (templates/demo_all_sources.html)")

FIXTURE_URL = "http://example.com/templates/demo_all_sources.html"
_fixture_css = open(os.path.join(ROOT, "templates", "demo_theme.css"), encoding="utf-8").read()


def fixture_fetch(url, timeout=None):
    if url.endswith("demo_theme.css"):
        return FakeResponse(_fixture_css)
    raise wc.requests.RequestException("404")


wc.requests.get = fixture_fetch
wc.reset_budget()
_stub.suggest_reply = None                      # deterministic path only

with open(os.path.join(ROOT, "templates", "demo_all_sources.html"), encoding="utf-8") as fh:
    fixture = soup_of(fh.read())
fixture_issues = wc.ChangeColor(FIXTURE_URL, fixture)
by_source = {}
for e in fixture_issues:
    by_source.setdefault(e["source"], []).append(e)

for src, human in (("inline", "1. inline style"), ("attribute", "2. legacy attribute"),
                   ("style-block", "3. <style> block"), ("stylesheet", "4. external sheet")):
    check("fixture exercises " + human, src in by_source, sorted(by_source))

check("fixture: every emitted colour passes 4.5:1",
      all(e["ratio_after"] >= 4.5 for e in fixture_issues),
      [(e["target"], e["ratio_after"]) for e in fixture_issues])
check("fixture: every fix is a genuine improvement",
      all(e["ratio_after"] > e["ratio_before"] for e in fixture_issues))
check("fixture: inline padding survived",
      "padding" in fixture.find("h2")["style"], fixture.find("h2")["style"])
check("fixture: bgcolor left untouched",
      fixture.find("body")["bgcolor"] == "#000000")
check("fixture: no inline style added to <body> (cascade preserved)",
      fixture.find("body").get("style") is None)
check("fixture: external sheet inlined",
      fixture.find("link", rel="stylesheet") is None)
check("fixture: url() absolutised against the SHEET",
      "http://example.com/templates/img/hero.png" in str(fixture),
      "url() not rewritten")

print("\n" + "=" * 62)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 62)
sys.exit(1 if FAILED else 0)
