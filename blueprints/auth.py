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

    next_url = (
        request.args.get("next")
        or request.form.get("next")
        or url_for("roosters.roosters")
    )

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
        flash("Onjuiste inloggegevens.")

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
