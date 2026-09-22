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

# The identity scheme every part of the pipeline depends on. Stamped on each
# element by the scraper before any fixer runs, and read back by the verifier
# to match old to new. Defined once here because both halves need it and two
# copies of a constant this load-bearing is a drift waiting to happen.
#
# A selector path like `div:nth-child(3) > input` cannot serve instead:
# inserting a <label> shifts the nth-child index of every following sibling,
# so the fix would invalidate its own identity scheme.
ID_ATTR = "data-aai-id"

# Set on a node the fixer inserted, so it is not read back as a mystery
# element that appeared from nowhere.
NEW_ATTR = "data-aai-new"
