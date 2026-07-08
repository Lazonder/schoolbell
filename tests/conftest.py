"""
Shared fixtures for route-level Flask tests.

webinterface reads several environment variables and the BASE_DIR
constant at import time, so anything tests want to control (admin
credentials, session secret, data dir) must be set up *before* the
first `import webinterface` happens. Conftest.py is loaded by pytest
ahead of any test module in this directory, which makes it the right
place to do that bootstrap.
"""

import os

import pytest
from werkzeug.security import generate_password_hash

from tests._helpers import TEST_PASSWORD, csrf_from_html

# ---------------------------------------------------------------------------
# Module-level setup
# ---------------------------------------------------------------------------

# TEST_PASSWORD lives in _helpers so test modules can import it too;
# the matching hash is generated at conftest-import time below and
# exported as SCHOOLBELL_WEB_PWHASH so webinterface picks it up. The
# real install.sh-generated hash on the user's machine never matches,
# which is exactly what we want during tests: the credentials a route
# sees come entirely from this file.

os.environ.setdefault("SCHOOLBELL_WEB_USER", "admin")
# Daemon basic-auth path. webinterface itself doesn't use this, but
# importing schoolbelldaemon (e.g. via test_daemon_*.py) hard-fails
# without it, so we set it for sibling test files.
os.environ.setdefault("SCHOOLBELL_WEB_PASS", "daemon-pass")
os.environ["SCHOOLBELL_WEB_PWHASH"] = generate_password_hash(TEST_PASSWORD)
os.environ.setdefault("SCHOOLBELL_SECRET", "test-secret-key-for-flask-session-only")
# The test client speaks plain HTTP, so cookies marked Secure would
# never come back. Force the flag off for tests.
os.environ["SCHOOLBELL_SECURE_COOKIES"] = "0"

# Import after env is set up.
import webinterface  # noqa: E402

# Multi-user user store. Tests need to redirect its USERS_PATH alongside
# webinterface.DATA_DIR so the before_request bootstrap doesn't write
# to the developer's real data/users.json on the host machine.
from core import users as core_users  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    """Clear the in-memory login throttle between tests.

    The limiter in blueprints.auth is module-global state keyed on
    client IP. Every test client posts from the same 127.0.0.1, so
    without this reset the failed-login attempts of unrelated tests
    would accumulate across the suite and trip the throttle at
    random. Tests that exercise the throttle itself build their own
    failure count within a single test body.
    """
    from blueprints import auth as auth_blueprint
    auth_blueprint._failed_logins.clear()
    yield
    auth_blueprint._failed_logins.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Anonymous Flask test client with isolated state directories.

    Every test starts from empty data/ and audio/ trees so there's no
    cross-test coupling and no risk of touching the user's real files.
    Each module-level path constant in webinterface that any route
    might write to is redirected via monkeypatch — `setattr` undoes
    itself at the end of the test.
    """
    data_dir = tmp_path / "data"
    audio_dir = tmp_path / "audio"
    data_dir.mkdir()
    audio_dir.mkdir()

    monkeypatch.setattr(webinterface, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(webinterface, "AUDIO_DIR", str(audio_dir))
    monkeypatch.setattr(
        webinterface, "ROOSTERS_PATH", str(data_dir / "roosters.json")
    )
    monkeypatch.setattr(
        webinterface, "STANDAARDWEEK_PATH", str(data_dir / "standaardweek.json")
    )
    monkeypatch.setattr(
        webinterface, "DAGPLANNING_PATH", str(data_dir / "dagplanning.json")
    )
    monkeypatch.setattr(
        webinterface, "WEEKDISABLE_PATH", str(data_dir / "weken_uit.json")
    )
    monkeypatch.setattr(
        webinterface, "EVENTS_LOG_PATH", str(data_dir / "events.jsonl")
    )
    monkeypatch.setattr(
        webinterface, "VAKANTIES_PATH", str(data_dir / "vakanties.json")
    )
    monkeypatch.setattr(
        webinterface,
        "DAEMON_HEARTBEAT_PATH",
        str(data_dir / "daemon_heartbeat.json"),
    )
    monkeypatch.setattr(
        webinterface, "STOP_FLAG_PATH", str(data_dir / "stop_playback")
    )
    # core.users keeps its own path constant (it deliberately doesn't
    # import webinterface to avoid a circular dependency, see
    # core/users.py header). Redirect it to the same tmp dir so the
    # before_request bootstrap writes to data_dir/users.json instead
    # of the real one on disk.
    monkeypatch.setattr(
        core_users, "USERS_PATH", str(data_dir / "users.json")
    )

    return webinterface.app.test_client()


@pytest.fixture
def logged_in_client(client):
    """Test client that's already past /login.

    Performs a real POST to /login so session state matches a normal
    browser flow (cookie set, csrf token seeded, session['user']
    populated). Tests that exercise the login flow itself should use
    the plain `client` fixture so they can drive the POST themselves.
    """
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "admin", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), f"login fixture failed: {r.status_code}"
    return client


@pytest.fixture
def csrf_token(logged_in_client):
    """A CSRF token valid for the `logged_in_client` session.

    The token is per-session (not per-page), so any rendered authed
    page works. We pull from /roosters because it's a plain GET that
    doesn't depend on data files existing — keeping the fixture
    cheap and free of incidental dependencies.
    """
    r = logged_in_client.get("/roosters")
    return csrf_from_html(r.get_data(as_text=True))
