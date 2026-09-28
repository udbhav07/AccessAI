"""Tests for colour parsing, the contrast formula, large-text thresholds and nearest_passing."""

import pytest

from accessai.core import colour


@pytest.mark.parametrize("value,expected", [
    ("#00f", (0, 0, 255)),
    ("rgb(255, 128, 0)", (255, 128, 0)),
    ("cadetblue", (95, 158, 160)),
    ("not a colour", None),
    (None, None),
])
def test_parse(value, expected):
    assert colour.parse(value) == expected


def test_black_on_white_is_21_to_1():
    assert colour.check_contrast("#000", "#fff") == pytest.approx(21.0)


def test_ratio_is_symmetric():
    assert colour.ratio((0, 0, 255), (0, 0, 0)) == colour.ratio((0, 0, 0), (0, 0, 255))


def test_an_unreadable_colour_has_no_ratio():
    assert colour.check_contrast("var(--text)", "#fff") is None


@pytest.mark.parametrize("size,weight,expected", [
    (16, 400, colour.AA_NORMAL),
    (24, 400, colour.AA_LARGE),
    (19, 700, colour.AA_LARGE),
    (19, 400, colour.AA_NORMAL),
])
def test_large_text_has_a_lower_threshold(size, weight, expected):
    assert colour.threshold_for(size, weight) == expected


def test_nearest_passing_always_passes():
    for bg in [(0, 0, 0), (255, 255, 255), (119, 119, 119), (0, 0, 255), (255, 255, 0)]:
        for fg in [(0, 0, 0), (128, 128, 128), (255, 255, 255), (0, 0, 238)]:
            fixed = colour.parse(colour.nearest_passing(fg, bg, colour.AA_NORMAL))
            assert colour.ratio(fixed, bg) >= colour.AA_NORMAL


def test_nearest_passing_keeps_close_to_the_original():
    fixed = colour.nearest_passing((0, 0, 255), (0, 0, 0), colour.AA_NORMAL)
    assert fixed != "#ffffff", "should lighten the blue, not jump straight to white"
