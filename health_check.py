#!/usr/bin/env python3
import os, re, sys
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from requests.auth import HTTPBasicAuth

# === Config ===
BASE_URL = os.getenv("SCHOOLBELL_BASE", "https://127.0.0.1:5000")
VERIFY_TLS = False  # self-signed? set to False; otherwise True
ADMIN_USER = os.getenv("SCHOOLBELL_WEB_USER", "admin")
ADMIN_PASS = os.getenv("SCHOOLBELL_WEB_PASS", None)  # or use hash in web, but plain here
DO_UPLOAD_TEST = os.getenv("SCHOOLBELL_HEALTH_UPLOAD", "0") == "1"  # set to 1 to test upload
TEST_FILENAME = "healthcheck_test_tone.mp3"  # extension must be allowed
TEST_FILE_BYTES = b"ID3" + b"\x00" * 256  # tiny 'mp3'-like file

sess = requests.Session()
sess.verify = VERIFY_TLS

def fail(msg):
    """Print a failure message and stop the script with an error exit code."""
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)

def ok(msg):
    """Print a success message to standard output."""
    print(f"[ OK ] {msg}")

def get_csrf_from_meta(html: str) -> str:
    """Find and return the CSRF security token embedded in an HTML page.

    The token is stored in a ``<meta name="csrf-token">`` tag. Returns
    an empty string if the tag is not found.
    """
    m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else ""

def login():
    """Log in to the app using the configured admin credentials.

    First fetches the login page to get a fresh CSRF token, then
    submits the login form. Calls ``fail()`` if either step goes wrong.
    """
    # 1) GET /login → CSRF
    r = sess.get(f"{BASE_URL}/login", timeout=10)
    if r.status_code != 200:
        fail(f"Login page unreachable: {r.status_code}")
    csrf = get_csrf_from_meta(r.text)
    if not csrf:
        fail("CSRF token not found on login page.")
    # 2) POST /login
    data = {
        "_csrf": csrf,
        "username": ADMIN_USER,
        "password": ADMIN_PASS or "",
    }
    r = sess.post(f"{BASE_URL}/login", data=data, timeout=10, allow_redirects=False)
    if r.status_code not in (302, 303):
        fail(f"Login failed: status {r.status_code}")
    ok("Login succeeded")

def check_page(path: str, must_contain: str | None = None):
    """Fetch a page and verify it returns a 200 OK response.

    If ``must_contain`` is given, also checks that the text appears
    somewhere in the response body. Calls ``fail()`` on any problem.
    """
    r = sess.get(f"{BASE_URL}{path}", timeout=10)
    if r.status_code != 200:
        fail(f"GET {path} → {r.status_code}")
    if must_contain and must_contain not in r.text:
        fail(f"GET {path} does not contain expected text: {must_contain}")
    ok(f"Page {path} loads")

def check_api_settings():
    """Check that the /api/settings endpoint responds and has the expected keys."""
    r = sess.get(f"{BASE_URL}/api/settings", timeout=10)
    if r.status_code != 200:
        fail(f"/api/settings → {r.status_code}")
    data = r.json()
    # expected keys
    for k in ("volume_percent", "max_file_size_mb", "poll_interval_sec", "allowed_extensions"):
        if k not in data:
            fail(f"/api/settings missing key: {k}")
    ok("API /api/settings works")

def check_api_effectief_rooster():
    """Check that the schedule API endpoint responds correctly.

    Uses HTTP Basic Auth (username + password in the request header)
    instead of a session cookie, because that is how the daemon calls it.
    A 200 (schedule found) or 204 (no schedule today) are both OK.
    """
    # Basic Auth — same admin credentials
    r = requests.get(
        f"{BASE_URL}/api/effectief-rooster?empty_204=1",
        auth=HTTPBasicAuth(ADMIN_USER, ADMIN_PASS or ""),
        timeout=10,
        verify=VERIFY_TLS,
    )
    if r.status_code not in (200, 204):
        fail(f"/api/effectief-rooster → {r.status_code}")
    ok("API /api/effectief-rooster works")

def get_csrf_from_page(path: str) -> str:
    """Load a page and extract its CSRF token. Calls ``fail()`` if not found."""
    r = sess.get(f"{BASE_URL}{path}", timeout=10)
    if r.status_code != 200:
        fail(f"Cannot fetch CSRF: GET {path} → {r.status_code}")
    csrf = get_csrf_from_meta(r.text)
    if not csrf:
        fail(f"CSRF meta token not found on {path}")
    return csrf

def upload_and_delete():
    """Test the upload and delete workflow end-to-end.

    Uploads a tiny fake MP3 file to /geluiden/upload and then
    immediately deletes it again via /geluiden/delete. Verifies that
    both actions succeed. Only runs when ``DO_UPLOAD_TEST`` is set to 1.
    """
    # Get accept extensions & CSRF via the page
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
        fail(f"Upload failed: status {r.status_code}")

    ok("Upload of test file succeeded")

    # Delete it again immediately
    csrf_del = get_csrf_from_page("/geluiden")
    r = sess.post(f"{BASE_URL}/geluiden/delete", data={"_csrf": csrf_del, "filename": TEST_FILENAME}, timeout=10, allow_redirects=False)
    if r.status_code not in (302, 303):
        fail(f"Delete of test file failed: status {r.status_code}")
    ok("Delete of test file succeeded")

def main():
    """Run all health checks in order and print the results.

    Checks the root redirect, logs in, visits a few pages, calls both
    APIs, and optionally tests upload + delete. Exits with code 1 if
    any check fails (via ``fail()``), or prints a success message when
    everything passes.
    """
    # Basic sanity: base page must redirect to login when not signed in
    r = sess.get(f"{BASE_URL}/", timeout=10, allow_redirects=False)
    if r.status_code not in (301, 302, 303):
        fail(f"Expected root redirect to /login, got {r.status_code}")
    ok("Root redirect ok")

    login()

    # UI pages
    check_page("/geluiden", "Geluiden")
    check_page("/roosters", "Roosters")
    check_page("/logs", "Logboek")

    # APIs
    check_api_settings()
    check_api_effectief_rooster()

    # Optionally test upload
    if DO_UPLOAD_TEST:
        upload_and_delete()

    print("\n✅ Health check passed.")

if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.SSLError as e:
        fail(f"TLS/cert problem: {e}")
    except requests.exceptions.RequestException as e:
        fail(f"HTTP problem: {e}")
