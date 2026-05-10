# Contributing to Schoolbell

Thanks for taking the time to look at the code or send in a fix.
Schoolbell is a small, friendly project — no contributor agreements,
no maintainer hierarchy. Open an issue, send a pull request, ask a
question. All of those are welcome.

This file covers two things people commonly want to do:

- [Translate to a new language or improve an existing translation](#translations)
- [Run the app locally and contribute code](#running-locally)

---

## Translations

The user interface speaks Dutch and English today. Adding German,
French, or anything else is a matter of editing a `.po` file —
you don't have to write any Python or HTML.

### Source language

The original strings (called *msgids*) are written in **Dutch**.
Every other language is a translation of those Dutch strings into
its target. Inside the codebase you'll see things like:

```python
flash(_("Onbekend rooster."))
```

The Dutch text *is* the message identifier. When the active locale
is English, gettext looks up that Dutch string in the `.po` file
and returns the matching English translation. If a translation is
missing, you see the Dutch source — never an empty string or a key.

### Tools you'll want

* A `.po` editor. Free options:
  - [Poedit](https://poedit.net/) — desktop app, Mac/Windows/Linux
  - [Lokalize](https://apps.kde.org/lokalize/) — Linux/KDE
  - any plain text editor works too, the format is human-readable
* Python 3.11+ with the project dependencies installed
  (`pip install -r requirements.txt`)

### Adding a new language

Pick a language code — two letters, lowercase. ISO 639-1.
Common ones: `de` (German), `fr` (French), `es` (Spanish),
`it` (Italian).

```bash
# Generate a fresh template from the source code
pybabel extract -F babel.cfg -o messages.pot .

# Initialize the new language (run once per language)
pybabel init -i messages.pot -d translations -l de
```

That creates `translations/de/LC_MESSAGES/messages.po`. Open it in
Poedit and translate each entry. Save.

```bash
# Compile the .po file into the binary .mo that the app actually reads
pybabel compile -d translations
```

Add `de` to `SUPPORTED_LOCALES` in `core/i18n.py`, and add an
`<option>` to the language select in `templates/settings.html`.

Restart the Flask app (or hit reload in dev mode) and switch to the
new language in *Settings* to see your work.

### Updating an existing translation

When the source code changes, new strings appear and old ones may
get edited or removed. To bring an existing translation up to date:

```bash
pybabel extract -F babel.cfg -o messages.pot .
pybabel update -i messages.pot -d translations -l de
```

`pybabel update` keeps your existing translations and marks edited
ones as **fuzzy** (which means: "the source changed, double-check
this translation"). Open the `.po` file, fix the fuzzy entries
(remove the `, fuzzy` flag once you've checked them), translate any
new ones, then `pybabel compile`.

### Glossary of project-specific terms

To keep translations consistent across pages, use these standard
words for the recurring domain terms:

| Dutch (source)   | English          | Deutsch       | Français          |
|------------------|------------------|---------------|-------------------|
| Rooster          | Schedule         | Stundenplan   | Horaire           |
| Standaardweek    | Default week     | Standardwoche | Semaine type      |
| Agenda           | Calendar         | Kalender      | Calendrier        |
| Bel uit / Geen bel | Bell off / No bell | Keine Glocke / Aus | Sans cloche / Désactivé |
| Geluid           | Sound            | Klang         | Son               |
| Vakantieweek     | Holiday week     | Ferienwoche   | Semaine de vacances |
| Voorkeuren       | Settings         | Einstellungen | Paramètres        |
| Huisstijl        | Branding         | Hausfarben    | Charte graphique  |
| Waarschuwing     | Warning          | Warnung       | Avertissement     |
| Moment           | Moment           | Moment        | Moment            |

If you find a term not in this table that recurs in multiple
strings, add it (in your PR or as a separate one) so the next
translator finds it.

---

## Running locally

You don't need a Raspberry Pi to develop. The web app runs on any
machine with Python 3.11+.

### Setup

```bash
git clone https://github.com/<your-account>/schoolbell.git
cd schoolbell
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

### Compile translations

The app loads `.mo` files at startup. Compile them once:

```bash
pybabel compile -d translations
```

### Run the dev server

The Flask app needs three environment variables to start:

```bash
export SCHOOLBELL_WEB_USER=admin
export SCHOOLBELL_WEB_PASS=devpassword
export SCHOOLBELL_WEB_PWHASH="$(python3 -c \
  'from werkzeug.security import generate_password_hash; print(generate_password_hash("devpassword"))')"
export SCHOOLBELL_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
export SCHOOLBELL_DEBUG=1     # optional: enables template auto-reload

flask --app webinterface run
```

Visit <http://localhost:5000/> and log in with `admin` / `devpassword`.

The bell daemon (`schoolbelldaemon.py`) requires `pygame` plus a
working audio device, so it's awkward to run on a Mac/Windows
laptop. Most UI development can happen without it — the daemon's
heartbeat indicator just shows "down".

### Run the tests

```bash
SCHOOLBELL_WEB_USER=admin SCHOOLBELL_WEB_PASS=test \
  SCHOOLBELL_WEB_PWHASH='pbkdf2:sha256:600000$x$0' \
  SCHOOLBELL_SECRET=test \
  python3 -m pytest tests/
```

### Pre-commit hook

Set up the git hook so lint and tests run on every commit:

```bash
pre-commit install
```

Run on demand without committing:

```bash
pre-commit run --all-files
```

### Code style

* Python: ruff handles linting (config in `ruff.toml`). Format
  rules are deliberately loose — ruff format is *not* enforced
  yet.
* Comments: written in **English**, using simple sentences (HAVO 5
  level) so a student picking up the code can follow what's going
  on without having to learn jargon. Define an acronym the first
  time you use it.
* Tests: prefer pinning behaviour to specific URLs / response
  fields rather than literal text. The literal text changes every
  time someone reorganizes a translation; the response shape is
  the actual contract.

---

## Reporting bugs

Open an issue with:

* What you did (the steps to reproduce)
* What you expected
* What happened instead
* Which version (output of `git rev-parse --short HEAD` if you
  built from source)

For security issues — reports of unauthenticated access, leaked
credentials, etc. — email the maintainer directly rather than
opening a public issue.
