"""App settings from the environment or backend/.env: CORS origins, rate limits,
trusted proxies, the runs folder and debug mode.

GEMINI_* and ACCESSAI_ALLOW_PRIVATE_HOSTS are read per call in core instead.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Vite dev server
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _list(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    # Must include the production frontend's URL.
    cors_origins: list = field(default_factory=lambda: list(DEFAULT_ORIGINS))
    scans_per_hour: int = 20
    verifications_per_hour: int = 30
    # 1 on Render. Too low and everyone shares the proxy's rate-limit
    # bucket; too high and clients can spoof X-Forwarded-For.
    trusted_proxies: int = 0
    runs_dir: str = os.path.join(BACKEND_ROOT, "runs")
    debug: bool = False

    @classmethod
    def from_env(cls):
        load_dotenv(os.path.join(BACKEND_ROOT, ".env"))
        return cls(
            cors_origins=_list("CORS_ORIGINS", DEFAULT_ORIGINS),
            scans_per_hour=max(0, _int("SCANS_PER_HOUR", 20)),
            verifications_per_hour=max(0, _int("VERIFICATIONS_PER_HOUR", 30)),
            trusted_proxies=max(0, _int("TRUSTED_PROXIES", 0)),
            runs_dir=os.environ.get("RUNS_DIR") or os.path.join(BACKEND_ROOT, "runs"),
            debug=_flag("FLASK_DEBUG"),
        )
