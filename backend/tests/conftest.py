"""Shared test setup. Gemini is always stubbed, so no test uses a real key.

Also provides a fake model reply, a local fixture server and a switch to allow private hosts.
"""

import os
import subprocess
import sys
import time

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BACKEND_ROOT, "tests", "fixtures")
sys.path.insert(0, BACKEND_ROOT)

# Set before backend/.env is loaded (dotenv never overrides), so a real key is never used.
for _name in ("GEMINI_API_KEY", "GEMAPI"):
    os.environ[_name] = ""

from accessai.core import nethttp                                  # noqa: E402

FIXTURE_PORT = 8791
FIXTURE_BASE = f"http://localhost:{FIXTURE_PORT}"


@pytest.fixture(scope="session", autouse=True)
def stub_model():
    """Session-scoped so session fixtures that scrape get the stubs too."""
    from accessai.core import contrast, scraper

    patch = pytest.MonkeyPatch()
    patch.setattr(scraper, "describe_images",
                  lambda srcs, failures=None: ["a stubbed description"] * len(srcs))
    patch.setattr(scraper, "label_fields", lambda fields, failures=None: ["Stub Label"] * len(fields))
    # None forces the deterministic colour path.
    patch.setattr(contrast, "suggest_text_colors", lambda pairs, failures=None: [None] * len(pairs))
    yield
    patch.undo()


@pytest.fixture
def allow_private(monkeypatch):
    """Let this test fetch from localhost."""
    monkeypatch.setenv(nethttp.ALLOW_PRIVATE_ENV, "1")


@pytest.fixture
def model_reply(monkeypatch):
    """Set the model's reply (or an Exception); returns the (contents, schema) requests."""
    from accessai.core import gemini

    monkeypatch.setattr(gemini, "ai_enabled", lambda: True)
    requests_seen = []

    def _set(reply):
        def fake_generate(contents, schema):
            requests_seen.append((contents, schema))
            answer = reply(contents) if callable(reply) else reply
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(gemini, "_generate", fake_generate)
        return requests_seen

    return _set


@pytest.fixture(scope="session")
def fixture_server():
    """Serve tests/fixtures over HTTP."""
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(FIXTURE_PORT)],
        cwd=FIXTURES_DIR,
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
