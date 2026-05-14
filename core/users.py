"""User store backed by ``data/users.json``.

This module is intentionally pure Python: no Flask, no
``webinterface`` import. That keeps unit tests cheap (no app context
needed) and sidesteps a circular import — ``core.auth`` will import
this module, and ``webinterface`` imports ``core.auth`` near the top
of its own module load.

The on-disk layout under ``data/users.json``::

    {
      "alice": {
        "pwhash":     "pbkdf2:sha256:...",
        "rol":        "admin",          # "admin" | "gebruiker"
        "tabs":       ["*"],            # tab keys, or ["*"] for all
        "aangemaakt": "2026-05-14T10:00:00Z"
      },
      ...
    }

Roles are kept simple: ``admin`` implicitly grants every tab. The
``tabs`` field on an admin record is still maintained so that demoting
an admin back to a regular user restores the previous tab set rather
than wiping it. ``["*"]`` is the canonical "all tabs" marker.

Usernames are case-insensitive on lookup, lowercased on write — so
``"Alice"`` and ``"alice"`` can never both exist.
"""

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash


# ---- Paths -----------------------------------------------------------
#
# Same env-var fallback pattern as ``webinterface.BASE_DIR``. We can't
# import DATA_DIR from there: ``core.auth`` (which webinterface itself
# imports at module load) will import this file, and pulling
# webinterface back in here would create a circular import. Computing
# the path locally keeps the module self-contained.
_BASE_DIR = os.environ.get("SCHOOLBELL_BASE_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
DATA_DIR = os.path.join(_BASE_DIR, "data")
USERS_PATH = os.path.join(DATA_DIR, "users.json")


# ---- Constants & validation -----------------------------------------

# Tab keys come from the ``tab=...`` argument every blueprint passes
# to render_template (see base.html navigation). When a new tab is
# added in the UI, append the key here so the validator accepts it.
# ``"gebruikers"`` is reserved for the user-management page itself
# (introduced as part of this multi-user feature).
KNOWN_TABS: frozenset = frozenset({
    "agenda",
    "roosters",
    "standaardweek",
    "geluiden",
    "logs",
    "settings",
    "gebruikers",
})

# Roles. ``ADMIN`` gets implicit access to every tab. We keep the
# string values as module-level constants so that callers don't
# hard-code "admin"/"gebruiker" everywhere — and so a typo like
# ``rol == "amdin"`` would fail at import time instead of silently
# evaluating to False.
ADMIN = "admin"
GEBRUIKER = "gebruiker"
KNOWN_ROLES: frozenset = frozenset({ADMIN, GEBRUIKER})

# Allowed username characters: lowercase letters, digits, underscore,
# hyphen. Length 2..32. Usernames are normalized to lowercase before
# this regex is applied, so the upper-case alternative isn't needed.
USERNAME_RE = re.compile(r"^[a-z0-9_-]{2,32}$")

MIN_PASSWORD_LEN = 8


# ---- File locking ---------------------------------------------------


@contextmanager
def _locked_users():
    """Hold an exclusive lock for read-modify-write of users.json.

    Mirrors ``webinterface.locked_json`` and ``settings_store.locked``:
    a sidecar ``users.json.lock`` file is locked via
    ``fcntl.flock(LOCK_EX)`` for the duration of the with-block. We
    yield ``(data, save)`` so callers can decide whether to persist
    their changes — not calling ``save()`` leaves the file unchanged
    on exit, which is useful when validation fails mid-flight.

    Concurrency model: two write callers serialize through the lock;
    a third process reading via :func:`load_users` sees either the
    pre- or post-write state thanks to ``os.replace`` (atomic on a
    single filesystem).
    """
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    lock_path = USERS_PATH + ".lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        data = _read()

        def save(new_data: dict) -> None:
            _write(new_data)

        yield data, save
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _read() -> dict:
    """Read users.json. Returns ``{}`` when the file is missing.

    A missing file is the expected fresh-install state: the migration
    in webinterface populates it from the env-var admin on first
    request. So we treat FileNotFoundError as a normal empty store
    rather than an error.
    """
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _write(data: dict) -> None:
    """Atomically replace users.json with ``data``.

    Writes to a temp file in the same directory, ``fsync`` for
    durability, then ``os.replace`` to swap in. The replace is atomic
    on a single filesystem, so on a crash you'll see either the old
    file or the complete new one — never a half-written JSON.
    """
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    tmp = USERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, USERS_PATH)


# ---- Public read API ------------------------------------------------


def load_users() -> dict:
    """Return the full users mapping. Read-only convenience helper.

    Callers that only need to check membership or iterate over names
    should prefer this over :func:`get_user` to avoid one disk read
    per lookup.
    """
    return _read()


def get_user(username: str) -> Optional[dict]:
    """Return the user record for ``username``, or None if missing.

    Lookup is case-insensitive (storage is lowercase): ``get_user("Alice")``
    and ``get_user("alice")`` return the same record.
    """
    return _read().get(username.strip().lower())


def is_admin(user: dict) -> bool:
    """True if the given user record has the admin role.

    Defensive against missing/malformed records: a dict without a
    ``rol`` field returns False rather than crashing.
    """
    return user.get("rol") == ADMIN


def user_can_access(user: dict, tab: str) -> bool:
    """True if ``user`` may see/edit the tab with key ``tab``.

    Admin role grants every tab implicitly. Regular users grant
    access only to tabs explicitly listed. The ``"*"`` marker is a
    shortcut for "all tabs" — present on admin records, never used on
    regular-user records (the write helpers normalize this).
    """
    if is_admin(user):
        return True
    tabs = user.get("tabs") or []
    return "*" in tabs or tab in tabs


def admin_count() -> int:
    """Number of admin accounts currently in the store.

    Used by :func:`update_user` and :func:`delete_user` to refuse the
    "last admin removal" case. Without that safety net you could lock
    yourself out of the management UI permanently.
    """
    return sum(1 for u in _read().values() if u.get("rol") == ADMIN)


def verify_user(username: str, password: str) -> Optional[dict]:
    """Return the user record on success, None on failure.

    A non-None return guarantees that ``password`` matched the stored
    hash. Callers can rely on this for populating the session — no
    further check needed.

    Note: both branches of "user missing" and "password wrong" return
    None without distinguishing between them. This is intentional: a
    login form should not leak which usernames exist.
    """
    user = get_user(username)
    if user is None:
        return None
    if not check_password_hash(user["pwhash"], password):
        return None
    return user


# ---- Validation -----------------------------------------------------


def _validate(
    username: str,
    *,
    password: Optional[str],
    rol: str,
    tabs: list,
) -> None:
    """Raise ValueError if any field fails the rules.

    Called from :func:`create_user` and :func:`update_user`. Pass
    ``password=None`` to skip the password check (used when only
    role or tabs are being updated).
    """
    if not USERNAME_RE.match(username):
        raise ValueError(
            "Ongeldige gebruikersnaam: alleen kleine letters, cijfers, "
            "'_' en '-' toegestaan (2-32 tekens)"
        )
    if password is not None and len(password) < MIN_PASSWORD_LEN:
        raise ValueError(
            f"Wachtwoord moet minstens {MIN_PASSWORD_LEN} tekens hebben"
        )
    if rol not in KNOWN_ROLES:
        raise ValueError(f"Onbekende rol: {rol!r}")
    for t in tabs:
        if t != "*" and t not in KNOWN_TABS:
            raise ValueError(f"Onbekende tab: {t!r}")


# ---- Public write API -----------------------------------------------


def create_user(
    username: str, password: str, rol: str, tabs: list
) -> None:
    """Add a new user.

    Raises ValueError on validation failure or duplicate username.
    Admins always get ``tabs=["*"]`` regardless of what the caller
    passed, so the persisted record is consistent with how
    :func:`is_admin` and :func:`user_can_access` read it back.
    """
    username = username.strip().lower()
    _validate(username, password=password, rol=rol, tabs=tabs)
    with _locked_users() as (data, save):
        if username in data:
            raise ValueError(f"Gebruiker bestaat al: {username!r}")
        data[username] = {
            "pwhash": generate_password_hash(password),
            "rol": rol,
            "tabs": ["*"] if rol == ADMIN else list(tabs),
            "aangemaakt": datetime.now(timezone.utc).isoformat(),
        }
        save(data)


def update_user(
    username: str,
    *,
    password: Optional[str] = None,
    rol: Optional[str] = None,
    tabs: Optional[list] = None,
) -> None:
    """Update one or more fields of an existing user.

    Any combination of ``password``/``rol``/``tabs`` is allowed;
    fields left at ``None`` are kept untouched. Raises ValueError when:

    - the user doesn't exist,
    - any new value fails validation,
    - the change would demote the last remaining admin (which would
      leave the user-management UI inaccessible to everyone).
    """
    username = username.strip().lower()
    with _locked_users() as (data, save):
        if username not in data:
            raise ValueError(f"Gebruiker niet gevonden: {username!r}")
        current = data[username]
        new_rol = rol if rol is not None else current["rol"]
        new_tabs = tabs if tabs is not None else current["tabs"]
        _validate(
            username, password=password, rol=new_rol, tabs=new_tabs
        )
        # Last-admin protection: refuse the demotion only when the
        # current record IS admin AND the new role isn't admin AND
        # no other admin exists. Counting inside the lock means we
        # can't race against a concurrent delete.
        if current["rol"] == ADMIN and new_rol != ADMIN:
            other_admins = sum(
                1
                for u, rec in data.items()
                if u != username and rec.get("rol") == ADMIN
            )
            if other_admins == 0:
                raise ValueError(
                    "De laatste admin kan niet gedegradeerd worden"
                )
        current["rol"] = new_rol
        current["tabs"] = ["*"] if new_rol == ADMIN else list(new_tabs)
        if password is not None:
            current["pwhash"] = generate_password_hash(password)
        save(data)


def delete_user(username: str) -> None:
    """Remove a user from the store.

    Idempotent: deleting a non-existent username is a no-op rather
    than an error. Raises ValueError when the deletion would remove
    the last admin — see :func:`update_user` for the same protection.
    """
    username = username.strip().lower()
    with _locked_users() as (data, save):
        if username not in data:
            return
        if data[username].get("rol") == ADMIN:
            other_admins = sum(
                1
                for u, rec in data.items()
                if u != username and rec.get("rol") == ADMIN
            )
            if other_admins == 0:
                raise ValueError(
                    "De laatste admin kan niet verwijderd worden"
                )
        del data[username]
        save(data)
