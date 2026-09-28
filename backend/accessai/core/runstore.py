"""Keeps each scan on disk for an hour (before.html, after.html, meta.json) so it can be
verified or downloaded later. Files are shared across worker processes; old runs are
swept by age and total size.
"""

import json
import os
import shutil
import time
import uuid

# backend/runs; the app factory overrides it from the RUNS_DIR env var.
RUNS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "runs",
)
MAX_AGE_SECONDS = 3600
# Each run stores two full page copies, so cap total size as well as age.
MAX_TOTAL_BYTES = 500_000_000


def new_run_id():
    return uuid.uuid4().hex


def is_writable():
    """True if new runs can be saved."""
    try:
        os.makedirs(RUNS_DIR, exist_ok=True)
    except OSError:
        return False
    return os.access(RUNS_DIR, os.W_OK)


def _run_path(run_id):
    # run_id comes from the request URL; refuse anything outside RUNS_DIR.
    if not run_id or not run_id.isalnum():
        return None
    path = os.path.abspath(os.path.join(RUNS_DIR, run_id))
    if os.path.dirname(path) != os.path.abspath(RUNS_DIR):
        return None
    return path


def save_run(run_id, before_html, after_html, meta):
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


def update_meta(run_id, **fields):
    """Merge `fields` into a saved run's metadata. False if the run is gone."""
    path = _run_path(run_id)
    if path is None or not os.path.isdir(path):
        return False
    meta_path = os.path.join(path, "meta.json")
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta.update(fields)
        # Atomic replace so a concurrent load_run never sees a partial file.
        tmp_path = f"{meta_path}.{uuid.uuid4().hex}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        os.replace(tmp_path, meta_path)
    except (OSError, ValueError):
        return False
    return True


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
    if time.time() - meta.get("created_at", 0) > MAX_AGE_SECONDS:
        return None
    return before, after, meta


def _run_dirs():
    """Every run on disk as (path, created, bytes), oldest first."""
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
            # before.html is written once; the directory changes when a report is saved.
            created = os.path.getmtime(os.path.join(path, "before.html"))
            found.append((path, created, size))
        except OSError:
            continue
    found.sort(key=lambda entry: entry[1])
    return found


def sweep(max_age=MAX_AGE_SECONDS, max_total=MAX_TOTAL_BYTES):
    """Evict runs past max_age, then oldest first until under max_total bytes."""
    if not os.path.isdir(RUNS_DIR):
        return

    cutoff = time.time() - max_age
    surviving = []
    for path, created, size in _run_dirs():
        if created < cutoff:
            shutil.rmtree(path, ignore_errors=True)
        else:
            surviving.append((path, created, size))

    total = sum(size for _, _, size in surviving)
    for path, _, size in surviving:
        if total <= max_total:
            break
        shutil.rmtree(path, ignore_errors=True)
        total -= size
