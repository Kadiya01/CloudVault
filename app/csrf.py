"""Minimal CSRF protection for CloudVault.

Implements synchroniser-token CSRF protection using Flask's signed session and
the standard library only. No additional dependency is introduced.
"""

import hmac
import secrets

from flask import abort, jsonify, request, session

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def generate_csrf_token():
    """Return the session CSRF token, creating it on first use."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf():
    """Reject a missing or invalid CSRF token with HTTP 400.

    Normal HTML requests abort with the standard 400 page; ``/api/`` requests
    receive a JSON error. The token may arrive as a form field or in the
    ``X-CSRF-Token`` header (checked first so binary bodies are not parsed).
    """
    expected = session.get(CSRF_SESSION_KEY)
    submitted = request.headers.get(CSRF_HEADER_NAME)
    if not submitted:
        submitted = request.form.get(CSRF_FORM_FIELD)
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        if request.path.startswith("/api/"):
            return jsonify(error="Invalid or missing CSRF token."), 400
        abort(400, description="Invalid or missing CSRF token.")
    return None


def protect_state_changing_requests():
    """before_request hook validating CSRF on state-changing methods."""
    if request.method in PROTECTED_METHODS:
        return validate_csrf()
    return None
