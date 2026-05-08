# IVKO Schoolbel

Een Raspberry Pi-gebaseerde schoolbel die automatisch geluiden afspeelt volgens instelbare roosters, met een webinterface voor beheer.
Ontwikkeld voor gebruik binnen het schoolnetwerk (optioneel via VPN extern bereikbaar).

---

## Overzicht

### Architectuur

```
Browser
  ↓ HTTP (LAN; HTTPS staat op de todo-lijst)
Nginx (poort 80)
  ↓ proxy_pass
Gunicorn (Flask app, 127.0.0.1:5000)
  ↓ API (Basic Auth, localhost)
schoolbell-daemon (systemd)
```

### Componenten

| Component             | Functie                                                 |
| --------------------- | ------------------------------------------------------- |
| `webinterface.py`     | Flask-webapp (beheer, agenda, roosters, geluiden, logs) |
| `schoolbelldaemon.py` | Achtergrondproces dat belmomenten uitvoert              |
| `settings_store.py`   | Leest en schrijft instellingen (JSON)                   |
| `vakanties_fetcher.py` | Schraapt vakantiedata van rijksoverheid.nl + parser    |
| `health_check.py`     | Testscript voor periodieke functionele checks           |
| `templates/`          | HTML/Jinja2-templates                                   |
| `static/geluiden/`    | Opslag voor mp3 / wav / ogg                             |
| `data/`               | JSON-bestanden voor roosters, planning, logs, vakanties, daemon-heartbeat |
| `certs/`              | TLS-certificaten (indien niet via Nginx geregeld)       |

---

## Functionaliteit

### Webinterface

* Roosters aanmaken en bewerken
* Standaardweek en agenda per dag instellen — agenda is responsive en wordt op smalle schermen (≤700px) gestapeld als kaart-per-week
* Geluiden uploaden en verwijderen
* Logboek bekijken
* Instellingen aanpassen (volume, uploadlimiet, polling-interval)
* Vakantieweken automatisch importeren van [rijksoverheid.nl](https://www.rijksoverheid.nl/onderwerpen/schoolvakanties), per regio (Noord / Midden / Zuid)
* In **Voorkeuren** een statuspaneel met de opgeslagen schooljaren, laatste fetch-tijdstip, en een toggle om de scrape-functionaliteit volledig uit te zetten (bv. voor gebruik buiten Nederland)
* Heartbeat-indicator in de header — een klein groen/rood bolletje dat aangeeft of de daemon recent heeft gepoll'd; klik erop voor de laatste poll-tijd

### Daemon

* Haalt periodiek het effectieve rooster op via de API
* Speelt belgeluiden af met ingesteld volume
* Logt elk belmoment in `data/events.jsonl`
* Herlaadt instellingen direct na `SIGHUP`
* Schrijft per poll-iteratie een heartbeat-bestand (`data/daemon_heartbeat.json`) — gelezen door de webinterface (header-indicator) en door `/healthz` (zie Monitoring)
* Ververst maandelijks de vakantiedata van rijksoverheid.nl, mits de scrape-toggle in Voorkeuren aanstaat. De manuele "Verversen"-knop op Agenda blijft altijd beschikbaar.

---

## Beveiliging

* Nginx draait voorlopig op **plain HTTP** (poort 80). HTTPS staat op de roadmap — tot die tijd alleen in vertrouwd LAN/VPN draaien.
* Sessiegebaseerde login met CSRF-bescherming
* Basic Auth voor de daemon → web-API (over localhost)
* Admin-wachtwoord wordt bij installatie willekeurig gegenereerd en als werkzeug-scrypt-hash opgeslagen (zie [Credentials & wachtwoord](#credentials--wachtwoord))
* Session-secret wordt bij installatie willekeurig gegenereerd (64 hex-bytes)
* Upload-limiet en extensie-check via `config.json`

---

## Installatie in 3 commando’s

> Getest op **Raspberry Pi OS (Debian)**
> Vereist: sudo-rechten en internetverbinding

```bash
git clone https://github.com/<jouw-account>/schoolbell.git
cd schoolbell
sudo ./install.sh
```

> **Belangrijk bij de eerste run:** het script genereert een willekeurig admin-wachtwoord en toont dat **één keer** in de output, in een omkaderd blok. Noteer het meteen. Kwijtgeraakt? Zie [Credentials & wachtwoord](#credentials--wachtwoord).

Na afloop:

* draait de webinterface via *Nginx + Gunicorn*
* draait de schoolbel als *systemd-daemon*
* zijn credentials, session-secret, logging, logrotate en basisconfig ingericht

Open daarna in je browser:

```
http://<ip-van-de-pi>/
```

---

## Wat doet het installatiescript?

Het script `install.sh` voert automatisch uit:

* installeren van systeempackages (nginx, audio, python)
* aanmaken van een Python virtual environment
* installeren van Python dependencies (`requirements.txt`)
* aanmaken van:

  * `/etc/schoolbell/config.json`
  * `/etc/schoolbell/web.env` en `daemon.env` (admin-credentials + session-secret, alleen bij eerste run)
  * `data/` en `static/geluiden/`
* installeren en activeren van:

  * `schoolbell-web.service` (Gunicorn)
  * `schoolbell-daemon.service`
* configureren van:

  * Nginx reverse proxy (poort 80 → Gunicorn op 5000)
  * logrotate voor `data/events.jsonl`

Het script is **veilig opnieuw uit te voeren** (idempotent): bestaande env-files worden niet overschreven, en beide services worden aan het eind altijd herstart zodat nieuwe code/config direct wordt opgepikt.

---
### Voorbeeld `/etc/schoolbell/config.json`

```json
{
  "volume_percent": 70,
  "max_file_size_mb": 15,
  "poll_interval_sec": 2,
  "allowed_extensions": [".mp3", ".wav", ".ogg"]
}
```

---

## Credentials & wachtwoord

Bij de eerste `install.sh`-run worden twee env-files aangemaakt. Beide hebben `chmod 640` met eigenaar `root:pi`.

* `/etc/schoolbell/web.env` — wordt gelezen door `schoolbell-web.service`:
  * `SCHOOLBELL_WEB_USER` — admin-gebruikersnaam
  * `SCHOOLBELL_WEB_PWHASH` — werkzeug-scrypt-hash van het wachtwoord
  * `SCHOOLBELL_SECRET` — Flask session-secret (64 hex)
  * `SCHOOLBELL_SECURE_COOKIES` — zie hieronder
* `/etc/schoolbell/daemon.env` — wordt gelezen door `schoolbell-daemon.service`:
  * `SCHOOLBELL_WEB_USER` — dezelfde gebruikersnaam
  * `SCHOOLBELL_WEB_PASS` — het wachtwoord in klare tekst (de daemon moet ermee inloggen op de web-API, dus een hash volstaat daar niet)

### Wachtwoord kwijt?

Het wachtwoord wordt maar één keer in de install-output getoond. Terug te vinden op de Pi:

```bash
sudo cat /etc/schoolbell/daemon.env
```

### Wachtwoord wijzigen

Genereer eerst een nieuwe hash:

```bash
/home/pi/schoolbell/venv/bin/python -c \
  'from werkzeug.security import generate_password_hash; print(generate_password_hash("nieuw-wachtwoord"))'
```

Werk vervolgens beide files bij:
* `web.env` → plak de hash in `SCHOOLBELL_WEB_PWHASH`
* `daemon.env` → plak het klare wachtwoord in `SCHOOLBELL_WEB_PASS`

Daarna:

```bash
sudo systemctl restart schoolbell-web.service schoolbell-daemon.service
```

### `SCHOOLBELL_SECURE_COOKIES`

Regelt de `Secure`-flag op de sessie-cookie.

* `0` — cookies mogen ook over plain HTTP. **Noodzakelijk zolang Nginx op HTTP draait**, anders weigert de browser de cookie terug te sturen en werkt inloggen niet.
* `1` — cookies alleen over HTTPS. Zet deze waarde zodra je HTTPS op Nginx hebt geconfigureerd.

De installer zet deze standaard op `0`. Tip: edit `/etc/schoolbell/web.env` en herstart `schoolbell-web.service` om te wisselen.

---

## Webserver (Nginx + Gunicorn)

### Gunicorn

* Draait de Flask-app
* Meerdere *workers* en *threads*
* Timeouts voorkomen vastlopende requests
* Periodieke worker refresh voorkomt geheugenlekken

Voorbeeld (systemd):

```
ExecStart=/home/pi/schoolbell/venv/bin/gunicorn \
  --workers 2 \
  --threads 4 \
  --timeout 30 \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --bind 127.0.0.1:5000 \
  webinterface:app
```

### Nginx

* Luistert op poort 80 (plain HTTP)
* Reverse proxy naar Gunicorn op `127.0.0.1:5000`
* HTTPS + certificaten zijn **nog niet geconfigureerd** — optionele toekomstige uitbreiding (self-signed of Let's Encrypt, daarna 80→443 redirect)
* Eventueel IP-restricties via `allow`/`deny` in het server-blok

Toegang:

```
http://<pi-ip>/
```

---

## Starten & beheren

### Services

```bash
sudo systemctl start schoolbell-web.service
sudo systemctl start schoolbell-daemon.service
```

### Herladen instellingen daemon

```bash
sudo systemctl kill -s HUP schoolbell-daemon.service
```

### Herstarten

```bash
sudo systemctl restart schoolbell-web.service
sudo systemctl restart schoolbell-daemon.service
```

---

## Logging & logrotate

### Runtime logs

* Web: `journalctl -u schoolbell-web.service`
* Daemon: `journalctl -u schoolbell-daemon.service`

### Bel-events

* Bestand: `data/events.jsonl`

Logrotate is geconfigureerd voor:

* Periodieke rotatie
* Compressie (`.gz`)
* Behoud van oudere logs
* Geen onbegrensde groei van het logbestand

---

## Monitoring

### `/healthz` endpoint

Korte JSON-statuscheck zonder authenticatie, bedoeld voor externe uptime-monitoring (bv. Uptime Kuma, een cron met `curl`, of een Pi-statusbord).

```bash
curl http://<pi-ip>/healthz
```

Antwoord:

* **200 OK** als alle checks slagen
* **503 Service Unavailable** als één of meer checks falen

De gecontroleerde keys zijn altijd alle vier aanwezig in de response, zodat een monitoring-tool op een specifieke key kan alerteren:

* `data_dir_writable` — kan de webinterface naar `data/` schrijven
* `audio_dir_readable` — kan `static/geluiden/` worden gelezen
* `settings_loadable` — laadt `Settings.load()` zonder error
* `daemon_alive` — heeft de daemon recent (binnen ~2× poll-interval) een heartbeat geschreven

### `health_check.py`

Uitgebreidere integratiecheck die echt inlogt, een aantal pagina's bezoekt, en optioneel een upload/delete-rondje doet. Dit script vereist credentials.

```bash
source venv/bin/activate
# Via Nginx (poort 80), plain HTTP zolang HTTPS nog niet aan staat.
export SCHOOLBELL_BASE="http://127.0.0.1"
export SCHOOLBELL_WEB_USER="admin"
# Zie /etc/schoolbell/daemon.env voor het gegenereerde wachtwoord.
export SCHOOLBELL_WEB_PASS="jouw-wachtwoord"
python health_check.py
```

Optioneel upload/delete testen:

```bash
export SCHOOLBELL_HEALTH_UPLOAD=1
python health_check.py
```

De check controleert:

* Login en CSRF-bescherming
* Bereikbaarheid van `/geluiden`, `/roosters`, `/logs`
* API’s `/api/settings` en `/api/effectief-rooster`

---

## Onderhoud

* **Back-up**: `data/` en `/etc/schoolbell/config.json`
* **Geluidstest**: upload kort mp3’tje en gebruik ▶ in de interface
* **Debuggen**: check `journalctl` en `events.jsonl`

