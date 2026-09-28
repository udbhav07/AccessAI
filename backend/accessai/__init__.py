"""AccessAI backend. `create_app` builds the Flask app: settings, CORS, rate limiters,
error handlers, security headers and the /api routes. The engine itself is in `accessai.core`.
"""

import logging

from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from .api import api
from .api.errors import register_error_handlers
from .api.rate_limit import RateLimiter
from .config import Config
from .core import gemini, runstore


def create_app(config=None):
    config = config or Config.from_env()

    app = Flask(__name__)
    app.config["ACCESSAI"] = config
    app.json.sort_keys = False

    if config.trusted_proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.trusted_proxies,
                                x_proto=config.trusted_proxies)

    runstore.RUNS_DIR = config.runs_dir
    # The sweep otherwise only runs on write, so clear leftovers at startup.
    runstore.sweep()

    app.extensions["scan_limiter"] = RateLimiter(config.scans_per_hour, window=3600)
    app.extensions["verify_limiter"] = RateLimiter(config.verifications_per_hour, window=3600)

    CORS(app, resources={r"/api/*": {"origins": config.cors_origins}},
         expose_headers=["Content-Disposition"], max_age=3600)
    register_error_handlers(app)
    app.register_blueprint(api)
    app.after_request(_security_headers)

    if not gemini.ai_enabled():
        # Fallback output looks like model output, so make this visible.
        logging.getLogger(__name__).warning(
            "GEMINI_API_KEY is not set: alt text and labels are skipped and "
            "colours use the deterministic fallback. See backend/.env.example."
        )

    return app


def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    return response
