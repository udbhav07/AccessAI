# 🌐 [AccessAI-AG33](https://accessai-r6or.onrender.com)

Enhancing web accessibility with AI-driven solutions! 🚀

## 📖 Overview

**AccessAI-AG33** takes the URL of a web page, fixes three common accessibility
problems in it, and then **checks its own work** — comparing the repaired page
against the original, element by element, in a real browser.

That last part is the unusual bit. An automated fixer that cannot tell you
whether it broke the page is not much use, so roughly half of this project is
the verifier.

## ✨ What it fixes

- 🖼️ **Alt text for images** — generates a short description for any `<img>`
  with a missing or empty `alt`. If the model cannot describe one, the image is
  left alone rather than given a placeholder: a screen reader announcing
  "Image description not available" on every image is worse than silence.
- 📝 **Form labels** — writes a `<label>` for inputs that have an `id`, and an
  `aria-label` for those that do not. Inputs that are already named — including
  by a wrapping `<label>` — are left as they are.
- 🎨 **Colour contrast** — finds text that fails the WCAG AA ratio (4.5:1, or
  3:1 for large text) and picks a replacement colour that passes. Covers all
  four places colour lives: inline `style`, legacy attributes like `bgcolor`,
  `<style>` blocks, and linked stylesheets.

The model proposes a colour; the WCAG maths decides. Any suggestion that does
not actually clear the threshold is thrown away and replaced by a deterministic
walk toward black or white, so nothing is ever written back that still fails.

### What it deliberately will not do

Guess. Where the real rendered colour cannot be determined from the markup —
text over a background image or gradient, or a colour set in a stylesheet the
inline pass cannot resolve — the fixer **abstains and says so** in the report,
rather than measuring against a colour that is not on screen. A skipped fix is
honest; a wrong one can leave a page less readable than it was found.

## ✅ Verification

After a scan, **Verify fixes** renders the before and after versions in headless
Chromium and compares them *per element*, matched by a `data-aai-id` stamped on
every element before any fixer ran.

A single "how different do these two pictures look" percentage cannot work here:
adding `alt` changes nothing visually, inserting a `<label>` changes the layout
on purpose, and recolouring text changes pixels on purpose. So there are seven
checks in three tiers:

| Check | Tier | Asks |
|---|---|---|
| Layout | blocking | did anything change size? (width and height, never `y`) |
| Visibility | blocking | did anything visible disappear? |
| Colour | blocking | did colour leak onto elements we never touched? |
| Contrast | objective | did the colours we changed come out right, and did anything regress? |
| Coverage | objective | did the alt and label fixes actually apply? |
| Remaining | advisory | what is still below threshold that this run could not reach |
| Pixels | advisory | per-element crop comparison outside the modified regions |

The verdict is a rule, not an average — a healthy average is exactly how a
vanished element hides:

- **PASS** — everything held.
- **REVIEW** — only advisory checks flagged something.
- **INCOMPLETE** — a fix did not land, or contrast regressed.
- **BROKEN** — the page itself changed. Do not ship this.
- **ERROR** — the page could not be rendered. Coverage is still reported, so a
  browser problem degrades the report rather than faking a pass.

## 🛠️ Tech stack

- **Backend** — Python, Flask
- **Scraping** — `requests` + BeautifulSoup; `cssutils` for stylesheets
- **AI** — Gemini, for alt text, label wording and colour suggestions
- **Verification** — Playwright (Chromium), Pillow and NumPy
- **Frontend** — HTML, CSS, Bootstrap

## 🚀 Getting started

### 1️⃣ Install

```bash
git clone https://github.com/RushiVivek/AccessAI-AG33.git
cd AccessAI-AG33

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
python -m playwright install chromium
```

The Playwright step is not optional if you want verification — without a
browser, every **Verify** returns the `ERROR` verdict.

### 2️⃣ Add an API key

```bash
cp googleapikey.env.example googleapikey.env
```

Then put a [Google AI Studio](https://aistudio.google.com/apikey) key in it as
`GEMAPI`. The app runs without one, but every model call falls back: images are
skipped, labels are left alone, and colours come from the deterministic walk.
The page shows a banner when this is the case, so you never mistake fallback
output for model output.

### 3️⃣ Run

```bash
python app.py
```

Then open http://localhost:5000. Enter a URL, and you get:

- a preview of the repaired page,
- a table of every change, with the contrast ratio before and after,
- **Verify fixes**, and
- **Download HTML**, which hands back the repaired page with the verifier's
  stamps removed.

Set `FLASK_DEBUG=1` for the reloader. It is off by default on purpose —
Werkzeug's debugger is an interactive console, and shipping it enabled is
remote code execution.

## 🧪 Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium
python -m pytest
```

201 tests. No API key needed and no network calls — the Gemini SDK is stubbed
in `tests/conftest.py`. The verifier tests do drive a real headless browser
against fixtures in `tests/fixtures/`, served from a local HTTP server.

Worth knowing about: the suite deliberately breaks the demo page seven ways —
hiding an element, removing one, resizing one, recolouring one without
declaring it, introducing a contrast regression, stripping an `alt`, and
injecting a script that tries to empty the page — and asserts the checks catch
each one. A verifier that never fails is not verifying anything.

## 🔒 Notes for anyone deploying this

The app fetches whatever URL it is given, so every outbound request goes
through `src/nethttp.py`, which refuses non-HTTP schemes and any host that
resolves to a loopback, private, link-local or reserved address, re-checking on
every redirect. Do not set `ACCESSAI_ALLOW_PRIVATE_HOSTS` on a deployment —
that switch exists so the test fixtures on localhost can be reached.

Scans are capped at 20 per hour per caller, and the run store is bounded by
both age and total size. `render.yaml` and `Procfile` carry the deploy
contract, including the Chromium install.

## 🌱 Future enhancements

- 🌍 **Multi-language support** for analysing pages in other languages.
- ⚡ **Dynamic content** — SPAs render under JavaScript, which the verifier
  deliberately blocks, so those pages currently verify as near-empty.
- 🎯 **Browser-driven colour detection** — computing colour with the browser
  that is already running for verification would remove every case where the
  fixer currently has to abstain.
- 📜 **Wider WCAG coverage** beyond contrast, alt text and labels.
- 🔄 **User feedback** — let people accept, reject or edit individual fixes.

## 🤝 Contributing

1. 🍴 Fork the repository.
2. 🛠️ Make your changes in a new branch.
3. ✅ Run `python -m pytest` — it should stay green.
4. 🔄 Submit a pull request!

## 📧 Contact

For any questions, feedback, or suggestions, feel free to reach out:  
✉️ **[udbhavsai.k@gmail.com](mailto:udbhavsai.k@gmail.com)**
✉️ **[b.abhi2790@gmail.com](mailto:b.abhi2790@gmail.com)**

## 📷 Screenshots

1. **Improved Alt Text for Images**  
   <img src="/DemoImages/AltBefore.png?raw=true" width=400px><img src="/DemoImages/AltAfter.png?raw=true" width=400px>

2. **Proper Labels for Inputs**  
   <img src="/DemoImages/LabelBefore.png?raw=true" width=400px><img src="/DemoImages/LabelAfter.png?raw=true" width=400px>

3. **Color Contrast Enhancements**  
   <img src="/DemoImages/ContrastBefore.png?raw=true" width=400px><img src="/DemoImages/ContrastAfter.png?raw=true" width=400px>

🎉 **Thank you for using AccessAI-AG33! Together, we can make the web a more inclusive place.**
