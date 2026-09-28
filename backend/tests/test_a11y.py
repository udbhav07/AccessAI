"""Tests for the alt-text and label rules, and which fields the scraper chooses to label."""

import pytest
from bs4 import BeautifulSoup

from accessai.core import a11y
from accessai.core.scraper import Scraper, stamp_ids


def first(html, tag):
    return BeautifulSoup(html, "html.parser").find(tag)


def scraper_for(html):
    soup = BeautifulSoup(html, "html.parser")
    stamp_ids(soup)
    scraper = Scraper()
    scraper.soup, scraper.url = soup, "http://example.com/"
    return soup, scraper


@pytest.mark.parametrize("html,needs", [
    ('<img src="a.png">', True),
    ('<img src="a.png" alt="A cat">', False),
    ('<img src="a.png" alt="">', False),
    ('<img src="a.png" alt="  ">', False),
    ('<img src="a.png" role="presentation">', False),
    ('<img src="a.png" aria-hidden="true">', False),
])
def test_needs_alt(html, needs):
    assert a11y.needs_alt(first(html, "img")) is needs


def test_decorative_images_are_left_alone():
    soup, scraper = scraper_for('<img src="a.png" alt=""><img src="b.png">')
    scraper.get_imgs()
    assert [img.get("alt") for img in soup.find_all("img")] == ["", "a stubbed description"]


@pytest.mark.parametrize("html,strategy", [
    ('<input id="q" aria-label="Search">', None),
    ('<input id="q" aria-labelledby="h">', None),
    ('<input id="q">', "for"),
    ('<input name="q">', "aria"),
    ('<select id="c"></select>', "for"),
    ('<textarea name="m"></textarea>', "aria"),
])
def test_labelling_strategy(html, strategy):
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find(a11y.FORM_FIELDS)
    assert a11y.labelling_strategy(soup, field) == strategy


def test_an_aria_named_input_gets_no_extra_label():
    soup, scraper = scraper_for('<input id="q" aria-label="Search">')
    assert scraper.get_label() == []
    assert soup.find("label") is None


def test_selects_and_textareas_are_labelled():
    soup, scraper = scraper_for('<select id="c"></select><textarea name="m"></textarea>')
    scraper.get_label()
    assert soup.find("label", attrs={"for": "c"}).get_text() == "Stub Label"
    assert soup.find("textarea")["aria-label"] == "Stub Label"


def test_form_fields_skip_buttons_and_hidden_inputs():
    soup = BeautifulSoup('<input type="hidden"><input type="submit"><input>'
                         '<select></select><textarea></textarea>', "html.parser")
    assert [f.name for f in a11y.form_fields(soup)] == ["input", "select", "textarea"]
