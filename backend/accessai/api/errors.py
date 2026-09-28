"""API errors. Routes raise `ApiError`; every error, expected or not, is returned as
{"error": {"code": ..., "message": ...}} without leaking internals.
"""

from flask import current_app, jsonify
from werkzeug.exceptions import HTTPException


class ApiError(Exception):
    def __init__(self, status, code, message, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def error_response(status, code, message, headers=None):
    response = jsonify({"error": {"code": code, "message": message}})
    response.status_code = status
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def handle_api_error(exc):
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        code = (exc.name or "error").lower().replace(" ", "_")
        return error_response(exc.code or 500, code, exc.description or exc.name)

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        # Log the traceback; don't leak internals to the client.
        current_app.logger.exception("unhandled error")
        return error_response(500, "internal_error", "Something went wrong on our side.")
