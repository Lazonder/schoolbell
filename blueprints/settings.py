"""Blueprint for the Voorkeuren page and the settings JSON API.

Three routes:

  GET  /settings       — admin page (HTML)
  GET  /api/settings   — JSON shape of the current Settings object
  POST /api/settings   — JSON payload, validated and saved

The pure validation logic lives in ``_apply_settings_payload``: it
takes a Settings dataclass and a dict from the browser, mutates the
dataclass in place when each field is OK, and calls ``abort(400)``
when something is wrong. Tests use that function directly to pin
the validation rules without going through HTTP.

``_build_vakanties_status`` puts together the status panel that the
settings template renders: which schooljaren are stored in
``data/vakanties.json``, when each was fetched, and what the
daemon's last attempt/result was.
"""

import json
import os
import re
from dataclasses import asdict

from flask import Blueprint, abort, jsonify, render_template, request

import settings_store
import webinterface as wi
from core.i18n import SUPPORTED_LOCALES
from settings_store import Settings


settings_bp = Blueprint("settings", __name__)


# CSS hex color: #rgb / #rrggbb (case-insensitive). Used to validate
# the huisstijl custom-color payload before it's stored and rendered
# unescaped into <html style="--sb-color-...">. A stricter check than
# eyeballing: anything that doesn't match isn't safe to inject.
_CSS_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def _apply_settings_payload(s: Settings, payload: dict) -> None:
    """Mutate `s` in place from `payload`, validating each field.

    Raises Flask abort(400) on invalid input. When called inside
    settings_store.locked(), the abort propagates through the
    context manager, which means save() is NOT called and the file
    is left untouched. That's the right behavior for invalid input.
    """
    if "volume_percent" in payload:
        v = int(payload["volume_percent"])
        if not (0 <= v <= 100): abort(400, "volume_percent must be 0..100")
        s.volume_percent = v

    if "max_file_size_mb" in payload:
        m = int(payload["max_file_size_mb"])
        if not (1 <= m <= 1024): abort(400, "max_file_size_mb must be 1..1024")
        s.max_file_size_mb = m

    if "poll_interval_sec" in payload:
        p = int(payload["poll_interval_sec"])
        if not (1 <= p <= 60): abort(400, "poll_interval_sec must be 1..60")
        s.poll_interval_sec = p

    # Note: a "timezone" key in the payload is silently ignored. The
    # field was removed; the OS timezone is the source of truth.

    if "taal" in payload:
        # Allowed: each language we ship a translation for, plus the
        # special string "auto" (follow browser). Anything else is a
        # client mistake. Abort 400 so the user gets a clear error
        # instead of a silently-ignored selection.
        t = str(payload["taal"]).strip().lower()
        allowed_taal = set(SUPPORTED_LOCALES) | {"auto"}
        if t not in allowed_taal:
            abort(400, f"taal must be one of: {', '.join(sorted(allowed_taal))}")
        s.taal = t

    if "theme_mode" in payload:
        tm = str(payload["theme_mode"]).strip().lower()
        if tm not in ("light", "dark", "auto"):
            abort(400, "theme_mode must be one of: light, dark, auto")
        s.theme_mode = tm

    if "huisstijl" in payload:
        hs = str(payload["huisstijl"]).strip().lower()
        if hs not in ("standaard", "aangepast"):
            abort(400, "huisstijl must be one of: standaard, aangepast")
        s.huisstijl = hs

    # Validate the three custom-color fields independently so the user
    # can save partial updates from the Voorkeuren UI (e.g. only
    # tweaking the nav color). Each must look like a CSS hex code.
    # We don't try to enforce contrast or other accessibility checks.
    # The user picked these intentionally and may know what they want.
    for key in ("theme_custom_bg", "theme_custom_table", "theme_custom_nav"):
        if key in payload:
            v = str(payload[key]).strip()
            if not _CSS_HEX_COLOR_RE.fullmatch(v):
                abort(400, f"{key} must be a CSS hex color like #rrggbb")
            setattr(s, key, v.lower())

    if "vakantieregio" in payload:
        vr = str(payload["vakantieregio"]).strip()
        if vr not in wi.VAKANTIE_REGIOS:
            abort(400, f"vakantieregio must be one of: {', '.join(wi.VAKANTIE_REGIOS)}")
        s.vakantieregio = vr

    if "vakanties_scrape_enabled" in payload:
        # Accept proper booleans plus the common JSON/HTML form
        # representations ('true'/'false', '1'/'0', 'on'/'off',
        # checkbox-style 'on'/missing). The settings page uses a
        # checkbox which sends 'on' when checked and nothing when
        # unchecked. The JSON POST in settings.html maps that to a
        # real bool, but be defensive in case a future form posts
        # raw form data.
        v = payload["vakanties_scrape_enabled"]
        if isinstance(v, bool):
            s.vakanties_scrape_enabled = v
        elif isinstance(v, str):
            s.vakanties_scrape_enabled = v.strip().lower() in ("1", "true", "yes", "on")
        else:
            s.vakanties_scrape_enabled = bool(v)


def _build_vakanties_status() -> dict:
    """Gather everything the Voorkeuren status panel needs to render.

    Pulls together:
      - which schooljaren are saved in data/vakanties.json
      - when each was fetched
      - last attempt / success / error from data/vakanties_fetch_state.json

    Returns a plain dict that the template can iterate over directly.
    Best-effort: any read error falls back to a sensible empty value
    rather than raising, so a corrupt state file doesn't break the
    settings page.
    """
    status = {
        "saved_schooljaren": [],   # list of {schooljaar, fetched_at}
        "last_attempt_at": "",
        "last_success_at": "",
        "last_error": "",
        "last_failed_schooljaren": [],
    }

    # Late import to avoid a top-level dependency between two
    # blueprints. blueprints.agenda is loaded by webinterface.py
    # at the same place as this module, so by the time this
    # function actually runs (per-request), the import is cheap
    # and circular-safe.
    from blueprints.agenda import _load_vakanties_file
    data, _err = _load_vakanties_file()
    if data and isinstance(data.get("schooljaren"), dict):
        for sj_key in sorted(data["schooljaren"].keys()):
            block = data["schooljaren"][sj_key]
            status["saved_schooljaren"].append({
                "schooljaar": sj_key,
                "fetched_at": (block.get("fetched_at", "") if isinstance(block, dict) else ""),
            })

    # Daemon writes state at data/vakanties_fetch_state.json. Read it
    # directly here rather than importing daemon code (which pulls in
    # pygame and would slow down the settings render).
    state_path = os.path.join(wi.DATA_DIR, "vakanties_fetch_state.json")
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        state = {}

    status["last_attempt_at"] = state.get("last_attempt_at", "")
    status["last_success_at"] = state.get("last_success_at", "")
    status["last_error"] = state.get("last_error", "")
    status["last_failed_schooljaren"] = state.get("last_failed_schooljaren", []) or []

    return status


# -- Settings (pagina) --
@settings_bp.get("/settings")
@wi.ui_login_required
def settings_page():
    return render_template(
        "settings.html",
        tab="settings",
        csrf_token=wi.get_csrf_token(),
        vakanties_status=_build_vakanties_status(),
    )


# -- Settings API --
@settings_bp.route("/api/settings", methods=["GET"])
@wi.require_admin
def api_settings_get():
    return jsonify(asdict(Settings.load()))


@settings_bp.route("/api/settings", methods=["POST"])
@wi.require_admin
def api_settings_post():
    if not request.is_json:
        abort(400, "JSON expected")
    payload = request.get_json() or {}
    # Hold the settings file lock for the entire load -> mutate ->
    # save sequence. Without the lock, two concurrent POSTs could
    # both load v1, each apply their own payload, and both save.
    # Last write wins, and the first user's change is silently lost.
    # If validation fails, _apply_settings_payload aborts, the
    # context manager unwinds without calling save(), and the file
    # is untouched.
    with settings_store.locked() as s:
        _apply_settings_payload(s, payload)
        result = asdict(s)
    return jsonify(result), 200
