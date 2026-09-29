"""Checks a fix by rendering the original and fixed page and comparing every element,
matched by `data-aai-id`. Seven checks in three tiers give the verdict: PASS, REVIEW,
INCOMPLETE, BROKEN or ERROR.
"""

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import a11y
from .browser import BrowserError, render

GEOMETRY_TOLERANCE = 1.0        # px
PIXEL_TOLERANCE = 1.0           # % mean absolute difference per element
MIN_PIXEL_AREA = 400            # px^2; below this a crop is too small to judge
MAX_PIXEL_ELEMENTS = 300


@dataclass
class CheckResult:
    name: str
    passed: bool
    summary: str
    details: list = field(default_factory=list)
    tier: str = "blocking"        # blocking | objective | advisory
    # Ids this check failed on (not warnings); the pixel check skips them.
    element_ids: set = field(default_factory=set)
    # Passed, but the job isn't finished (e.g. some images still have no alt text).
    partial: bool = False


@dataclass
class Report:
    checks: list = field(default_factory=list)
    error: str = None

    @property
    def verdict(self):
        if self.error:
            return "ERROR"
        for tier, label in (("blocking", "BROKEN"),
                            ("objective", "INCOMPLETE"),
                            ("advisory", "REVIEW")):
            if any(not c.passed for c in self.checks if c.tier == tier):
                return label
        return "PASS"

    def as_text(self):
        head = f"VERDICT: {self.verdict}"
        if self.error:
            return f"{head}\n{self.error}"
        body = "\n".join(
            f"{('PART' if c.partial else 'PASS') if c.passed else 'FAIL'}  {c.name:<12} {c.summary}"
            for c in self.checks
        )
        return f"{head}\n\n{body}"

    def as_dict(self):
        return {
            "verdict": self.verdict,
            "error": self.error,
            "checks": [
                {"name": c.name, "passed": c.passed, "partial": c.partial,
                 "summary": c.summary, "details": c.details, "tier": c.tier}
                for c in self.checks
            ],
        }


def capture(url, html_before, html_after):
    """Render both versions; returns (snap_before, snap_after, png_before, png_after)."""
    (snap_before, png_before), (snap_after, png_after) = render(url, html_before, html_after)
    return snap_before, snap_after, png_before, png_after


# --- helpers ----------------------------------------------------------------

def _by_id(snapshots):
    return {s.id: s for s in snapshots if s.id is not None}


def _parent_map(snapshots):
    return {s.id: s.parent_id for s in snapshots if s.id is not None}


def _insertion_ancestors(snap_after):
    """Ids of every ancestor of an inserted node (these may grow taller)."""
    parents = _parent_map(snap_after)
    ancestors = set()
    for snap in snap_after:
        if not snap.is_new:
            continue
        node = snap.parent_id
        while node is not None and node not in ancestors:
            ancestors.add(node)
            node = parents.get(node)
    return ancestors


def _ancestors_of(ids, snapshots):
    parents = _parent_map(snapshots)
    found = set()
    for start in ids:
        node = parents.get(start)
        while node is not None and node not in found:
            found.add(node)
            node = parents.get(node)
    return found


def _with_descendants(ids, snapshots):
    # Colour inherits, so a recoloured parent changes its descendants too.
    parents = _parent_map(snapshots)
    expanded = set(ids)
    for snap in snapshots:
        chain, node = [], snap.id
        while node is not None and node not in expanded:
            chain.append(node)
            node = parents.get(node)
        if node is not None:
            expanded.update(chain)
    return expanded


def _summarise(items, limit=3):
    shown = ", ".join(items[:limit])
    extra = len(items) - limit
    return f"{shown}, ... (+{extra} more)" if extra > 0 else shown


# --- checks -----------------------------------------------------------------

def check_layout(snap_before, snap_after, modified_ids=()):
    """Compare width, height and x, but not y (insertions shift everything down).

    Modified elements are skipped; their ancestors are exempt from height only.
    """
    before, after = _by_id(snap_before), _by_id(snap_after)
    modified = set(modified_ids or ())
    exempt_height = _insertion_ancestors(snap_after) | _ancestors_of(modified, snap_after)

    checked, failures, warnings, failed_ids = 0, [], [], set()
    for eid, b in before.items():
        a = after.get(eid)
        if a is None or not (b.visible and a.visible) or eid in modified:
            continue
        checked += 1

        if abs(a.w - b.w) > GEOMETRY_TOLERANCE:
            failures.append(f"{b.tag.lower()}#{eid} width {b.w:.0f}->{a.w:.0f}px")
            failed_ids.add(eid)
        if abs(a.h - b.h) > GEOMETRY_TOLERANCE and eid not in exempt_height:
            failures.append(f"{b.tag.lower()}#{eid} height {b.h:.0f}->{a.h:.0f}px")
            failed_ids.add(eid)
        if abs(a.x - b.x) > GEOMETRY_TOLERANCE and eid not in failed_ids:
            warnings.append(f"{b.tag.lower()}#{eid} x {b.x:.0f}->{a.x:.0f}px")

    passed = not failures
    if passed:
        summary = f"{checked}/{checked} elements unchanged in size"
        if warnings:
            summary += f" ({len(warnings)} shifted horizontally)"
    else:
        summary = (f"{len(failed_ids)} of {checked} elements changed size  "
                   f"-> {_summarise(failures)}")
    return CheckResult("Layout", passed, summary, failures + warnings, "blocking",
                       failed_ids)


def check_visibility(snap_before, snap_after):
    after = _by_id(snap_after)
    lost = []
    for snap in snap_before:
        if snap.id is None or not snap.visible:
            continue
        a = after.get(snap.id)
        if a is None:
            lost.append(f"{snap.tag.lower()}#{snap.id} removed")
        elif not a.visible:
            lost.append(f"{snap.tag.lower()}#{snap.id} hidden")
    passed = not lost
    summary = "no elements lost" if passed else f"{len(lost)} lost  -> {_summarise(lost)}"
    return CheckResult("Visibility", passed, summary, lost, "blocking")


def _label_coverage(soup):
    fields = a11y.form_fields(soup)
    return sum(1 for f in fields if a11y.is_labelled(soup, f)), len(fields)


def _alt_coverage(soup):
    imgs = soup.find_all("img")
    return sum(1 for i in imgs if not a11y.needs_alt(i)), len(imgs)


def check_coverage(soup_before, soup_after):
    alt_b, alt_total = _alt_coverage(soup_before)
    alt_a, alt_total_a = _alt_coverage(soup_after)
    lbl_b, lbl_total = _label_coverage(soup_before)
    lbl_a, lbl_total_a = _label_coverage(soup_after)

    details = []
    if alt_a < alt_b:
        details.append(f"alt coverage fell {alt_b}->{alt_a}")
    if lbl_a < lbl_b:
        details.append(f"label coverage fell {lbl_b}->{lbl_a}")

    summary = (f"alt {alt_b}/{alt_total} -> {alt_a}/{alt_total_a},  "
               f"labels {lbl_b}/{lbl_total} -> {lbl_a}/{lbl_total_a}")
    passed = not details
    unfinished = alt_a < alt_total_a or lbl_a < lbl_total_a
    return CheckResult("Coverage", passed, summary, details, "objective",
                       partial=passed and unfinished)


def check_contrast_goal(snap_before, snap_after, modified_ids=()):
    """Touched elements must meet their threshold, and nothing may get worse.

    Untouched elements still below threshold go to check_remaining_contrast.
    """
    targeted = _with_descendants(set(modified_ids or ()), snap_after)
    before = _by_id(snap_before)
    checked, failures, regressions = 0, [], []

    for snap in snap_after:
        if snap.id is None or not snap.visible or not snap.has_text:
            continue
        ratio_after = snap.contrast
        if ratio_after is None:
            continue
        threshold = snap.threshold

        b = before.get(snap.id)
        ratio_before = b.contrast if b else None

        if snap.id in targeted:
            checked += 1
            if ratio_after < threshold:
                was = f"{ratio_before:.1f}" if ratio_before is not None else "?"
                failures.append(
                    f"{snap.tag.lower()}#{snap.id} ({was}->{ratio_after:.1f}, "
                    f"needs {threshold})"
                )
                continue

        if ratio_before is not None and ratio_after < ratio_before - 0.05:
            regressions.append(
                f"{snap.tag.lower()}#{snap.id} got worse "
                f"({ratio_before:.1f}->{ratio_after:.1f})"
            )

    problems = failures + regressions
    passed = not problems
    if passed:
        summary = (f"all {checked} remediated text elements meet their threshold"
                   if checked else "no contrast changes to verify")
    else:
        parts = []
        if failures:
            parts.append(f"{len(failures)} of {checked} still below threshold")
        if regressions:
            parts.append(f"{len(regressions)} got worse")
        summary = f"{' and '.join(parts)}  -> {_summarise(problems)}"
    return CheckResult("Contrast", passed, summary, problems, "objective")


def check_remaining_contrast(snap_after, modified_ids=()):
    """Report (never fail) text still below threshold that this run didn't touch."""
    targeted = _with_descendants(set(modified_ids or ()), snap_after)
    remaining = []

    for snap in snap_after:
        if snap.id is None or snap.id in targeted:
            continue
        if not snap.visible or not snap.has_text:
            continue
        ratio = snap.contrast
        if ratio is None:
            continue
        threshold = snap.threshold
        if ratio < threshold:
            remaining.append(
                f"{snap.tag.lower()}#{snap.id} {ratio:.1f}:1 (needs {threshold})"
            )

    summary = ("nothing left below threshold" if not remaining
               else f"{len(remaining)} element(s) still below threshold, "
                    f"not reached by this run  -> {_summarise(remaining)}")
    return CheckResult("Remaining", True, summary, remaining, "advisory")


def check_colour(snap_before, snap_after, modified_ids):
    """Colour must not change on elements the fixer never touched."""
    excluded = _with_descendants(set(modified_ids or ()), snap_after)
    before = _by_id(snap_before)
    checked, changes, changed_ids = 0, [], set()

    for snap in snap_after:
        if snap.id is None or snap.id in excluded or snap.is_new:
            continue
        b = before.get(snap.id)
        if b is None or not (b.visible and snap.visible):
            continue
        checked += 1
        if b.fg != snap.fg:
            changes.append(f"{snap.tag.lower()}#{snap.id} colour {b.fg}->{snap.fg}")
            changed_ids.add(snap.id)
        if None not in (b.bg, snap.bg) and b.bg != snap.bg:
            changes.append(f"{snap.tag.lower()}#{snap.id} background {b.bg}->{snap.bg}")
            changed_ids.add(snap.id)

    passed = not changes
    summary = ("no unintended colour changes" if passed
               else f"{len(changed_ids)} of {checked} untouched elements changed  "
                    f"-> {_summarise(changes)}")
    return CheckResult("Colour", passed, summary, changes, "blocking", changed_ids)


# --- pixels -----------------------------------------------------------------

def _crop(image, snap):
    left = max(0, int(round(snap.x)))
    top = max(0, int(round(snap.y)))
    right = min(image.width, int(round(snap.x + snap.w)))
    bottom = min(image.height, int(round(snap.y + snap.h)))
    if right <= left or bottom <= top:
        return None
    return image.crop((left, top, right, bottom))


def check_pixels(snap_before, snap_after, shot_before, shot_after, modified_ids,
                 layout_failed_ids=()):
    """Compare per-element crops, each at its own coordinates, for untouched elements."""
    try:
        img_before = Image.open(io.BytesIO(shot_before)).convert("RGB")
        img_after = Image.open(io.BytesIO(shot_after)).convert("RGB")
    except Exception as exc:
        return CheckResult("Pixels", True, f"skipped ({exc})", [], "advisory")

    # Ancestors' crops contain the modified/inserted elements, so skip them too.
    modified = set(modified_ids or ())
    excluded = _with_descendants(modified, snap_after)
    excluded |= _ancestors_of(modified, snap_after)
    excluded |= _insertion_ancestors(snap_after)
    excluded.update(layout_failed_ids)
    before = _by_id(snap_before)

    candidates = [
        s for s in snap_after
        if s.id is not None and s.id not in excluded and not s.is_new
        and s.visible and s.w * s.h >= MIN_PIXEL_AREA and s.id in before
        and before[s.id].visible
    ]
    candidates.sort(key=lambda s: s.w * s.h, reverse=True)
    candidates = candidates[:MAX_PIXEL_ELEMENTS]

    total_area, weighted, offenders = 0.0, 0.0, []
    for snap in candidates:
        crop_a = _crop(img_after, snap)
        crop_b = _crop(img_before, before[snap.id])
        if crop_a is None or crop_b is None:
            continue
        if crop_a.size != crop_b.size:
            # Compare the overlap; resampling would add interpolation noise.
            width = min(crop_a.width, crop_b.width)
            height = min(crop_a.height, crop_b.height)
            crop_a = crop_a.crop((0, 0, width, height))
            crop_b = crop_b.crop((0, 0, width, height))
            offenders.append(f"{snap.tag.lower()}#{snap.id} crop size changed")

        # float32 because uint8 subtraction wraps around
        arr_b = np.asarray(crop_b, dtype=np.float32)
        arr_a = np.asarray(crop_a, dtype=np.float32)
        diff = float(np.abs(arr_a - arr_b).mean()) / 255 * 100

        area = crop_b.size[0] * crop_b.size[1]
        total_area += area
        weighted += diff * area
        if diff > PIXEL_TOLERANCE:
            offenders.append(f"{snap.tag.lower()}#{snap.id} {diff:.1f}%")

    if not total_area:
        return CheckResult("Pixels", True, "no comparable regions", [], "advisory")

    mean = weighted / total_area
    passed = mean <= PIXEL_TOLERANCE
    summary = f"{mean:.1f}% difference outside modified regions"
    if offenders:
        summary += f"  -> {_summarise(offenders)}"
    return CheckResult("Pixels", passed, summary, offenders, "advisory")


# --- orchestration ----------------------------------------------------------

def run_checks(url, html_before, html_after, modified_ids=()):
    """Run every check and return a Report. Coverage runs even if capture fails."""
    from bs4 import BeautifulSoup

    report = Report()
    soup_before = BeautifulSoup(html_before, "html.parser")
    soup_after = BeautifulSoup(html_after, "html.parser")
    coverage = check_coverage(soup_before, soup_after)

    try:
        snap_before, snap_after, shot_before, shot_after = capture(
            url, html_before, html_after
        )
    except BrowserError as exc:
        report.error = f"Could not render the page for verification: {exc}"
        report.checks = [coverage]
        return report

    layout = check_layout(snap_before, snap_after, modified_ids)

    report.checks = [
        layout,
        check_visibility(snap_before, snap_after),
        check_contrast_goal(snap_before, snap_after, modified_ids),
        check_colour(snap_before, snap_after, modified_ids),
        coverage,
        check_remaining_contrast(snap_after, modified_ids),
        check_pixels(snap_before, snap_after, shot_before, shot_after,
                     modified_ids, layout.element_ids),
    ]
    return report
