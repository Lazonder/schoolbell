#!/usr/bin/env python3
import os, re, sys, time
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from requests.auth import HTTPBasicAuth

# === Config ===
BASE_URL = os.getenv("SCHOOLBELL_BASE", "https://127.0.0.1:5000")
VERIFY_TLS = False  # self-signed? zet op False; anders True
ADMIN_USER = os.getenv("SCHOOLBELL_WEB_USER", "admin")
ADMIN_PASS = os.getenv("SCHOOLBELL_WEB_PASS", None)  # of gebruik hash in web, maar hier plain
DO_UPLOAD_TEST = os.getenv("SCHOOLBELL_HEALTH_UPLOAD", "0") == "1"  # zet 1 om upload te testen
TEST_FILENAME = "healthcheck_test_tone.mp3"  # extensie moet toegestaan zijn
TEST_FILE_BYTES = b"ID3" + b"\x00" * 256  # piepklein 'mp3'-achtig bestand

sess = requests.Session()
sess.verify = VERIFY_TLS

def fail(msg):
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)

def get_csrf_from_html(html: str) -> str:
    # eerst meta-tag zoeken (base.html)
    m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        return m.group(1)
    # anders hidden input (login.html)
    m = re.search(r'<input[^>]+name=["\']_csrf["\'][^>]+value=["\']([^"\']+)["\']', html, re.I)
    if m:
        return m.group(1)
    return ""

def login():
    # 1) GET /login → CSRF uit meta of hidden input
    r = sess.get(f"{BASE_URL}/login", timeout=10)
    if r.status_code != 200:
        fail(f"Login-pagina niet bereikbaar: {r.status_code}")

    csrf = get_csrf_from_html(r.text)
    if not csrf:
        fail("CSRF token niet gevonden op loginpagina (meta of hidden input).")

    # 2) POST /login
    data = {
        "_csrf": csrf,
        "username": ADMIN_USER,
        "password": ADMIN_PASS or "",
    }
    r = sess.post(f"{BASE_URL}/login", data=data, timeout=10, allow_redirects=False)
    if r.status_code not in (302, 303):
        fail(f"Inloggen mislukt: status {r.status_code}")

    ok("Inloggen gelukt")

def ok(msg):
    print(f"[ OK ] {msg}")

def get_csrf_from_meta(html: str) -> str:
    m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else ""

def login():
    # 1) GET /login → CSRF
    r = sess.get(f"{BASE_URL}/login", timeout=10)
    if r.status_code != 200:
        fail(f"Login-pagina niet bereikbaar: {r.status_code}")
    csrf = get_csrf_from_meta(r.text)
    if not csrf:
        fail("CSRF token niet gevonden op loginpagina.")
    # 2) POST /login
    data = {
        "_csrf": csrf,
        "username": ADMIN_USER,
        "password": ADMIN_PASS or "",
    }
    r = sess.post(f"{BASE_URL}/login", data=data, timeout=10, allow_redirects=False)
    if r.status_code not in (302, 303):
        fail(f"Inloggen mislukt: status {r.status_code}")
    ok("Inloggen gelukt")

def check_page(path: str, must_contain: str | None = None):
    r = sess.get(f"{BASE_URL}{path}", timeout=10)
    if r.status_code != 200:
        fail(f"GET {path} → {r.status_code}")
    if must_contain and must_contain not in r.text:
        fail(f"GET {path} bevat niet de verwachte tekst: {must_contain}")
    ok(f"Pagina {path} laadt")

def check_api_settings():
    r = sess.get(f"{BASE_URL}/api/settings", timeout=10)
    if r.status_code != 200:
        fail(f"/api/settings → {r.status_code}")
    data = r.json()
    # verwachte sleutels
    for k in ("volume_percent", "max_file_size_mb", "poll_interval_sec", "allowed_extensions"):
        if k not in data:
            fail(f"/api/settings ontbreekt sleutel: {k}")
    ok("API /api/settings werkt")

def check_api_effectief_rooster():
    # Basic Auth — dezelfde admin credentials
    r = requests.get(
        f"{BASE_URL}/api/effectief-rooster?empty_204=1",
        auth=HTTPBasicAuth(ADMIN_USER, ADMIN_PASS or ""),
        timeout=10,
        verify=VERIFY_TLS,
    )
    if r.status_code not in (200, 204):
        fail(f"/api/effectief-rooster → {r.status_code}")
    ok("API /api/effectief-rooster werkt")

def get_csrf_from_page(path: str) -> str:
    r = sess.get(f"{BASE_URL}{path}", timeout=10)
    if r.status_code != 200:
        fail(f"Kan CSRF niet ophalen: GET {path} → {r.status_code}")
    csrf = get_csrf_from_meta(r.text)
    if not csrf:
        fail(f"CSRF meta-token niet gevonden op {path}")
    return csrf

def upload_and_delete():
    # Haal accept-extensies & CSRF via de pagina
    csrf = get_csrf_from_page("/geluiden")

    files = {
        "file": (TEST_FILENAME, TEST_FILE_BYTES, "audio/mpeg")
    }
    data = {
        "_csrf": csrf,
        "naam": os.path.splitext(TEST_FILENAME)[0],
    }
    r = sess.post(f"{BASE_URL}/geluiden/upload", data=data, files=files, timeout=10, allow_redirects=False)
    if r.status_code not in (302, 303):
        fail(f"Upload mislukt: status {r.status_code}")

    ok("Upload testbestand gelukt")

    # Direct weer verwijderen
    csrf_del = get_csrf_from_page("/geluiden")
    r = sess.post(f"{BASE_URL}/geluiden/delete", data={"_csrf": csrf_del, "filename": TEST_FILENAME}, timeout=10, allow_redirects=False)
    if r.status_code not in (302, 303):
        fail(f"Verwijderen testbestand mislukt: status {r.status_code}")
    ok("Verwijderen testbestand gelukt")

def main():
    # Basis sanity: base page moet redirecten naar login als niet ingelogd
    r = sess.get(f"{BASE_URL}/", timeout=10, allow_redirects=False)
    if r.status_code not in (301, 302, 303):
        fail(f"Root redirect naar /login verwacht, kreeg {r.status_code}")
    ok("Root redirect in orde")

    login()

    # UI pagina’s
    check_page("/geluiden", "Geluiden")
    check_page("/roosters", "Roosters")
    check_page("/logs", "Logboek")

    # API’s
    check_api_settings()
    check_api_effectief_rooster()

    # Optioneel upload testen
    if DO_UPLOAD_TEST:
        upload_and_delete()

    print("\n✅ Health-check geslaagd.")

if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.SSLError as e:
        fail(f"TLS/Cert-probleem: {e}")
    except requests.exceptions.RequestException as e:
        fail(f"HTTP-probleem: {e}")
