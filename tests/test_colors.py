"""Offline tests for the colour-contrast remediation.

No key, no network: the model is stubbed in conftest, and `suggestions`
below replaces the one call webColorss makes so a test can dictate what the
model "answers" and count how often it was asked.
"""

import os
import types

import pytest
from bs4 import BeautifulSoup

from src import webColorss as wc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def fixes(report):
    """Only the entries that changed a colour -- abstentions carry no ratio."""
    return [e for e in report if e["type"] == "contrast"]


def abstentions(report):
    return [e for e in report if e["type"] == "contrast-skipped"]


@pytest.fixture
def suggestions(monkeypatch):
    """Control and record what the model suggests.

    `reply` is what it answers; `calls` is every (fg, bg) it was asked about.
    The default of None forces the deterministic path.
    """
    state = types.SimpleNamespace(reply=None, calls=[])

    def fake(fg, bg):
        state.calls.append((fg, bg))
        return state.reply

    monkeypatch.setattr(wc, "suggest_text_color", fake)
    return state


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        pass


@pytest.fixture
def sheets(monkeypatch):
    """Serve canned stylesheets and record which URLs were fetched."""
    state = types.SimpleNamespace(files={}, fetched=[])

    def fake_get(url, timeout=None, max_bytes=None):
        state.fetched.append(url)
        for suffix, body in state.files.items():
            if url.endswith(suffix):
                return FakeResponse(body)
        raise wc.requests.RequestException("404")

    monkeypatch.setattr(wc.nethttp, "get", fake_get)
    return state


# --- colour parsing: named colours were the hidden blocker ------------------

@pytest.mark.parametrize("value,expected", [
    ("cadetblue", (95, 158, 160)),
    ("#00f", (0, 0, 255)),
    ("rgb(255, 0, 0)", (255, 0, 0)),
    ("black !important", (0, 0, 0)),
])
def test_colours_that_resolve(value, expected):
    assert wc.resolve_color(value) == expected


@pytest.mark.parametrize("value", [
    "transparent", "inherit", "currentColor", "rgba(0,0,0,0)", "no-repeat", None,
])
def test_values_that_carry_no_colour(value):
    """None means skip this element -- never silently treated as a real colour."""
    assert wc.resolve_color(value) is None


# --- contrast maths ---------------------------------------------------------

def test_black_on_white_is_21_to_1():
    assert abs(wc.check_contrast("black", "white") - 21.0) < 0.01


def test_identical_colours_are_1_to_1():
    assert abs(wc.check_contrast("#123456", "#123456") - 1.0) < 0.01


def test_the_demo_pair_is_about_2_4():
    """cadetblue on aqua. The old parser scored every named colour 1.0."""
    assert 2.3 < wc.check_contrast("cadetblue", "aqua") < 2.5


def test_unresolvable_returns_none_not_a_fake_midpoint():
    assert wc.check_contrast("transparent", "white") is None


def test_deterministic_fallback_clears_the_threshold_on_every_background():
    worst = 99.0
    for r in range(0, 256, 15):
        for g in range(0, 256, 15):
            for b in range(0, 256, 15):
                fixed = wc._deterministic_color((128, 128, 128), (r, g, b),
                                                wc.WCAG_AA_NORMAL)
                worst = min(worst, wc.check_contrast(fixed, wc._to_hex((r, g, b))))
    assert worst >= wc.WCAG_AA_NORMAL, f"worst case was {worst:.3f}:1"


# --- ensure_contrast: verify the model, then fall back ----------------------

def test_a_passing_suggestion_is_accepted(suggestions):
    suggestions.reply = "#ffffff"
    assert wc.ensure_contrast("#00f", "#000", budget=wc.ColourBudget()) == "#ffffff"


@pytest.mark.parametrize("reply", ["#0000ee", "not a colour at all", None])
def test_a_suggestion_that_does_not_pass_is_replaced(suggestions, reply):
    suggestions.reply = reply
    got = wc.ensure_contrast("#00f", "#000", budget=wc.ColourBudget())
    assert got != reply
    assert wc.check_contrast(got, "#000") >= 4.5


def test_cache_collapses_repeated_pairs_to_one_call(suggestions):
    suggestions.reply = "#ffffff"
    budget = wc.ColourBudget()
    for _ in range(30):
        wc.ensure_contrast("#00f", "#000", budget=budget)
    assert len(suggestions.calls) == 1


def test_the_call_budget_caps(suggestions):
    suggestions.reply = "#ffffff"
    budget = wc.ColourBudget()
    for i in range(40):                       # 40 distinct pairs
        wc.ensure_contrast(f"#0000{i:02x}", "#000", budget=budget)
    assert len(suggestions.calls) == wc.MAX_GEMINI_CALLS


def test_two_budgets_share_nothing(suggestions):
    """The whole reason this is an object: one page must not reach another."""
    suggestions.reply = "#ffffff"
    a, b = wc.ColourBudget(), wc.ColourBudget()
    wc.ensure_contrast("#00f", "#000", budget=a)
    wc.ensure_contrast("#00f", "#000", budget=b)
    assert len(suggestions.calls) == 2
    assert a.used == 1 and b.used == 1


# --- inline styles ----------------------------------------------------------

def test_blue_on_black_is_fixed_and_padding_survives(suggestions):
    report = []
    s = soup_of('<h2 style="background-color: #000; color: #00f; padding: 2em">x</h2>')
    wc.fix_inline_styles(s, report)

    style = s.find("h2")["style"]
    assert "#00f" not in style.lower()
    assert wc.check_contrast(
        wc.cssutils.parseStyle(style).getPropertyValue("color"), "#000") >= 4.5
    assert "padding" in style, "the whole style attribute used to be replaced"
    assert len(report) == 1


def test_a_background_only_element_uses_the_inherited_text_colour(suggestions):
    report = []
    s = soup_of('<h2 style="background-color: black">x</h2>')
    wc.fix_inline_styles(s, report)

    colour = wc.cssutils.parseStyle(s.find("h2")["style"]).getPropertyValue("color")
    assert colour and wc.check_contrast(colour, "black") >= 4.5


def test_the_ancestor_background_walk_works(suggestions):
    report = []
    s = soup_of('<div style="background:black"><h2 style="color:#111">x</h2></div>')
    wc.fix_inline_styles(s, report)

    colour = wc.cssutils.parseStyle(s.find("h2")["style"]).getPropertyValue("color")
    assert wc.check_contrast(colour, "black") >= 4.5
    assert report, "the `background:` shorthand should have been understood"


def test_a_passing_pair_is_left_alone(suggestions):
    report = []
    wc.fix_inline_styles(soup_of('<p style="color:#000; background:#fff">x</p>'), report)
    assert report == []


def test_an_element_with_no_colours_is_skipped(suggestions):
    report = []
    wc.fix_inline_styles(soup_of('<p style="padding:4px">x</p>'), report)
    assert report == []


# --- abstaining rather than assuming a white canvas -------------------------

@pytest.mark.parametrize("head", [
    "<style>body{background:#000}</style>",
    '<link rel="stylesheet" href="t.css">',
])
def test_unreadable_css_means_abstain_not_guess(suggestions, head):
    """Guessing white is what pushed text on a dark page darker."""
    report = []
    s = soup_of(f'<html><head>{head}</head>'
                '<body><p style="color:#333">dim</p></body></html>')
    wc.fix_inline_styles(s, report)

    colour = wc.cssutils.parseStyle(s.find("p")["style"]).getPropertyValue("color")
    assert colour.lower() in ("#333", "#333333"), "the element must not be recoloured"
    assert abstentions(report), "and the skip must be reported, not silent"


def test_with_no_stylesheet_the_canvas_is_still_assumed(suggestions):
    report = []
    wc.fix_inline_styles(
        soup_of('<html><body><p style="color:#eee">pale</p></body></html>'), report)
    assert fixes(report)


@pytest.mark.parametrize("html,expected", [
    ("<style>p{color:red}</style>", True),
    ('<link rel="stylesheet" href="a.css">', True),
    ('<link rel="icon" href="f.ico">', False),
    ("<p>hi</p>", False),
])
def test_document_has_stylesheets(html, expected):
    assert wc.document_has_stylesheets(soup_of(html)) is expected


# --- an image or gradient backdrop is not a colour --------------------------

@pytest.mark.parametrize("style", [
    "color:#888; background:url(hero.png) #fff",
    "color:#888; background:linear-gradient(#000,#fff)",
    # A colour behind an image does not help: the image covers it.
    "color:#888; background:url(hero.png) no-repeat #000",
])
def test_a_non_uniform_backdrop_abstains(suggestions, style):
    report = []
    s = soup_of(f'<p style="{style}">x</p>')
    wc.fix_inline_styles(s, report)

    assert abstentions(report)
    assert "#888" in s.find("p")["style"], "the colour must be left as it was"


def test_the_walk_stops_at_an_image_ancestor(suggestions):
    """Rather than falling through to a parent's flat colour, or to white."""
    report = []
    wc.fix_inline_styles(
        soup_of('<div style="background:url(h.png)"><p style="color:#eee">x</p></div>'),
        report)
    assert abstentions(report)


def test_a_plain_background_colour_is_unaffected(suggestions):
    report = []
    wc.fix_inline_styles(soup_of('<p style="color:#333; background-color:#000">x</p>'),
                         report)
    assert fixes(report)


def test_a_stylesheet_rule_over_an_image_abstains(suggestions):
    report = []
    s = soup_of("<style>.hero{background:url('img/x.png') no-repeat;color:#888}</style>")
    wc.fix_style_blocks(s, "https://example.com/page.html", report)

    assert abstentions(report)
    assert "https://example.com/img/x.png" in s.find("style").string, \
        "the url should still be absolutised"


# --- legacy presentational attributes ---------------------------------------

def test_body_text_is_fixed_against_bgcolor(suggestions):
    report = []
    s = soup_of('<body bgcolor="#000000" text="#0000ff"><p>hi</p></body>')
    wc.fix_presentational_attributes(s, report)

    body = s.find("body")
    assert wc.check_contrast(body["text"], "#000000") >= 4.5
    assert body["bgcolor"] == "#000000", "the background must not be touched"
    assert body.get("style") is None, \
        "converting to inline style would promote it above the stylesheet"


def test_font_color_is_fixed_against_an_ancestor_bgcolor(suggestions):
    report = []
    s = soup_of('<body bgcolor="black"><font color="#222">dim</font></body>')
    wc.fix_presentational_attributes(s, report)
    assert wc.check_contrast(s.find("font")["color"], "black") >= 4.5


def test_a_passing_attribute_pair_is_left_alone(suggestions):
    report = []
    wc.fix_presentational_attributes(soup_of('<body bgcolor="white" text="black">'),
                                     report)
    assert report == []


# --- <style> blocks ---------------------------------------------------------

def test_a_style_block_is_fixed_and_its_urls_absolutised(suggestions):
    report = []
    css = ("#ab{background-color:aqua;color:cadetblue}\n"
           ".hero{background:url('img/x.png') no-repeat}")
    s = soup_of(f"<style>{css}</style>")
    wc.fix_style_blocks(s, "https://example.com/assets/page.html", report)

    out = s.find("style").string
    assert "cadetblue" not in out
    assert any(e["target"] == "#ab" for e in fixes(report))
    assert any(2.3 < (e["ratio_before"] or 0) < 2.5 for e in fixes(report))
    assert all((e["ratio_after"] or 0) >= 4.5 for e in fixes(report))
    assert "https://example.com/assets/img/x.png" in out
    assert "&gt;" not in out, "selectors must not be HTML-escaped"


# --- linked stylesheets -----------------------------------------------------

def test_linked_stylesheets(suggestions, sheets):
    sheets.files = {
        "bad.css": "#ab{background-color:aqua;color:cadetblue}"
                   ".h{background:url('img/hero.png')}",
        "good.css": "p{color:#000;background-color:#fff}",
    }
    report = []
    s = soup_of('<link rel="stylesheet" href="css/bad.css">'
                '<link rel="stylesheet" href="css/good.css">'
                '<link rel="stylesheet" href="css/missing.css">'
                '<link rel="icon" href="favicon.ico">')
    wc.fix_linked_stylesheets(s, "https://example.com/page.html", report)

    assert "https://example.com/css/bad.css" in sheets.fetched, \
        "each sheet is fetched from its own URL"
    assert not any("favicon" in u for u in sheets.fetched)

    inlined = s.find("style").string
    assert "cadetblue" not in inlined
    assert "https://example.com/css/img/hero.png" in inlined, \
        "url() resolves against the SHEET, not the page"

    remaining = [link.get("href", "") for link in s.find_all("link")]
    assert any(h.endswith("good.css") for h in remaining), \
        "an unmodified sheet stays a <link> rather than being inlined"
    assert any(h.endswith("missing.css") for h in remaining), \
        "an unreachable sheet leaves its <link> intact"
    assert "favicon.ico" in remaining


# --- end to end -------------------------------------------------------------

def test_change_color_on_the_demo_page(suggestions, sheets):
    sheets.files = {".css": "#ab{background-color:aqua;color:cadetblue}"}
    demo = """<html><head>
    <link rel="stylesheet" href="demostyles.css">
    </head><body>
    <div><h2 style="background-color: #000; color: #00f;">line-1</h2></div>
    <div><h2 style="background-color: black;">line-2</h2></div>
    <div><h2 id="ab">line-3</h2></div>
    <div><h2 id="abc">line-4</h2></div>
    </body></html>"""

    issues = wc.ChangeColor("http://localhost:8000/x.html", soup_of(demo))
    sources = {e["source"] for e in issues}

    assert len(issues) >= 3, "the report used to always come back empty"
    assert "inline" in sources
    assert "stylesheet" in sources
    assert all(e["ratio_after"] >= 4.5 for e in fixes(issues))
    assert all(e["ratio_after"] > e["ratio_before"] for e in fixes(issues))
    assert any(e["target"] == "#ab" for e in fixes(issues)), \
        "a stylesheet-only failure is the case the old code could never see"


def test_the_four_source_fixture(suggestions, sheets):
    """tests/fixtures/demo_all_sources.html exercises all four colour sources."""
    with open(os.path.join(FIXTURES, "demo_theme.css"), encoding="utf-8") as fh:
        sheets.files = {"demo_theme.css": fh.read()}
    with open(os.path.join(FIXTURES, "demo_all_sources.html"), encoding="utf-8") as fh:
        fixture = soup_of(fh.read())

    url = "http://example.com/tests/fixtures/demo_all_sources.html"
    issues = wc.ChangeColor(url, fixture)
    by_source = {e["source"] for e in issues}

    for source in ("inline", "attribute", "style-block", "stylesheet"):
        assert source in by_source, f"{source} was not exercised"

    assert all(e["ratio_after"] >= 4.5 for e in fixes(issues))
    assert all(e["ratio_after"] > e["ratio_before"] for e in fixes(issues))
    assert "padding" in fixture.find("h2")["style"]
    assert fixture.find("body")["bgcolor"] == "#000000"
    assert fixture.find("body").get("style") is None
    assert fixture.find("link", rel="stylesheet") is None, "the sheet should be inlined"
    assert "http://example.com/tests/fixtures/img/hero.png" in str(fixture), \
        "url() must be absolutised against the sheet"
