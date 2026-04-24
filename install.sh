#!/usr/bin/env bash
# IVKO Schoolbel installer
# Tested on Raspberry Pi OS (Debian)

set -euo pipefail

APP_USER="${SUDO_USER:-pi}"
APP_HOME="$(eval echo "~${APP_USER}")"
APP_DIR="${APP_HOME}/schoolbell"

CONFIG_DIR="/etc/schoolbell"
LOG_DIR="/var/log/schoolbell"

GUNICORN_BIND="127.0.0.1:5000"
GUNICORN_WORKERS="2"
GUNICORN_THREADS="4"
GUNICORN_TIMEOUT="30"
GUNICORN_MAX_REQUESTS="1000"
GUNICORN_MAX_REQUESTS_JITTER="100"

echo "== IVKO Schoolbel installer =="
echo "User: ${APP_USER}"
echo "App dir: ${APP_DIR}"

if [[ ! -d "${APP_DIR}" ]]; then
  echo "ERROR: verwacht repo in ${APP_DIR}."
  echo "Clone de repo daarheen, of pas APP_DIR aan in dit script."
  exit 1
fi

if [[ ! -f "${APP_DIR}/requirements.txt" ]]; then
  echo "ERROR: requirements.txt ontbreekt in ${APP_DIR}."
  exit 1
fi

echo "== 1) System packages =="
apt-get update
# libasound2 is het runtime-pakket waar pygame mee linkt. Eerder stond
# hier libasound2-dev (header-files); die zijn alleen nodig om iets te
# compileren dat ermee wil linken, niet om te draaien.
apt-get install -y \
  nginx \
  python3 python3-venv python3-pip \
  logrotate \
  ffmpeg \
  alsa-utils \
  libasound2

echo "== 2) Python venv + deps (requirements.txt) =="
cd "${APP_DIR}"
if [[ ! -d venv ]]; then
  sudo -u "${APP_USER}" python3 -m venv venv
fi

sudo -u "${APP_USER}" bash -lc "
  ${APP_DIR}/venv/bin/pip install --upgrade pip &&
  ${APP_DIR}/venv/bin/pip install -r ${APP_DIR}/requirements.txt
"

echo "== 3) Directories + permissions =="
mkdir -p "${CONFIG_DIR}" "${LOG_DIR}"
mkdir -p "${APP_DIR}/data" "${APP_DIR}/static/geluiden"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data" "${APP_DIR}/static/geluiden" "${LOG_DIR}"

# ${CONFIG_DIR} moet groep-schrijfbaar zijn voor ${APP_USER}. Reden:
# settings_store.save() schrijft atomair via een tmp-bestand dat eerst in
# deze map aangemaakt wordt en daarna over config.json wordt ge-rename'd.
# Als de map root:root 755 is (Linux-default) kan de webinterface (die
# onder ${APP_USER} draait) geen tmp-bestand aanmaken → PermissionError.
# setgid (2775 = rwxrwsr-x) zorgt bovendien dat nieuwe bestanden in deze
# map automatisch groep=${APP_USER} erven, zodat web + daemon beide
# toegang houden zonder dat we umask moeten tunen.
chown root:"${APP_USER}" "${CONFIG_DIR}"
chmod 2775 "${CONFIG_DIR}"

echo "== 4) Create default config if missing =="
if [[ ! -f "${CONFIG_DIR}/config.json" ]]; then
  cat > "${CONFIG_DIR}/config.json" <<'JSON'
{
  "volume_percent": 70,
  "max_file_size_mb": 15,
  "poll_interval_sec": 2,
  "timezone": "Europe/Amsterdam",
  "allowed_extensions": [".mp3", ".wav", ".ogg"]
}
JSON
  chmod 640 "${CONFIG_DIR}/config.json"
  chown root:"${APP_USER}" "${CONFIG_DIR}/config.json"
  echo "Created ${CONFIG_DIR}/config.json"
else
  echo "Exists: ${CONFIG_DIR}/config.json"
fi

echo "== 4b) Generate env files (credentials + session secret) if missing =="
WEB_ENV="${CONFIG_DIR}/web.env"
DAEMON_ENV="${CONFIG_DIR}/daemon.env"

if [[ ! -f "${WEB_ENV}" && ! -f "${DAEMON_ENV}" ]]; then
  ADMIN_USER="admin"
  # Gebruik Python voor de password-generatie i.p.v. `tr | head`, om SIGPIPE
  # onder `set -o pipefail` te vermijden. Ook cryptografisch correct via secrets.
  ADMIN_PASS="$("${APP_DIR}/venv/bin/python" -c \
    'import secrets, string; print("".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)))')"
  ADMIN_HASH="$(printf '%s' "${ADMIN_PASS}" \
    | "${APP_DIR}/venv/bin/python" -c \
      'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.stdin.read()))')"
  SECRET="$("${APP_DIR}/venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"

  umask 077
  cat > "${WEB_ENV}" <<EOF
# Gegenereerd door install.sh — niet committen.
SCHOOLBELL_WEB_USER=${ADMIN_USER}
SCHOOLBELL_WEB_PWHASH=${ADMIN_HASH}
SCHOOLBELL_SECRET=${SECRET}

# Deze install draait Nginx op HTTP (poort 80). Sessie-cookies mogen dus
# geen Secure-flag hebben, anders stuurt de browser ze niet terug.
# Zet op 1 zodra je HTTPS hebt geconfigureerd in Nginx.
SCHOOLBELL_SECURE_COOKIES=0
EOF
  cat > "${DAEMON_ENV}" <<EOF
# Gegenereerd door install.sh — niet committen.
SCHOOLBELL_WEB_USER=${ADMIN_USER}
SCHOOLBELL_WEB_PASS=${ADMIN_PASS}
EOF
  umask 022

  chown root:"${APP_USER}" "${WEB_ENV}" "${DAEMON_ENV}"
  chmod 640 "${WEB_ENV}" "${DAEMON_ENV}"

  echo ""
  echo "=============================================================="
  echo "  Admin-login gegenereerd. NOTEER NU (wordt niet herhaald):"
  echo ""
  echo "    Gebruiker:   ${ADMIN_USER}"
  echo "    Wachtwoord:  ${ADMIN_PASS}"
  echo ""
  echo "  Wijzigen kan door ${WEB_ENV} en ${DAEMON_ENV} te bewerken."
  echo "=============================================================="
  echo ""
elif [[ -f "${WEB_ENV}" && -f "${DAEMON_ENV}" ]]; then
  echo "Env-bestanden bestaan al, overgeslagen: ${WEB_ENV}, ${DAEMON_ENV}"
else
  echo "ERROR: slechts één van ${WEB_ENV} / ${DAEMON_ENV} bestaat."
  echo "Verwijder beide en draai opnieuw, of bewerk handmatig."
  exit 1
fi

echo "== 5) systemd units =="
cat > /etc/systemd/system/schoolbell-web.service <<EOF
[Unit]
Description=Schoolbell Web (Gunicorn behind Nginx)
After=network.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${WEB_ENV}
Environment="SCHOOLBELL_CONFIG=${CONFIG_DIR}/config.json"
ExecStart=${APP_DIR}/venv/bin/gunicorn \
  --workers ${GUNICORN_WORKERS} \
  --threads ${GUNICORN_THREADS} \
  --timeout ${GUNICORN_TIMEOUT} \
  --max-requests ${GUNICORN_MAX_REQUESTS} \
  --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER} \
  --bind ${GUNICORN_BIND} \
  webinterface:app
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/schoolbell-daemon.service <<EOF
[Unit]
Description=Schoolbell Daemon
After=network.target schoolbell-web.service

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${DAEMON_ENV}
Environment="SCHOOLBELL_CONFIG=${CONFIG_DIR}/config.json"
Environment="API_BASE=http://127.0.0.1:5000"
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/schoolbelldaemon.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

echo "== 6) Nginx config =="
cat > /etc/nginx/sites-available/schoolbell <<'NGINX'
server {
    listen 80 default_server;
    server_name _;

    # Later: HTTPS toevoegen (self-signed of Let's Encrypt) en 80->443 redirect.

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/schoolbell /etc/nginx/sites-enabled/schoolbell
rm -f /etc/nginx/sites-enabled/default || true

nginx -t
systemctl restart nginx

echo "== 7) logrotate for events.jsonl =="
cat > /etc/logrotate.d/schoolbell-events <<EOF
${APP_DIR}/data/events.jsonl {
  daily
  rotate 14
  compress
  delaycompress
  missingok
  notifempty
  copytruncate
}
EOF

echo "== 8) Enable + (re)start services =="
# `enable` zorgt dat de services bij boot starten. Losse `restart` i.p.v.
# `--now` zodat bij een re-run ook daadwerkelijk nieuwe code, env-files
# en unit-file-wijzigingen worden opgepikt. Restart op een niet-lopende
# service werkt gewoon (systemd ziet het als start).
systemctl enable schoolbell-web.service schoolbell-daemon.service
systemctl restart schoolbell-web.service schoolbell-daemon.service

echo "== DONE =="
echo "Open: http://<pi-ip>/"
echo
echo "Status:"
systemctl --no-pager --full status schoolbell-web.service || true
systemctl --no-pager --full status schoolbell-daemon.service || true
echo "Re-run safe: ja"
echo "To uninstall: stop + disable services, remove files + dirs created by this script."
echo "De webinterface draait op Flask 3.x via Gunicorn; handmatig starten van de Flask dev server wordt niet ondersteund."
