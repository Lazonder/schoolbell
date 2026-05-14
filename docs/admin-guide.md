# Schoolbell Admin Guide

Detailed reference for system administrators installing, configuring,
and maintaining a Schoolbell instance. For a friendly tour of what
Schoolbell does, read the [README](../README.md) first.

---

## Architecture

```
Browser
  ↓ HTTP (LAN; HTTPS planned — see roadmap.md)
Nginx (port 80)
  ↓ proxy_pass
Gunicorn (Flask app, 127.0.0.1:5000)
  ↓ API (Basic Auth, localhost)
schoolbell-daemon (systemd)
```

### Components

| Component               | Function                                                              |
| ----------------------- | --------------------------------------------------------------------- |
| `webinterface.py`       | Flask application entry point + framework-level wiring                |
| `blueprints/`           | All HTTP routes, grouped by topic (auth, agenda, roosters, …)         |
| `core/`                 | Pure helpers (date math, audio metadata, auth checks)                 |
| `schoolbelldaemon.py`   | Background process that plays bell sounds at scheduled times          |
| `settings_store.py`     | Reads and writes the JSON settings file with file locking             |
| `vakanties_fetcher.py`  | Scrapes Dutch school holidays from rijksoverheid.nl                   |
| `health_check.py`       | Test script for periodic functional checks                            |
| `templates/`            | Jinja2 HTML templates                                                 |
| `static/geluiden/`      | Storage for mp3 / wav / ogg sound files                               |
| `data/`                 | Runtime JSON: schedules, calendar, logs, holidays, daemon heartbeat   |
| `translations/`         | Per-language `.po` (source) and `.mo` (compiled) translation catalogs |

---

## Installation

Tested on **Raspberry Pi OS (Debian)**. Requires `sudo` and an
internet connection.

```bash
git clone https://github.com/<your-account>/schoolbell.git
cd schoolbell
sudo ./install.sh
```

> **First run only:** the script generates a random admin password
> and prints it **once** in a boxed output block. Write it down.
> Lost it? See [Credentials & password](#credentials--password).

After completion:

* the web interface is running via *Nginx + Gunicorn*
* the schoolbell daemon is running as a *systemd unit*
* credentials, session secret, logging, logrotate and basic config
  are in place

Open a browser to `http://<pi-ip>/`.

### What `install.sh` does

* installs system packages (`nginx`, audio, Python)
* creates a Python virtual environment under the project
* installs Python dependencies (`requirements.txt`)
* compiles translation catalogs (`pybabel compile`) so the UI can
  switch language at runtime
* creates and owns:
  * `/etc/schoolbell/config.json`
  * `/etc/schoolbell/web.env` and `daemon.env` (admin credentials +
    session secret, only on first run)
  * `data/` and `static/geluiden/`
* installs and activates two systemd units:
  * `schoolbell-web.service` (Gunicorn)
  * `schoolbell-daemon.service`
* configures:
  * Nginx as a reverse proxy (port 80 → Gunicorn on 5000)
  * `logrotate` for `data/events.jsonl`

The script is **safe to re-run**: existing env files are not
overwritten, and both services are restarted at the end so any
new code or config takes effect.

### Example `/etc/schoolbell/config.json`

```json
{
  "volume_percent": 70,
  "max_file_size_mb": 15,
  "poll_interval_sec": 2,
  "allowed_extensions": [".mp3", ".wav", ".ogg"],
  "taal": "nl",
  "theme_mode": "light",
  "huisstijl": "standaard"
}
```

Older config files without `taal`, `theme_mode` or `huisstijl` keep
working — `Settings.load()` filters unknown keys and merges defaults.

---

## Credentials & password

On the first `install.sh` run, two env files are created. Both have
`chmod 640` with owner `root:pi`.

* `/etc/schoolbell/web.env` — read by `schoolbell-web.service`:
  * `SCHOOLBELL_WEB_USER` — admin username
  * `SCHOOLBELL_WEB_PWHASH` — werkzeug-scrypt hash of the password
  * `SCHOOLBELL_SECRET` — Flask session secret (64 hex)
  * `SCHOOLBELL_SECURE_COOKIES` — see below
* `/etc/schoolbell/daemon.env` — read by `schoolbell-daemon.service`:
  * `SCHOOLBELL_WEB_USER` — same username
  * `SCHOOLBELL_WEB_PASS` — the password in plaintext (the daemon
    needs to log in to the web API; a hash is not enough there)

### Forgot the password?

The password is shown only once in install output. Recover it on the
Pi:

```bash
sudo cat /etc/schoolbell/daemon.env
```

### Change the password

Generate a new hash:

```bash
/home/pi/schoolbell/venv/bin/python -c \
  'from werkzeug.security import generate_password_hash; print(generate_password_hash("new-password"))'
```

Then update both files:

* `web.env` → paste the hash into `SCHOOLBELL_WEB_PWHASH`
* `daemon.env` → paste the plaintext password into `SCHOOLBELL_WEB_PASS`

Restart the services:

```bash
sudo systemctl restart schoolbell-web.service schoolbell-daemon.service
```

### `SCHOOLBELL_SECURE_COOKIES`

Controls the `Secure` flag on the session cookie.

* `0` — cookies allowed over plain HTTP. **Required while Nginx is
  on HTTP**, otherwise the browser refuses to send the cookie back
  and login breaks.
* `1` — cookies sent only over HTTPS. Set this once you've configured
  HTTPS on Nginx.

The installer defaults to `0`. To change: edit
`/etc/schoolbell/web.env` and restart `schoolbell-web.service`.

---

## Security

* Nginx currently runs **plain HTTP** (port 80). HTTPS is planned —
  see [roadmap.md](roadmap.md). Until then, only run on a trusted
  LAN/VPN.
* Session-based login with CSRF protection.
* Basic Auth for the daemon → web-API path (over localhost).
* Admin password is randomly generated on install and stored as a
  werkzeug-scrypt hash.
* Session secret is randomly generated on install (64 hex bytes).
* Upload size limit and extension whitelist are configured in
  `config.json`.

---

## Web server (Nginx + Gunicorn)

### Gunicorn

* Runs the Flask app
* Multiple *workers* and *threads*
* Timeouts prevent stuck requests
* Periodic worker refresh prevents memory leaks

Example (systemd):

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

* Listens on port 80 (plain HTTP)
* Reverse proxies to Gunicorn on `127.0.0.1:5000`
* HTTPS + certificates **not yet configured** — planned (self-signed
  or Let's Encrypt, then 80→443 redirect). See [roadmap.md](roadmap.md).
* Optional IP restrictions via `allow`/`deny` in the server block

---

## Service management

### Start

```bash
sudo systemctl start schoolbell-web.service
sudo systemctl start schoolbell-daemon.service
```

### Reload daemon settings without restart

```bash
sudo systemctl kill -s HUP schoolbell-daemon.service
```

### Restart

```bash
sudo systemctl restart schoolbell-web.service
sudo systemctl restart schoolbell-daemon.service
```

---

## Logging & log rotation

### Runtime logs

* Web: `journalctl -u schoolbell-web.service`
* Daemon: `journalctl -u schoolbell-daemon.service`

### Bell events

* File: `data/events.jsonl`

`logrotate` is configured for:

* periodic rotation
* compression (`.gz`)
* retention of older logs
* unbounded growth prevention

---

## Monitoring

### `/healthz` endpoint

Quick JSON status check, no authentication. Intended for external
uptime monitoring (e.g. Uptime Kuma, a cron with `curl`, or a Pi
status board).

```bash
curl http://<pi-ip>/healthz
```

Response:

* **200 OK** if all checks pass
* **503 Service Unavailable** if any check fails

The four check keys are always present in the response so a
monitoring tool can alert on a specific one:

* `data_dir_writable` — can the web interface write to `data/`
* `audio_dir_readable` — is `static/geluiden/` readable
* `settings_loadable` — does `Settings.load()` succeed
* `daemon_alive` — has the daemon written a heartbeat within
  ~2× the poll interval

### `health_check.py`

Heavier integration check that actually logs in, visits a few
pages, and optionally does an upload/delete round-trip. Requires
credentials.

```bash
source venv/bin/activate
# Via Nginx (port 80), plain HTTP while HTTPS is not yet enabled.
export SCHOOLBELL_BASE="http://127.0.0.1"
export SCHOOLBELL_WEB_USER="admin"
# See /etc/schoolbell/daemon.env for the generated password.
export SCHOOLBELL_WEB_PASS="your-password"
python health_check.py
```

Optionally test upload/delete:

```bash
export SCHOOLBELL_HEALTH_UPLOAD=1
python health_check.py
```

The check verifies:

* Login and CSRF protection
* Reachability of `/geluiden`, `/roosters`, `/logs`
* APIs `/api/settings` and `/api/effectief-rooster`

---

## Maintenance

* **Backup**: `data/` and `/etc/schoolbell/config.json`
* **Sound test**: upload a short mp3 and use the ▶ button in the UI
* **Debugging**: check `journalctl` and `events.jsonl`

---

## Development

### Run the test suite

```bash
pip install -r requirements-dev.txt
SCHOOLBELL_WEB_USER=admin SCHOOLBELL_WEB_PASS=test \
  SCHOOLBELL_WEB_PWHASH='pbkdf2:sha256:600000$x$0' \
  SCHOOLBELL_SECRET=test \
  python3 -m pytest tests/
```

### Pre-commit hook

Set up once per checkout:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

From then on, every `git commit` automatically runs:

* **ruff** — lint (see `ruff.toml`; format-check is off, can be
  enabled later)
* **pytest** — all tests in `tests/`

Run manually on all files without committing:

```bash
pre-commit run --all-files
```

### Adding new translatable strings

After editing templates or Python code that contains user-facing
strings, refresh the catalog and translations. See
[CONTRIBUTING.md](../CONTRIBUTING.md) for the full workflow.
