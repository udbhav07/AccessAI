"""Fix low-contrast text using the colours the browser actually renders.

`plan_contrast` finds text below its WCAG threshold and picks a passing colour for each
(Gemini's, if the maths confirms it); `apply_contrast` writes them onto the elements.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field

import cssutils

from . import ID_ATTR, browser, colour
from .gemini import suggest_text_colors

cssutils.log.setLevel(logging.CRITICAL)   # cssutils is very noisy on real-world CSS

MAX_MODEL_PAIRS = 60            # distinct colour pairs sent to the model per page
# Large text only needs 3:1 to pass, but a fix that stops there still looks dim,
# so every fix aims for the normal-text level.
FIX_TARGET = colour.AA_NORMAL


@dataclass
class ContrastPlan:
    fixes: dict = field(default_factory=dict)    # element id -> new text colour
    pins: dict = field(default_factory=dict)     # element id -> colour to keep
    issues: list = field(default_factory=list)   # report entries
    warning: str | None = None


def plan_contrast(url, html, failures=None):
    """Render `html` as if served from `url` and plan every contrast fix."""
    try:
        [(snapshots, _)] = browser.render(url, html, screenshots=False)
    except browser.BrowserError as exc:
        return ContrastPlan(warning=f"Colour contrast was not checked: {exc}")
    return plan_from_snapshots(snapshots, failures)


def plan_from_snapshots(snapshots, failures=None):
    text = [s for s in snapshots
            if s.id is not None and s.visible and s.has_text and not s.is_new]
    failing = [s for s in text if s.contrast is not None and s.contrast < s.threshold]
    unmeasurable = [s for s in text if s.bg is None]

    choices = _choose_colours([(s.fg, s.bg, _target(s)) for s in failing], failures)
    fixes = {s.id: choices[(s.fg, s.bg, _target(s))] for s in failing}
    pins = _pins(snapshots, fixes)

    issues = _report(failing, fixes)
    if unmeasurable:
        issues.append({
            "type": "contrast-skipped", "source": "rendered", "target": None,
            "old": None, "new": None, "ids": [],
            "reason": f"{len(unmeasurable)} text element(s) sit on an image or gradient, "
                      "so their contrast can't be measured",
        })
    return ContrastPlan(fixes=fixes, pins=pins, issues=issues)


def _target(snap):
    return max(snap.threshold, FIX_TARGET)


def _choose_colours(pairs, failures):
    """A passing colour for each (fg, bg, threshold): the model's if it passes, else ours."""
    pairs = list(dict.fromkeys(pairs))
    asked = pairs[:MAX_MODEL_PAIRS]
    suggestions = suggest_text_colors(
        [(_hex(fg), _hex(bg), threshold) for fg, bg, threshold in asked], failures)
    suggested = dict(zip(asked, suggestions))

    chosen = {}
    for fg, bg, threshold in pairs:
        suggestion = suggested.get((fg, bg, threshold))
        ratio = colour.check_contrast(suggestion, bg) if suggestion else None
        if ratio is not None and ratio >= threshold:
            chosen[(fg, bg, threshold)] = colour.to_hex(colour.parse(suggestion))
        else:
            chosen[(fg, bg, threshold)] = colour.nearest_passing(
                colour.parse(fg), colour.parse(bg), threshold)
    return chosen


def _pins(snapshots, fixes):
    """Text inside a fixed element that would inherit the new colour, mapped to its current one.

    A descendant showing the same colour as the fixed element inherits it; if that text
    already passes (e.g. it sits on its own lighter box), it must keep its colour.
    """
    children = defaultdict(list)
    for snap in snapshots:
        if snap.id is not None:
            children[snap.parent_id].append(snap)

    pins = {}
    by_id = {s.id: s for s in snapshots if s.id is not None}
    for fixed_id in fixes:
        old_fg = by_id[fixed_id].fg
        stack = list(children[fixed_id])
        while stack:
            snap = stack.pop()
            stack.extend(children[snap.id])
            if snap.has_text and snap.id not in fixes and snap.fg == old_fg:
                pins[snap.id] = colour.to_hex(colour.parse(snap.fg))
    return pins


def _report(failing, fixes):
    """One report entry per distinct (old colour, background, new colour)."""
    groups = defaultdict(list)
    for snap in failing:
        groups[(snap.fg, snap.bg, fixes[snap.id])].append(snap)

    issues = []
    for (fg, bg, new), snaps in groups.items():
        tags = sorted({s.tag.lower() for s in snaps})
        target = tags[0] if len(snaps) == 1 else f"{len(snaps)} elements ({', '.join(tags[:3])})"
        issues.append({
            "type": "contrast", "source": "rendered", "target": target,
            "old": _hex(fg), "new": new,
            "ratio_before": round(snaps[0].contrast, 2),
            "ratio_after": round(colour.check_contrast(new, bg), 2),
            "ids": [s.id for s in snaps],
        })
    return issues


def apply_contrast(soup, plan):
    """Write the planned colours into `soup`; returns the report entries."""
    elements = {el[ID_ATTR]: el for el in soup.find_all(attrs={ID_ATTR: True})}
    for element_id, value in {**plan.pins, **plan.fixes}.items():
        if element_id in elements:
            _set_colour(elements[element_id], value)
    return plan.issues


def _set_colour(element, value):
    # !important so a stylesheet's own !important rule can't win over the fix.
    style = cssutils.parseStyle(element.get("style", ""))
    style.setProperty("color", value, "important")
    element["style"] = style.getCssText(separator=" ")


def _hex(css_colour):
    rgb = colour.parse(css_colour)
    return colour.to_hex(rgb) if rgb else css_colour
