"""
Unit-tests voor de pure helper-functies uit webinterface.py.

'Pure' = geen side-effects (geen I/O, geen globale state), dus we kunnen
ze veilig rechtstreeks importeren en aanroepen — geen fixtures of
mocking nodig. Dit is de meest waardevolle set om als eerste te
beschermen tegen regressies: deze functies doen het échte werk
(tijd-normalisatie, bestandsnaam-validatie, dagrooster-selectie),
terwijl de Flask-routes er bovenop slechts dun zijn.

Gebruikelijke testvorm (AAA):
  - Arrange: bouw input op
  - Act:     roep de functie aan
  - Assert:  controleer het resultaat

Draaien:
  pip install -r requirements-dev.txt
  pytest            # vanuit de project-root
  pytest -v         # verbose: laat per test zien of hij slaagt
"""

from datetime import date

import pytest

# Deze import heeft als bijeffect dat webinterface.py wordt uitgevoerd.
# Dat is OK: alle side-effects in dat bestand (Flask-app aanmaken,
# env-vars lezen) zijn idempotent en raken de filesystem niet.
from webinterface import (
    _env_bool,
    effectieve_rooster_naam_for_date,
    iso_week_key,
    normalize_and_sort_moments,
    normalize_time,
    safe_audio_filename,
    weekday_key,
)


# ---------------------------------------------------------------------------
# normalize_time: string -> "HH:MM" of ""
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("8:05", "08:05"),      # enkele-digit uur wordt gepadded
        ("08:05", "08:05"),     # al goed, blijft zo
        ("08:5", ""),           # minuten moeten altijd 2 digits zijn (regex)
        ("23:59", "23:59"),     # bovengrens geldig
        ("00:00", "00:00"),     # ondergrens geldig
        ("11:30:00", "11:30"),  # seconden worden weggesneden
        ("  9:15  ", "09:15"),  # strip() werkt
    ],
)
def test_normalize_time_happy_path(raw, expected):
    assert normalize_time(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",         # leeg
        "abc",      # geen tijd
        "24:00",    # uur uit range
        "12:60",    # minuut uit range
        "007:03",   # drie digits uur: regex matcht niet
        "12",       # mist :mm
        ":30",      # mist uur
        None,       # None-invoer wordt door strip() op "" geforceerd
    ],
)
def test_normalize_time_invalid_returns_empty(raw):
    # normalize_time() geeft bewust "" terug i.p.v. None, zodat callers
    # in templates een `if tijd:` kunnen doen zonder type-check.
    assert normalize_time(raw) == ""


# ---------------------------------------------------------------------------
# safe_audio_filename: voorkomt path-traversal en rommel in filenames
# ---------------------------------------------------------------------------


def test_safe_audio_filename_happy_path():
    assert safe_audio_filename("bel01", ".mp3") == "bel01.mp3"


def test_safe_audio_filename_strips_whitespace():
    assert safe_audio_filename("  intro  ", ".wav") == "intro.wav"


@pytest.mark.parametrize(
    "base",
    [
        "../etc/passwd",     # path-traversal poging
        "bel/01",            # slash verboden
        "bel\\01",           # backslash verboden
        "bel.01",            # punt verboden (zou dubbele extensie geven)
        "",                  # leeg
        "a" * 36,            # 1 te lang (limiet is 35)
        "bel@home",          # @ verboden
        "héllo",             # accent verboden (buiten [A-Za-z0-9 _-])
    ],
)
def test_safe_audio_filename_rejects_invalid(base):
    # Bij een ongeldige naam krijg je een lege string terug.
    # De caller hoort daarop te checken en te weigeren.
    assert safe_audio_filename(base, ".mp3") == ""


# ---------------------------------------------------------------------------
# normalize_and_sort_moments: cleanup + sort van een lijst bel-momenten
# ---------------------------------------------------------------------------


def test_normalize_and_sort_moments_drops_invalid_and_sorts():
    input_moments = [
        {"tijd": "10:05", "naam": "Pauze", "bestand": "pauze.mp3"},
        {"tijd": "bogus", "naam": "X", "bestand": "x.mp3"},          # ongeldige tijd
        {"tijd": "8:00", "naam": "Start", "bestand": "start.mp3"},    # niet-gepadded
        {"tijd": "09:15", "naam": "", "bestand": "a.mp3"},            # lege naam
        {"tijd": "09:20", "naam": "Z", "bestand": ""},                # leeg bestand
    ]

    result = normalize_and_sort_moments(input_moments)

    # Alleen de twee geldige blijven over, en ze staan op tijd-volgorde.
    assert result == [
        {"tijd": "08:00", "naam": "Start", "bestand": "start.mp3"},
        {"tijd": "10:05", "naam": "Pauze", "bestand": "pauze.mp3"},
    ]


def test_normalize_and_sort_moments_empty_list():
    assert normalize_and_sort_moments([]) == []


# ---------------------------------------------------------------------------
# weekday_key + effectieve_rooster_naam_for_date:
# de kern van "welk rooster geldt op een gegeven datum?"
# ---------------------------------------------------------------------------


def test_weekday_key_mapping():
    # 2026-04-20 is een maandag, 04-26 een zondag — handige checkset.
    assert weekday_key(date(2026, 4, 20)) == "Mon"
    assert weekday_key(date(2026, 4, 21)) == "Tue"
    assert weekday_key(date(2026, 4, 22)) == "Wed"
    assert weekday_key(date(2026, 4, 23)) == "Thu"
    assert weekday_key(date(2026, 4, 24)) == "Fri"
    assert weekday_key(date(2026, 4, 25)) == "Sat"
    assert weekday_key(date(2026, 4, 26)) == "Sun"


def test_effectief_rooster_dagplanning_wint_van_standaardweek():
    # Dagplanning is een per-datum-override: als er voor die dag iets
    # staat, gaat dat voor boven de standaardweek.
    dagplanning = {"2026-04-20": "feestdag"}
    standaardweek = {"Mon": "gewone_week", "Tue": "gewone_week"}

    naam = effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    )
    assert naam == "feestdag"


def test_effectief_rooster_valt_terug_op_standaardweek():
    # Geen dagplanning-entry → neem de weekdag uit standaardweek.
    dagplanning = {}
    standaardweek = {"Mon": "gewone_week", "Tue": "andere_week"}

    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    ) == "gewone_week"
    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 21), dagplanning, standaardweek,
    ) == "andere_week"


def test_effectief_rooster_lege_state_geeft_lege_string():
    # Niks bekend → "", niet None. Dat houdt de aanroepende code simpel.
    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), {}, {},
    ) == ""


def test_effectief_rooster_dagplanning_met_lege_waarde_valt_terug():
    # Regel uit de code: `if d_iso in dagplanning and dagplanning[d_iso]`
    # — een lege string in dagplanning telt NIET als override. Belangrijk,
    # want de UI zet soms bewust "" om een dag leeg te maken, en in die
    # gevallen moet standaardweek de fallback zijn.
    dagplanning = {"2026-04-20": ""}
    standaardweek = {"Mon": "gewone_week"}

    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    ) == "gewone_week"


# ---------------------------------------------------------------------------
# iso_week_key: formatteert een datum als "YYYY-Www"
# ---------------------------------------------------------------------------


def test_iso_week_key_format():
    # 2026-01-05 = maandag van ISO-week 2. Handige edge: rond jaarwissel
    # kan de ISO-week een ander jaar dragen, dat willen we goed testen.
    assert iso_week_key(date(2026, 1, 5)) == "2026-W02"


def test_iso_week_key_rond_jaarwissel():
    # 2025-12-29 valt in ISO-week 1 van 2026 (eerste week met 4+ dagen
    # in het nieuwe jaar). Die grensgevallen zijn regressie-gevoelig
    # bij refactors van datumcode.
    assert iso_week_key(date(2025, 12, 29)) == "2026-W01"


# ---------------------------------------------------------------------------
# _env_bool: environment-parsing met monkeypatch
# ---------------------------------------------------------------------------


def test_env_bool_default_bij_onzet(monkeypatch):
    # monkeypatch.delenv met raising=False doet niks als de var al niet
    # bestaat, maar verzekert ons dat we een clean state hebben.
    monkeypatch.delenv("FAKE_FLAG", raising=False)

    assert _env_bool("FAKE_FLAG", default=True) is True
    assert _env_bool("FAKE_FLAG", default=False) is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", "Off", "", "  "])
def test_env_bool_false_varianten(monkeypatch, value):
    monkeypatch.setenv("FAKE_FLAG", value)
    assert _env_bool("FAKE_FLAG", default=True) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "YES", "something"])
def test_env_bool_true_varianten(monkeypatch, value):
    monkeypatch.setenv("FAKE_FLAG", value)
    assert _env_bool("FAKE_FLAG", default=False) is True
