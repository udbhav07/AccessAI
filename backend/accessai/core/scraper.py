"""The scan pipeline: fetch the page, make its URLs absolute and stamp every element,
then add alt text, labels and contrast fixes. The Gemini and browser work runs in
parallel; all edits to the page happen afterwards on one thread.
"""

import re
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from . import ID_ATTR, NEW_ATTR, a11y, nethttp
from .contrast import apply_contrast, plan_contrast
from .gemini import describe_images, label_fields

# ai_fallback: the model was not used for at least part of the page (no key, quota,
# timeout, ...). gemini logs the reason; users see one generic notice.
ScrapeResult = namedtuple(
    "ScrapeResult", "url soup issues html_before modified_ids warnings ai_fallback",
    defaults=((), False)
)

# Anything else parses into an empty soup that would falsely report a PASS.
HTML_TYPES = ("text/html", "application/xhtml+xml")


class UnsupportedContent(ValueError):
    """The URL did not return an HTML document."""


URL_ATTRS = (
    ('a', 'href'), ('area', 'href'), ('link', 'href'),
    ('img', 'src'), ('script', 'src'), ('iframe', 'src'), ('embed', 'src'),
    ('source', 'src'), ('audio', 'src'), ('video', 'src'), ('track', 'src'),
    ('input', 'src'), ('video', 'poster'), ('object', 'data'),
    ('form', 'action'),
)
SRCSET_ATTRS = (('img', 'srcset'), ('source', 'srcset'))

_CSS_URL = re.compile(r"""url\(\s*(['"]?)(?!['"]?(?:data:|#))([^'")]+)\1\s*\)""", re.I)
_CSS_IMPORT = re.compile(r"""@import\s+(['"])([^'"]+)\1""", re.I)


def _absolutise_srcset(value, base):
    out = []
    for candidate in value.split(','):
        parts = candidate.strip().split(maxsplit=1)
        if not parts:
            continue
        parts[0] = urljoin(base, parts[0])
        out.append(' '.join(parts))
    return ', '.join(out)


def _absolutise_css_urls(css, base):
    """Absolutise ``url(...)`` and ``@import "..."`` in CSS, skipping data: and fragment URLs."""
    css = _CSS_URL.sub(
        lambda m: f"url({m.group(1)}{urljoin(base, m.group(2).strip())}{m.group(1)})",
        css,
    )
    return _CSS_IMPORT.sub(
        lambda m: f"@import {m.group(1)}{urljoin(base, m.group(2))}{m.group(1)}", css)


def stamp_ids(soup):
    for i, el in enumerate(soup.find_all(True)):
        el[ID_ATTR] = str(i)


def mark_new(tag):
    tag[NEW_ATTR] = "1"
    return tag


def strip_ids(soup):
    """Remove the id stamps. The app itself keeps them in the served HTML."""
    for el in soup.find_all(True):
        el.attrs.pop(ID_ATTR, None)
        el.attrs.pop(NEW_ATTR, None)


class Scraper:
    def __init__(self):
        self.url = ""
        self.html = ""
        self.soup = None

    def scrape_url(self, url):
        """Fetch and fix a page, returning before/after HTML and the changes."""
        issues = []
        req = nethttp.get(url)
        req.raise_for_status()

        # Browsers sniff a missing content-type as HTML, so allow it.
        content_type = req.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type not in HTML_TYPES:
            raise UnsupportedContent(
                f"that URL returned {content_type}, not an HTML page")

        self.soup = BeautifulSoup(req.text, 'html.parser')
        self.url = req.url          # post-redirect
        self.html = req.text

        self.absolutise_urls()
        stamp_ids(self.soup)
        # Serialise only after absolutising and stamping, so before/after
        # differ by the fixes alone.
        html_before = str(self.soup)

        # BeautifulSoup isn't thread-safe: the jobs only read the soup, and
        # all writes happen on this thread after the pool has finished.
        images = self.image_targets()
        fields = self.label_targets()
        with ThreadPoolExecutor(max_workers=3) as pool:
            failures = []
            alts = pool.submit(describe_images, [src for _, src in images], failures)
            labels = pool.submit(label_fields, [(inp, text) for inp, text, _, _ in fields],
                                 failures)
            contrast = pool.submit(plan_contrast, self.url, html_before, failures)

        contrast_plan = contrast.result()
        issues.extend(self.apply_alts(images, alts.result()))
        issues.extend(self.apply_labels(fields, labels.result()))
        issues.extend(apply_contrast(self.soup, contrast_plan))

        modified_ids = set()
        for issue in issues:
            modified_ids.update(issue.get("ids") or ())

        warnings = [contrast_plan.warning] if contrast_plan.warning else []
        return ScrapeResult(self.url, self.soup, issues, html_before, modified_ids, warnings,
                            ai_fallback=bool(failures))

    def absolutise_urls(self):
        """Make asset URLs absolute, since the srcdoc preview has our origin.

        A relative form action would otherwise post to this app.
        """
        base_el = self.soup.find('base', href=True)
        base = urljoin(self.url, base_el['href']) if base_el else self.url

        for tag, attr in URL_ATTRS:
            for el in self.soup.find_all(tag):
                value = el.get(attr)
                if value is None or value.startswith("#"):
                    # Absolutising a fragment would navigate the preview away.
                    continue
                el[attr] = urljoin(base, value)

        for tag, attr in SRCSET_ATTRS:
            for el in self.soup.find_all(tag):
                if attr in el.attrs:
                    el[attr] = _absolutise_srcset(el[attr], base)

        for el in self.soup.find_all(style=True):
            el['style'] = _absolutise_css_urls(el['style'], base)

        for tag in self.soup.find_all('style'):
            if tag.string:
                tag.string = _absolutise_css_urls(tag.string, base)

        if base_el:
            # URLs are already absolute; a leftover <base> would re-resolve them.
            base_el.decompose()

    def image_targets(self):
        targets = []
        for img in self.soup.find_all('img'):
            if not a11y.needs_alt(img):
                continue
            src = img.get('src')
            if not src:
                continue
            if not src.startswith(("http://", "https://")):
                src = urljoin(self.url, src)
            targets.append((img, src))
        return targets

    def apply_alts(self, targets, alts):
        issues, skipped = [], 0
        for (img, _), alt in zip(targets, alts):
            if not alt:
                skipped += 1
                continue
            img['alt'] = alt
            issues.append({
                "type": "alt", "source": "image", "target": img.get('src'),
                "old": "", "new": alt, "ids": [img.get(ID_ATTR)],
            })
        if skipped:
            issues.append({
                "type": "alt", "source": "skipped", "target": None,
                "old": None, "new": None, "ids": [],
                "note": f"{skipped} image(s) could not be described",
            })
        return issues

    def get_imgs(self):
        targets = self.image_targets()
        return self.apply_alts(targets, describe_images([src for _, src in targets]))

    def label_targets(self):
        """``(field, existing_label_text, existing_label, strategy)`` per unnamed field."""
        targets = []
        for inp in a11y.form_fields(self.soup):
            strategy = a11y.labelling_strategy(self.soup, inp)
            if strategy is None:
                continue
            existing = a11y.explicit_label(self.soup, inp) if strategy == 'for' else None
            text = existing.get_text(strip=True) if existing is not None else ""
            targets.append((inp, text, existing, strategy))
        return targets

    def apply_labels(self, targets, suggestions):
        issues = []
        for (inp, _, label, strategy), new_label in zip(targets, suggestions):
            if not new_label:
                continue

            if strategy == 'aria':
                inp['aria-label'] = new_label
                issues.append({
                    "type": "label", "source": "aria-label",
                    "target": inp.get('name') or inp.get('placeholder') or 'input',
                    "old": None, "new": new_label, "ids": [inp.get(ID_ATTR)],
                })
            elif label is None:
                label = self.soup.new_tag('label')
                label['for'] = inp['id']
                label.string = new_label
                inp.insert_before(mark_new(label))
                inp.insert_before(mark_new(self.soup.new_tag('br')))
                issues.append({
                    "type": "label", "source": "inserted", "target": inp['id'],
                    "old": None, "new": new_label, "ids": [inp.get(ID_ATTR)],
                })
            else:
                old = label.get_text(strip=True)
                label.string = new_label
                # Recording the label's id tells the verifier its width change is expected.
                issues.append({
                    "type": "label", "source": "rewritten", "target": inp['id'],
                    "old": old, "new": new_label, "ids": [label.get(ID_ATTR)],
                })
        return issues

    def get_label(self):
        targets = self.label_targets()
        return self.apply_labels(
            targets, label_fields([(inp, text) for inp, text, _, _ in targets]))

    def get_colors(self):
        return apply_contrast(self.soup, plan_contrast(self.url, str(self.soup)))


# Run from backend/ against the fixture page:
#     python -m http.server 8000 -d tests/fixtures
#     ACCESSAI_ALLOW_PRIVATE_HOSTS=1 python -m accessai.core.scraper
if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/experiment.html"
    result = Scraper().scrape_url(target)
    print(f"{len(result.issues)} colour fixes, {len(result.modified_ids)} elements touched")
    for entry in result.issues:
        print("   ", entry)
