"""
Tests for the language-picker plumbing (issue #29 phase 1).

This phase only wires up the machinery — it does NOT mark any
strings for translation. So these tests check:

- Settings.taal default and forward-compat with older config files.
- _apply_settings_payload accepts the supported locales + 'auto'
  and rejects anything else.
- core.i18n.select_locale picks the right language given the
  setting and (for 'auto') the Accept-Language header.

Phase 2 will mark strings; phase 3+ will add translations and
those will need their own tests for actual localized rendering.
"""

import json

import pytest
from werkzeug.exceptions import HTTPException

import settings_store
import webinterface
from blueprints.settings import _apply_settings_payload
from core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, select_locale
from settings_store import Settings


@pytest.fixture(autouse=True)
def temp_config(tmp_path, monkeypatch):
    """Each test gets its own config.json so they don't bleed state."""
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(settings_store, "CONFIG_PATH", cfg)
    yield cfg


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------


def test_default_taal_is_dutch():
    assert Settings().taal == "nl"


def test_taal_persists_across_save_load():
    with settings_store.locked() as s:
        s.taal = "fr"
    assert Settings.load().taal == "fr"


def test_settings_load_handles_missing_taal_key(temp_config):
    # Forward-compat: an older config file without the 'taal' field
    # must still load and get the default.
    with open(temp_config, "w") as f:
        json.dump({
            "volume_percent": 70,
            "max_file_size_mb": 15,
            "poll_interval_sec": 2,
            "theme_mode": "light",
            "vakantieregio": "Noord",
            "vakanties_scrape_enabled": True,
            "allowed_extensions": [".mp3"],
            # no taal
        }, f)
    s = Settings.load()
    assert s.taal == "nl"
    assert s.volume_percent == 70  # other fields preserved


# ---------------------------------------------------------------------------
# Validation (route-side)
# ---------------------------------------------------------------------------


def _apply(payload):
    """Run _apply_settings_payload inside an app context so abort() works."""
    s = Settings()
    with webinterface.app.test_request_context():
        _apply_settings_payload(s, payload)
    return s


@pytest.mark.parametrize("taal", SUPPORTED_LOCALES + ("auto",))
def test_taal_accepts_supported_values(taal):
    s = _apply({"taal": taal})
    assert s.taal == taal


def test_taal_normalizes_case():
    # The select sends lowercased values, but a curl request might
    # send 'EN' or 'Auto'. Normalize before comparing.
    s = _apply({"taal": "EN"})
    assert s.taal == "en"


@pytest.mark.parametrize(
    "bad",
    ["", "italiano", "es", "1", "nl-be", "<script>", "auto-mode"],
)
def test_taal_rejects_unknown_values(bad):
    with pytest.raises(HTTPException) as exc:
        _apply({"taal": bad})
    assert exc.value.code == 400


# ---------------------------------------------------------------------------
# Locale selector
# ---------------------------------------------------------------------------


def test_select_locale_uses_explicit_setting():
    # When taal is a real locale, the selector returns it without
    # looking at the request at all.
    with settings_store.locked() as s:
        s.taal = "de"
    with webinterface.app.test_request_context():
        assert select_locale() == "de"


def test_select_locale_auto_picks_from_accept_language():
    # taal="auto" -> use the browser's preference list, picking the
    # best match against SUPPORTED_LOCALES.
    with settings_store.locked() as s:
        s.taal = "auto"
    with webinterface.app.test_request_context(
        headers={"Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8"}
    ):
        assert select_locale() == "fr"


def test_select_locale_auto_falls_back_when_browser_lang_unsupported():
    # An Italian visitor on an NL/EN/DE/FR app: best_match returns
    # None, and the selector substitutes the default locale instead
    # of returning None to Babel (which would itself fall back, but
    # explicit is better than implicit).
    with settings_store.locked() as s:
        s.taal = "auto"
    with webinterface.app.test_request_context(
        headers={"Accept-Language": "it,it-IT;q=0.9"}
    ):
        assert select_locale() == DEFAULT_LOCALE


def test_select_locale_falls_back_when_settings_corrupt(monkeypatch):
    # Config-file parse error → Settings.load() raises. The selector
    # must not crash the request; serve the default locale instead.
    def boom():
        raise RuntimeError("config.json: broken")

    monkeypatch.setattr(Settings, "load", staticmethod(boom))
    with webinterface.app.test_request_context():
        assert select_locale() == DEFAULT_LOCALE
