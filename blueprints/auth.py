"""Blueprint for the session-cookie login flow.

Two routes:

  GET/POST /login   — show the form / accept credentials
  GET/POST /logout  — clear the session cookie

The actual credential check lives in :mod:`core.users` (via
:func:`core.users.verify_user`); this file is only the HTTP wiring
around it. After a successful login the session carries three keys:

  * ``user`` — the lowercased username
  * ``rol``  — ``"admin"`` or ``"gebruiker"``
  * ``tabs`` — list of tab keys the user may access, or ``["*"]``
                for admins

Logout supports both GET (admin typing /logout into the address bar)
and POST (the nav-bar form with a CSRF token); both end up doing the
same ``session.clear()``.
"""

import threading
import time
from urllib.parse import urlparse

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import gettext as _

from core import users as users_mod
from core.auth import (
    ADMIN_USER,
    get_csrf_token,
    ui_logged_in,
)


auth_bp = Blueprint("auth", __name__)


# ---- Login rate limiting ---------------------------------------------
#
# Simple in-memory throttle against password brute-forcing. Per client
# IP we remember the timestamps of recent *failed* attempts; once
# LOGIN_MAX_FAILURES within LOGIN_WINDOW_SEC are on record, further
# attempts are refused until the window slides past.
#
# Deliberately unsophisticated:
# - In-memory means each Gunicorn worker keeps its own counter, so an
#   attacker effectively gets (workers x limit) tries. With 2 workers
#   and 10 tries per 15 min that is still only ~2 guesses/minute —
#   plenty to stop a dictionary attack on a LAN install, without a
#   Redis dependency.
# - Keyed on request.remote_addr, which is the real client IP because
#   ProxyFix is configured for the Nginx X-Forwarded-For header.
# - A successful login clears the counter for that IP, so a teacher
#   who fat-fingers their password twice doesn't lock out the shared
#   staff-room computer.
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SEC = 15 * 60

_failed_logins: dict[str, list[float]] = {}
_failed_logins_lock = threading.Lock()


def _login_throttled(ip: str) -> bool:
    """True when this IP has too many recent failed login attempts.

    Also prunes expired timestamps for the IP as a side effect, so
    the dict entry shrinks back once the window slides past.
    """
    now = time.monotonic()
    with _failed_logins_lock:
        recent = [
            t for t in _failed_logins.get(ip, ())
            if now - t < LOGIN_WINDOW_SEC
        ]
        if recent:
            _failed_logins[ip] = recent
        else:
            _failed_logins.pop(ip, None)
        return len(recent) >= LOGIN_MAX_FAILURES


def _record_failed_login(ip: str) -> None:
    """Remember one failed attempt; garbage-collect stale IPs."""
    now = time.monotonic()
    with _failed_logins_lock:
        _failed_logins.setdefault(ip, []).append(now)
        # Bound the dict: drop IPs whose attempts have all expired.
        # Only bother when the map grows unusually large.
        if len(_failed_logins) > 1000:
            for k in list(_failed_logins):
                if all(now - t >= LOGIN_WINDOW_SEC for t in _failed_logins[k]):
                    del _failed_logins[k]


def _clear_failed_logins(ip: str) -> None:
    """Forget an IP's failures after a successful login."""
    with _failed_logins_lock:
        _failed_logins.pop(ip, None)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Show the login form and handle a login attempt.

    On GET: render the login page with an empty form.
    On POST: check the username and password. If they are correct,
    store the user info in the session and redirect to the next page.
    If they are wrong, show an error message and stay on the login page.
    """
    if ui_logged_in():
        return redirect(url_for("agenda.agenda"))

    # Open-redirect defence. Drop unsafe ?next= values silently and
    # fall back to the default landing page. The pattern below is
    # the exact one from CodeQL's py/url-redirection documentation:
    # only accept the value when urlparse reports no scheme and no
    # netloc, i.e. a plain relative path. Backslashes are stripped
    # first because browsers treat them like forward slashes but
    # urlparse does not, so '\evil.example' would otherwise slip
    # through.
    #
    # The check is inlined here (rather than hidden in a helper)
    # so that CodeQL's static analysis sees the sanitizer guard in
    # the same scope as the redirect() call it protects.
    raw_next = request.args.get("next") or request.form.get("next") or ""
    candidate = raw_next.replace("\\", "")
    parsed = urlparse(candidate)
    if candidate and not parsed.scheme and not parsed.netloc:
        next_url = candidate
    else:
        # No explicit ?next= → bounce through the bare site root.
        # monitoring.home then redirects the user to their first
        # accessible tab. Previously this defaulted to /roosters,
        # which 403'd for any user without the "roosters" tab — fine
        # for the single-admin world, broken now that we support
        # restricted-access users.
        next_url = url_for("monitoring.home")

    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _login_throttled(ip):
            # Don't even check the password: that's the whole point.
            # The message stays vague on purpose (no "N minutes left")
            # so the throttle leaks as little as possible.
            flash(_("Te veel mislukte pogingen. Probeer het later opnieuw."))
            return render_template(
                "login.html",
                next_url=next_url,
                admin_user=ADMIN_USER,
                csrf_token=get_csrf_token(),
                tab=None,
            ), 429

        # Normalize the username to lowercase: the user store is
        # case-insensitive, so "Alice" and "alice" are the same
        # account. Trimming whitespace handles accidental space
        # before/after when typing.
        u = (request.form.get("username") or "").strip().lower()
        p = request.form.get("password") or ""
        user = users_mod.verify_user(u, p)
        if user is not None:
            _clear_failed_logins(ip)
            # Close session fixation: discard everything that was in
            # the session before login (including the CSRF token that
            # was already generated on the login page), so any
            # injected cookie is immediately worthless. get_csrf_token()
            # then generates a fresh token on the first next render.
            session.clear()
            session.permanent = True
            session["user"] = u
            # rol/tabs are seeded here for the redirect that follows,
            # but the authoritative copy lives in users.json: the
            # _refresh_user_permissions hook in webinterface re-syncs
            # them from the store on every request, so permission
            # changes (and deletions) take effect immediately instead
            # of at the next login.
            session["rol"] = user.get("rol", "gebruiker")
            session["tabs"] = list(user.get("tabs") or [])
            return redirect(next_url)
        _record_failed_login(ip)
        flash(_("Onjuiste inloggegevens."))

    return render_template(
        "login.html",
        next_url=next_url,
        admin_user=ADMIN_USER,
        csrf_token=get_csrf_token(),
        tab=None,  # no active tab on the login page
    )


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Log the current user out by clearing their session.

    POST-only (the nav-bar button with a CSRF token). The old GET
    variant meant any third-party page could log a user out with a
    hidden <img src="/logout"> — harmless data-wise, but there's no
    reason to allow it. Admins who used to type /logout into the
    address bar can simply use the nav-bar button.
    """
    # session.clear() instead of just pop("user"): this also discards the
    # CSRF token and session.permanent flag. On the next login, everything
    # is rebuilt fresh.
    session.clear()
    return redirect(url_for("auth.login"))
