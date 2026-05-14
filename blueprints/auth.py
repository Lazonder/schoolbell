"""Blueprint for the session-cookie login flow.

Two routes:

  GET/POST /login   — show the form / accept credentials
  GET/POST /logout  — clear the session cookie

The actual credential check lives in ``core.auth``; this file is
only the HTTP wiring around it. Logout supports both GET (admin
typing /logout into the address bar) and POST (the nav-bar form
with a CSRF token); both end up doing the same ``session.clear()``.
"""

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


def _is_safe_next_url(target: str) -> bool:
    """True if ``target`` is a safe local URL to redirect to after login.

    Defends against the open-redirect pattern where an attacker
    crafts a link like ``/login?next=https://evil.example/phishing``
    and uses the post-login redirect to push the user onto a
    look-alike site that asks for credentials again.

    We accept only paths that begin with a single ``/`` and do not
    begin with ``//`` (protocol-relative URLs that browsers resolve
    against the current scheme but a different host). That covers
    all legitimate use inside this app — every internal route is a
    plain local path — and rejects everything else, including
    absolute URLs, javascript: pseudo-URLs and the like.
    """
    if not isinstance(target, str) or not target:
        return False
    if not target.startswith("/"):
        return False
    if target.startswith("//"):
        return False
    return True


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if ui_logged_in():
        return redirect(url_for("agenda.agenda"))

    # Drop unsafe ?next= values silently and fall back to the
    # default. We do not flash an error because the user did not
    # type the URL — it was almost certainly handed to them.
    raw_next = request.args.get("next") or request.form.get("next") or ""
    if _is_safe_next_url(raw_next):
        next_url = raw_next
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
