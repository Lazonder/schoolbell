# Schoolbell Roadmap

English · [Nederlands](roadmap.nl.md)

This document collects the planned additions to Schoolbell.
Intentionally not dated strictly — Schoolbell is a hobby/school
project and the order depends on what turns out to be needed first
in practice. Every item has a short motivation and rough scope, so
it's clear *what* it covers and *why* it's on the list.

Status legend:

- **Planned** — agreed it's coming, not started yet.
- **Planned (on request)** — picked up when someone asks for it;
  not pursued on our own.
- **In design** — design is settled, code to follow.
- **In progress** — actively being worked on.

A finished feature drops off this list and is folded into the README
or admin guide.

---

## Password reset by e-mail

**Status:** Planned

This follows naturally on top of the multi-user system (now
released; see the
[User management section](admin-guide.md#user-management) of the
admin guide). With a single
admin, "env-var fallback + restart" is an acceptable rescue. With
several named accounts it becomes awkward if regular users have to
queue up at the admin every time they forget a password.

**Scope (rough sketch):**

- Add e-mail address as an optional field in `users.json`.
- SMTP config in `/etc/schoolbell/web.env` (host, port, user, pass,
  from address).
- New routes: `/forgot-password` (asks for the e-mail and sends a
  token mail), `/reset-password/<token>` (the form).
- Tokens short-lived (15 minutes), single-use, stored in a separate
  JSON with TTL — not in `users.json` itself, to keep the user
  store clean.
- Document: SMTP configuration, troubleshooting when mail does not
  arrive.

Open question: a school install rarely has its own SMTP server.
One alternative is "make the reset link appear in the Log, then
share that link out-of-band". Less elegant, but many installs have
no mail server.

---

## More languages on request

**Status:** Planned (on request)

Schoolbell currently ships with full UI translations in **Dutch**,
**English**, **German** and **French**. Translations are managed
through `gettext`/`.po` files (see `translations/` and
CONTRIBUTING.md).

There are no extra languages firmly on the roadmap, but adding a
new one is a light effort — mostly translation work, no Python
work. Requests welcome.

**Scope per new language:**

- `pybabel init -l <code>` to create a fresh catalog file.
- Translate the strings via a `.po` editor (Poedit) or by hand.
- `pybabel compile` to generate the `.mo` files.
- Add the code to `SUPPORTED_LOCALES` in `core/i18n.py` so it
  shows up in the language dropdown.
- Verify that Babel's date formatting is correct for the locale
  (holiday dates, weekdays).

Whether a request lands depends mostly on whether a translator
volunteers — not on the programming side.

---

## Questions, suggestions, requests

Open an issue on the repository, or amend this file in a pull
request. A roadmap without context isn't worth much, so please add
a short motivation ("why this is useful").
