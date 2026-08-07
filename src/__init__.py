"""AccessAI source package.

The Flask entry point (`app.py`) stays at the project root so Flask resolves
`templates/` and `static/` relative to it, and so deployment can keep running
`gunicorn app:app` unchanged.
"""

import os

# Absolute path to the project root, so modules in here can reach files that
# live beside app.py (the .env, the run store) regardless of the working
# directory the app or the tests happen to be launched from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
