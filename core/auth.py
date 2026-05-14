"""Authentication helpers used by every protected route.

Two ways to log in live in this project:

1. **Session login** — used by humans through the browser. The
   ``/login`` page validates against the user store
   (:mod:`core.users`), then sets ``session["user"]``,
   ``session["rol"]`` and ``session["tabs"]``. Every protected
   route checks the session via :func:`ui_login_required`.
2. **HTTP Basic Auth** — used by the daemon when it polls
   ``/api/effectief-rooster``. The daemon sends a username and
   password header on every request; ``flask_httpauth`` checks
   them through the ``auth`` instance below, which delegates to
   :func:`core.users.verify_user` and additionally requires the
   admin role.

The two ways are intentionally separate. A browser user gets a
nice redirect to /login when not signed in. The daemon (which
can't render an HTML login page) gets a 401 with a
``WWW-Authenticate`` header so requests can retry.

This module also exposes :func:`require_admin` for API endpoints
that the browser calls with ``fetch()``. Instead of redirecting
(which fetch() would silently follow), it returns ``401`` /
``403`` JSON so the JS can show a useful error.

Pre-multi-user installs only had ``SCHOOLBELL_WEB_USER`` /
``SCHOOLBELL_WEB_PWHASH`` in ``/etc/schoolbell/web.env``. Those
values are still read at import time and used by
:func:`core.users.bootstrap_from_env` to seed ``data/users.json``
on first start, so existing installations keep working without any
manual migration.
"""

import os
import secrets
from functools import wraps

from flask import abort, jsonify, redirect, request, session, url_for
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash

from core import users as users_mod


# Legacy admin credentials read from environment at import time. Both
# are expected to be set by /etc/schoolbell/web.env in production. They
# are now only used to seed users.json on first start
# (see core.users.bootstrap_from_env) — after that, the user store is
# the canonical source of truth.
#
# FALLBACK_PLAIN is the one-time bootstrap state during a fresh
# install.sh run. Once a password hash exists, SCHOOLBELL_WEB_PASS is
# expected to be removed from the env file.
ADMIN_USER = os.getenv("SCHOOLBELL_WEB_USER", "admin")
ADMIN_HASH = os.getenv("SCHOOLBELL_WEB_PWHASH")      # e.g. pbkdf2:sha256:...
FALLBACK_PLAIN = os.getenv("SCHOOLBELL_WEB_PASS")    # only for first test


# Single HTTPBasicAuth instance for the whole app. Routes that need
# Basic Auth use ``@auth.login_required``. The verifier registered
# below is what flask_httpauth calls to check the username/password.
auth = HTTPBasicAuth()


# ---- UI login (session-cookie based) ----------------------------


def _check_password(username: str, plain: str) -> bool:
    """Backwards-compatible wrapper around :func:`core.users.verify_user`.

    The single-arg ``_check_password(plain)`` form from before
    multi-user is gone; callers must now pass the username they want
    to verify. The implementation is a thin pass-through: any new
    code should call :func:`core.users.verify_user` directly so it
    can read back the user's role and tabs in the same call.
    """
    return users_mod.verify_user(username, plain) is not None


def ui_logged_in() -> bool:
    """True when the current request carries a logged-in session.

    Multi-user aware: the presence of ``session["user"]`` is enough,
    we no longer compare against a single admin name. Role-based
    checks (e.g. "is the user an admin?") use :func:`require_admin`
    or directly inspect ``session.get("rol")``.
    """
    return bool(session.get("user"))


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


def tab_required(tab_naam: str):
    """Decorator factory: gate a route on a specific tab key.

    Behaviour matrix:

    - Anonymous visitor:
        * HTML route  -> redirect to /login?next=... (so the browser
                         lands back on the original page after login).
        * API route   -> 401 JSON ``{"error": "auth_required"}``.
    - Logged-in user without the tab:
        * HTML route  -> 403 (a plain Flask abort; later we can swap
                         this for a custom "no access" template).
        * API route   -> 403 JSON ``{"error": "tab_required"}``.
    - Admins always pass: an admin record carries ``tabs == ["*"]``
      from the user store, and ``"*"`` is treated as "every tab".

    Detection of API routes: we look at ``request.path``. Anything
    under ``/api/`` is treated as a fetch() / programmatic call, so
    it gets a JSON body instead of an HTTP redirect (which fetch()
    would silently follow, hiding the auth failure from the page's
    JavaScript).

    Usage::

        @blueprint.route("/agenda")
        @tab_required("agenda")
        def agenda():
            ...
    """
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            is_api = request.path.startswith("/api/")
            if not ui_logged_in():
                if is_api:
                    return jsonify(error="auth_required"), 401
                nxt = (
                    request.full_path
                    if request.query_string
                    else request.path
                )
                return redirect(url_for("auth.login", next=nxt))
            tabs = session.get("tabs") or []
            if "*" in tabs or tab_naam in tabs:
                return view(*args, **kwargs)
            if is_api:
                return jsonify(error="tab_required"), 403
            abort(403)
        return wrapper
    return deco


def require_admin(f):
    """Decorator for JSON API endpoints that need a logged-in admin.

    Returns a JSON 401 (no session) or 403 (logged in but not admin)
    instead of redirecting to /login, so that fetch() callers in the
    UI get a machine-readable response. A redirect would be silently
    followed by fetch() and the page's JavaScript would never see
    the auth failure.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ui_logged_in():
            return jsonify(error="auth_required"), 401
        if session.get("rol") != "admin":
            return jsonify(error="admin_required"), 403
        return f(*args, **kwargs)
    return wrapper


# ---- HTTP Basic Auth (used only by the daemon) ------------------


@auth.verify_password
def verify_password(username, password):
    """Verifier the daemon's HTTP Basic Auth requests are checked against.

    Per the multi-user design (multi-user-plan.md §3.4): the daemon
    authenticates against any admin account in users.json. A regular
    (non-admin) user — even one that happens to have the ``roosters``
    tab — cannot use this path; the daemon conceptually belongs to
    the admin role.

    The legacy env-var admin (``SCHOOLBELL_WEB_USER`` /
    ``SCHOOLBELL_WEB_PWHASH`` / ``SCHOOLBELL_WEB_PASS``) is honoured
    as an emergency fallback so a freshly-installed Pi still works
    before the bootstrap has run, and so an admin who deletes
    ``users.json`` for rescue can still get back in.
    """
    user = users_mod.verify_user(username, password)
    if user is not None and users_mod.is_admin(user):
        return True

    # Env-var fallback: only the configured admin name reaches here.
    if username != ADMIN_USER:
        return False
    if ADMIN_HASH:
        return check_password_hash(ADMIN_HASH, password)
    if FALLBACK_PLAIN:
        return password == FALLBACK_PLAIN  # only for first test
    return False
