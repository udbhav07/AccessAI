"""Entry point. Production: `gunicorn wsgi:app`. Local: `python wsgi.py` (port 8000),
which restarts on changes to Python files or backend/.env."""

import os

from accessai import create_app
from accessai.config import BACKEND_ROOT

app = create_app()

if __name__ == "__main__":
    # The Werkzeug debugger allows remote code execution; local use only.
    # The reloader is separate from the debugger and safe to keep on.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)),
            debug=app.config["ACCESSAI"].debug,
            use_reloader=True,
            extra_files=[os.path.join(BACKEND_ROOT, ".env")])
