"""Persist a completed scrape so it can be verified later.

Verification needs the before-HTML, the after-HTML and the set of touched
element ids -- all produced during the POST to `/` and otherwise discarded.
Re-running the scrape to rebuild them is not an option: Gemini is
non-deterministic, so a second run produces different alt text and different
colour choices, and you would end up verifying a page the user never saw (at
double the API cost).

Plain files rather than an in-memory dict, so a run survives across worker
processes.
"""

import json
import os
import shutil
import time
import uuid

from . import PROJECT_ROOT

# Beside app.py rather than inside the package, so the source tree stays
# clean and the .gitignore `runs/` rule keeps matching.
RUNS_DIR = os.path.join(PROJECT_ROOT, "runs")
MAX_AGE_SECONDS = 3600
# Each run holds two full copies of a third-party page, so a burst of large
# pages can fill a disk well inside the hour the age limit allows. The age
# rule alone also collects nothing while the app sits idle, so whatever the
# last burst left behind stays until someone scans again.
MAX_TOTAL_BYTES = 500_000_000


def new_run_id():
    return uuid.uuid4().hex


def _run_path(run_id):
    """Resolve a run directory, refusing anything that escapes RUNS_DIR.

    `run_id` arrives from a form field, so it is untrusted input -- without
    this a crafted value could read or overwrite files elsewhere on disk.
    """
    if not run_id or not run_id.isalnum():
        return None
    path = os.path.abspath(os.path.join(RUNS_DIR, run_id))
    if os.path.dirname(path) != os.path.abspath(RUNS_DIR):
        return None
    return path


def save_run(run_id, before_html, after_html, meta):
    """Write one run to disk and sweep any stale ones."""
    path = _run_path(run_id)
    if path is None:
        raise ValueError(f"invalid run id: {run_id!r}")
    os.makedirs(path, exist_ok=True)

    with open(os.path.join(path, "before.html"), "w", encoding="utf-8") as fh:
        fh.write(before_html)
    with open(os.path.join(path, "after.html"), "w", encoding="utf-8") as fh:
        fh.write(after_html)

    payload = dict(meta)
    payload.setdefault("created_at", time.time())
    payload["modified_ids"] = sorted(payload.get("modified_ids") or [])
    with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    sweep()


def load_run(run_id):
    """Return ``(before_html, after_html, meta)`` or None if it is gone."""
    path = _run_path(run_id)
    if path is None or not os.path.isdir(path):
        return None
    try:
        with open(os.path.join(path, "before.html"), encoding="utf-8") as fh:
            before = fh.read()
        with open(os.path.join(path, "after.html"), encoding="utf-8") as fh:
            after = fh.read()
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    return before, after, meta


def _run_dirs():
    """Every run on disk as (path, mtime, bytes), newest last."""
    found = []
    for name in os.listdir(RUNS_DIR):
        path = os.path.join(RUNS_DIR, name)
        if not os.path.isdir(path):
            continue
        try:
            size = sum(
                os.path.getsize(os.path.join(path, f))
                for f in os.listdir(path)
                if os.path.isfile(os.path.join(path, f))
            )
            found.append((path, os.path.getmtime(path), size))
        except OSError:
            continue
    found.sort(key=lambda entry: entry[1])
    return found


def sweep(max_age=MAX_AGE_SECONDS, max_total=MAX_TOTAL_BYTES):
    """Keep `runs/` bounded by age and by total size, oldest evicted first."""
    if not os.path.isdir(RUNS_DIR):
        return

    cutoff = time.time() - max_age
    surviving = []
    for path, mtime, size in _run_dirs():
        if mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
        else:
            surviving.append((path, mtime, size))

    total = sum(size for _, _, size in surviving)
    for path, _, size in surviving:          # already oldest-first
        if total <= max_total:
            break
        shutil.rmtree(path, ignore_errors=True)
        total -= size
