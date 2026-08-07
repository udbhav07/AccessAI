import requests
from collections import namedtuple
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .gemini import getAlt, getLabel
from .webColorss import ChangeColor

# Stamped on every element before any fixer runs, so the verifier can match
# old -> new. A selector path like `div:nth-child(3) > input` cannot be used:
# inserting a <label> shifts the nth-child index of every following sibling,
# so the fix would invalidate its own identity scheme.
ID_ATTR = "data-aai-id"
NEW_ATTR = "data-aai-new"

ScrapeResult = namedtuple(
    "ScrapeResult", "url soup issues html_before modified_ids"
)


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
        req = requests.get(url)
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

        Every change is recorded so the verifier knows this element was
        modified on purpose -- an alt attribute is invisible on a loading
        image, but a *broken* image renders its alt text and therefore
        changes size.
        """
        issues = []
        for img in self.soup.find_all('img'):
            if not img.get('alt') or img.get('alt') == '':
                img_src = img.get('src')
                if img_src:
                    #if src is relative make it absolute
                    if img_src[0:7] != "http://" and img_src[0:8] != "https://":
                        img_src = urljoin(self.url, img_src)

                    #get alt text here
                    imgAlt = getAlt(img_src)
                    img['alt'] = imgAlt
                    issues.append({
                        "type": "alt", "source": "image", "target": img.get('src'),
                        "old": "", "new": imgAlt, "ids": [img.get(ID_ATTR)],
                    })
        return issues

    def get_label(self):
        """Add missing labels and replace ones that don't describe their input."""
        issues = []
        for inp in self.soup.find_all('input'):
            if not inp.get('id'): #labeling happens for imputs which have ids , so if id is not there ,labeling not possible
                continue
            label = self.soup.find('label', attrs={'for': inp['id']})
            gemLabel = getLabel(inp, label)
            if gemLabel == 'y':
                continue

            if label is None:          # `not label` is falsy for an EMPTY <label></label>
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


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/experiment.html"
    result = Scraper().scrape_url(target)
    print(f"{len(result.issues)} colour fixes, {len(result.modified_ids)} elements touched")
    for entry in result.issues:
        print("   ", entry)
