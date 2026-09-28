"""The /api blueprint. Importing `routes` registers the endpoints on it."""

from flask import Blueprint

api = Blueprint("api", __name__, url_prefix="/api")

from . import routes  # noqa: E402,F401
