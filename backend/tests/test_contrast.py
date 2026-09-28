"""Tests for planning and applying contrast fixes, from fake snapshots and in a real
browser (CSS classes, inherited colours, var() and pinning).
"""

import pytest
from bs4 import BeautifulSoup

from accessai.core import ID_ATTR, browser, colour, contrast
from accessai.core.contrast import ContrastPlan, apply_contrast, plan_contrast, plan_from_snapshots
from accessai.core.scraper import stamp_ids

GREY, WHITE, BLACK, LIGHT = "rgb(130, 130, 130)", "rgb(255, 255, 255)", "rgb(0, 0, 0)", "rgb(221, 221, 255)"


def snap(id, fg, bg, parent=None, has_text=True, tag="P", size=16, weight=400):
    return browser.ElementSnapshot(id, parent, False, tag, 0, 0, 10, 10, fg, bg,
                                   size, weight, True, has_text)


@pytest.fixture
def suggestions(monkeypatch):
    """Record model requests; answer with `reply` for every pair (None = no suggestion)."""
    state = type("State", (), {"reply": None, "requests": []})()

    def fake(pairs, failures=None):
        state.requests.append(pairs)
        return [state.reply] * len(pairs)

    monkeypatch.setattr(contrast, "suggest_text_colors", fake)
    return state


# --- planning, from snapshots ---------------------------------------------------

def test_failing_text_is_fixed_and_passing_text_is_left(suggestions):
    plan = plan_from_snapshots([snap("1", GREY, WHITE), snap("2", BLACK, WHITE)])
    assert list(plan.fixes) == ["1"]
    assert colour.check_contrast(plan.fixes["1"], WHITE) >= colour.AA_NORMAL


def test_large_text_uses_the_lower_threshold(suggestions):
    grey_31 = "rgb(148, 148, 148)"      # ~3:1 on white
    plan = plan_from_snapshots([snap("1", grey_31, WHITE, size=24)])
    assert colour.check_contrast(grey_31, WHITE) >= colour.AA_LARGE
    assert plan.fixes == {}


def test_a_passing_model_suggestion_is_used(suggestions):
    suggestions.reply = "#333333"
    plan = plan_from_snapshots([snap("1", GREY, WHITE)])
    assert plan.fixes["1"] == "#333333"


def test_a_failing_model_suggestion_is_replaced(suggestions):
    suggestions.reply = "#cccccc"
    plan = plan_from_snapshots([snap("1", GREY, WHITE)])
    assert plan.fixes["1"] != "#cccccc"
    assert colour.check_contrast(plan.fixes["1"], WHITE) >= colour.AA_NORMAL


def test_every_pair_on_the_page_is_one_request(suggestions):
    snaps = [snap(str(i), f"rgb({120 + i}, {120 + i}, {120 + i})", WHITE) for i in range(20)]
    snaps += [snap("dup", "rgb(120, 120, 120)", WHITE)]
    plan_from_snapshots(snaps)
    assert len(suggestions.requests) == 1
    assert len(suggestions.requests[0]) == 20, "repeated pairs are asked about once"


def test_the_model_is_asked_about_at_most_a_capped_number_of_pairs(suggestions, monkeypatch):
    monkeypatch.setattr(contrast, "MAX_MODEL_PAIRS", 3)
    snaps = [snap(str(i), f"rgb({120 + i}, {120 + i}, {120 + i})", WHITE) for i in range(5)]
    plan = plan_from_snapshots(snaps)
    assert len(suggestions.requests[0]) == 3
    assert len(plan.fixes) == 5, "the rest still get the deterministic fix"


def test_text_on_an_image_is_reported_not_fixed(suggestions):
    plan = plan_from_snapshots([snap("1", GREY, None)])
    assert plan.fixes == {}
    assert plan.issues[0]["type"] == "contrast-skipped"


def test_non_text_and_hidden_elements_are_ignored(suggestions):
    hidden = snap("2", GREY, WHITE)
    hidden.visible = False
    plan = plan_from_snapshots([snap("1", GREY, WHITE, has_text=False), hidden])
    assert plan.fixes == {} and plan.issues == []


def test_passing_text_that_would_inherit_the_fix_is_pinned(suggestions):
    parent = snap("1", GREY, WHITE, tag="DIV")
    inherits_on_black_box = snap("2", GREY, BLACK, parent="1")   # grey passes on black
    own_colour = snap("3", BLACK, WHITE, parent="1")
    plan = plan_from_snapshots([parent, inherits_on_black_box, own_colour])

    assert set(plan.fixes) == {"1"}
    assert plan.pins == {"2": "#828282"}


def test_changes_are_reported_once_per_colour_pair(suggestions):
    plan = plan_from_snapshots([snap("1", GREY, WHITE, tag="SPAN"),
                                snap("2", GREY, WHITE, tag="A"),
                                snap("3", GREY, WHITE, tag="SPAN")])
    [issue] = plan.issues
    assert issue["target"] == "3 elements (a, span)"
    assert issue["old"] == "#828282" and issue["ids"] == ["1", "2", "3"]
    assert issue["ratio_before"] < 4.5 <= issue["ratio_after"]


def test_a_browser_failure_becomes_a_warning(monkeypatch):
    def fail(*args, **kwargs):
        raise browser.BrowserError("no chromium")

    monkeypatch.setattr(browser, "render", fail)
    plan = plan_contrast("http://example.com/", "<p>x</p>")
    assert plan.fixes == {} and "no chromium" in plan.warning


# --- applying -------------------------------------------------------------------

def test_apply_sets_an_important_colour_and_keeps_other_styles():
    soup = BeautifulSoup('<p style="padding: 2px; color: #999">x</p>', "html.parser")
    stamp_ids(soup)
    apply_contrast(soup, ContrastPlan(fixes={"0": "#595959"}, pins={}))
    style = soup.find("p")["style"]
    assert "padding: 2px" in style
    assert "color: #595959 !important" in style and "#999" not in style


# --- end to end, in a real browser ------------------------------------------------

def fix_in_browser(body):
    soup = BeautifulSoup(f"<html><head></head><body>{body}</body></html>", "html.parser")
    stamp_ids(soup)
    plan = plan_contrast("http://example.com/", str(soup))
    apply_contrast(soup, plan)
    [(after, _)] = browser.render("http://example.com/", str(soup), screenshots=False)
    return plan, {s.id: s for s in after if s.id is not None}, soup


def failing_text(snapshots):
    return [s for s in snapshots.values()
            if s.has_text and s.contrast is not None and s.contrast < s.threshold]


def test_grey_text_from_a_css_rule_is_fixed(suggestions):
    """Hacker News: grey text set by a class, on a background from a legacy attribute."""
    plan, after, _ = fix_in_browser(
        '<style>.subtext{color:#828282}</style>'
        '<table bgcolor="#f6f6ef"><tr><td><span class="subtext">12 points</span></td></tr></table>')
    assert plan.fixes and not failing_text(after)


def test_an_inherited_dark_colour_on_black_is_fixed(suggestions):
    """The demo page's line-2: black background, text colour inherited."""
    plan, after, _ = fix_in_browser(
        '<style>body{color:#212529}</style><h2 style="background-color: black;">line-2</h2>')
    assert plan.fixes and not failing_text(after)


def test_good_inherited_text_on_a_light_box_is_not_touched(suggestions):
    """Wikipedia: light sidebar titles inherit dark text that already passes."""
    plan, _, soup = fix_in_browser(
        '<style>body{color:#202122}</style><div style="background:#ddf">Title</div>')
    assert plan.fixes == {}
    assert "color" not in soup.find("div")["style"]


def test_a_css_variable_colour_is_measured_like_any_other(suggestions):
    plan, after, _ = fix_in_browser(
        '<style>:root{--muted:#aaa}</style><p style="color: var(--muted)">muted</p>')
    assert plan.fixes and not failing_text(after)


def test_passing_text_inside_a_fixed_element_keeps_its_colour(suggestions):
    plan, after, soup = fix_in_browser(
        '<div style="color:#555; background:#000">dark box '
        '<span style="background:#fff">light box</span></div>')
    span_id = soup.find("span")[ID_ATTR]
    assert span_id in plan.pins
    assert after[span_id].fg == "rgb(85, 85, 85)", "the span keeps its original #555"
    assert not failing_text(after)


def test_large_text_is_fixed_to_the_normal_level_so_it_reads_clearly(suggestions):
    dim = "rgb(33, 37, 41)"             # line-2: #212529 on black, 32px heading
    plan = plan_from_snapshots([snap("1", dim, BLACK, size=32, tag="H2")])
    assert colour.check_contrast(plan.fixes["1"], BLACK) >= colour.AA_NORMAL
