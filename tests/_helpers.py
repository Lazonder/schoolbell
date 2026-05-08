"""
Plain helpers for route tests. Lives outside conftest.py so test
modules can `from tests._helpers import ...` directly — pytest's
conftest module is auto-loaded but isn't on the import path the same
way an ordinary module is.
"""

import re

# Known plaintext password for the test admin. The matching hash is
# generated in conftest.py and exported as SCHOOLBELL_WEB_PWHASH.
TEST_PASSWORD = "test-pass-1234"


_CSRF_META_RE = re.compile(r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)')
_CSRF_FORM_RE = re.compile(r'name=["\']_csrf["\'][^>]*value=["\']([^"\']+)')


def csrf_from_html(html: str) -> str:
    """Pull the CSRF token out of a rendered page.

    Tries the <meta> tag first (set on every authed page via
    base.html) and falls back to the hidden <input> in the login
    form. Raising on miss makes test failures obvious instead of
    silently sending an empty token.
    """
    m = _CSRF_META_RE.search(html) or _CSRF_FORM_RE.search(html)
    if not m:
        raise AssertionError("No CSRF token found in HTML")
    return m.group(1)
