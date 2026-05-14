"""Blueprint for the user-management page (admin only).

One page that lists every account in ``data/users.json`` and offers
forms to add a new user, change someone's role/tabs, reset a
password, or delete an account. Every route is admin-only via
:func:`webinterface.admin_page_required`.

Routes:

  GET   /gebruikers                    — list + new-user form
  POST  /gebruikers/nieuw              — create
  POST  /gebruikers/<u>/wijzig         — change role/tabs
  POST  /gebruikers/<u>/wachtwoord     — reset password
  POST  /gebruikers/<u>/verwijder      — delete

Validation errors raised by :mod:`core.users` are caught and shown as
flash messages, so the page always renders rather than 500-ing on a
typo. The user store itself enforces the hard constraints
(last-admin protection, password length, etc.); this blueprint is
just the HTTP wiring.
"""

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import gettext as _

import webinterface as wi
from core import users as users_mod


gebruikers_bp = Blueprint("gebruikers", __name__)


@gebruikers_bp.route("/gebruikers", methods=["GET"])
@wi.admin_page_required
def lijst():
    """Render the user-management page.

    Users are sorted by username for a stable display order. The
    template gets the canonical tab order so the checkboxes line up
    with the nav-bar.
    """
    users = users_mod.load_users()
    return render_template(
        "gebruikers.html",
        tab="gebruikers",
        csrf_token=wi.get_csrf_token(),
        users=sorted(users.items()),
        known_tabs=list(users_mod.TAB_ORDER),
    )


@gebruikers_bp.route("/gebruikers/nieuw", methods=["POST"])
@wi.admin_page_required
def nieuw():
    """Create a new user from a posted form.

    All validation lives in :func:`core.users.create_user`; this
    handler just feeds the form values through and flashes whatever
    comes back. The username is lowercased here so that the flash
    message shows the form the user will be stored under.
    """
    username = (request.form.get("username") or "").strip().lower()
    password = request.form.get("password") or ""
    rol = (request.form.get("rol") or "gebruiker").strip().lower()
    # getlist returns [] when no checkbox is checked, which is what
    # we want for "user with no tabs" (allowed but pointless; the
    # root redirect logs them out). Admins ignore this list anyway
    # because create_user normalises them to ["*"].
    tabs = request.form.getlist("tabs")
    try:
        users_mod.create_user(username, password, rol, tabs)
        flash(_("Gebruiker '%(u)s' aangemaakt.", u=username))
    except ValueError as e:
        flash(_("Fout bij aanmaken: %(err)s", err=str(e)))
    return redirect(url_for("gebruikers.lijst"))


@gebruikers_bp.route("/gebruikers/<username>/wijzig", methods=["POST"])
@wi.admin_page_required
def wijzig(username):
    """Update an existing user's role and/or tabs.

    The form always submits both fields, even when only one
    changed; :func:`core.users.update_user` is fine with that and
    re-validates the combination. Password is changed via a
    separate route so this form doesn't need to ship a password
    field on every render.
    """
    rol = (request.form.get("rol") or "").strip().lower()
    tabs = request.form.getlist("tabs")
    try:
        users_mod.update_user(username, rol=rol, tabs=tabs)
        flash(_("Wijzigingen voor '%(u)s' opgeslagen.", u=username))
    except ValueError as e:
        flash(_("Fout bij wijzigen: %(err)s", err=str(e)))
    return redirect(url_for("gebruikers.lijst"))


@gebruikers_bp.route(
    "/gebruikers/<username>/wachtwoord", methods=["POST"]
)
@wi.admin_page_required
def wachtwoord(username):
    """Reset a user's password.

    Separate route from /wijzig so the regular edit form doesn't
    need to round-trip the password (and so the password field
    doesn't get auto-filled by browsers when an admin just wants to
    flip a tab).
    """
    password = request.form.get("password") or ""
    try:
        users_mod.update_user(username, password=password)
        flash(_("Wachtwoord voor '%(u)s' bijgewerkt.", u=username))
    except ValueError as e:
        flash(_("Fout: %(err)s", err=str(e)))
    return redirect(url_for("gebruikers.lijst"))


@gebruikers_bp.route(
    "/gebruikers/<username>/verwijder", methods=["POST"]
)
@wi.admin_page_required
def verwijder(username):
    """Delete a user.

    The last-admin protection lives in
    :func:`core.users.delete_user`; we catch its ValueError and
    flash it. The template additionally hides the delete button on
    the row representing the currently logged-in admin so the
    common foot-gun ("I deleted myself") doesn't even surface.
    """
    try:
        users_mod.delete_user(username)
        flash(_("Gebruiker '%(u)s' verwijderd.", u=username))
    except ValueError as e:
        flash(_("Fout bij verwijderen: %(err)s", err=str(e)))
    return redirect(url_for("gebruikers.lijst"))
