"""Verify a remediation: did the fix work, and did anything else move?

A single "how different do these two pictures look" percentage cannot answer
that. Adding `alt` text changes nothing visually, inserting a `<label>`
changes the layout on purpose, and recolouring text changes pixels on
purpose -- one number is blind to all three distinctions.

So this compares *properties per element*, not pixels per page: geometry,
visibility, computed colour, contrast, and coverage, matched by the
`data-aai-id` stamped on every element before any fixer ran.
"""

import io
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from webColorss import check_contrast

VIEWPORT = {"width": 1280, "height": 720}
GEOMETRY_TOLERANCE = 1.0        # px; sub-pixel font rendering is not a layout break
PIXEL_TOLERANCE = 1.0           # % mean absolute difference per element
MIN_PIXEL_AREA = 400            # px^2; below this a crop is too small to judge
MAX_PIXEL_ELEMENTS = 300        # cap the per-crop pass on huge pages
NAV_TIMEOUT = 20000             # ms

LARGE_TEXT_PX = 24.0            # WCAG "large text": >=24px, or >=18.66px at weight >=700
LARGE_TEXT_BOLD_PX = 18.66
AA_NORMAL = 4.5
AA_LARGE = 3.0

# Disables animation so two captures of the same markup are identical.
DETERMINISM_CSS = (
    "*,*::before,*::after{animation:none!important;"
    "transition:none!important;caret-color:transparent!important}"
)

SNAPSHOT_JS = """
() => {
  const effectiveBg = el => {
    for (let n = el; n; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c;
    }
    return 'rgb(255, 255, 255)';          // the browser's canvas default
  };
  const hasDirectText = el =>
    [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const stampedParent = el => {
    for (let n = el.parentElement; n; n = n.parentElement) {
      if (n.dataset.aaiId !== undefined) return n.dataset.aaiId;
    }
    return null;
  };

  return [...document.querySelectorAll('*')].map(el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      id: el.dataset.aaiId ?? null,
      parent_id: stampedParent(el),
      is_new: el.dataset.aaiNew === '1',
      tag: el.tagName,
      x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height,
      fg: s.color, bg: effectiveBg(el),
      font_size: parseFloat(s.fontSize) || 0,
      font_weight: parseInt(s.fontWeight) || 400,
      visible: s.display !== 'none' && s.visibility !== 'hidden'
               && parseFloat(s.opacity) > 0 && r.width > 0 && r.height > 0,
      has_text: hasDirectText(el)
    };
  });
}
"""


class CaptureError(RuntimeError):
    """Raised when the browser could not render both versions."""


@dataclass
class ElementSnapshot:
    id: str
    parent_id: str
    is_new: bool
    tag: str
    x: float
    y: float
    w: float
    h: float
    fg: str
    bg: str
    font_size: float
    font_weight: int
    visible: bool
    has_text: bool

    @classmethod
    def from_js(cls, d):
        return cls(**d)


@dataclass
class CheckResult:
    name: str
    passed: bool
    summary: str
    details: list = field(default_factory=list)
    tier: str = "blocking"        # blocking | objective | advisory


@dataclass
class Report:
    checks: list = field(default_factory=list)
    error: str = None

    @property
    def verdict(self):
        """A rule, not an average.

        Averaging is what made the old score useless -- a healthy number could
        hide an element having vanished. "The fix didn't finish" and "the fix
        broke the page" demand different responses, so they get different
        verdicts.
        """
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
            f"{'PASS' if c.passed else 'FAIL'}  {c.name:<12} {c.summary}"
            for c in self.checks
        )
        return f"{head}\n\n{body}"

    def as_dict(self):
        return {
            "verdict": self.verdict,
            "error": self.error,
            "checks": [
                {"name": c.name, "passed": c.passed, "summary": c.summary,
                 "details": c.details, "tier": c.tier}
                for c in self.checks
            ],
        }


# --- Phase 2: capture -------------------------------------------------------

def capture(url, html_before, html_after):
    """Render both versions and snapshot them.

    One browser, one page, two `set_content` calls. `set_content` operates on
    the current document, so the base URL established by `goto` survives and
    relative assets still resolve. Rendering both from the same page also
    means no dynamic-content drift between the two captures.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CaptureError(f"Playwright is not installed: {exc}") from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport=VIEWPORT,
                    device_scale_factor=1,   # 1 CSS px == 1 image px, so crops line up
                )
                page = context.new_page()
                page.set_default_timeout(NAV_TIMEOUT)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                except Exception:
                    pass          # only needed to establish a base URL; content is replaced

                def render(html):
                    page.set_content(html, wait_until="load", timeout=NAV_TIMEOUT)
                    page.add_style_tag(content=DETERMINISM_CSS)
                    snap = [ElementSnapshot.from_js(d) for d in page.evaluate(SNAPSHOT_JS)]
                    shot = page.screenshot(type="png", full_page=True)
                    return snap, shot

                snap_before, shot_before = render(html_before)
                snap_after, shot_after = render(html_after)
            finally:
                browser.close()
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(f"could not render the page: {exc}") from exc

    return snap_before, snap_after, shot_before, shot_after


# --- helpers ----------------------------------------------------------------

def _by_id(snapshots):
    return {s.id: s for s in snapshots if s.id is not None}


def _parent_map(snapshots):
    return {s.id: s.parent_id for s in snapshots if s.id is not None}


def _insertion_ancestors(snap_after):
    """Ids of every ancestor of an inserted node.

    A <div> wrapping an input legitimately grows taller when a <label> and
    <br> are inserted inside it -- that is the fix working, not a layout
    break. Without this exemption check A fails on every form container.
    """
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
    """Ids of every ancestor of the given elements.

    Needed only by the pixel check: an element's crop contains all of its
    descendants, so an ancestor of a deliberately-recoloured element shows
    that recolouring inside its own crop. Without this, <html> and <body>
    report a "difference" that is exactly the fix working.
    """
    parents = _parent_map(snapshots)
    found = set()
    for start in ids:
        node = parents.get(start)
        while node is not None and node not in found:
            found.add(node)
            node = parents.get(node)
    return found


def _with_descendants(ids, snapshots):
    """Expand a set of ids to include everything beneath it.

    `color` inherits, so recolouring a parent changes every descendant's
    *computed* colour even though none were touched directly. The same
    applies to the effective background.
    """
    parents = _parent_map(snapshots)
    expanded = set(ids)
    for snap in snapshots:
        chain, node = [], snap.id
        while node is not None and node not in expanded:
            chain.append(node)
            node = parents.get(node)
        if node is not None:          # walked up into an already-included ancestor
            expanded.update(chain)
    return expanded


_RGB_RE = re.compile(r"rgba?\(([^)]*)\)")


def _threshold_for(snap):
    bold_large = snap.font_size >= LARGE_TEXT_BOLD_PX and snap.font_weight >= 700
    return AA_LARGE if (snap.font_size >= LARGE_TEXT_PX or bold_large) else AA_NORMAL


def _summarise(items, limit=3):
    shown = ", ".join(items[:limit])
    extra = len(items) - limit
    return f"{shown}, ... (+{extra} more)" if extra > 0 else shown


# --- Phase 3: checks A, B, E ------------------------------------------------

def check_layout(snap_before, snap_after, modified_ids=()):
    """Compare width, height and x. Never y.

    Inserting a label pushes everything below it down, so absolute y differs
    for most of the page and carries no signal. A changed width or height is
    a real layout break.

    Two exemptions keep the fix itself from reading as breakage. Elements the
    fixer *deliberately changed* are skipped entirely -- rewriting a label's
    text legitimately changes its width. Their ancestors, and the ancestors of
    anything inserted, are exempt from the *height* check only: a container
    grows taller when content is added inside it, but a vertical insertion
    should never change its width.
    """
    before, after = _by_id(snap_before), _by_id(snap_after)
    modified = set(modified_ids or ())
    exempt_height = _insertion_ancestors(snap_after) | _ancestors_of(modified, snap_after)

    checked, failures, warnings = 0, [], []
    for eid, b in before.items():
        a = after.get(eid)
        if a is None or not (b.visible and a.visible) or eid in modified:
            continue
        checked += 1
        if abs(a.w - b.w) > GEOMETRY_TOLERANCE:
            failures.append(f"{b.tag.lower()}#{eid} width {b.w:.0f}->{a.w:.0f}px")
        elif abs(a.h - b.h) > GEOMETRY_TOLERANCE and eid not in exempt_height:
            failures.append(f"{b.tag.lower()}#{eid} height {b.h:.0f}->{a.h:.0f}px")
        elif abs(a.x - b.x) > GEOMETRY_TOLERANCE:
            warnings.append(f"{b.tag.lower()}#{eid} x {b.x:.0f}->{a.x:.0f}px")

    passed = not failures
    if passed:
        summary = f"{checked}/{checked} elements unchanged in size"
        if warnings:
            summary += f" ({len(warnings)} shifted horizontally)"
    else:
        summary = f"{len(failures)} of {checked} elements changed size  -> {_summarise(failures)}"
    return CheckResult("Layout", passed, summary, failures + warnings, "blocking")


def check_visibility(snap_before, snap_after):
    """Nothing that was visible may disappear."""
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
    inputs = [i for i in soup.find_all("input")
              if (i.get("type") or "text").lower() not in
              ("hidden", "submit", "button", "reset", "image")]
    labelled = 0
    for inp in inputs:
        if inp.find_parent("label") is not None:
            labelled += 1
        elif inp.get("aria-label") or inp.get("aria-labelledby"):
            labelled += 1
        elif inp.get("id"):
            match = soup.find("label", attrs={"for": inp["id"]})
            if match is not None and match.get_text(strip=True):
                labelled += 1
    return labelled, len(inputs)


def _alt_coverage(soup):
    imgs = soup.find_all("img")
    return sum(1 for i in imgs if (i.get("alt") or "").strip()), len(imgs)


def check_coverage(soup_before, soup_after):
    """Did the alt-text and label fixes actually apply? No browser needed."""
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
    return CheckResult("Coverage", not details, summary, details, "objective")


# --- Phase 4: check C -------------------------------------------------------

def check_contrast_goal(snap_before, snap_after):
    """Did the remediation reach its objective?

    This is a regression test on the *goal*, not on side effects -- nothing
    else in the pipeline verifies that contrast actually cleared the
    threshold.
    """
    before = _by_id(snap_before)
    checked, failures, regressions = 0, [], []

    for snap in snap_after:
        if snap.id is None or not snap.visible or not snap.has_text:
            continue
        ratio_after = check_contrast(snap.fg, snap.bg)
        if ratio_after is None:
            continue
        checked += 1
        threshold = _threshold_for(snap)

        b = before.get(snap.id)
        ratio_before = check_contrast(b.fg, b.bg) if b else None

        if ratio_after < threshold:
            was = f"{ratio_before:.1f}" if ratio_before is not None else "?"
            failures.append(
                f"{snap.tag.lower()}#{snap.id} ({was}->{ratio_after:.1f}, "
                f"needs {threshold})"
            )
        elif ratio_before is not None and ratio_after < ratio_before - 0.05:
            regressions.append(
                f"{snap.tag.lower()}#{snap.id} got worse "
                f"({ratio_before:.1f}->{ratio_after:.1f})"
            )

    problems = failures + regressions
    passed = not problems
    if passed:
        summary = f"all {checked} text elements meet their threshold"
    else:
        parts = []
        if failures:
            parts.append(f"{len(failures)} still below threshold")
        if regressions:
            parts.append(f"{len(regressions)} got worse")
        summary = f"{' and '.join(parts)}  -> {_summarise(problems)}"
    return CheckResult("Contrast", passed, summary, problems, "objective")


# --- Phase 5: check D -------------------------------------------------------

def check_colour(snap_before, snap_after, modified_ids):
    """Colour must not leak onto elements the fixer never touched.

    Catches the whole-attribute-replacement failure mode, where rewriting a
    style string silently drops or alters something it shouldn't.
    """
    excluded = _with_descendants(set(modified_ids or ()), snap_after)
    before = _by_id(snap_before)
    checked, changes = 0, []

    for snap in snap_after:
        if snap.id is None or snap.id in excluded or snap.is_new:
            continue
        b = before.get(snap.id)
        if b is None or not (b.visible and snap.visible):
            continue
        checked += 1
        if b.fg != snap.fg:
            changes.append(f"{snap.tag.lower()}#{snap.id} colour {b.fg}->{snap.fg}")
        elif b.bg != snap.bg:
            changes.append(f"{snap.tag.lower()}#{snap.id} background {b.bg}->{snap.bg}")

    passed = not changes
    summary = ("no unintended colour changes" if passed
               else f"{len(changes)} of {checked} untouched elements changed  "
                    f"-> {_summarise(changes)}")
    return CheckResult("Colour", passed, summary, changes, "blocking")


# --- Phase 6: check F -------------------------------------------------------

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
    """Per-element crop comparison for everything we did not deliberately change.

    Whole-page diffing is meaningless here: the two screenshots have
    different heights and everything below an inserted label is offset.
    Cropping each element with its *own* version's coordinates makes the
    global offset irrelevant, and a failure names a specific element.
    """
    try:
        img_before = Image.open(io.BytesIO(shot_before)).convert("RGB")
        img_after = Image.open(io.BytesIO(shot_after)).convert("RGB")
    except Exception as exc:
        return CheckResult("Pixels", True, f"skipped ({exc})", [], "advisory")

    # A crop contains everything inside it, so this check has to exclude more
    # than check D does: the modified elements, their descendants (colour
    # inherits), their ANCESTORS (whose crops contain them), and the ancestors
    # of anything inserted. Otherwise <html> and <body> always "differ" -- by
    # exactly the amount the fix changed on purpose.
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
            crop_a = crop_a.resize(crop_b.size)

        # float32 first: uint8 arithmetic wraps, so 10 - 200 would give 66
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


# --- Phase 7: orchestration -------------------------------------------------

def run_checks(url, html_before, html_after, modified_ids=()):
    """Run every check and return a Report.

    Coverage needs no browser, so it still runs (and still reports) when
    capture fails -- a browser problem degrades the report rather than
    silently producing a false pass.
    """
    from bs4 import BeautifulSoup

    report = Report()
    soup_before = BeautifulSoup(html_before, "html.parser")
    soup_after = BeautifulSoup(html_after, "html.parser")
    coverage = check_coverage(soup_before, soup_after)

    try:
        snap_before, snap_after, shot_before, shot_after = capture(
            url, html_before, html_after
        )
    except CaptureError as exc:
        report.error = f"Could not render the page for verification: {exc}"
        report.checks = [coverage]
        return report

    layout = check_layout(snap_before, snap_after, modified_ids)
    layout_failed_ids = {
        part.split("#")[1].split(" ")[0]
        for part in layout.details if "#" in part
    }

    report.checks = [
        layout,
        check_visibility(snap_before, snap_after),
        check_contrast_goal(snap_before, snap_after),
        check_colour(snap_before, snap_after, modified_ids),
        coverage,
        check_pixels(snap_before, snap_after, shot_before, shot_after,
                     modified_ids, layout_failed_ids),
    ]
    return report
