"""Tests for the inline edit-moment feature on the Roosters page.

Covers:
- the ✎ button appears next to ✕ in normal render
- ?edit_r=...&edit_i=... opens the edit form with pre-filled values
- a bogus edit_i falls back to normal render
- POST /roosters/<r>/edit-moment/<i> updates the stored moment
- validation errors on POST redirect back into edit mode
"""

import json

import webinterface as wi


def _seed_two_moments():
    with open(wi.ROOSTERS_PATH, "w") as f:
        json.dump({"School": [
            {"tijd": "08:30", "naam": "Ochtend", "bestand": "country.mp3"},
            {"tijd": "10:00", "naam": "Pauze",   "bestand": "country.mp3",
             "warn_min": 2, "warn_bestand": "country.mp3"},
        ]}, f)


def test_roosters_page_shows_edit_and_delete_buttons(logged_in_client):
    _seed_two_moments()
    r = logged_in_client.get("/roosters")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "✎" in body, "edit pencil (✎) missing"
    assert "✕" in body, "delete cross (✕) missing"
    assert "edit_r=School" in body, "edit link should carry rooster name"


def test_edit_mode_renders_prefilled_form(logged_in_client):
    _seed_two_moments()
    r = logged_in_client.get("/roosters?edit_r=School&edit_i=1")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'id="edit"' in body, "edit anchor row should be present"
    assert 'value="10:00"' in body, "tijd not pre-filled"
    assert 'value="Pauze"' in body, "naam not pre-filled"
    assert 'value="2"' in body, "warn_min not pre-filled"
    # The other moment must still be visible as a normal read-only row
    assert "08:30" in body


def test_out_of_range_edit_i_falls_back_to_normal_render(logged_in_client):
    _seed_two_moments()
    r = logged_in_client.get("/roosters?edit_r=School&edit_i=99")
    assert r.status_code == 200
    assert 'id="edit"' not in r.get_data(as_text=True)


def test_non_numeric_edit_i_falls_back_to_normal_render(logged_in_client):
    _seed_two_moments()
    r = logged_in_client.get("/roosters?edit_r=School&edit_i=nonsense")
    assert r.status_code == 200
    assert 'id="edit"' not in r.get_data(as_text=True)


def test_post_edit_moment_updates_storage(logged_in_client, csrf_token):
    _seed_two_moments()
    r = logged_in_client.post("/roosters/School/edit-moment/1", data={
        "_csrf": csrf_token,
        "tijd": "10:15",
        "naam": "Speelkwartier",
        "bestand": "country.mp3",
        "warn_min": "0",
        "warn_bestand": "",
    }, follow_redirects=False)
    assert r.status_code in (302, 303), r.status_code

    with open(wi.ROOSTERS_PATH) as f:
        saved = json.load(f)
    # After re-sort the order may shift; find by name.
    pauze_or_new = [m for m in saved["School"] if m["naam"] == "Speelkwartier"]
    assert len(pauze_or_new) == 1
    assert pauze_or_new[0]["tijd"] == "10:15"
    assert "warn_min" not in pauze_or_new[0], "warning should be cleared"


def test_post_edit_moment_changes_warning(logged_in_client, csrf_token):
    _seed_two_moments()
    r = logged_in_client.post("/roosters/School/edit-moment/0", data={
        "_csrf": csrf_token,
        "tijd": "08:30",
        "naam": "Ochtend",
        "bestand": "country.mp3",
        "warn_min": "5",
        "warn_bestand": "country.mp3",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)

    with open(wi.ROOSTERS_PATH) as f:
        saved = json.load(f)
    ochtend = [m for m in saved["School"] if m["naam"] == "Ochtend"][0]
    assert ochtend["warn_min"] == 5
    assert ochtend["warn_bestand"] == "country.mp3"


def test_post_edit_moment_invalid_time_keeps_edit_mode(logged_in_client, csrf_token):
    _seed_two_moments()
    r = logged_in_client.post("/roosters/School/edit-moment/0", data={
        "_csrf": csrf_token,
        "tijd": "not-a-time",
        "naam": "Ochtend",
        "bestand": "country.mp3",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers.get("Location", "")
    assert "edit_r=School" in loc
    assert "edit_i=0" in loc


def test_post_edit_moment_unknown_rooster_flashes(logged_in_client, csrf_token):
    _seed_two_moments()
    r = logged_in_client.post("/roosters/Nope/edit-moment/0", data={
        "_csrf": csrf_token,
        "tijd": "09:00",
        "naam": "X",
        "bestand": "country.mp3",
    }, follow_redirects=False)
    # We expect a redirect back to /roosters (not into edit mode).
    assert r.status_code in (302, 303)
    loc = r.headers.get("Location", "")
    assert loc.endswith("/roosters") or "/roosters?" in loc
