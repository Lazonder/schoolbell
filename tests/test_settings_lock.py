"""
Tests for settings_store.locked() — the read-modify-write context
manager that serializes concurrent settings writers.

Same risk profile as locked_json (which guards the data files):
without locking, two concurrent settings POSTs can each load v1,
mutate independently, and both save — the second one wins and the
first user's change is silently lost. The settings page is rarely
touched so this is mostly belt-and-braces, but the helper is
trivially small and the consistency with the rest of the codebase
is worth the few lines.
"""

import threading

import pytest

import settings_store


@pytest.fixture(autouse=True)
def temp_config(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a fresh temp file for every test."""
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(settings_store, "CONFIG_PATH", cfg)
    yield cfg


def test_locked_basic_roundtrip():
    with settings_store.locked() as s:
        s.volume_percent = 42

    # Re-load to confirm the change persisted.
    s2 = settings_store.Settings.load()
    assert s2.volume_percent == 42


def test_default_vakantieregio_is_noord():
    # Sanity check on the new Settings field. Default is "Noord" so a
    # fresh install can hit 'Vakanties importeren' without first
    # opening Voorkeuren.
    s = settings_store.Settings()
    assert s.vakantieregio == "Noord"


def test_vakantieregio_persists_across_save_load():
    with settings_store.locked() as s:
        s.vakantieregio = "Zuid"

    s2 = settings_store.Settings.load()
    assert s2.vakantieregio == "Zuid"


def test_settings_load_handles_missing_vakantieregio_key():
    # Forward-compat: an existing config.json from before this field
    # was added must still load. Settings.load() filters unknown keys
    # and merges defaults, so a legacy file without 'vakantieregio'
    # gets the default value.
    import json
    with open(settings_store.CONFIG_PATH, "w") as f:
        json.dump({
            "volume_percent": 80,
            "max_file_size_mb": 20,
            "poll_interval_sec": 5,
            "theme_mode": "dark",
            "allowed_extensions": [".mp3"],
            # no vakantieregio
        }, f)

    s = settings_store.Settings.load()
    assert s.vakantieregio == "Noord"  # default kicks in
    assert s.volume_percent == 80      # other fields preserved


def test_default_vakanties_scrape_enabled_is_true():
    # Default-on so a fresh NL install gets the scrape feature without
    # extra setup. Schools outside the Netherlands toggle it off in
    # Voorkeuren.
    s = settings_store.Settings()
    assert s.vakanties_scrape_enabled is True


def test_vakanties_scrape_enabled_persists_across_save_load():
    with settings_store.locked() as s:
        s.vakanties_scrape_enabled = False

    s2 = settings_store.Settings.load()
    assert s2.vakanties_scrape_enabled is False


def test_settings_load_handles_missing_vakanties_scrape_enabled_key():
    # Same forward-compat story as vakantieregio: an older config.json
    # without this field must still load.
    import json
    with open(settings_store.CONFIG_PATH, "w") as f:
        json.dump({
            "volume_percent": 80,
            "max_file_size_mb": 20,
            "poll_interval_sec": 5,
            "theme_mode": "dark",
            "vakantieregio": "Zuid",
            "allowed_extensions": [".mp3"],
            # no vakanties_scrape_enabled
        }, f)

    s = settings_store.Settings.load()
    assert s.vakanties_scrape_enabled is True  # default
    assert s.vakantieregio == "Zuid"           # other fields preserved


def test_default_huisstijl_is_standaard():
    # "standaard" means: don't override anything, follow theme_mode.
    # A fresh install should look exactly like before this feature
    # was added.
    s = settings_store.Settings()
    assert s.huisstijl == "standaard"


def test_default_custom_colors_match_light_theme():
    # The custom-color defaults track the light :root values in
    # static/css/school.css. Picking 'Aangepast' without changing
    # anything should look identical to 'Standaard + Licht'.
    s = settings_store.Settings()
    assert s.theme_custom_bg == "#ffffff"
    assert s.theme_custom_table == "#f7f7f9"
    # Primary blue from :root in school.css.
    assert s.theme_custom_nav == "#5b62ff"


def test_huisstijl_and_custom_colors_persist_across_save_load():
    with settings_store.locked() as s:
        s.huisstijl = "aangepast"
        s.theme_custom_bg = "#fafafa"
        s.theme_custom_table = "#e8eef5"
        s.theme_custom_nav = "#0066cc"

    s2 = settings_store.Settings.load()
    assert s2.huisstijl == "aangepast"
    assert s2.theme_custom_bg == "#fafafa"
    assert s2.theme_custom_table == "#e8eef5"
    assert s2.theme_custom_nav == "#0066cc"


def test_settings_load_handles_missing_huisstijl_key():
    # Forward-compat: a config.json that predates the huisstijl field
    # must still load, defaulting all four new fields.
    import json
    with open(settings_store.CONFIG_PATH, "w") as f:
        json.dump({
            "volume_percent": 80,
            "max_file_size_mb": 20,
            "poll_interval_sec": 5,
            "theme_mode": "dark",
            "vakantieregio": "Zuid",
            "vakanties_scrape_enabled": True,
            "allowed_extensions": [".mp3"],
            # no huisstijl, no theme_custom_*
        }, f)

    s = settings_store.Settings.load()
    assert s.huisstijl == "standaard"      # default
    assert s.theme_custom_bg == "#ffffff"  # default
    assert s.vakantieregio == "Zuid"       # other fields preserved


def test_locked_does_not_save_on_exception():
    # First write a baseline.
    with settings_store.locked() as s:
        s.volume_percent = 30

    # Now mutate but raise — the context manager should NOT save.
    with pytest.raises(RuntimeError):
        with settings_store.locked() as s:
            s.volume_percent = 99
            raise RuntimeError("validation failed")

    s = settings_store.Settings.load()
    assert s.volume_percent == 30  # baseline preserved


def test_locked_serializes_concurrent_writers():
    """20 threads each set volume_percent to their thread index, in a
    barrier-aligned race. The lock guarantees serialized read-modify-
    write, so the final value is *some* thread's index (we don't care
    which) and never an intermediate corruption.

    More importantly: every save must complete without raising. Without
    the lock, the file replace itself wouldn't corrupt (os.replace is
    atomic) but updates would be silently lost. Here we just confirm
    the lock plumbing works under concurrency."""
    # Seed an initial value.
    with settings_store.locked() as s:
        s.volume_percent = 0

    n = 20
    barrier = threading.Barrier(n)
    errors = []

    def worker(i):
        try:
            barrier.wait()
            with settings_store.locked() as s:
                s.volume_percent = (i % 100) + 1  # avoid 0 so test below is unambiguous
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"unexpected errors: {errors}"
    final = settings_store.Settings.load()
    # Final value must be one of the workers' assignments — not 0
    # (the seed). If the lock were broken under contention we'd
    # also catch corruption (TypeError on load, ValueError, etc.)
    # via the autouse fixture and the load above.
    assert 1 <= final.volume_percent <= n
