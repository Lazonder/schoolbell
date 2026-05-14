"""Tests for core/users.py — the user store.

These tests don't go through Flask: ``core.users`` is a pure module
and can be exercised in isolation. Each test redirects
``users_mod.USERS_PATH`` to a per-test tmp file via monkeypatch, so
nothing here ever touches the developer's real ``data/users.json``.

Note on conftest.py: pytest still loads it for this test module
(it's in the same directory), which means ``webinterface`` gets
imported during collection. That's incidental — we don't use it
here, and the import is harmless.
"""

import json

import pytest

from core import users as users_mod


# ---- Fixtures -------------------------------------------------------


@pytest.fixture
def users_path(tmp_path, monkeypatch):
    """Redirect ``users.USERS_PATH`` to a per-test tmp file.

    Returns the Path object so individual tests can read the file
    directly (e.g. to assert on the stored shape).
    """
    p = tmp_path / "users.json"
    monkeypatch.setattr(users_mod, "USERS_PATH", str(p))
    return p


# ---- Happy-path round-trips -----------------------------------------


def test_create_then_verify(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    user = users_mod.verify_user("alice", "passw0rd!")
    assert user is not None
    assert user["rol"] == "admin"
    # tabs always normalize to ["*"] for admins.
    assert user["tabs"] == ["*"]


def test_create_gebruiker_round_trip(users_path):
    """A regular user keeps the explicit tab list it was created with."""
    users_mod.create_user(
        "anna", "passw0rd!", "gebruiker", ["agenda", "roosters"]
    )
    user = users_mod.get_user("anna")
    assert user is not None
    assert user["rol"] == "gebruiker"
    assert user["tabs"] == ["agenda", "roosters"]


def test_get_user_is_case_insensitive(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    assert users_mod.get_user("Alice") is not None
    assert users_mod.get_user("ALICE") is not None
    assert users_mod.get_user("alice") is not None


def test_verify_with_wrong_password(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    assert users_mod.verify_user("alice", "wrong") is None


def test_verify_unknown_user(users_path):
    """Missing username and wrong password return the same None.

    This is intentional: a login form must not leak which usernames
    exist by responding differently. The test pins that behaviour so
    a future change can't regress it accidentally.
    """
    assert users_mod.verify_user("ghost", "anything") is None


def test_pwhash_is_not_plaintext(users_path):
    """The stored hash must not contain the plain password.

    Catches an accidental regression where a future code change forgets
    to call generate_password_hash. A real hash contains the algorithm
    name, iterations, salt and base64 derived key — never the input.
    """
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    raw = json.loads(users_path.read_text())
    assert "passw0rd!" not in raw["alice"]["pwhash"]
    # Sanity check that hashing actually happened:
    assert raw["alice"]["pwhash"].startswith(("pbkdf2:", "scrypt:"))


def test_aangemaakt_is_iso_utc(users_path):
    """The aangemaakt timestamp should be ISO-formatted and UTC.

    Without this, a future refactor could quietly switch to a local
    timezone and break log parsing or admin UIs that assume UTC.
    """
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    raw = json.loads(users_path.read_text())
    ts = raw["alice"]["aangemaakt"]
    # ISO suffix is either "+00:00" or "Z" depending on Python version
    # — datetime.isoformat() emits "+00:00".
    assert ts.endswith("+00:00") or ts.endswith("Z")


# ---- Validation -----------------------------------------------------


def test_username_too_short(users_path):
    with pytest.raises(ValueError, match="gebruikersnaam"):
        users_mod.create_user("a", "passw0rd!", "admin", ["*"])


def test_username_with_invalid_chars(users_path):
    with pytest.raises(ValueError, match="gebruikersnaam"):
        users_mod.create_user("alice!", "passw0rd!", "admin", ["*"])


def test_username_uppercase_normalized_to_lowercase(users_path):
    """Uppercase input is normalized, not rejected.

    The regex enforces lowercase, but create_user lowercases first.
    The end result: "Alice" is stored as "alice" without an error.
    """
    users_mod.create_user("Alice", "passw0rd!", "admin", ["*"])
    raw = json.loads(users_path.read_text())
    assert "alice" in raw
    assert "Alice" not in raw


def test_password_too_short(users_path):
    with pytest.raises(ValueError, match="Wachtwoord"):
        users_mod.create_user("alice", "short", "gebruiker", ["agenda"])


def test_unknown_role(users_path):
    with pytest.raises(ValueError, match="rol"):
        users_mod.create_user("alice", "passw0rd!", "owner", ["agenda"])


def test_unknown_tab(users_path):
    with pytest.raises(ValueError, match="tab"):
        users_mod.create_user(
            "alice", "passw0rd!", "gebruiker", ["dashboard"]
        )


def test_duplicate_user(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    with pytest.raises(ValueError, match="bestaat al"):
        users_mod.create_user("alice", "other-pass!", "admin", ["*"])


# ---- Access checks --------------------------------------------------


def test_admin_can_access_anything(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    user = users_mod.get_user("alice")
    assert users_mod.user_can_access(user, "agenda")
    assert users_mod.user_can_access(user, "settings")
    # Admins are not gated on the KNOWN_TABS set — even an unknown
    # tab string returns True. The actual route-level decorator
    # decides which tabs exist; this function only enforces "may".
    assert users_mod.user_can_access(user, "future-tab")


def test_gebruiker_only_allowed_tabs(users_path):
    users_mod.create_user(
        "anna", "passw0rd!", "gebruiker", ["agenda", "roosters"]
    )
    user = users_mod.get_user("anna")
    assert users_mod.user_can_access(user, "agenda")
    assert users_mod.user_can_access(user, "roosters")
    assert not users_mod.user_can_access(user, "settings")
    assert not users_mod.user_can_access(user, "geluiden")


def test_is_admin_defensive(users_path):
    """is_admin tolerates malformed/legacy records.

    A record with no ``rol`` key shouldn't crash this check — it
    should simply return False. This guards against future migrations
    that introduce a transitional state where fields are missing.
    """
    assert users_mod.is_admin({}) is False
    assert users_mod.is_admin({"rol": "gebruiker"}) is False
    assert users_mod.is_admin({"rol": "admin"}) is True


# ---- update_user ----------------------------------------------------


def test_update_password(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    users_mod.update_user("alice", password="new-pass!")
    # Old password no longer works.
    assert users_mod.verify_user("alice", "passw0rd!") is None
    # New one does.
    assert users_mod.verify_user("alice", "new-pass!") is not None


def test_update_tabs(users_path):
    users_mod.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    users_mod.update_user("anna", tabs=["agenda", "geluiden"])
    user = users_mod.get_user("anna")
    assert user["tabs"] == ["agenda", "geluiden"]


def test_update_promote_to_admin_normalizes_tabs(users_path):
    """Promoting to admin replaces tabs with ['*'] regardless of input."""
    users_mod.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    users_mod.update_user("anna", rol="admin")
    assert users_mod.get_user("anna")["tabs"] == ["*"]


def test_update_unknown_user(users_path):
    with pytest.raises(ValueError, match="niet gevonden"):
        users_mod.update_user("ghost", password="passw0rd!")


def test_update_short_password_rejected(users_path):
    """Validation runs on update, not just on create."""
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    with pytest.raises(ValueError, match="Wachtwoord"):
        users_mod.update_user("alice", password="short")
    # The original password still works — validation rejected before
    # writing anything.
    assert users_mod.verify_user("alice", "passw0rd!") is not None


# ---- Last-admin protection -----------------------------------------


def test_cannot_demote_last_admin(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    with pytest.raises(ValueError, match="laatste admin"):
        users_mod.update_user("alice", rol="gebruiker", tabs=["agenda"])


def test_can_demote_admin_if_another_exists(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    users_mod.create_user("bob", "passw0rd!", "admin", ["*"])
    # No exception: there's still an admin left.
    users_mod.update_user("alice", rol="gebruiker", tabs=["agenda"])
    assert users_mod.get_user("alice")["rol"] == "gebruiker"
    assert users_mod.admin_count() == 1


def test_cannot_delete_last_admin(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    with pytest.raises(ValueError, match="laatste admin"):
        users_mod.delete_user("alice")


def test_can_delete_admin_if_another_exists(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    users_mod.create_user("bob", "passw0rd!", "admin", ["*"])
    users_mod.delete_user("alice")
    assert users_mod.get_user("alice") is None
    assert users_mod.admin_count() == 1


def test_delete_unknown_user_is_noop(users_path):
    """Deleting a missing user shouldn't error.

    Matches the principle of being forgiving on idempotent
    operations: calling delete again after the first one returns the
    same end state.
    """
    users_mod.delete_user("ghost")
    # No exception, file may not even exist yet:
    assert users_mod.load_users() == {}


def test_can_delete_regular_user_with_one_admin_present(users_path):
    """Single-admin protection only fires for admin deletions."""
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    users_mod.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    users_mod.delete_user("anna")
    assert users_mod.get_user("anna") is None
    # The lone admin survives — that's a regular-user delete, no
    # last-admin check needed.
    assert users_mod.get_user("alice") is not None


# ---- admin_count ---------------------------------------------------


def test_admin_count_zero_when_empty(users_path):
    assert users_mod.admin_count() == 0


def test_admin_count_counts_only_admins(users_path):
    users_mod.create_user("alice", "passw0rd!", "admin", ["*"])
    users_mod.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    users_mod.create_user("bob", "passw0rd!", "admin", ["*"])
    assert users_mod.admin_count() == 2
