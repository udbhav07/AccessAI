from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from . import a11y, nethttp
from .gemini import getAlt, getLabel
from .webColorss import ChangeColor

# Stamped on every element before any fixer runs, so the verifier can match
# old -> new. A selector path like `div:nth-child(3) > input` cannot be used:
# inserting a <label> shifts the nth-child index of every following sibling,
# so the fix would invalidate its own identity scheme.
# Each image and each input needs its own Gemini round-trip (~1-3s). They are
# independent, so they overlap; the cap keeps a large page from opening
# hundreds of concurrent connections.
MAX_WORKERS = 8

ID_ATTR = "data-aai-id"
NEW_ATTR = "data-aai-new"

ScrapeResult = namedtuple(
    "ScrapeResult", "url soup issues html_before modified_ids"
)

# What a browser would actually parse as a document. Anything else fed to
# html.parser produces a soup of decoded binary: no images, no inputs, no
# colours, an empty issue list and a PASS -- the tool reporting success on
# something it never read.
HTML_TYPES = ("text/html", "application/xhtml+xml")


class UnsupportedContent(ValueError):
    """The URL returned something that is not an HTML document."""


def stamp_ids(soup):
    """Give every element a stable identity before anything is modified."""
    for i, el in enumerate(soup.find_all(True)):
        el[ID_ATTR] = str(i)


def mark_new(tag):
    """Flag a node the fixer inserted, so it isn't read as a mystery element."""
    tag[NEW_ATTR] = "1"
    return tag


def strip_ids(soup):
    """Remove the stamps. Not used by the app -- the served HTML keeps them so
    what the user sees is byte-identical to what was verified -- but useful if
    a caller wants clean output."""
    for el in soup.find_all(True):
        el.attrs.pop(ID_ATTR, None)
        el.attrs.pop(NEW_ATTR, None)


class Scraper:
    def __init__(self):
        self.url = ""
        self.html = ""
        self.soup = None

    def scrape_url(self, url):
        """Fetch, patch, and return both versions plus what changed.

        Order matters: URLs are absolutised and ids stamped *before*
        `html_before` is serialised, so the two versions differ only by the
        fixes themselves -- not by asset resolution or missing stamps.
        """
        issues = []
        req = nethttp.get(url)
        req.raise_for_status()

        # An empty content-type is allowed through: plenty of small servers
        # send none at all, and a browser sniffs those as HTML.
        content_type = req.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type not in HTML_TYPES:
            raise UnsupportedContent(
                f"that URL returned {content_type}, not an HTML page")

        self.soup = BeautifulSoup(req.text, 'html.parser')
        self.url = req.url          # post-redirect, so relative paths resolve correctly
        self.html = req.text

        self.absolutise_urls()
        stamp_ids(self.soup)
        html_before = str(self.soup)

        issues.extend(self.get_imgs())
        issues.extend(self.get_label())
        issues.extend(self.get_colors())

        modified_ids = set()
        for issue in issues:
            modified_ids.update(issue.get("ids") or ())

        return ScrapeResult(self.url, self.soup, issues, html_before, modified_ids)

    def absolutise_urls(self):
        """Rewrite relative asset URLs to absolute.

        A `srcdoc` iframe inherits its base URL from the parent document, so
        without this every stylesheet, image and script would be requested
        from the app's own origin instead of the scraped site.
        """
        for tag, attr in (('a', 'href'), ('img', 'src'),
                          ('link', 'href'), ('script', 'src')):
            for el in self.soup.find_all(tag):
                if attr in el.attrs:
                    el[attr] = urljoin(self.url, el[attr])

    def get_imgs(self):
        """Generate alt text for images that have none.

        The API calls fan out; the soup is only ever mutated from this thread.
        BeautifulSoup is not thread-safe, so the shape is always: collect
        targets, run the network calls in parallel, then apply results in order.
        """
        targets = []
        for img in self.soup.find_all('img'):
            if img.get('alt'):          # covers both missing and empty alt
                continue
            src = img.get('src')
            if not src:
                continue
            if not src.startswith(("http://", "https://")):
                src = urljoin(self.url, src)     # relative -> absolute
            targets.append((img, src))

        if not targets:
            return []

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(targets))) as pool:
            alts = list(pool.map(lambda t: getAlt(t[1]), targets))

        issues, skipped = [], 0
        for (img, _), alt in zip(targets, alts):
            if not alt:         # getAlt gave up; leave the image untouched
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

    def get_label(self):
        """Add missing labels and replace ones that don't describe their input.

        An input that already has a label costs two calls (suitability check,
        then generation), so parallelising matters more here than for images.

        Inputs with no id are handled too, via `aria-label` -- they used to be
        skipped outright while still counting against coverage, which on a
        real form is most of them.
        """
        targets = []
        for inp in self.soup.find_all('input'):
            strategy = a11y.labelling_strategy(self.soup, inp)
            if strategy is None:
                continue
            existing = a11y.explicit_label(self.soup, inp) if strategy == 'for' else None
            targets.append((inp, existing, strategy))

        if not targets:
            return []

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(targets))) as pool:
            suggestions = list(pool.map(lambda t: getLabel(t[0], t[1]), targets))

        issues = []
        for (inp, label, strategy), gemLabel in zip(targets, suggestions):
            if gemLabel == 'y':     # existing label already fits, or the call failed
                continue

            if strategy == 'aria':
                # An attribute, not a node: nothing moves, so this needs none
                # of the layout exemptions an inserted <label> does.
                inp['aria-label'] = gemLabel
                issues.append({
                    "type": "label", "source": "aria-label",
                    "target": inp.get('name') or inp.get('placeholder') or 'input',
                    "old": None, "new": gemLabel, "ids": [inp.get(ID_ATTR)],
                })
            elif label is None:     # `not label` is falsy for an EMPTY <label></label>
                label = self.soup.new_tag('label')
                label['for'] = inp['id']
                label.string = gemLabel
                inp.insert_before(mark_new(label))
                inp.insert_before(mark_new(self.soup.new_tag('br')))
                issues.append({
                    "type": "label", "source": "inserted", "target": inp['id'],
                    "old": None, "new": gemLabel, "ids": [inp.get(ID_ATTR)],
                })
            else:
                old = label.get_text(strip=True)
                label.string = gemLabel
                # Rewriting the text changes the label's width; recording the id
                # tells the verifier that was deliberate.
                issues.append({
                    "type": "label", "source": "rewritten", "target": inp['id'],
                    "old": old, "new": gemLabel, "ids": [label.get(ID_ATTR)],
                })

        return issues

    def get_colors(self):
        return ChangeColor(self.url, self.soup)


# Run as a module so the relative imports resolve:
#     python -m src.webScraper http://localhost:8000/templates/experiment.html
if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/experiment.html"
    result = Scraper().scrape_url(target)
    print(f"{len(result.issues)} colour fixes, {len(result.modified_ids)} elements touched")
    for entry in result.issues:
        print("   ", entry)
