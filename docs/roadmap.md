# Schoolbell Roadmap

Dit document verzamelt de geplande uitbreidingen voor Schoolbell. Het
is bewust niet streng gedateerd — Schoolbell is een hobby/school-
project en de volgorde hangt af van wat eerst nodig blijkt in de
praktijk. Wel staat per onderdeel een korte motivatie en ruwe scope,
zodat duidelijk is *wat* het inhoudt en *waarom* het op de lijst staat.

Status-legenda:

- **Gepland** — besloten dat het komt, nog niet aan begonnen.
- **Gepland (per aanvraag)** — wordt opgepakt zodra een aanvraag
  binnenkomt; niet op eigen initiatief.
- **In uitwerking** — ontwerp is af, code volgt.
- **In ontwikkeling** — er wordt actief aan gewerkt.

Een afgeronde feature verdwijnt van deze lijst en wordt opgenomen in
de README of admin-guide.

---

## HTTPS

**Status:** Gepland

Nu draait Nginx alleen op poort 80 (plain HTTP). Voor een schoolnetwerk
binnen één gebouw is dat hanteerbaar, maar:

- Login-cookies kunnen meegelezen worden door wie het netwerk
  passief afluistert.
- Moderne browsers tonen "Niet veilig" in de adresbalk, wat het
  onnodig onprofessioneel laat lijken.
- `SCHOOLBELL_SECURE_COOKIES` staat nu op `0` omdat anders de login
  breekt — een waarschuwingsteken in de configuratie.

**Scope (ruwe schets):**

- Nginx-config uitbreiden met een `listen 443 ssl`-server-block.
- Twee paden ondersteunen:
  - **Self-signed certificaat** voor LAN-only installs (snel,
    geen DNS nodig, browser-waarschuwing acceptabel binnen school).
  - **Let's Encrypt** via `certbot` voor wie de Pi via een
    DNS-naam bereikbaar maakt.
- `install.sh` vragen of HTTPS aangezet moet worden, en zo ja welk
  pad.
- 80 → 443 redirect-server-block.
- `SCHOOLBELL_SECURE_COOKIES=1` als default in `web.env` zodra HTTPS
  draait.
- Documenteren in admin-guide: certificaat-vernieuwing, troubleshoot
  als certbot faalt.

---

## Multi-user systeem met per-tab rechten

**Status:** In uitwerking

Op dit moment is er één admin-account (uit env-vars). Voor scholen
met meerdere personeelsleden die het belschema beheren is dat te
beperkt: ofwel je deelt één wachtwoord (geen audit), ofwel iedereen
moet voor elke wijziging langs één persoon.

**Doel:** meerdere benoemde accounts, elk met een eigen
wachtwoord, en per account de mogelijkheid om alleen bepaalde tabs
te zien/bewerken (bv. een conciërge die alleen Geluiden mag beheren,
of een stagiair die alleen Agenda mag bekijken).

**Het volledige implementatieplan staat in
[multi-user-plan.md](multi-user-plan.md).** Daarin staat per stap
wat er moet gebeuren in `core/users.py`, `core/auth.py`, de
blueprints en de templates.

**Bewust buiten scope van de eerste iteratie:**

- Geen wachtwoord-reset zonder admin (zie volgende item).
- Geen automatische lockout na X mislukte pogingen — wel wordt elke
  mislukte login gelogd.
- Sessie-invalidatie bij rechtenwijziging gebeurt pas na re-login.

---

## Wachtwoord-reset via e-mail

**Status:** Gepland (na multi-user)

Volgt logisch ná het multi-user systeem. Zolang er één admin is, is
"env-var fallback + herstart" een acceptabele rescue. Met meerdere
benoemde accounts wordt het onhandig als gewone gebruikers voor elke
vergeten wachtwoord langs de admin moeten.

**Scope (ruwe schets):**

- E-mailadres als optioneel veld in `users.json` toevoegen.
- SMTP-config in `/etc/schoolbell/web.env` (host, poort, user, pass,
  from-adres).
- Nieuwe routes: `/wachtwoord-vergeten` (vraagt e-mailadres en
  verstuurt token-mail), `/wachtwoord-reset/<token>` (formulier).
- Tokens kort houdbaar (15 minuten), eenmalig bruikbaar, opgeslagen
  in een aparte JSON met TTL — niet in `users.json` zelf zodat de
  user-store schoon blijft.
- Documenteren: SMTP-configuratie, troubleshooting als de mail niet
  aankomt.

Open vraag: bij een schoolinstallatie is een eigen SMTP-server
zelden voorhanden. Een alternatief is "reset-link in het Logboek
laten verschijnen, kopieer-link delen via een ander kanaal". Minder
elegant, maar veel installs hebben geen mailserver.

---

## Extra talen op aanvraag

**Status:** Gepland (per aanvraag)

Schoolbell ondersteunt op dit moment **Nederlands**, **Engels**,
**Duits** en **Frans** als volwaardige UI-talen. Vertalingen worden
via `gettext`/`.po`-bestanden beheerd (zie `translations/` en
CONTRIBUTING.md).

Er staan geen extra talen vast op de planning, maar het toevoegen
van een nieuwe taal is een licht traject — vooral vertaalwerk, geen
Python-werk. Aanvragen welkom.

**Scope per nieuwe taal:**

- `pybabel init -l <code>` om een nieuw catalog-bestand te maken.
- Strings vertalen via een `.po`-editor (Poedit) of handmatig.
- `pybabel compile` om `.mo`-bestanden te genereren.
- `core/i18n.py` aanvullen in `SUPPORTED_LOCALES` zodat de taal in
  het dropdown verschijnt.
- Testen dat date-formatting van Babel klopt voor die locale
  (vakantie-datums, weekdagen).

Of een aanvraag terechtkomt hangt vooral af van de beschikbaarheid
van een vertaler — niet van programmeerwerk.

---

## Vragen, suggesties, eigen wensen

Open een issue op de repository, of werk dit bestand bij in een
pull-request. Een roadmap zonder context is niet veel waard, dus geef
liefst een korte motivatie ("waarom is dit handig") mee.
