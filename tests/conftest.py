"""Shared setup for the test suite.

The three fake-Gemini test files used to each drop their own module into
`sys.modules["src.gemini"]` before importing anything. That works when every
file is its own process, but under pytest they share one -- and test_gemini
needs the *real* module, so whichever imported first would win.

So nothing replaces `src.gemini` any more. The SDK underneath it is stubbed
instead, which lets the real module import with no key and no network, and
individual tests patch the function they care about. That has to be done on
the module that *binds* the name: `webScraper` does `from .gemini import
getAlt`, so patching `src.gemini.getAlt` would have no effect on it.
"""

import os
import subprocess
import sys
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# --- stub the Gemini SDK before src.gemini is imported ----------------------

class FakeModel:
    """Returns whatever `reply` is set to, or raises it if it is an exception."""

    reply = None

    def __init__(self, *args, **kwargs):
        pass

    def generate_content(self, *args, **kwargs):
        if isinstance(FakeModel.reply, Exception):
            raise FakeModel.reply
        return types.SimpleNamespace(text=FakeModel.reply)


_genai = types.ModuleType("google.generativeai")
_genai.configure = lambda **kwargs: None
_genai.GenerativeModel = FakeModel

_google = sys.modules.get("google") or types.ModuleType("google")
_google.generativeai = _genai
sys.modules["google"] = _google
sys.modules["google.generativeai"] = _genai

_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *args, **kwargs: None
sys.modules["dotenv"] = _dotenv

from src import nethttp                                          # noqa: E402

FIXTURE_PORT = 8791
FIXTURE_BASE = f"http://localhost:{FIXTURE_PORT}/tests/fixtures"


@pytest.fixture(scope="session", autouse=True)
def stub_model():
    """Keep every test off the network and away from a real model.

    Patched where the names are bound, not where they are defined: webScraper
    does `from .gemini import getAlt`, so patching src.gemini would not reach
    it.

    Session-scoped on purpose. pytest builds higher-scoped fixtures first, so
    a function-scoped version of this would be applied *after* any
    session-scoped fixture that scrapes -- which silently gave that fixture
    the real functions and a page with no fixes in it. A test that wants
    different behaviour still overrides these with the ordinary
    function-scoped `monkeypatch`.
    """
    from src import contrast, webScraper

    patch = pytest.MonkeyPatch()
    patch.setattr(webScraper, "getAlt", lambda src: "a stubbed description")
    patch.setattr(webScraper, "getLabel", lambda inp, label="": "Stub Label")
    # None forces the deterministic colour path, which is what makes the
    # colour assertions reproducible.
    patch.setattr(contrast, "suggest_text_color", lambda fg, bg: None)
    yield
    patch.undo()


@pytest.fixture
def allow_private(monkeypatch):
    """Let this test fetch from localhost.

    Deliberately opt-in per test rather than set once for the suite: the
    nethttp tests exist to prove the guard refuses private addresses, and a
    blanket opt-in would turn every one of them green for the wrong reason.
    """
    monkeypatch.setenv(nethttp.ALLOW_PRIVATE_ENV, "1")


@pytest.fixture
def model_reply():
    """Set the fake model's answer: `model_reply("[Email]")`, or pass an Exception."""
    from src import gemini

    def _set(text):
        FakeModel.reply = text
        gemini.model = FakeModel()
        return gemini

    yield _set
    FakeModel.reply = None


@pytest.fixture(scope="session")
def fixture_server():
    """Serve the project root so the HTML fixtures and their images resolve.

    Served from the root rather than the fixture directory because the demo
    page's images live at ../DemoImages -- and a broken image renders at 16px
    without alt text but expands once alt is added, which is a real layout
    change that has nothing to do with the code under test.
    """
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(FIXTURE_PORT)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            nethttp.requests.get(f"{FIXTURE_BASE}/experiment.html", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        server.terminate()
        pytest.fail("fixture server did not come up")

    yield FIXTURE_BASE

    server.terminate()
    server.wait(timeout=5)
