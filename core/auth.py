"""Authentication helpers used by every protected route.

Two ways to log in live in this project:

1. **Session login** — used by humans through the browser. The
   ``/login`` page sets ``session["user"]`` and every protected
   route checks it via the ``ui_login_required`` decorator.
2. **HTTP Basic Auth** — used by the daemon when it polls
   ``/api/effectief-rooster``. The daemon sends a username and
   password header on every request; ``flask_httpauth`` checks
   them through the ``auth`` instance below.

The two ways are intentionally separate. A browser user gets a
nice redirect to /login when not signed in. The daemon (which
can't render an HTML login page) gets a 401 with a
``WWW-Authenticate`` header so requests can retry.

This module also exposes ``require_admin`` for API endpoints that
the browser calls with ``fetch()``. Instead of redirecting (which
fetch() would silently follow), it returns ``401 {"error": ...}``
so the JS can show a useful error.
"""

import os
import secrets
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash


# Admin credentials read from environment at import time. Both are
# expected to be set by /etc/schoolbell/web.env in production.
# FALLBACK_PLAIN is for the very first install only. Once a
# password hash is generated, SCHOOLBELL_WEB_PASS should be removed.
ADMIN_USER = os.getenv("SCHOOLBELL_WEB_USER", "admin")
ADMIN_HASH = os.getenv("SCHOOLBELL_WEB_PWHASH")      # e.g. pbkdf2:sha256:...
FALLBACK_PLAIN = os.getenv("SCHOOLBELL_WEB_PASS")    # only for first test


# Single HTTPBasicAuth instance for the whole app. Routes that need
# Basic Auth use ``@auth.login_required``. The verifier registered
# below is what flask_httpauth calls to check the username/password.
auth = HTTPBasicAuth()


# ---- UI login (session-cookie based) ----------------------------


def _check_password(plain: str) -> bool:
    """Compare ``plain`` against the stored admin credential.

    Tries the secure hash first. Only falls back to the plaintext
    SCHOOLBELL_WEB_PASS when no hash is set, which is a one-time
    bootstrap state. install.sh generates a hash on the first
    run and clears the plaintext.
    """
    if ADMIN_HASH:
        return check_password_hash(ADMIN_HASH, plain)
    if FALLBACK_PLAIN:
        return plain == FALLBACK_PLAIN
    return False


def ui_logged_in() -> bool:
    """True when the current request has a valid admin session cookie."""
    return session.get("user") == ADMIN_USER


def ui_login_required(view):
    """Decorator that sends anonymous browsers to /login.

    Wraps a view function so that, when the visitor isn't logged
    in, Flask responds with a redirect to ``/login?next=...``.
    The ``next`` parameter remembers where the user was going so
    the login page can bounce them back after a successful sign-in.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if ui_logged_in():
            return view(*args, **kwargs)
        # remember where we need to return to
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.login", next=nxt))
    return wrapper


def get_csrf_token() -> str:
    """Return the CSRF token for the current session, creating one if needed.

    Each visitor gets a single random token stored in their session
    cookie. Forms include it as ``_csrf``. fetch() calls send it as
    the ``X-CSRF-Token`` header. ``csrf_protect`` (registered on the
    Flask app in webinterface.py) checks the value on every POST.
    """
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok


def require_admin(f):
    """Decorator for JSON API endpoints that need a logged-in admin.

    Returns a JSON 401 instead of redirecting to /login, so that
    fetch() callers in the UI get a machine-readable response. A
    redirect would be silently followed by fetch() and the page's
    JavaScript would never see the auth failure.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ui_logged_in():
            return jsonify(error="auth_required"), 401
        return f(*args, **kwargs)
    return wrapper


# ---- HTTP Basic Auth (used only by the daemon) ------------------


@auth.verify_password
def verify_password(username, password):
    """Verifier the daemon's HTTP Basic Auth requests are checked against.

    Same admin credentials as the session login. The daemon and
    the browser user are conceptually the same admin, just talking
    to the app over different protocols.
    """
    if username != ADMIN_USER:
        return False
    if ADMIN_HASH:
        return check_password_hash(ADMIN_HASH, password)
    if FALLBACK_PLAIN:
        return password == FALLBACK_PLAIN  # only for first test
    return False
