# Schoolbell Admin Guide

Detailed reference for system administrators installing, configuring,
and maintaining a Schoolbell instance. For a friendly tour of what
Schoolbell does, read the [README](../README.md) first.

---

## Architecture

```
Browser
  ↓ HTTPS (self-signed; LAN-only by default)
Nginx (port 443; port 80 redirects to 443)
  ↓ proxy_pass (HTTP, localhost)
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

Runs on any Debian/Ubuntu-family system with systemd — Raspberry Pi
OS, Debian, Ubuntu, including an old laptop. Tested on **Raspberry
Pi OS (Debian)**. Requires `sudo` and an internet connection.

```bash
git clone https://github.com/Lazonder/schoolbell.git
cd schoolbell
sudo ./install.sh
```

The script installs for the account that invoked `sudo` and expects
the repo at `~/schoolbell` of that account. Installing for a
different account: `sudo SCHOOLBELL_USER=<name> ./install.sh`.

> **First run only:** the script generates a random admin password
> and prints it **once** in a boxed output block. Write it down.
> Lost it? See [Credentials & password](#credentials--password).

After completion:

* the web interface is running via *Nginx + Gunicorn*
* the schoolbell daemon is running as a *systemd unit*
* credentials, session secret, logging, logrotate and basic config
  are in place

Open a browser to `https://<pi-ip>/` (or `https://schoolbell.local/`).
The first visit per device shows a "connection is not private"
warning — see [HTTPS](#https) below for what to do and why.

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

## Updating an existing install

After pulling new code on a running Pi, two things may need a nudge
before changes take effect.

### 1. Recompile translations (if any `.po` changed)

Schoolbell loads compiled `.mo` files at startup; only the `.po`
source is in git (see `.gitignore`). If the pull touched any
translation, regenerate the `.mo` files first:

```bash
cd ~/schoolbell
./venv/bin/python -m babel.messages.frontend compile -d translations
```

Skipping this is silent but visible: every translated string falls
back to its Dutch source. If your UI suddenly looks Dutch again
after an update, this is why.

### 2. Restart the affected service(s)

Gunicorn caches the imported Flask app, so Python code changes need
a service restart to take effect. (Templates would reload
automatically only when `SCHOOLBELL_DEBUG=1`, which is off in
production.)

```bash
sudo systemctl restart schoolbell-web.service
```

Restart the daemon too if `schoolbelldaemon.py` changed or if you
updated `daemon.env`:

```bash
sudo systemctl restart schoolbell-daemon.service
```

Settings changed through the web UI are picked up automatically:
the daemon watches `config.json`'s modification time on every poll.
For manual edits to `/etc/schoolbell/config.json` you can force a
reload without a full restart (the unit maps `reload` to SIGHUP):

```bash
sudo systemctl reload schoolbell-daemon.service
```

### The lazy alternative

Re-running `sudo ./install.sh` does both steps (and a few more) and
is safe: the script never overwrites existing credentials, env
files, or runtime data. Use this when you don't want to think about
what specifically changed:

```bash
cd ~/schoolbell
git pull
sudo ./install.sh
```

The script is explicitly designed to be safe to re-run.

---

## Credentials & password

On the first `install.sh` run, two env files are created. Both have
`chmod 640` with owner `root:<user>` (the account the app runs as).

* `/etc/schoolbell/web.env` — read by `schoolbell-web.service`:
  * `SCHOOLBELL_WEB_USER` — admin username
  * `SCHOOLBELL_WEB_PWHASH` — werkzeug-scrypt hash of the password
  * `SCHOOLBELL_SECRET` — Flask session secret (64 hex)
  * `SCHOOLBELL_SECURE_COOKIES` — see below
* `/etc/schoolbell/daemon.env` — read by `schoolbell-daemon.service`:
  * `SCHOOLBELL_WEB_USER` — same username
  * `SCHOOLBELL_WEB_PASS` — the password in plaintext (the daemon
    needs to log in to the web API; a hash is not enough there)

Since the multi-user feature landed, these env values are mainly a
**seed** for the user store: on first start, the values are
copied into `data/users.json` and from then on that file is the
source of truth (see [User management](#user-management)). Editing
`web.env` after the seed has run no longer affects login — change
the admin password through the UI instead. The daemon still reads
`daemon.env` on every poll for its HTTP Basic Auth header.

### Forgot the password?

The password is shown only once in install output. Recover it on the
Pi:

```bash
sudo cat /etc/schoolbell/daemon.env
```

### Change the password

Generate a new hash:

```bash
~/schoolbell/venv/bin/python -c \
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

* `1` (default) — the session cookie is only sent over HTTPS.
  Strictly better; matches the default install which runs Nginx on
  port 443.
* `0` — the cookie is also sent over plain HTTP. Only needed if you
  intentionally drop Nginx back to plain HTTP, otherwise login breaks
  because the browser refuses to send the cookie back.

To change: edit `/etc/schoolbell/web.env` and restart
`schoolbell-web.service`.

---

## User management

Schoolbell supports multiple named accounts, each with its own
password and a list of tabs they're allowed to see. Accounts live in
`data/users.json`. Every entry has:

* `pwhash` — werkzeug-scrypt hash of the password
* `rol` — `"admin"` or `"gebruiker"` (regular user)
* `tabs` — list of tab keys, or `["*"]` for admins
* `aangemaakt` — UTC ISO timestamp

Admins implicitly get access to every tab, regardless of what their
`tabs` list says — the explicit list is still kept so a demoted
admin returns to a sensible state. Regular users only reach the
tabs explicitly granted.

### Bootstrap on first start

If `data/users.json` doesn't exist, the web app seeds it on the
next request from `SCHOOLBELL_WEB_USER` and `SCHOOLBELL_WEB_PWHASH`
(see [Credentials & password](#credentials--password)). The result
is a single admin account that matches what was in `web.env`.

After the seed has run, the env file is no longer read for login —
change credentials through the UI from then on.

### Managing accounts (UI)

Admins see a **Gebruikers** tab in the navigation bar. Click it to
reach `/gebruikers`, where you can:

* Create a new account (username + password + role + tab checkboxes).
* Change someone's role and tabs.
* Reset a user's password (under "Wachtwoord" per row).
* Delete an account (button is hidden for the row representing
  yourself, so you can't accidentally lock yourself out).

Tab keys correspond to the navigation items: `agenda`, `roosters`,
`standaardweek`, `geluiden`, `logs`, `settings`. `gebruikers` is
implicitly granted to admins only and is **not** something you can
hand to a regular user via the UI.

### Validation rules

Enforced in `core/users.py`:

* Username: lowercase letters, digits, `_`, `-`; length 2..32.
* Password: minimum 8 characters.
* Last-admin protection: the user store refuses to delete or
  demote the only remaining admin account, so the management UI
  always stays reachable.

A failed POST surfaces as a flash message on `/gebruikers`; the
underlying user store is never partially updated.

### Daemon authentication

The daemon's HTTP Basic Auth verifier (`/api/effectief-rooster`)
accepts any **admin** account in `users.json`. A regular user with
the `roosters` tab cannot impersonate the daemon — that's by
design, the daemon conceptually belongs to the admin role.

`SCHOOLBELL_WEB_USER` / `SCHOOLBELL_WEB_PWHASH` /
`SCHOOLBELL_WEB_PASS` in `/etc/schoolbell/daemon.env` remain the
daemon's source of credentials. As long as the same username +
hash combo exists as an admin in `users.json`, everything works.

### Locked out? (recovery)

If `data/users.json` somehow disappears, gets corrupted, or every
admin lost their password:

1. Stop the web service: `sudo systemctl stop schoolbell-web.service`
2. Delete (or rename) `data/users.json` so the bootstrap re-runs.
3. Make sure `SCHOOLBELL_WEB_USER` and `SCHOOLBELL_WEB_PWHASH` in
   `/etc/schoolbell/web.env` point at the admin you want to
   re-seed. Generate a fresh hash if needed (see [Change the
   password](#change-the-password)).
4. Start the service: `sudo systemctl start schoolbell-web.service`
5. Log in with that account, fix things via the UI.

---

## Security

* Nginx runs **HTTPS** on port 443 with a self-signed certificate
  (generated by `install.sh`). Port 80 redirects to 443. See
  [HTTPS](#https) for certificate details. Even with HTTPS, treat
  Schoolbell as a LAN-only service unless you explicitly opt in to
  public exposure.
* Session-based login with CSRF protection. Per-tab access control
  via `@tab_required` decorators; user-management page guarded by
  `@admin_page_required`. See [User management](#user-management).
* Basic Auth for the daemon → web-API path (over localhost), gated
  on the admin role.
* Admin password is randomly generated on install and stored as a
  werkzeug-scrypt hash. Multi-user accounts use the same hash
  algorithm.
* Session secret is randomly generated on install (64 hex bytes).
* Upload size limit and extension whitelist are configured in
  `config.json`.

---

## HTTPS

`install.sh` configures Nginx with a **self-signed TLS certificate**
generated on first install. This is deliberate: most Schoolbell
installs only need to be reachable inside the school LAN, so a
Let's Encrypt certificate (which requires a public DNS name and an
externally reachable Pi) is overkill. The trade-off is the one-time
"connection is not private" warning per device on first visit.

### Files

| Path                              | Purpose                       | Permissions |
| --------------------------------- | ----------------------------- | ----------- |
| `/etc/schoolbell/certs/cert.pem`  | The certificate (public)      | `644 root`  |
| `/etc/schoolbell/certs/key.pem`   | The private key — keep secret | `600 root`  |

Both are referenced by the Nginx config:

```
ssl_certificate     /etc/schoolbell/certs/cert.pem;
ssl_certificate_key /etc/schoolbell/certs/key.pem;
```

The certificate is valid for **10 years** from install. The
Subject Alternative Names (SAN) are:

* `DNS:schoolbell.local` — primary hostname, works on most LANs
  via mDNS/Avahi.
* `DNS:localhost`
* `IP:127.0.0.1`

Browsing via `https://schoolbell.local/` (or `localhost` when
SSH-tunneling) only triggers the one-time "unknown CA" warning.
Browsing via the raw LAN IP (e.g. `https://192.168.1.42/`) gives
an additional SAN-mismatch warning — unavoidable without a DHCP
reservation that pins the Pi's IP, so prefer the hostname.

### First-visit warning

Modern browsers refuse to silently trust a certificate that wasn't
issued by a public CA. The expected flow on **first** visit per
device:

1. The browser shows a full-page warning ("Your connection is not
   private", "Verbinding is niet privé", "NET::ERR_CERT_AUTHORITY_INVALID").
2. Click *Advanced* (or *Geavanceerd*) → *Proceed to schoolbell.local
   (unsafe)*.
3. The browser remembers the choice and stops asking on that device.

The warning is correct: the certificate really isn't signed by a
public CA. Inside a school LAN, where you trust the network and the
Pi, that's acceptable. Outside a school LAN, it isn't — use a VPN
(e.g. Tailscale) to reach the Pi rather than exposing it publicly
with a self-signed certificate.

### Browser won't offer to save the password

A side effect of the self-signed certificate that is easy to
misread as a bug in the login page: Chrome and Edge **disable their
password manager entirely** on origins with a certificate error.
The same warning you clicked through also suppresses the "Save
password?" prompt and autofill, so the login form appears to be at
fault while it isn't — it is a regular POST form with
`autocomplete="username"` / `autocomplete="current-password"`,
exactly what password managers look for. Firefox is more lenient
and usually does offer to save after you accept the exception.

Three ways out, in increasing order of effort:

1. **Pick a typeable password (two minutes, no code).** The
   generated random password is only a first-boot default. Change
   it — or give each person their own account — via the
   *Gebruikers* tab (see [User management](#user-management)), and
   choose something strong but typeable, e.g. three unrelated
   words. Then nothing needs to be remembered by the browser.
2. **Trust the certificate on the school devices.** Import
   `/etc/schoolbell/certs/cert.pem` into each device's trust store
   (Windows: *Trusted Root Certification Authorities*; macOS:
   Keychain). The padlock turns valid and the password manager
   works again. Visit the site as `https://schoolbell.local` — a
   bare IP is not in the certificate's SAN and keeps the error.
3. **Get a real certificate.** Free options: `tailscale cert` when
   the Pi is on a tailnet, automatic TLS when it sits behind a
   Cloudflare Tunnel, or Let's Encrypt with a DNS-01 challenge —
   the latter works on your own domain without the Pi being
   reachable from the internet (unlike the HTTP-01 flow described
   [below](#switching-to-lets-encrypt-optional)).

### Kiosk / `/now` displays

Most school installs put `/now` on a permanent display in the staff
room. Accept the warning once on that device; from then on the page
auto-refreshes without prompting. If the display browser is locked
down to refuse self-signed certificates, you have two options:

1. Switch to Let's Encrypt (see below) so the certificate is trusted
   by default.
2. Import the cert into the device's trust store. Copy
   `/etc/schoolbell/certs/cert.pem` to the device, install it as a
   trusted CA. (Procedure differs per OS — outside the scope here.)

### Renewing the certificate

After 10 years the cert expires. To regenerate:

```bash
sudo rm /etc/schoolbell/certs/cert.pem /etc/schoolbell/certs/key.pem
sudo ./install.sh    # regenerates because both files are absent
sudo systemctl restart nginx
```

Every device that previously accepted the old cert will see the
warning again — the new key has a different fingerprint.

### Switching to Let's Encrypt (optional)

Only relevant if you want the Pi reachable from the public internet
via a real DNS name (e.g. `schoolbell.example.org`):

1. Make sure the DNS name points at the Pi's public address and that
   port 80 (HTTP-01 challenge) is forwarded.
2. Install certbot: `sudo apt-get install certbot python3-certbot-nginx`.
3. Run: `sudo certbot --nginx -d schoolbell.example.org`.
4. Certbot will edit the Nginx config in place, swapping the
   self-signed paths for the Let's Encrypt ones, and install a
   renewal timer.

Note: exposing Schoolbell publicly increases the attack surface
considerably. Prefer a VPN (e.g. Tailscale) for remote access — the
self-signed setup keeps working without any change.

---

## Web server (Nginx + Gunicorn)

### Gunicorn

* Runs the Flask app
* Multiple *workers* and *threads*
* Timeouts prevent stuck requests
* Periodic worker refresh prevents memory leaks

Example (systemd; install.sh fills in the actual path):

```
ExecStart=/pad/naar/schoolbell/venv/bin/gunicorn \
  --workers 2 \
  --threads 4 \
  --timeout 30 \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --bind 127.0.0.1:5000 \
  webinterface:app
```

### Nginx

* Listens on port 443 (HTTPS) with a self-signed certificate.
* Port 80 is kept open only to issue a 301 redirect to 443.
* Reverse proxies to Gunicorn on `127.0.0.1:5000` (plain HTTP,
  localhost only — TLS terminates at Nginx).
* TLS 1.2 + 1.3 only; 1.0/1.1 are explicitly disabled.
* Optional IP restrictions via `allow`/`deny` in the server block.
* For certificate details and Let's Encrypt, see [HTTPS](#https).

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
