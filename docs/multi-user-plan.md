# Implementatieplan: multi-user met per-tab rechten

Doel: meerdere gebruikers kunnen inloggen op de schoolbel-app, elk met
toegang tot een eigen subset van tabbladen. Eén of meer admins kunnen
gebruikers aanmaken, bewerken en verwijderen.

Dit document beschrijft het *wat* en *waarom* per stap, met kleine
code-snippets ter illustratie. De daadwerkelijke implementatie volgt in
aparte sessies, stap voor stap.

---

## 1. Uitgangspunten

- We blijven JSON gebruiken voor opslag (consistent met `data/*.json`).
- We breken niets aan voor bestaande installaties: bij eerste start
  zonder `users.json` migreert de huidige env-var-admin automatisch.
- Beveiliging gebeurt server-side. Tabs verbergen in de UI is alleen
  cosmetisch — elke route checkt zelf of de gebruiker er bij mag.
- We voegen geen nieuwe externe afhankelijkheden toe (Flask-Login,
  SQLAlchemy etc.). De `werkzeug.security` hash-functies die we al
  gebruiken volstaan.

De zes bestaande tabs noemen we voortaan met deze sleutelnamen
(matchen de `tab=...` variabele in elke route):

| Sleutel        | Tab              | Blueprint                 |
|----------------|------------------|---------------------------|
| `agenda`       | Agenda           | `blueprints/agenda.py`    |
| `roosters`     | Roosters         | `blueprints/roosters.py`  |
| `standaardweek`| Standaardweek    | `blueprints/roosters.py`  |
| `geluiden`     | Geluiden         | `blueprints/geluiden.py`  |
| `logs`         | Logboek          | `blueprints/monitoring.py`|
| `settings`     | Voorkeuren       | `blueprints/settings.py`  |

Plus één nieuwe tab `gebruikers` (alleen voor admins), zie §5.

---

## 2. Datamodel

### 2.1 Nieuw bestand: `data/users.json`

```json
{
  "kees": {
    "pwhash": "pbkdf2:sha256:600000$abcd...$ef01...",
    "rol": "admin",
    "tabs": ["*"],
    "aangemaakt": "2026-05-14T10:00:00Z"
  },
  "anna": {
    "pwhash": "pbkdf2:sha256:600000$....$....",
    "rol": "gebruiker",
    "tabs": ["agenda", "roosters", "standaardweek"],
    "aangemaakt": "2026-05-14T10:05:00Z"
  }
}
```

Velden:

- **pwhash** — pbkdf2 hash via `werkzeug.security.generate_password_hash()`.
  Nooit platte tekst, ook niet "tijdelijk".
- **rol** — `"admin"` of `"gebruiker"`. Admin krijgt automatisch alle
  tabs (zie §3). Het `tabs`-veld wordt voor admins genegeerd maar wel
  bewaard, zodat een admin terug "gedemoveerd" kan worden zonder zijn
  rechten te verliezen.
- **tabs** — lijst van sleutels uit de tabel hierboven. `["*"]` betekent
  "alles" en wordt alleen door de migratie/admin-promotie gezet.
- **aangemaakt** — ISO-tijd, puur informatief voor het beheer-scherm.

### 2.2 Nieuw bestand: `core/users.py`

Een dunne abstractielaag bovenop het JSON-bestand. Reden: we willen
niet dat elke route zelf `users.json` openleest. Eén plek zorgt voor
locking, validatie en hashing.

Concrete functies (signatures):

```python
USERS_PATH = os.path.join(DATA_DIR, "users.json")

def load_users() -> dict[str, dict]: ...
def get_user(username: str) -> dict | None: ...
def verify_user(username: str, password: str) -> dict | None:
    """Return the user dict on success, None on failure."""

def create_user(username: str, password: str, rol: str, tabs: list[str]) -> None: ...
def update_user(username: str, *, password=None, rol=None, tabs=None) -> None: ...
def delete_user(username: str) -> None: ...

def is_admin(user: dict) -> bool: ...
def user_can_access(user: dict, tab: str) -> bool: ...
def admin_count() -> int:
    """Used to prevent deleting the last admin."""
```

Validatieregels (gegooid als `ValueError`, opgevangen in de blueprint):

- Username: alleen `[a-z0-9_-]`, lengte 2–32. Lowercase forceren.
- Wachtwoord: minstens 8 tekens. (Geen sterkere check; de app draait
  op een Pi binnen schoolnetwerk en complexiteit-rules werken in de
  praktijk vaak averechts.)
- Rol: alleen `"admin"` of `"gebruiker"`.
- Tabs: alle entries moeten in de bekende tab-set zitten (zie §1) of `"*"`.
- `delete_user`/`update_user` mag niet de laatste admin verwijderen of
  demoten.

Locking gebruikt `webinterface.locked_json` zoals overal in het project.

---

## 3. Auth uitbreiden (`core/auth.py`)

### 3.1 Wat verandert

`_check_password()` en `verify_password()` werken nu hard op `ADMIN_USER`
+ `ADMIN_HASH`. Die globals blijven bestaan voor de migratie en voor
HTTP Basic Auth-fallback, maar de check zelf wordt:

```python
from core import users as users_mod

def _check_password(username: str, plain: str) -> bool:
    user = users_mod.verify_user(username, plain)
    return user is not None
```

Let op de signature-wijziging: `_check_password` krijgt nu óók `username`
mee. Dat raakt `blueprints/auth.py` (zie §3.2) en de tests
(`tests/test_routes_auth_csrf.py`).

### 3.2 Login-flow (`blueprints/auth.py`)

```python
if request.method == "POST":
    u = (request.form.get("username") or "").strip().lower()
    p = request.form.get("password") or ""
    user = users_mod.verify_user(u, p)
    if user is not None:
        session.clear()
        session.permanent = True
        session["user"] = u
        session["rol"]  = user["rol"]
        session["tabs"] = user["tabs"]
        return redirect(next_url)
    flash(_("Onjuiste inloggegevens."))
```

Belangrijke detail: we slaan `rol` en `tabs` óók in de session op. Dat
spaart een file-read op elke request. Risico: als een admin de
rechten van een ingelogde gebruiker aanpast, ziet die gebruiker dat
pas na uitloggen. Acceptabel voor deze schaal, maar documenteren in
het beheer-scherm ("wijziging actief na volgende login").

### 3.3 Nieuwe decorators

In `core/auth.py` toevoegen naast `ui_login_required`:

```python
def tab_required(tab_naam: str):
    def deco(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not ui_logged_in():
                nxt = request.full_path if request.query_string else request.path
                return redirect(url_for("auth.login", next=nxt))
            tabs = session.get("tabs", [])
            if "*" not in tabs and tab_naam not in tabs:
                return render_template("403.html"), 403
            return view(*a, **kw)
        return wrapper
    return deco

def admin_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not ui_logged_in():
            return redirect(url_for("auth.login"))
        if session.get("rol") != "admin":
            return render_template("403.html"), 403
        return view(*a, **kw)
    return wrapper
```

`require_admin` (de bestaande JSON-decorator) wijzigt analoog: niet
alleen ingelogd zijn, maar ook rol = admin.

### 3.4 HTTP Basic Auth (daemon)

De daemon roept `/api/effectief-rooster` aan met Basic Auth.
`verify_password` (in `core/auth.py`) moet daarvoor blijven werken.

**Keuze: de daemon authenticeert tegen elk account in `users.json` dat
admin-rechten heeft.** Een gewone gebruiker (rol `"gebruiker"`) kan dus
geen Basic Auth doen tegen `/api/effectief-rooster`, ook niet als die
de tab-sleutel `roosters` in zijn lijst heeft. Reden: de daemon hoort
conceptueel bij de admin-rol, en zo voorkomen we dat een
laag-bevoegde gebruiker per ongeluk daemon-credentials krijgt.

Concrete implementatie in `verify_password`:

```python
@auth.verify_password
def verify_password(username, password):
    user = users_mod.verify_user(username, password)
    if user is None:
        # fallback voor bestaande installaties: env-var admin
        if username == ADMIN_USER and _check_legacy_password(password):
            return username
        return False
    if not users_mod.is_admin(user):
        return False
    return username
```

De env-var-fallback (`SCHOOLBELL_WEB_USER` + `SCHOOLBELL_WEB_PWHASH`)
blijft bestaan zodat bestaande Pi-installs blijven werken zonder
install.sh aan te passen.

---

## 4. Routes per tab beschermen

Per blueprint één regel veranderen: `@ui_login_required` →
`@tab_required("...")`. Concreet:

| Bestand                     | Routes                                | Tab-sleutel       |
|-----------------------------|---------------------------------------|-------------------|
| `blueprints/agenda.py`      | `/agenda`, `/agenda/import-vakanties`, `/agenda/refresh-vakanties` | `agenda` |
| `blueprints/roosters.py`    | `/roosters/...`                       | `roosters`        |
| `blueprints/roosters.py`    | `/standaardweek`                      | `standaardweek`   |
| `blueprints/geluiden.py`    | `/geluiden*`                          | `geluiden`        |
| `blueprints/monitoring.py`  | `/logs`                               | `logs`            |
| `blueprints/settings.py`    | `/settings`, `/api/settings`          | `settings`        |

De API endpoints (`/api/...`) die door fetch() worden aangeroepen,
krijgen `tab_required` óók — maar omdat fetch() geen redirect wil zien,
moet `tab_required` JSON-401/403 teruggeven als de request een
`Accept: application/json` header heeft of als het pad met `/api/` begint.
Eenvoudigste implementatie: detecteer dat in `tab_required` zelf.

**Niet** veranderen: `/`, `/now`, `/api/now`, `/healthz`,
`/api/effectief-rooster`. Die zijn nu al openbaar of via Basic Auth.

---

## 5. Beheer-UI: nieuwe tab "Gebruikers"

### 5.1 Nieuw blueprint: `blueprints/gebruikers.py`

Routes:

- `GET  /gebruikers`              — lijst + formulier voor nieuwe
- `POST /gebruikers/nieuw`        — gebruiker aanmaken
- `POST /gebruikers/<u>/wijzig`   — rol/tabs aanpassen
- `POST /gebruikers/<u>/wachtwoord` — wachtwoord resetten
- `POST /gebruikers/<u>/verwijder` — verwijderen

Alle routes met `@admin_required`. Form-submits gebruiken het bestaande
confirm-modal (`data-confirm="..."`).

### 5.2 Nieuw template: `templates/gebruikers.html`

Layout: tabel met alle gebruikers (kolommen: naam, rol, tabs, knoppen).
Daaronder een paneel "Nieuwe gebruiker" met:

- Tekstveld gebruikersnaam
- Wachtwoord + bevestiging
- Radio: rol (admin/gebruiker)
- Checkboxes: één per tab (alleen relevant bij rol=gebruiker; via JS
  uitgrijzen wanneer admin geselecteerd is)

### 5.3 Nav-link in `base.html`

```jinja
{% if session.get('rol') == 'admin' %}
  <a class="sb-nav__link {{ 'active' if tab=='gebruikers' else '' }}"
     href="{{ url_for('gebruikers.lijst') }}">{{ _('Gebruikers') }}</a>
{% endif %}
```

### 5.4 Tabs cosmetisch verbergen

Voor alle bestaande nav-links wikkel je dezelfde `{% if %}` om de `<a>`:

```jinja
{% if 'agenda' in (session.get('tabs') or []) or '*' in (session.get('tabs') or []) %}
  <a class="sb-nav__link ..." href="...">{{ _('Agenda') }}</a>
{% endif %}
```

Dit wordt repetitief — overweeg een Jinja-macro of een
context-processor die een `mag_tab(naam)` functie aan de templates
geeft:

```python
@app.context_processor
def _inject_perms():
    tabs = session.get("tabs") or []
    return {"mag_tab": lambda t: "*" in tabs or t in tabs}
```

Dan in `base.html`:

```jinja
{% if mag_tab('agenda') %}<a ...>Agenda</a>{% endif %}
```

---

## 6. Migratie van bestaande installaties

Bij elke app-start (in `webinterface.py` of in `core/users.py` lazy):
als `users.json` ontbreekt, en `ADMIN_USER` + `ADMIN_HASH` zijn gezet
in de env, dan maak je `users.json` aan met één entry:

```python
{
  ADMIN_USER: {
    "pwhash": ADMIN_HASH,
    "rol": "admin",
    "tabs": ["*"],
    "aangemaakt": <now>,
  }
}
```

Daarna kan de admin via de UI zijn wachtwoord aanpassen en extra
gebruikers maken. De env-vars blijven als noodingang voor de daemon
en als rescue-pad ("ik ben mijn admin-wachtwoord vergeten — zet env
weer aan, herstart, en je kunt weer in").

Documenteer dit in `docs/admin-guide.md`.

---

## 7. Tests

Bestaande tests die mogelijk breken:

- `tests/test_settings_huisstijl_api.py` — zet `ADMIN_HASH` als env-var
  bij import van `webinterface`. Door de migratie (§6) krijgt het
  test-process automatisch een `users.json` met die admin erin —
  controleren dat de fixture nog werkt of een tmp-dir voor `DATA_DIR`
  gebruiken zodat tests elkaar niet besmetten.
- `tests/test_routes_auth_csrf.py` — login-flow gebruikt sessions; werkt
  na de wijziging nog steeds zolang we admin als user accepteren.
- `tests/conftest.py` — fixture voor "ingelogde client" uitbreiden met
  een variant voor "ingelogde niet-admin met beperkte tabs".

Nieuwe tests:

- `tests/test_users_module.py`
  - `create_user` + `verify_user` happy path
  - validatieregels: te kort wachtwoord, ongeldige username, ongeldige tab
  - `delete_user` blokkeert verwijderen van laatste admin
- `tests/test_tab_required.py`
  - gebruiker met `tabs=["agenda"]` krijgt 403 op `/roosters`
  - gebruiker met `tabs=["*"]` mag overal
  - niet-ingelogd krijgt redirect naar `/login`
  - fetch()-style request (Accept: application/json) krijgt 403 JSON ipv HTML
- `tests/test_admin_required.py`
  - gewone gebruiker krijgt 403 op `/gebruikers`
- `tests/test_migration.py`
  - als `users.json` ontbreekt en env-vars zijn gezet, wordt het
    bestand correct aangemaakt bij eerste request.

---

## 8. Volgorde van implementatie

Klein-naar-groot, zodat na elke fase de app nog steeds werkt:

1. **`core/users.py` + tests**. Pure logica, geen Flask. Op zichzelf
   te testen en te begrijpen. Goed leermateriaal voor Python.
2. **Migratie + login uitbreiden**. `users.json` wordt aangemaakt bij
   eerste run, `blueprints/auth.py` checkt voortaan tegen users-module.
   App gedraagt zich nog identiek: één admin, zelfde wachtwoord.
3. **`tab_required` + `admin_required` decorators**. Routes
   `@ui_login_required` vervangen door `@tab_required("...")`.
   Functioneel nog niets veranderd voor de admin (heeft `["*"]`), maar
   de infrastructuur staat klaar.
4. **Beheer-UI**: blueprint + template + nav-link. Vanaf nu kun je
   gebruikers aanmaken via de browser.
5. **UI-verbergen** van tabs op basis van permissions
   (`mag_tab` context-processor).
6. **Daemon-auth check** (§3.4) afronden + documentatie bijwerken.

Elke stap is een eigen pull-request / commit, met groene tests.

---

## 9. Bewuste beperkingen (out of scope voor deze iteratie)

Deze drie zaken zijn besproken en bewust *niet* opgenomen. Documenteer
ze in `docs/admin-guide.md` zodat het verwachtingsmanagement duidelijk
is.

- **Wachtwoord-reset zonder admin-toegang.** Geen e-mail-flow. Als een
  gebruiker zijn wachtwoord vergeet: een admin reset het via de
  beheer-UI. Als de admin zelf zijn wachtwoord kwijt is: de
  env-var-fallback weer activeren in `web.env`, herstarten, en via de
  beheer-UI een nieuw wachtwoord zetten.
- **Sessie-invalidatie bij rechtenwijziging.** Aanpassingen door een
  admin worden pas actief nadat de betreffende gebruiker opnieuw
  inlogt. De beheer-UI toont expliciet de melding "wijziging actief
  na volgende login".
- **Account-lockout na X mislukte pogingen.** Geen automatische
  lockout. Wel: elke mislukte login wordt via `log_event` weggeschreven
  zodat misbruik zichtbaar is in het Logboek.

---

## Bijlage A: voorbeeld `core/users.py` (skelet)

```python
"""User store backed by data/users.json.

Pure module — no Flask imports. Easy to unit-test.
"""
import os
import re
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

import webinterface as wi  # for locked_json + DATA_DIR


USERS_PATH = os.path.join(wi.DATA_DIR, "users.json")

USERNAME_RE = re.compile(r"^[a-z0-9_-]{2,32}$")
KNOWN_TABS = {"agenda", "roosters", "standaardweek",
              "geluiden", "logs", "settings", "gebruikers"}


def _default() -> dict:
    return {}


def load_users() -> dict:
    return wi.load_json(USERS_PATH, _default())


def get_user(username: str) -> dict | None:
    return load_users().get(username)


def verify_user(username: str, password: str) -> dict | None:
    user = get_user(username)
    if not user:
        return None
    if not check_password_hash(user["pwhash"], password):
        return None
    return user


def is_admin(user: dict) -> bool:
    return user.get("rol") == "admin"


def user_can_access(user: dict, tab: str) -> bool:
    tabs = user.get("tabs") or []
    return "*" in tabs or tab in tabs


def admin_count() -> int:
    return sum(1 for u in load_users().values() if u.get("rol") == "admin")


def _validate(username: str, password: str | None, rol: str, tabs: list[str]):
    if not USERNAME_RE.match(username):
        raise ValueError("Ongeldige gebruikersnaam")
    if password is not None and len(password) < 8:
        raise ValueError("Wachtwoord moet minstens 8 tekens hebben")
    if rol not in ("admin", "gebruiker"):
        raise ValueError("Ongeldige rol")
    for t in tabs:
        if t != "*" and t not in KNOWN_TABS:
            raise ValueError(f"Onbekende tab: {t}")


def create_user(username: str, password: str, rol: str, tabs: list[str]) -> None:
    username = username.strip().lower()
    _validate(username, password, rol, tabs)
    with wi.locked_json(USERS_PATH, _default()) as (data, save):
        if username in data:
            raise ValueError("Gebruiker bestaat al")
        data[username] = {
            "pwhash": generate_password_hash(password),
            "rol": rol,
            "tabs": ["*"] if rol == "admin" else list(tabs),
            "aangemaakt": datetime.now(timezone.utc).isoformat(),
        }
        save(data)


def update_user(username: str, *, password=None, rol=None, tabs=None) -> None:
    with wi.locked_json(USERS_PATH, _default()) as (data, save):
        if username not in data:
            raise ValueError("Gebruiker niet gevonden")
        u = data[username]
        new_rol = rol if rol is not None else u["rol"]
        new_tabs = tabs if tabs is not None else u["tabs"]
        _validate(username, password, new_rol, new_tabs)
        if u["rol"] == "admin" and new_rol != "admin":
            if admin_count() <= 1:
                raise ValueError("Laatste admin kan niet gedemoveerd worden")
        u["rol"] = new_rol
        u["tabs"] = ["*"] if new_rol == "admin" else list(new_tabs)
        if password:
            u["pwhash"] = generate_password_hash(password)
        save(data)


def delete_user(username: str) -> None:
    with wi.locked_json(USERS_PATH, _default()) as (data, save):
        if username not in data:
            return
        if data[username]["rol"] == "admin" and admin_count() <= 1:
            raise ValueError("Laatste admin kan niet verwijderd worden")
        del data[username]
        save(data)
```

Dit is alleen een schets — niet "klaar voor productie". De tests
(§7) zijn waar je tegenaan loopt of het skelet klopt.
