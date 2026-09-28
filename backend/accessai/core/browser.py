"""Render pages in headless Chromium and snapshot every element's box and colours.

Shared by the contrast fixer and the verifier. Scripts never run, and every
request the page makes goes through the SSRF guard.
"""

import re
from dataclasses import dataclass

from . import colour, nethttp

VIEWPORT = {"width": 1280, "height": 720}
NAV_TIMEOUT = 20000             # ms

# Freeze animations so two captures of the same markup match.
DETERMINISM_CSS = (
    "*,*::before,*::after{animation:none!important;"
    "transition:none!important;caret-color:transparent!important}"
)

SNAPSHOT_JS = """
() => {
  const parseRgba = css => {
    const m = /rgba?\\(([^)]*)\\)/.exec(css || '');
    if (!m) return null;
    const [r, g, b, a = 1] = m[1].split(/[\\s,\\/]+/).filter(Boolean).map(parseFloat);
    return {r, g, b, a};
  };
  const over = (top, under) => ({
    r: top.r * top.a + under.r * (1 - top.a),
    g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a),
    a: 1
  });
  const toCss = c => `rgb(${Math.round(c.r)}, ${Math.round(c.g)}, ${Math.round(c.b)})`;
  const CANVAS = {r: 255, g: 255, b: 255, a: 1};

  // The opaque colour behind el, or null if an image or gradient paints it.
  const effectiveBg = el => {
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage !== 'none') return null;
      const c = parseRgba(s.backgroundColor);
      if (!c) return null;
      if (c.a > 0) layers.push(c);
      if (c.a >= 1) break;
    }
    return layers.reduceRight((under, top) => over(top, under), CANVAS);
  };
  const effectiveFg = (css, bg) => {
    const c = parseRgba(css);
    if (!c) return css;
    return toCss(bg ? over(c, bg) : c);
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
    const bg = effectiveBg(el);
    return {
      id: el.dataset.aaiId ?? null,
      parent_id: stampedParent(el),
      is_new: el.dataset.aaiNew === '1',
      tag: el.tagName,
      x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height,
      fg: effectiveFg(s.color, bg), bg: bg && toCss(bg),
      font_size: parseFloat(s.fontSize) || 0,
      font_weight: parseInt(s.fontWeight) || 400,
      visible: s.display !== 'none' && s.visibility !== 'hidden'
               && parseFloat(s.opacity) > 0 && r.width > 0 && r.height > 0,
      has_text: hasDirectText(el)
    };
  });
}
"""


class BrowserError(RuntimeError):
    """The page could not be rendered."""


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
    bg: str | None      # None under a background image or gradient
    font_size: float
    font_weight: int
    visible: bool
    has_text: bool

    @classmethod
    def from_js(cls, d):
        return cls(**d)

    @property
    def contrast(self):
        """Text contrast ratio, or None when the background isn't a single colour."""
        return None if self.bg is None else colour.check_contrast(self.fg, self.bg)

    @property
    def threshold(self):
        return colour.threshold_for(self.font_size, self.font_weight)


# Multiple CSPs intersect, so the page's own policy can't loosen this one.
CSP_META = ('<meta http-equiv="Content-Security-Policy" '
            "content=\"script-src 'none'\">")

_HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.I)


def _no_scripts(html):
    """Insert the script-blocking CSP right after <head>, or at the very start."""
    match = _HEAD_OPEN.search(html)
    if match:
        return html[:match.end()] + CSP_META + html[match.end():]
    return CSP_META + html


def request_allowed(url, resource_type, is_main_frame):
    """Scripts and iframes never load; everything else must pass the SSRF guard."""
    if resource_type == "script":
        return False
    if resource_type == "document" and not is_main_frame:
        return False
    if url.startswith(("data:", "blob:")):
        return True
    try:
        nethttp.assert_fetchable(url)
    except nethttp.BlockedURL:
        return False
    return True


def _guard_request(route):
    request = route.request
    is_main_frame = request.frame.parent_frame is None
    if request_allowed(request.url, request.resource_type, is_main_frame):
        route.continue_()
    else:
        route.abort()


def _render_one(browser, url, html, screenshot):
    # A fresh context per page, so nothing (cache, fonts, layout) carries over.
    context = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=1,   # 1 CSS px == 1 image px, so crops line up
    )
    try:
        page = context.new_page()
        page.set_default_timeout(NAV_TIMEOUT)

        # Also a backup for the CSP. java_script_enabled=False would break
        # Playwright's set_content.
        page.route("**/*", _guard_request)

        # goto only sets the base URL, so serve a stub for the document.
        # Playwright checks routes newest-first, so this must come last.
        page.route(
            lambda candidate: candidate == url,
            lambda route: route.fulfill(
                status=200, content_type="text/html",
                body="<!doctype html><title>base</title>"),
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        except Exception:
            pass

        page.set_content(_no_scripts(html), wait_until="load", timeout=NAV_TIMEOUT)
        page.add_style_tag(content=DETERMINISM_CSS)
        snapshots = [ElementSnapshot.from_js(d) for d in page.evaluate(SNAPSHOT_JS)]
        png = page.screenshot(type="png", full_page=True) if screenshot else None
        return snapshots, png
    finally:
        context.close()


_chromium_works = False


def is_available():
    """True if Chromium can be launched."""
    # A launch takes about a second, so only a success is cached.
    global _chromium_works
    if not _chromium_works:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                p.chromium.launch(headless=True).close()
            _chromium_works = True
        except Exception:
            return False
    return _chromium_works


def render(url, *htmls, screenshots=True):
    """Render each HTML version as if served from `url`.

    Returns one ``(snapshots, png)`` pair per version; png is None without screenshots.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserError(f"Playwright is not installed: {exc}") from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                return [_render_one(browser, url, html, screenshots) for html in htmls]
            finally:
                browser.close()
    except Exception as exc:
        raise BrowserError(f"could not render the page: {exc}") from exc
