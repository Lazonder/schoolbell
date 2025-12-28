🎓 IVKO Schoolbel

Een Raspberry Pi-gebaseerde schoolbel die automatisch geluiden afspeelt volgens instelbare roosters, met een webinterface voor beheer.

🚀 Overzicht
Component	Functie
webinterface.py	Flask-webapp (beheer, uploads, agenda, instellingen)
schoolbelldaemon.py	Achtergrondproces dat belmomenten uitvoert
settings_store.py	Leest en schrijft instellingen (JSON)
health_check.py	Testscript om periodiek de werking te controleren
templates/	HTML-templates voor alle pagina’s
static/geluiden/	Opslag voor mp3/wav/ogg-bestanden
data/	JSON-data voor roosters, planning en logs
certs/	HTTPS-certificaten (self-signed of Let’s Encrypt)
🧩 Functionaliteit

Webinterface

Roosters aanmaken en bewerken

Standaardweek en agenda instellen

Geluiden uploaden en verwijderen

Logboek bekijken

Voorkeuren aanpassen (volume, max. bestandsgrootte, polling-interval)

Daemon

Haalt elke paar seconden het actuele rooster op via de API

Speelt belgeluiden af met het ingestelde volume

Schrijft bel-events naar data/events.jsonl

Herlaadt instellingen direct na SIGHUP

Beveiliging

HTTPS met certificaat in certs/

Sessie-login met CSRF-bescherming

Basic Auth voor de daemon-API

Upload-limiet en extensie-check uit config.json

⚙️ Installatie
cd /home/pi/schoolbell
python3 -m venv venv
source venv/bin/activate
pip install flask flask_httpauth pygame schedule requests


Controleer dat deze bestanden bestaan:

/etc/schoolbell/config.json
certs/cert.pem
certs/key.pem


Voorbeeld config.json:

{
  "volume_percent": 70,
  "max_file_size_mb": 15,
  "poll_interval_sec": 2,
  "timezone": "Europe/Amsterdam",
  "allowed_extensions": [".mp3", ".wav", ".ogg"]
}

▶️ Starten

Webinterface

sudo systemctl start schoolbell-web.service


Toegang via
👉 https://<pi-ip>:5000

Daemon

sudo systemctl start schoolbell-daemon.service


Na wijzigingen in instellingen:

sudo systemctl kill -s HUP schoolbell-daemon.service

🔍 Health-check

Gebruik health_check.py om de werking te testen:

source venv/bin/activate
export SCHOOLBELL_BASE="https://127.0.0.1:5000"
export SCHOOLBELL_WEB_USER="admin"
export SCHOOLBELL_WEB_PASS="jouw-wachtwoord"
python health_check.py


Optioneel ook upload/delete testen:

export SCHOOLBELL_HEALTH_UPLOAD=1
python health_check.py


De check controleert:

login en CSRF-beveiliging

bereikbaarheid van /geluiden, /roosters, /logs

API’s /api/settings en /api/effectief-rooster

📂 Belangrijke mappen
Pad	Inhoud
/home/pi/schoolbell/data/	JSON-bestanden voor roosters, planning, logs
/home/pi/schoolbell/static/geluiden/	Alle audiobestanden
/etc/schoolbell/config.json	Algemene instellingen
/home/pi/schoolbell/templates/	Jinja2-templates
/home/pi/schoolbell/certs/	Certificaten voor HTTPS
🛠️ Onderhoud

Back-up: bewaar de mappen data/ en /etc/schoolbell/config.json

Geluidstest: upload een kort mp3’tje en druk op ▶ in de interface

Daemon herstarten:
sudo systemctl restart schoolbell-daemon.service

Webinterface herstarten:
sudo systemctl restart schoolbell-web.service

Log bekijken:
journalctl -u schoolbell-daemon.service -f
of tail -f data/events.jsonl
