"""Colour-contrast detection and remediation.

Covers all four places colour can live in an HTML document:

  1. inline ``style="..."`` attributes
  2. legacy presentational attributes (``bgcolor``, ``<font color>``, ...)
  3. ``<style>`` blocks embedded in the page
  4. external stylesheets referenced by ``<link rel="stylesheet">``

Every pair is measured against the WCAG AA threshold (4.5:1) *before* any fix
is attempted, and every replacement colour is re-measured *before* it is
written back -- so nothing is ever emitted that still fails.
"""

import logging
import re
from urllib.parse import urljoin

import cssutils
import requests
from PIL import ImageColor

from gemini import suggest_text_color

cssutils.log.setLevel(logging.CRITICAL)   # cssutils is extremely noisy on real-world CSS

# --- tunables ---------------------------------------------------------------

WCAG_AA_NORMAL = 4.5          # contrast ratio required for normal-size text
MAX_GEMINI_CALLS = 25         # per page; past this we go straight to the deterministic fix
MAX_SHEET_BYTES = 500_000     # skip stylesheets larger than this
SHEET_TIMEOUT = 10            # seconds

CANVAS_BACKGROUND = (255, 255, 255)   # what a browser paints behind an unstyled page
DEFAULT_TEXT = (0, 0, 0)              # a browser's default text colour

_NON_COLOURS = {
    "transparent", "inherit", "initial", "unset", "revert",
    "currentcolor", "none", "auto", "",
}

_URL_TOKEN = re.compile(r"url\([^)]*\)", re.I)
_FUNC_COLOUR = re.compile(r"(?:rgba?|hsla?)\([^)]*\)", re.I)


# --- colour parsing ---------------------------------------------------------

def resolve_color(value):
    """Parse any CSS colour into an ``(r, g, b)`` tuple.

    Returns ``None`` when the value is missing, is a keyword that carries no
    colour of its own (``transparent``, ``inherit``, ...), is effectively
    see-through, or simply cannot be parsed. ``None`` means *skip this
    element* -- it is never silently treated as a real colour.

    ``ImageColor`` does the parsing so every CSS named colour works; the old
    hand-rolled parser returned a hardcoded 0.5 luminance for all of them,
    which made every named-colour comparison meaningless.
    """
    if value is None:
        return None
    text = str(value).strip().strip("'\"").replace("!important", "").strip().lower()
    if text in _NON_COLOURS:
        return None
    try:
        parsed = ImageColor.getrgb(text)
    except (ValueError, AttributeError):
        return None
    if len(parsed) == 4:
        r, g, b, alpha = parsed
        if alpha < 128:               # mostly see-through: the real backdrop is behind it
            return None
        return (r, g, b)
    return parsed[:3]


def _linearise(channel):
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(rgb):
    r, g, b = (_linearise(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def luminance(color):
    """WCAG relative luminance for a CSS colour string, or None if unparseable."""
    rgb = resolve_color(color)
    return None if rgb is None else _relative_luminance(rgb)


def _ratio(lum_a, lum_b):
    return (max(lum_a, lum_b) + 0.05) / (min(lum_a, lum_b) + 0.05)


def check_contrast(text_color, background_color):
    """WCAG contrast ratio between two CSS colours, from 1.0 to 21.0.

    Returns ``None`` if either colour cannot be resolved -- callers must check
    for that before comparing against a threshold, rather than scoring an
    unknown colour as if it were mid-grey.
    """
    fg = luminance(text_color)
    bg = luminance(background_color)
    if fg is None or bg is None:
        return None
    return _ratio(fg, bg)


# --- picking a replacement colour -------------------------------------------

def _to_hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def _deterministic_color(fg_rgb, bg_rgb, threshold):
    """Walk the foreground toward black or white until the ratio clears.

    Whichever extreme contrasts better with a given background always reaches
    at least 4.58:1, so this cannot fail for the AA threshold. Stepping from
    the original colour rather than jumping straight to the extreme keeps as
    much of the original hue as the threshold allows.
    """
    bg_lum = _relative_luminance(bg_rgb)
    target = (0, 0, 0) if _ratio(0.0, bg_lum) >= _ratio(1.0, bg_lum) else (255, 255, 255)

    for step in range(1, 21):
        t = step / 20
        candidate = tuple(
            round(fg_rgb[i] + (target[i] - fg_rgb[i]) * t) for i in range(3)
        )
        if _ratio(_relative_luminance(candidate), bg_lum) >= threshold:
            return _to_hex(candidate)
    return _to_hex(target)


_cache = {}
_calls_used = 0


def reset_budget():
    """Clear the per-page suggestion cache and the Gemini call counter."""
    global _calls_used
    _cache.clear()
    _calls_used = 0


def ensure_contrast(fg_raw, bg_raw, threshold=WCAG_AA_NORMAL):
    """Return a text colour that is guaranteed to pass `threshold` against `bg_raw`.

    Asks Gemini first, **verifies its answer** with check_contrast, and falls
    back to a deterministic lightness walk when the suggestion still fails or
    the per-page API budget is spent. Results are cached on the resolved
    colour pair, so a page that reuses one bad pair 30 times costs one call.
    Returns None only when the inputs themselves cannot be resolved.
    """
    global _calls_used

    fg_rgb = resolve_color(fg_raw)
    bg_rgb = resolve_color(bg_raw)
    if fg_rgb is None or bg_rgb is None:
        return None

    key = (fg_rgb, bg_rgb, threshold)
    if key in _cache:
        return _cache[key]

    chosen = None
    if _calls_used < MAX_GEMINI_CALLS:
        _calls_used += 1
        suggestion = suggest_text_color(_to_hex(fg_rgb), _to_hex(bg_rgb))
        if suggestion:
            verified = check_contrast(suggestion, _to_hex(bg_rgb))
            if verified is not None and verified >= threshold:
                chosen = suggestion

    if chosen is None:
        chosen = _deterministic_color(fg_rgb, bg_rgb, threshold)

    _cache[key] = chosen
    return chosen


# --- reading colours off the document ---------------------------------------

def _background_shorthand_color(value):
    """Pull the colour out of a ``background:`` shorthand, if it has one.

    cssutils does not expand shorthands, so ``background: black`` would
    otherwise look like no background at all.
    """
    if not value:
        return None
    cleaned = _URL_TOKEN.sub(" ", value)
    for match in _FUNC_COLOUR.finditer(cleaned):
        if resolve_color(match.group(0)):
            return match.group(0)
    for token in _FUNC_COLOUR.sub(" ", cleaned).split():
        if resolve_color(token):
            return token
    return None


def _declared_background(decl):
    return decl.getPropertyValue("background-color") or _background_shorthand_color(
        decl.getPropertyValue("background")
    )


def _inline_decl(node):
    style = node.get("style") if hasattr(node, "get") else None
    if not style:
        return None
    try:
        return cssutils.parseStyle(style)
    except Exception:
        return None


def _effective_background(el):
    """Nearest painted background on the element or one of its ancestors.

    ``background-color`` does not inherit, so an element with no background of
    its own shows whatever its nearest painted ancestor paints. Without this
    walk, ``<div style="background:black"><h2>x</h2></div>`` is unjudgeable.
    Returns ``(raw_value, rgb)``, falling back to the browser's white canvas.
    """
    node = el
    while node is not None:
        decl = _inline_decl(node)
        if decl is not None:
            raw = _declared_background(decl)
            rgb = resolve_color(raw)
            if rgb:
                return raw, rgb
        raw = node.get("bgcolor") if hasattr(node, "get") else None
        rgb = resolve_color(raw)
        if rgb:
            return raw, rgb
        node = node.parent
    return None, CANVAS_BACKGROUND


def _effective_foreground(el):
    """Nearest inherited text colour; a browser's default is black."""
    node = el
    while node is not None:
        decl = _inline_decl(node)
        if decl is not None:
            raw = decl.getPropertyValue("color")
            rgb = resolve_color(raw)
            if rgb:
                return raw, rgb
        node = node.parent
    return None, DEFAULT_TEXT


ID_ATTR = "data-aai-id"


def _record(report, source, target, old, new, before, after, ids=()):
    report.append({
        "type": "contrast",
        "source": source,
        "target": target,
        "old": old,
        "new": new,
        "ratio_before": round(before, 2) if before is not None else None,
        "ratio_after": round(after, 2) if after is not None else None,
        "ids": [i for i in ids if i],
    })


def _ids_for_selector(soup, selector):
    """Which stamped elements a CSS rule actually applies to.

    The verifier needs this to exclude deliberately-recoloured elements from
    its "nothing else changed colour" check. Complex selectors soupsieve
    cannot handle simply yield nothing, which errs toward reporting a change
    rather than hiding one.
    """
    if soup is None or not selector:
        return []
    try:
        return [el.get(ID_ATTR) for el in soup.select(selector)]
    except Exception:
        return []


# --- the four fixers --------------------------------------------------------

def fix_inline_styles(soup, report, threshold=WCAG_AA_NORMAL):
    """Fix failing colour pairs declared in ``style="..."`` attributes.

    Only the ``color`` property is touched; every other declaration on the
    element is preserved exactly as written.
    """
    for el in soup.find_all(style=True):
        decl = _inline_decl(el)
        if decl is None:
            continue

        own_fg = decl.getPropertyValue("color")
        own_bg = _declared_background(decl)
        if not own_fg and not own_bg:
            continue          # element takes no part in the colour decision

        if resolve_color(own_fg):
            fg_raw, fg_rgb = own_fg, resolve_color(own_fg)
        else:
            fg_raw, fg_rgb = _effective_foreground(el)

        if resolve_color(own_bg):
            bg_raw, bg_rgb = own_bg, resolve_color(own_bg)
        else:
            bg_raw, bg_rgb = _effective_background(el)

        before = _ratio(_relative_luminance(fg_rgb), _relative_luminance(bg_rgb))
        if before >= threshold:
            continue

        fg_key = fg_raw or _to_hex(fg_rgb)
        bg_key = bg_raw or _to_hex(bg_rgb)
        new = ensure_contrast(fg_key, bg_key, threshold)
        if new is None:
            continue

        decl.setProperty("color", new, priority=decl.getPropertyPriority("color"))
        el["style"] = decl.getCssText(separator=" ")
        _record(report, "inline", el.name, fg_key, new,
                before, check_contrast(new, bg_key), ids=[el.get(ID_ATTR)])


# Legacy pre-CSS colour attributes. Only the foreground-bearing ones are
# listed: an element that carries `bgcolor` alone (a <td>, say) has no
# presentational attribute for text colour, and adding an inline style
# instead would promote it above any stylesheet that overrides it.
_PRESENTATIONAL_PAIRS = {
    "body": ("text", "link", "vlink", "alink"),
    "font": ("color",),
    "hr": ("color",),
    "basefont": ("color",),
}


def fix_presentational_attributes(soup, report, threshold=WCAG_AA_NORMAL):
    """Fix legacy colour attributes **in place**.

    Deliberately does not convert these to inline styles: presentational
    attributes sit at the bottom of the cascade and inline styles at the top,
    so converting would silently promote them above any stylesheet that was
    overriding them, visibly changing the page.
    """
    for tag, attrs in _PRESENTATIONAL_PAIRS.items():
        for el in soup.find_all(tag):
            bg_raw, bg_rgb = _effective_background(el)
            bg_key = bg_raw or _to_hex(bg_rgb)
            for attr in attrs:
                fg_raw = el.get(attr)
                fg_rgb = resolve_color(fg_raw)
                if fg_rgb is None:
                    continue
                before = _ratio(_relative_luminance(fg_rgb), _relative_luminance(bg_rgb))
                if before >= threshold:
                    continue
                new = ensure_contrast(fg_raw, bg_key, threshold)
                if new is None:
                    continue
                el[attr] = new
                _record(report, "attribute", f"{tag}[{attr}]", fg_raw, new,
                        before, check_contrast(new, bg_key), ids=[el.get(ID_ATTR)])


def fix_stylesheet(css_text, sheet_url, report, source, threshold=WCAG_AA_NORMAL,
                   soup=None):
    """Absolutise every ``url()`` and rewrite failing colour pairs.

    Returns ``(css, contrast_changed)``. The returned CSS *always* has its
    url() references rewritten against `sheet_url`; `contrast_changed` says
    only whether a colour was actually replaced.
    """
    try:
        sheet = cssutils.parseString(css_text)
    except Exception as exc:
        print(f"could not parse {source} at {sheet_url}: {exc}")
        return css_text, False

    # Relative url()s resolve against THE STYLESHEET, not the document. Once
    # this CSS is inlined into a <style> block they would resolve against the
    # page instead, so background images and @font-face fonts would 404.
    # urljoin leaves absolute URLs, protocol-relative URLs and data: URIs alone.
    try:
        cssutils.replaceUrls(sheet, lambda u: urljoin(sheet_url, u), ignoreImportRules=False)
    except Exception as exc:
        print(f"could not rewrite urls in {source} at {sheet_url}: {exc}")

    changed = False
    for rule in sheet:
        if rule.type != rule.STYLE_RULE:
            continue
        fg_raw = rule.style.getPropertyValue("color")
        bg_raw = _declared_background(rule.style)
        if not fg_raw or not bg_raw:
            continue          # only one half declared here; resolving the rest
                              # of the cascade needs a browser, not a parser
        before = check_contrast(fg_raw, bg_raw)
        if before is None or before >= threshold:
            continue
        new = ensure_contrast(fg_raw, bg_raw, threshold)
        if new is None:
            continue
        rule.style.setProperty(
            "color", new, priority=rule.style.getPropertyPriority("color")
        )
        changed = True
        _record(report, source, rule.selectorText, fg_raw, new,
                before, check_contrast(new, bg_raw),
                ids=_ids_for_selector(soup, rule.selectorText))

    css = sheet.cssText
    if isinstance(css, bytes):        # CSSStyleSheet.cssText returns bytes
        css = css.decode("utf-8", "replace")
    return css, changed


def fix_style_blocks(soup, page_url, report, threshold=WCAG_AA_NORMAL):
    """Handle ``<style>`` blocks -- already downloaded, so no network needed."""
    for tag in soup.find_all("style"):
        css_text = tag.string if tag.string is not None else tag.get_text()
        if not css_text or not css_text.strip():
            continue
        css, _ = fix_stylesheet(css_text, page_url, report, "style-block",
                                threshold, soup=soup)
        # Always written back: the url() rewrite matters even when no colour
        # changed, because this CSS gets rendered from a different base URL.
        tag.string = css


def fix_linked_stylesheets(soup, page_url, report, threshold=WCAG_AA_NORMAL):
    """Fetch each linked stylesheet; inline it only if a colour actually changed."""
    for link in list(soup.find_all("link")):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        if "stylesheet" not in [str(r).lower() for r in rel]:
            continue
        href = link.get("href")
        if not href:
            continue

        sheet_url = urljoin(page_url, href)        # the per-sheet base
        try:
            response = requests.get(sheet_url, timeout=SHEET_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"skipping stylesheet {sheet_url}: {exc}")
            continue                                # leave the <link> intact
        if len(response.content) > MAX_SHEET_BYTES:
            continue

        css, changed = fix_stylesheet(
            response.text, sheet_url, report, "stylesheet", threshold, soup=soup
        )
        if not changed:
            continue          # nothing to fix, so don't inline it and don't
                              # pay the bloat -- the browser fetches it as-is

        style_tag = soup.new_tag("style")
        style_tag.string = css
        style_tag["data-aai-new"] = "1"
        link.replace_with(style_tag)   # preserves position, so cascade order survives


def ChangeColor(url, soup):
    """Detect and remediate contrast failures across all four colour sources.

    Mutates `soup` in place and returns a list of what changed, each entry
    carrying the selector/element, the old and new colour, and the contrast
    ratio before and after.
    """
    reset_budget()
    report = []
    fix_inline_styles(soup, report)
    fix_presentational_attributes(soup, report)
    fix_style_blocks(soup, url, report)      # before the linked pass, so freshly
    fix_linked_stylesheets(soup, url, report)  # inlined sheets aren't re-processed
    return report


if __name__ == "__main__":
    # Offline smoke test of the maths -- no network, no API calls.
    for fg, bg in [("cadetblue", "aqua"), ("#00f", "#000"),
                   ("black", "white"), ("black", "black")]:
        ratio = check_contrast(fg, bg)
        verdict = "PASS" if ratio >= WCAG_AA_NORMAL else "FAIL"
        print(f"{fg:>12} on {bg:<8} {ratio:6.2f}:1  {verdict}")
