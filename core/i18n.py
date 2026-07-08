"""Internationalization helpers.

Flask-Babel needs to know which language to use for each incoming
request. This file holds:

  - SUPPORTED_LOCALES — the list of language codes the app ships
    translations for.
  - DEFAULT_LOCALE   — what to fall back to when nothing matches
    (Dutch, the original language of the app).
  - select_locale()  — the callback Flask-Babel calls per request.
    It looks at Settings.taal first. If that says "auto", it asks
    the browser via the Accept-Language header.

The actual ``Babel`` instance is built in ``webinterface.py`` next
to the Flask app it attaches to. Keeping the selector and the
locale list here lets tests import them without dragging in Flask.
"""

from flask import request

from settings_store import Settings


# Languages the app currently has translations for. Keys must match
# the folder name under ``translations/`` (e.g. translations/de/).
# Adding a new language: append the code here, run
# ``pybabel init -i messages.pot -d translations -l <code>``,
# then translate the resulting .po file.
SUPPORTED_LOCALES = ("nl", "en", "de", "fr")

# What we use when nothing else applies. Dutch matches the language
# the source strings are written in, so missing translations show
# up as Dutch rather than as a key like "settings.title".
DEFAULT_LOCALE = "nl"


def select_locale() -> str:
    """Decide which locale Flask-Babel should use for this request.

    Priority order:
      1. Settings.taal is one of the supported locales: use it.
      2. Settings.taal is "auto": ask the browser via
         Accept-Language and pick the best supported match.
      3. Anything else (corrupt config, unknown value): fall back
         to DEFAULT_LOCALE.

    Reading Settings on every request is cheap (load_json hits a
    small JSON file) and keeps this function stateless. If we ever
    care about the I/O cost we can cache the value here for a few
    seconds instead of re-reading the file every time.
    """
    try:
        taal = Settings.load().taal
    except Exception:
        return DEFAULT_LOCALE

    if taal in SUPPORTED_LOCALES:
        return taal

    if taal == "auto":
        # request.accept_languages.best_match returns None when the
        # browser's preferences don't overlap with our supported set
        # (e.g. an Italian visitor on an NL/EN/DE/FR app).
        return request.accept_languages.best_match(SUPPORTED_LOCALES) or DEFAULT_LOCALE

    return DEFAULT_LOCALE
