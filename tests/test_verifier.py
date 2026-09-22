"""Tests for the remediation verifier.

Runs a real headless browser but never reaches a model, so it is
deterministic and free. The deliberate-break section matters most: a
verifier that cannot fail is not verifying anything.
"""

import os
import shutil
import time

import pytest
from bs4 import BeautifulSoup

from src import a11y, runstore, verifier, webScraper
from src.webScraper import Scraper, stamp_ids, strip_ids

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stamped(html):
    soup = BeautifulSoup(html, "html.parser")
    stamp_ids(soup)
    return soup


def named(report, name):
    return next(check for check in report.checks if check.name == name)


@pytest.fixture
def verify(fixture_server, allow_private):
    """Run the checks against two documents, using the fixture page's URL."""
    def _verify(before_soup, after_soup, modified_ids=()):
        return verifier.run_checks(
            f"{fixture_server}/experiment.html",
            str(before_soup), str(after_soup), modified_ids,
        )
    return _verify


@pytest.fixture(scope="session")
def demo_scrape(fixture_server):
    """Scrape the demo fixture once; several tests read the same result."""
    os.environ[webScraper.nethttp.ALLOW_PRIVATE_ENV] = "1"
    result = Scraper().scrape_url(f"{fixture_server}/experiment.html")
    report = verifier.run_checks(
        result.url, result.html_before, str(result.soup), result.modified_ids)
    return result, report


# --- the run store ----------------------------------------------------------

def test_a_run_round_trips():
    run_id = runstore.new_run_id()
    runstore.save_run(run_id, "<p>before</p>", "<p>after</p>",
                      {"url": "http://x/", "modified_ids": ["3", "7"]})

    before, _after, meta = runstore.load_run(run_id)
    assert before == "<p>before</p>"
    assert meta["modified_ids"] == ["3", "7"]


@pytest.mark.parametrize("run_id", ["deadbeef", "", "../../etc"])
def test_an_unusable_run_id_returns_none(run_id):
    assert runstore.load_run(run_id) is None


def test_path_traversal_is_refused_on_save():
    with pytest.raises(ValueError):
        runstore.save_run("../evil", "a", "b", {})


def test_the_run_store_stays_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path))
    ids = []
    for n in range(4):
        run_id = runstore.new_run_id()
        runstore.save_run(run_id, "x" * 40_000, "y" * 40_000, {"url": f"http://x/{n}"})
        stamp = time.time() - n
        os.utime(tmp_path / run_id, (stamp, stamp))
        ids.append(run_id)

    assert sum(1 for i in ids if runstore.load_run(i)) == 4

    runstore.sweep(max_total=200_000)            # room for about two runs
    kept = [i for i in ids if runstore.load_run(i)]
    assert len(kept) < 4
    assert set(kept) <= {ids[0], ids[1]}, "the newest should survive"

    runstore.sweep(max_age=0)
    assert not any(runstore.load_run(i) for i in ids)


# --- identity stamping ------------------------------------------------------

def test_every_element_is_stamped_uniquely():
    soup = stamped("<html><body><div><p>hi</p></div></body></html>")
    ids = [el.get("data-aai-id") for el in soup.find_all(True)]

    assert all(i is not None for i in ids)
    assert len(set(ids)) == len(ids)

    strip_ids(soup)
    assert all(el.get("data-aai-id") is None for el in soup.find_all(True))


# --- snapshot helpers -------------------------------------------------------

S = verifier.ElementSnapshot
SNAPS = [
    S("1", None, False, "DIV", 0, 0, 10, 10, "rgb(0,0,0)", "rgb(255,255,255)",
      16, 400, True, False),
    S("2", "1", False, "P", 0, 0, 10, 10, "rgb(0,0,0)", "rgb(255,255,255)",
      16, 400, True, True),
    S(None, "2", True, "LABEL", 0, 0, 5, 5, "rgb(0,0,0)", "rgb(255,255,255)",
      16, 400, True, True),
]


def test_insertion_ancestors_walk_the_whole_chain():
    assert verifier._insertion_ancestors(SNAPS) == {"1", "2"}


def test_descendants_of_a_modified_element_are_excluded():
    """Colour inherits, so a recoloured parent changes its children too."""
    assert verifier._with_descendants({"1"}, SNAPS) == {"1", "2"}


@pytest.mark.parametrize("size,weight,expected", [
    (30, 400, 3.0),        # large text
    (19, 700, 3.0),        # bold and nearly large
    (16, 400, 4.5),        # normal
])
def test_the_wcag_threshold_depends_on_the_text_size(size, weight, expected):
    snap = S("9", None, False, "P", 0, 0, 9, 9, "", "", size, weight, True, True)
    assert verifier._threshold_for(snap) == expected


# --- labelling: one definition for the scraper and the verifier -------------

@pytest.mark.parametrize("html,expected", [
    ("<label>Email <input></label>", None),          # already named by its wrapper
    ('<input type="hidden" name="csrf">', None),
    ('<input type="submit" value="Go">', None),
    ('<input type="reset">', None),
    ('<input id="e">', "for"),
    ('<input placeholder="Email">', "aria"),
    ('<input aria-label="Email">', None),
    ('<input type="box">', "aria"),                  # unknown type renders as text
])
def test_labelling_strategy(html, expected):
    soup = BeautifulSoup(html, "html.parser")
    assert a11y.labelling_strategy(soup, soup.find("input")) == expected


def scraper_for(html):
    soup = stamped(html)
    scraper = Scraper()
    scraper.soup = soup
    scraper.url = "http://example.com/"
    return soup, scraper


def test_an_input_with_no_id_gets_an_aria_label():
    soup, scraper = scraper_for('<form><input name="email" placeholder="Email"></form>')
    issues = scraper.get_label()

    assert soup.find("input").get("aria-label") == "Stub Label"
    assert soup.find("label") is None and soup.find("br") is None, \
        "an attribute inserts no node, so nothing can move"
    assert any(i["source"] == "aria-label" for i in issues)


def test_a_wrapped_input_is_not_given_a_second_label():
    soup, scraper = scraper_for('<label>Email <input name="e"></label>')
    issues = scraper.get_label()

    assert len(soup.find_all("label")) == 1
    assert issues == []


def test_hidden_and_submit_inputs_are_skipped():
    soup, scraper = scraper_for(
        '<form><input type="hidden" name="csrf" value="x">'
        '<input type="submit" value="Go"></form>')

    assert scraper.get_label() == []
    assert soup.find("label") is None


@pytest.mark.parametrize("html", [
    '<form><input aria-label="Email"><input type="hidden"></form>',
    "<label>Email <input></label>",
])
def test_coverage_counts_the_same_things_the_scraper_fixes(html):
    assert verifier._label_coverage(stamped(html)) == (1, 1)


# --- url absolutisation -----------------------------------------------------

def absolutised(html, url="http://example.com/dir/page.html"):
    scraper = Scraper()
    scraper.soup = BeautifulSoup(html, "html.parser")
    scraper.url = url
    scraper.absolutise_urls()
    return scraper.soup


def test_a_relative_form_action_would_have_posted_to_this_app():
    soup = absolutised('<form action="/submit"><input name="pw"></form>')
    assert soup.find("form")["action"] == "http://example.com/submit"


def test_srcset_candidates_are_rewritten():
    soup = absolutised('<img srcset="a.png 1x, sub/b.png 2x" src="a.png">')
    assert soup.find("img")["srcset"] == (
        "http://example.com/dir/a.png 1x, http://example.com/dir/sub/b.png 2x")


def test_url_in_an_inline_style_is_rewritten():
    soup = absolutised('<div style="background:url(bg.png) #fff"></div>')
    assert "http://example.com/dir/bg.png" in soup.find("div")["style"]


def test_a_data_uri_is_left_alone():
    soup = absolutised('<div style="background:url(data:image/gif;base64,R0lGOD)"></div>')
    assert "data:image/gif;base64,R0lGOD" in soup.find("div")["style"]


def test_media_and_embed_urls_are_covered():
    soup = absolutised('<video poster="p.jpg" src="v.mp4"></video>'
                       '<object data="o.swf"></object><iframe src="f.html"></iframe>')
    for tag, attr in (("video", "poster"), ("video", "src"),
                      ("object", "data"), ("iframe", "src")):
        assert "http://example.com/dir/" in soup.find(tag)[attr]


def test_base_href_is_honoured_then_removed():
    soup = absolutised('<head><base href="/assets/"></head><body>'
                       '<img src="x.png"><a href="y.html">y</a></body>')

    assert soup.find("img")["src"] == "http://example.com/assets/x.png"
    assert soup.find("a")["href"] == "http://example.com/assets/y.html"
    assert soup.find("base") is None, \
        "a surviving <base> would re-resolve what is now absolute"


def test_absolute_fragment_and_mailto_hrefs_survive():
    soup = absolutised('<a href="https://other.example/x">a</a>'
                       '<a href="#top">b</a><a href="mailto:a@b.c">c</a>')
    assert [a["href"] for a in soup.find_all("a")] == [
        "https://other.example/x", "#top", "mailto:a@b.c"]


# --- non-HTML responses -----------------------------------------------------

def test_a_stylesheet_url_is_not_parsed_as_a_page(fixture_server, allow_private):
    with pytest.raises(webScraper.UnsupportedContent):
        Scraper().scrape_url(f"{fixture_server}/demo_theme.css")


def test_a_404_is_not_scraped_as_if_it_were_the_page(fixture_server, allow_private):
    with pytest.raises(Exception):
        Scraper().scrape_url(f"{fixture_server}/does-not-exist.html")


# --- a failed getAlt leaves the image alone ---------------------------------

def test_a_failed_get_alt_writes_nothing(fixture_server, allow_private, monkeypatch):
    """A placeholder string would be read aloud for every image, and counted."""
    monkeypatch.setattr(webScraper, "getAlt", lambda src: None)
    result = Scraper().scrape_url(f"{fixture_server}/experiment.html")

    assert [i.get("alt") for i in result.soup.find_all("img") if i.get("alt")] == []

    coverage = verifier.check_coverage(
        BeautifulSoup(result.html_before, "html.parser"),
        BeautifulSoup(str(result.soup), "html.parser"))
    assert "alt 0/4 -> 0/4" in coverage.summary
    assert any(i.get("source") == "skipped" for i in result.issues)


# --- the live capture -------------------------------------------------------

def test_the_demo_fixture_verifies_clean(demo_scrape):
    _result, report = demo_scrape
    assert report.error is None
    assert len(report.checks) == 7
    assert report.verdict == "PASS", report.as_text()


@pytest.mark.parametrize("name", [
    "Layout", "Visibility", "Contrast", "Colour", "Coverage", "Remaining", "Pixels",
])
def test_every_check_passes_on_the_demo_fixture(demo_scrape, name):
    _result, report = demo_scrape
    assert named(report, name).passed, named(report, name).summary


def test_coverage_improves_on_the_demo_fixture(demo_scrape):
    # inp1 and inp3 already carry non-empty labels; inp2's is whitespace-only.
    # Coverage measures presence, not correctness.
    _result, report = demo_scrape
    summary = named(report, "Coverage").summary
    assert "alt 0/4 -> 4/4" in summary
    assert "labels 2/4 -> 4/4" in summary


def test_what_the_fixer_could_not_reach_is_listed_not_failed(demo_scrape):
    """line-2 takes its colour from a stylesheet the inline pass cannot read."""
    _result, report = demo_scrape
    remaining = named(report, "Remaining")
    assert remaining.passed and remaining.details
    assert remaining.tier == "advisory"


# --- deliberate breaks ------------------------------------------------------

@pytest.fixture
def base_html(demo_scrape):
    result, _report = demo_scrape
    return result.html_before


def test_a_hidden_element_is_caught(verify, base_html):
    broken = stamped(base_html)
    broken.find("h1")["style"] = "display:none"
    report = verify(stamped(base_html), broken)

    assert not named(report, "Visibility").passed
    assert report.verdict == "BROKEN"


def test_a_removed_element_is_caught(verify, base_html):
    broken = stamped(base_html)
    broken.find("h1").decompose()
    assert not named(verify(stamped(base_html), broken), "Visibility").passed


def test_a_changed_width_is_caught(verify, base_html):
    broken = stamped(base_html)
    broken.find("h1")["style"] = "width:80px;display:block"
    layout = named(verify(stamped(base_html), broken), "Layout")

    assert not layout.passed
    assert layout.element_ids, "the ids must be carried as data"
    assert layout.element_ids == {d.split("#")[1].split(" ")[0]
                                  for d in layout.details if "#" in d}


def test_both_dimensions_are_reported(verify, base_html):
    broken = stamped(base_html)
    broken.find("h1")["style"] = "width:80px;height:200px;display:block"
    details = named(verify(stamped(base_html), broken), "Layout").details

    assert any("width" in d for d in details)
    assert any("height" in d for d in details)


def test_an_unattributed_colour_change_is_caught(verify, base_html):
    broken = stamped(base_html)
    broken.find("h1")["style"] = "color:#c0ffee"
    assert not named(verify(stamped(base_html), broken, []), "Colour").passed


def test_a_declared_colour_change_is_allowed(verify, base_html):
    broken = stamped(base_html)
    target = broken.find("h1")
    target["style"] = "color:#c0ffee"
    report = verify(stamped(base_html), broken, [target.get("data-aai-id")])
    assert named(report, "Colour").passed


def test_a_contrast_regression_is_caught(verify, base_html):
    """Ours whether we aimed at that element or not."""
    broken = stamped(base_html)
    broken.find("h1")["style"] = "color:#777;background:#888"
    contrast = named(verify(stamped(base_html), broken, []), "Contrast")

    assert not contrast.passed
    assert any("got worse" in d for d in contrast.details)


def test_an_untouched_pre_existing_failure_is_not_our_failure(verify):
    soup = stamped('<html><body><p style="color:#777;background:#888">dim</p></body></html>')
    report = verify(stamped(str(soup)), soup, [])

    assert named(report, "Contrast").passed
    assert named(report, "Remaining").details
    assert report.verdict == "PASS"


def test_a_claimed_element_that_still_fails_is_reported(verify):
    soup = stamped('<html><body><p style="color:#777;background:#888">dim</p></body></html>')
    target = soup.find("p").get("data-aai-id")
    assert not named(verify(stamped(str(soup)), soup, [target]), "Contrast").passed


def test_dropped_alt_coverage_is_caught(verify, base_html):
    before = stamped(base_html)
    for img in before.find_all("img"):
        img["alt"] = "described"
    after = stamped(str(before))
    del after.find("img")["alt"]

    assert not named(verify(before, after), "Coverage").passed


def test_the_scraped_pages_scripts_do_not_run(verify):
    hostile = stamped('<html><body><h1>kept</h1>'
                      '<script>document.body.innerHTML = "";</script></body></html>')
    report = verify(stamped(str(hostile)), hostile)

    assert named(report, "Visibility").passed
    assert report.verdict == "PASS", "a page must not be able to dictate its verdict"


# --- verdict tiering --------------------------------------------------------

def report_with(**kwargs):
    report = verifier.Report()
    report.checks = [
        verifier.CheckResult("Layout", kwargs.get("layout", True), "", [], "blocking"),
        verifier.CheckResult("Contrast", kwargs.get("contrast", True), "", [],
                             "objective"),
        verifier.CheckResult("Pixels", kwargs.get("pixels", True), "", [], "advisory"),
    ]
    return report


@pytest.mark.parametrize("kwargs,expected", [
    ({}, "PASS"),
    ({"pixels": False}, "REVIEW"),
    ({"contrast": False}, "INCOMPLETE"),
    ({"layout": False}, "BROKEN"),
    ({"layout": False, "contrast": False}, "BROKEN"),
    # No blended score: one healthy check cannot mask a fatal one.
    ({"layout": False, "contrast": True, "pixels": True}, "BROKEN"),
])
def test_the_verdict_is_a_rule_not_an_average(kwargs, expected):
    assert report_with(**kwargs).verdict == expected


# --- graceful degradation ---------------------------------------------------

def test_a_capture_failure_is_an_error_not_a_false_pass(verify, monkeypatch):
    def boom(*args, **kwargs):
        raise verifier.CaptureError("no browser")

    monkeypatch.setattr(verifier, "capture", boom)
    report = verify(stamped("<p>x</p>"), stamped("<p>x</p>"))

    assert report.verdict == "ERROR"
    assert any(c.name == "Coverage" for c in report.checks), \
        "coverage needs no browser, so it should still be reported"
    assert report.error
