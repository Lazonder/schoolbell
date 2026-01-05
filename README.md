# IVKO Schoolbel

Een Raspberry Pi-gebaseerde schoolbel die automatisch geluiden afspeelt volgens instelbare roosters, met een webinterface voor beheer.
Ontwikkeld voor gebruik binnen het schoolnetwerk (optioneel via VPN extern bereikbaar).

---

## Overzicht

### Architectuur

```
Browser
  ↓ HTTPS
Nginx
  ↓ proxy_pass
Gunicorn (Flask app)
  ↓ API
schoolbell-daemon (systemd)
```

### Componenten

| Component             | Functie                                                 |
| --------------------- | ------------------------------------------------------- |
| `webinterface.py`     | Flask-webapp (beheer, agenda, roosters, geluiden, logs) |
| `schoolbelldaemon.py` | Achtergrondproces dat belmomenten uitvoert              |
| `settings_store.py`   | Leest en schrijft instellingen (JSON)                   |
| `health_check.py`     | Testscript voor periodieke functionele checks           |
| `templates/`          | HTML/Jinja2-templates                                   |
| `static/geluiden/`    | Opslag voor mp3 / wav / ogg                             |
| `data/`               | JSON-bestanden voor roosters, planning en logs          |
| `certs/`              | TLS-certificaten (indien niet via Nginx geregeld)       |

---

## Functionaliteit

### Webinterface

* Roosters aanmaken en bewerken
* Standaardweek en agenda per dag instellen
* Geluiden uploaden en verwijderen
* Logboek bekijken
* Instellingen aanpassen (volume, uploadlimiet, polling-interval)

### Daemon

* Haalt periodiek het effectieve rooster op via de API
* Speelt belgeluiden af met ingesteld volume
* Logt elk belmoment in `data/events.jsonl`
* Herlaadt instellingen direct na `SIGHUP`

---

## Beveiliging

* HTTPS via Nginx
* Sessiegebaseerde login met CSRF-bescherming
* Basic Auth voor daemon-API
* Upload-limiet en extensie-check via `config.json`
* Webinterface alleen toegankelijk binnen LAN/VPN

---

## Installatie

```bash
cd /home/pi/schoolbell
python3 -m venv venv
source venv/bin/activate
pip install flask flask_httpauth pygame schedule requests gunicorn
```

Controleer dat deze bestanden bestaan:

```
/etc/schoolbell/config.json
/home/pi/schoolbell/certs/   (alleen nodig als TLS niet via Nginx loopt)
```

### Voorbeeld `/etc/schoolbell/config.json`

```json
{
  "volume_percent": 70,
  "max_file_size_mb": 15,
  "poll_interval_sec": 2,
  "timezone": "Europe/Amsterdam",
  "allowed_extensions": [".mp3", ".wav", ".ogg"]
}
```

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
  --bind 127.0.0.1:8000 \
  webinterface:app
```

### Nginx

* Verzorgt HTTPS
* Reverse proxy naar Gunicorn
* Afhandeling van certificaten
* Eventueel IP-restricties

Toegang:

```
https://<pi-ip>/
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

## Health-check

Gebruik `health_check.py`:

```bash
source venv/bin/activate
export SCHOOLBELL_BASE="https://127.0.0.1"
export SCHOOLBELL_WEB_USER="admin"
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

