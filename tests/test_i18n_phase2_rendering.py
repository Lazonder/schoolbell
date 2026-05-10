"""
End-to-end tests that the English locale actually renders English.

Phase 1 of the i18n work proved the picker plumbing. Phase 2 marked
all UI strings and shipped translations/en/. This test file locks
that working state in: if a future change reverts a {% trans %},
forgets to ship a .mo, or breaks the locale selector, these tests
fail loudly.

The Dutch source language doesn't need a separate test — every
existing route test already renders the Dutch UI implicitly (no
'taal' field set in their tmp config -> default 'nl').
"""

import re

from tests._helpers import TEST_PASSWORD, csrf_from_html


def _login(client) -> None:
    """Run the same login flow used by the logged_in_client fixture
    but allow the caller to switch the language before /settings is
    fetched. Mutates the client's session cookie in place."""
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "admin", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), f"login failed: {r.status_code}"


def test_settings_page_renders_in_english_when_taal_is_en(client, monkeypatch):
    """Pick taal=en, then load /settings. The page should show
    English labels (Settings, Bell volume) and not the Dutch source
    words.

    Uses monkeypatch on settings_store.CONFIG_PATH (already set by
    the `client` fixture's tmp_path setup) and seeds taal=en directly
    in the config file so the locale selector picks it up on the
    very next request.
    """
    import json
    import settings_store

    # Write a config with taal=en before we ever hit a route. The
    # client fixture already redirected DATA_DIR/AUDIO_DIR to a tmp
    # path; the Settings file lives at SCHOOLBELL_CONFIG (or the
    # default, which conftest doesn't touch). Patch CONFIG_PATH so
    # the test's seed and the route's read agree on the same file.
    cfg_path = settings_store.CONFIG_PATH
    if hasattr(cfg_path, "write_text"):
        # pathlib.Path
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps({"taal": "en"}))
    else:
        # str path
        import os
        os.makedirs(os.path.dirname(str(cfg_path)) or ".", exist_ok=True)
        with open(cfg_path, "w") as f:
            json.dump({"taal": "en"}, f)

    _login(client)
    r = client.get("/settings")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)

    # English strings present.
    assert "Settings" in body, "Page title 'Settings' missing"
    assert "Bell volume:" in body, "'Bell volume:' label missing"
    assert "Language" in body, "'Language' label missing"
    assert "Theme" in body, "'Theme' label missing"

    # Dutch source words absent. Pin a few that have no innocent
    # English overlap so the assertion is unambiguous.
    assert "Belvolume:" not in body, "Dutch 'Belvolume:' leaked"
    assert "Voorkeuren</h1>" not in body, "Dutch 'Voorkeuren' heading leaked"


def test_login_page_renders_in_english_when_browser_prefers_english(client):
    """Anonymous /login with Settings.taal='auto' and an English
    Accept-Language header should render English. This is the path a
    public-page visitor takes before any session cookie exists.
    """
    import json
    import settings_store

    cfg_path = settings_store.CONFIG_PATH
    if hasattr(cfg_path, "write_text"):
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps({"taal": "auto"}))
    else:
        import os
        os.makedirs(os.path.dirname(str(cfg_path)) or ".", exist_ok=True)
        with open(cfg_path, "w") as f:
            json.dump({"taal": "auto"}, f)

    r = client.get("/login", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Sign in" in body
    # And the html lang attribute reflects the resolved locale.
    assert re.search(r'<html\s+lang="en"', body), "<html lang='en'> not set"


def test_login_page_stays_dutch_for_dutch_browser(client):
    """Mirror of the previous test: Settings.taal='auto' +
    Dutch Accept-Language header should yield Dutch."""
    import json
    import settings_store

    cfg_path = settings_store.CONFIG_PATH
    if hasattr(cfg_path, "write_text"):
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps({"taal": "auto"}))
    else:
        import os
        os.makedirs(os.path.dirname(str(cfg_path)) or ".", exist_ok=True)
        with open(cfg_path, "w") as f:
            json.dump({"taal": "auto"}, f)

    r = client.get("/login", headers={"Accept-Language": "nl-NL,nl;q=0.9"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Inloggen" in body
    assert re.search(r'<html\s+lang="nl"', body)
