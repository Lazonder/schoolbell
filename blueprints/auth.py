"""Blueprint for the session-cookie login flow.

Two routes:

  GET/POST /login   — show the form / accept credentials
  GET/POST /logout  — clear the session cookie

The actual credential check lives in ``core.auth``; this file is
only the HTTP wiring around it. Logout supports both GET (admin
typing /logout into the address bar) and POST (the nav-bar form
with a CSRF token); both end up doing the same ``session.clear()``.
"""

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

from core.auth import (
    ADMIN_USER,
    _check_password,
    get_csrf_token,
    ui_logged_in,
)


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
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
        next_url = url_for("roosters.roosters")

    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        if u == ADMIN_USER and _check_password(p):
            # Close session fixation: discard everything that was in the session
            # before login (including the CSRF token that was already generated
            # on the login page), so any injected cookie is immediately worthless.
            # get_csrf_token() then generates a fresh token on the first next render.
            session.clear()
            session.permanent = True
            session["user"] = ADMIN_USER
            return redirect(next_url)
        flash(_("Onjuiste inloggegevens."))

    return render_template(
        "login.html",
        next_url=next_url,
        admin_user=ADMIN_USER,
        csrf_token=get_csrf_token(),
        tab=None,  # no active tab on the login page
    )


@auth_bp.route("/logout", methods=["POST", "GET"])
def logout():
    # session.clear() instead of just pop("user"): this also discards the
    # CSRF token and session.permanent flag. On the next login, everything
    # is rebuilt fresh.
    session.clear()
    return redirect(url_for("auth.login"))
