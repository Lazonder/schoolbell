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
  echo "ERROR: expected repo at ${APP_DIR}."
  echo "Clone the repo there, or adjust APP_DIR in this script."
  exit 1
fi

if [[ ! -f "${APP_DIR}/requirements.txt" ]]; then
  echo "ERROR: requirements.txt missing in ${APP_DIR}."
  exit 1
fi

echo "== 1) System packages =="
apt-get update
# libasound2 is the runtime package pygame links against. Previously this
# was libasound2-dev (header files); those are only needed to compile
# something that wants to link against it, not to run.
apt-get install -y \
  nginx \
  python3 python3-venv python3-pip \
  logrotate \
  ffmpeg \
  alsa-utils \
  libasound2

echo "== 1b) OS timezone =="
# The schoolbell runs on local time. The application no longer carries
# its own timezone setting (was unused and confusing as a second source
# of truth) — the OS timezone is authoritative. Set it explicitly here
# so a freshly imaged Pi (often UTC by default) doesn't ring at the
# wrong moment. Idempotent: setting the same tz twice is a no-op.
TARGET_TZ="${SCHOOLBELL_TZ:-Europe/Amsterdam}"
if command -v timedatectl >/dev/null 2>&1; then
  CURRENT_TZ="$(timedatectl show --property=Timezone --value 2>/dev/null || echo '')"
  if [[ "${CURRENT_TZ}" != "${TARGET_TZ}" ]]; then
    timedatectl set-timezone "${TARGET_TZ}"
    echo "OS timezone set to ${TARGET_TZ} (was: ${CURRENT_TZ:-unknown})"
  else
    echo "OS timezone already ${TARGET_TZ}"
  fi
else
  echo "WARN: timedatectl not available, skipping OS timezone step."
fi

echo "== 2) Python venv + deps (requirements.txt) =="
cd "${APP_DIR}"
if [[ ! -d venv ]]; then
  sudo -u "${APP_USER}" python3 -m venv venv
fi

sudo -u "${APP_USER}" bash -lc "
  ${APP_DIR}/venv/bin/pip install --upgrade pip &&
  ${APP_DIR}/venv/bin/pip install -r ${APP_DIR}/requirements.txt
"

# Compile translation catalogs. The .po files in translations/ are
# the human-readable source; Flask-Babel needs the binary .mo
# version at runtime. .mo files are no longer committed (see
# .gitignore), so this step is REQUIRED — without it the app falls
# back to the source language for every string. Skipped silently if
# the translations folder doesn't exist — older checkouts that
# predate i18n keep working.
if [[ -d "${APP_DIR}/translations" ]]; then
  echo "== 2b) Compile translation catalogs =="
  sudo -u "${APP_USER}" bash -lc "
    ${APP_DIR}/venv/bin/python -m babel.messages.frontend compile -d ${APP_DIR}/translations
  " || echo "[WARN] pybabel compile failed; UI will fall back to source language."
fi

echo "== 3) Directories + permissions =="
mkdir -p "${CONFIG_DIR}" "${LOG_DIR}"
mkdir -p "${APP_DIR}/data" "${APP_DIR}/static/geluiden"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data" "${APP_DIR}/static/geluiden" "${LOG_DIR}"

# ${CONFIG_DIR} must be group-writable by ${APP_USER}. Reason:
# settings_store.save() writes atomically via a tmp file that is first
# created in this directory and then renamed over config.json. If the
# directory is root:root 755 (Linux default) the webinterface (running
# as ${APP_USER}) can't create a tmp file → PermissionError.
# setgid (2775 = rwxrwsr-x) also makes new files in this directory
# automatically inherit group=${APP_USER}, so both web and daemon keep
# access without having to tune the umask.
chown root:"${APP_USER}" "${CONFIG_DIR}"
chmod 2775 "${CONFIG_DIR}"

echo "== 4) Create default config if missing =="
if [[ ! -f "${CONFIG_DIR}/config.json" ]]; then
  cat > "${CONFIG_DIR}/config.json" <<'JSON'
{
  "volume_percent": 70,
  "max_file_size_mb": 15,
  "poll_interval_sec": 2,
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
  # Use Python for password generation instead of `tr | head`, to avoid
  # SIGPIPE under `set -o pipefail`. Also cryptographically correct via secrets.
  ADMIN_PASS="$("${APP_DIR}/venv/bin/python" -c \
    'import secrets, string; print("".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)))')"
  ADMIN_HASH="$(printf '%s' "${ADMIN_PASS}" \
    | "${APP_DIR}/venv/bin/python" -c \
      'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.stdin.read()))')"
  SECRET="$("${APP_DIR}/venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"

  umask 077
  cat > "${WEB_ENV}" <<EOF
# Generated by install.sh — do not commit.
SCHOOLBELL_WEB_USER=${ADMIN_USER}
SCHOOLBELL_WEB_PWHASH=${ADMIN_HASH}
SCHOOLBELL_SECRET=${SECRET}

# Nginx serves over HTTPS (see step 4c + 6). The Secure flag makes the
# browser only send the session cookie over TLS — strictly better.
# Only set to 0 if you intentionally drop back to plain HTTP (not
# recommended); otherwise login breaks because the cookie isn't sent.
SCHOOLBELL_SECURE_COOKIES=1
EOF
  cat > "${DAEMON_ENV}" <<EOF
# Generated by install.sh — do not commit.
SCHOOLBELL_WEB_USER=${ADMIN_USER}
SCHOOLBELL_WEB_PASS=${ADMIN_PASS}
EOF
  umask 022

  chown root:"${APP_USER}" "${WEB_ENV}" "${DAEMON_ENV}"
  chmod 640 "${WEB_ENV}" "${DAEMON_ENV}"

  echo ""
  echo "=============================================================="
  echo "  Admin login generated. NOTE NOW (will not be repeated):"
  echo ""
  echo "    User:        ${ADMIN_USER}"
  echo "    Password:    ${ADMIN_PASS}"
  echo ""
  echo "  Change by editing ${WEB_ENV} and ${DAEMON_ENV}."
  echo "=============================================================="
  echo ""
elif [[ -f "${WEB_ENV}" && -f "${DAEMON_ENV}" ]]; then
  echo "Env files already exist, skipped: ${WEB_ENV}, ${DAEMON_ENV}"
else
  echo "ERROR: only one of ${WEB_ENV} / ${DAEMON_ENV} exists."
  echo "Remove both and re-run, or edit manually."
  exit 1
fi

echo "== 4c) TLS certificate (self-signed) =="
# Zelf-ondertekend cert voor HTTPS binnen het schoolnetwerk. Voor een
# LAN-only Pi is dit voldoende: één browserwaarschuwing per apparaat,
# daarna gewoon https://schoolbell.local of https://<pi-ip>/.
#
# Wie de Pi via een echte DNS-naam publiek bereikbaar maakt: schakel
# om naar Let's Encrypt (zie docs/admin-guide.md → HTTPS).
CERT_DIR="${CONFIG_DIR}/certs"
CERT_FILE="${CERT_DIR}/cert.pem"
KEY_FILE="${CERT_DIR}/key.pem"

mkdir -p "${CERT_DIR}"
chown root:root "${CERT_DIR}"
chmod 755 "${CERT_DIR}"

if [[ -f "${CERT_FILE}" && -f "${KEY_FILE}" ]]; then
  echo "Cert + key already present: ${CERT_FILE}"
else
  echo "Generating self-signed certificate (10 years validity) ..."
  # SAN (Subject Alternative Name) is vereist door moderne browsers —
  # alleen een Common Name accepteren ze niet meer. We zetten erin:
  #   - DNS:schoolbell.local   primaire hostname; .local werkt via
  #                            mDNS/Avahi op de meeste LANs.
  #   - DNS:localhost          handig bij SSH-tunneling voor tests.
  #   - IP:127.0.0.1           idem.
  # Bezoek via raw LAN-IP (bv. 192.168.1.42) geeft een extra
  # waarschuwing omdat dat IP niet in SAN staat — onvermijdelijk
  # zonder DHCP-reservering. Gebruik schoolbell.local waar kan.
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}" \
    -days 3650 \
    -subj "/C=NL/O=Schoolbell/CN=schoolbell.local" \
    -addext "subjectAltName=DNS:schoolbell.local,DNS:localhost,IP:127.0.0.1"

  chown root:root "${CERT_FILE}" "${KEY_FILE}"
  chmod 644 "${CERT_FILE}"
  chmod 600 "${KEY_FILE}"
  echo "Generated: ${CERT_FILE} (expires in 10 years)"
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
# HTTP → HTTPS: bezoekers die per ongeluk "http://" typen worden
# permanent (301) doorgestuurd. Geen proxy_pass hier — niets mag
# unencrypted bij de Flask-app aankomen.
server {
    listen 80 default_server;
    server_name _;
    return 301 https://$host$request_uri;
}

server {
    # `http2` op de listen-regel ipv als losse directive: dat is de
    # vorm die werkt op nginx 1.22 (Debian bookworm / Raspberry Pi OS).
    # De losse `http2 on;` directive bestaat pas vanaf nginx 1.25.1
    # (juni 2023) en geeft op oudere installs een unknown-directive.
    listen 443 ssl http2 default_server;
    listen [::]:443 ssl http2 default_server;
    server_name _;

    ssl_certificate     /etc/schoolbell/certs/cert.pem;
    ssl_certificate_key /etc/schoolbell/certs/key.pem;

    # TLS 1.2 + 1.3 only. 1.0 en 1.1 zijn deprecated en worden door
    # alle moderne browsers afgewezen.
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    # Upload size limit. Nginx default is 1 MB, which would block any
    # bell-sized MP3 with a bare '413 Request Entity Too Large' page
    # before Flask even sees the request — meaning the per-install
    # Settings.max_file_size_mb cap and the friendly upload-rejected
    # flash never get a chance to run. Match webinterface.py's
    # MAX_CONTENT_LENGTH (100 MiB) so nginx is the outer hard cap and
    # Flask handles everything below it with a clear error.
    client_max_body_size 100M;

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
# `enable` makes the services start at boot. Separate `restart` instead
# of `--now` so that on a re-run new code, env files and unit-file
# changes are actually picked up. Restart on a non-running service just
# works (systemd treats it as a start).
systemctl enable schoolbell-web.service schoolbell-daemon.service
systemctl restart schoolbell-web.service schoolbell-daemon.service

echo "== DONE =="
echo "Open: https://schoolbell.local/  (of https://<pi-ip>/)"
echo "Eerste bezoek per apparaat: 'self-signed' waarschuwing accepteren."
echo
echo "Status:"
systemctl --no-pager --full status schoolbell-web.service || true
systemctl --no-pager --full status schoolbell-daemon.service || true
echo "Re-run safe: yes"
echo "To uninstall: stop + disable services, remove files + dirs created by this script."
echo "The webinterface runs on Flask 3.x via Gunicorn; running the Flask dev server manually is not supported."
echo
echo "Tip: verify audio output independently of the daemon with:"
echo "     ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/belltest.py <path-to-audio-file>"
