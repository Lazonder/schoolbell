"""Blueprint for the 'Geluiden' page.

A Blueprint is a Flask object that lets you group routes together
in a separate file. The lines below define five URLs that all deal
with audio files: viewing the list, uploading a new sound, playing
one as a test, deleting one, and serving the file itself for the
HTML <audio> tag.

The main app (in ``webinterface.py``) imports this file and calls
``app.register_blueprint(geluiden_bp)`` to plug it in. After that,
Flask treats these routes the same as any other. The only difference
is that ``url_for`` needs the blueprint name as a prefix:

    url_for("geluiden.geluiden")          # the page itself
    url_for("geluiden.serve_audio", ...)  # an MP3 download
"""

import os

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_babel import gettext as _

# Importing the parent module gives us access to constants and
# helpers that have not yet been split out (AUDIO_DIR, ensure_dirs,
# list_audio, log_event, ...). When more phases of issue #28 land,
# many of these moves will tidy up these imports.
import webinterface as wi
from core.audio_files import (
    _play_via_pygame,
    _validate_audio_file,
    safe_audio_filename,
    safe_audio_path,
)
from core.rooster import default_roosters_obj
from settings_store import Settings


# A Blueprint is a Python object that collects routes until they
# are registered on the real app. The first argument ("geluiden")
# becomes part of the endpoint name used by url_for().
geluiden_bp = Blueprint("geluiden", __name__)


@geluiden_bp.route("/audio/<path:filename>")
@wi.tab_required("geluiden")
def serve_audio(filename):
    """Send the requested audio file from AUDIO_DIR back to the browser.

    Used by the <audio> player on the geluiden page. The login
    check keeps the files inside the school's network. Anonymous
    visitors can't download arbitrary mp3s out of the system.
    ``send_from_directory`` blocks any path tricks like '../../etc'
    so the filename can come straight from the URL.
    """
    wi.ensure_dirs()
    return send_from_directory(wi.AUDIO_DIR, filename)


@geluiden_bp.route("/geluiden", methods=["GET"])
@wi.tab_required("geluiden")
def geluiden():
    """Show the audio files page.

    Lists all sound files that have been uploaded. Also passes the
    allowed file extensions and the upload size limit to the template
    so the form can show the right hints to the user.
    """
    wi.ensure_dirs()
    files = wi.list_audio()

    # Read settings for accept/hint
    s = Settings.load()
    allowed_exts = [e.lower() for e in s.allowed_extensions]
    accept_attr = ",".join(allowed_exts)

    return render_template(
        "geluiden.html",
        tab="geluiden",
        csrf_token=wi.get_csrf_token(),
        files=files,
        allowed_exts=allowed_exts,
        accept_attr=accept_attr,
        max_mb=s.max_file_size_mb,
    )


@geluiden_bp.route("/geluiden/upload", methods=["POST"])
@wi.tab_required("geluiden")
def geluiden_upload():
    """Handle the upload of a new audio file.

    Checks that the file has an allowed extension, a safe name, and is
    within the configured size limit. Then tries to load it with pygame
    to verify the audio is actually playable. If all checks pass, the
    file is saved to the audio folder.
    """
    wi.ensure_dirs()

    s = Settings.load()
    max_bytes = int(s.max_file_size_mb) * 1024 * 1024
    allowed_exts = tuple(e.lower() for e in s.allowed_extensions)

    base = (request.form.get("naam") or "").strip()
    if "file" not in request.files:
        flash(_("Geen bestand ontvangen."))
        return redirect(url_for("geluiden.geluiden"))

    f = request.files["file"]
    if not f or f.filename == "":
        flash(_("Geen bestand geselecteerd."))
        return redirect(url_for("geluiden.geluiden"))

    # Extension + validation
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed_exts:
        flash(_("Alleen bestanden met deze extensies zijn toegestaan: %(exts)s.", exts=", ".join(allowed_exts)))
        return redirect(url_for("geluiden.geluiden"))

    filename = safe_audio_filename(base, ext)
    if not filename:
        flash(_("Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -."))
        return redirect(url_for("geluiden.geluiden"))

    # Double-check: safe_audio_filename already constrained the stem,
    # but safe_audio_path also pins the result inside AUDIO_DIR via
    # realpath. That makes path traversal impossible even if the
    # validation rules above ever loosen.
    dest = safe_audio_path(filename, wi.AUDIO_DIR)
    if dest is None:
        flash(_("Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -."))
        return redirect(url_for("geluiden.geluiden"))

    if os.path.exists(dest):
        flash(_("Er bestaat al een audiobestand met deze naam. Kies een andere naam."))
        return redirect(url_for("geluiden.geluiden"))

    # Quick pre-check
    if request.content_length and request.content_length > max_bytes + 64 * 1024:
        flash(_("Bestand is groter dan de ingestelde limiet van %(mb)s MB.", mb=s.max_file_size_mb))
        return redirect(url_for("geluiden.geluiden"))

    # Precise check
    try:
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
    except Exception:
        data = f.read()
        size = len(data)
        f.stream.seek(0)

    if size > max_bytes:
        flash(_("Bestand is groter dan de ingestelde limiet van %(mb)s MB.", mb=s.max_file_size_mb))
        return redirect(url_for("geluiden.geluiden"))

    try:
        f.save(dest)
    except Exception as e:
        flash(_("Kon bestand niet opslaan: %(err)s", err=e))
        return redirect(url_for("geluiden.geluiden"))

    # Pre-flight: try to actually load the file via pygame, the same
    # library the daemon uses to play it. If pygame can't decode it
    # (corrupt MP3, wrong-format renamed-to-mp3, 0-byte file with the
    # right extension), we delete the bad upload and tell the user
    # immediately. This is better than letting it sit in the audio
    # dir until the bell tries to ring at 8:30 and the daemon logs
    # 'File not found' or a decoder error to events.jsonl.
    ok, msg = _validate_audio_file(dest)
    if not ok:
        try:
            os.remove(dest)
        except OSError:
            pass  # if cleanup fails, the user can delete via the UI
        wi.log_event("ui", {
            "action": "upload_audio_rejected",
            "filename": filename,
            "size": size,
            "reason": msg,
        })
        flash(_("Bestand afgewezen: %(reason)s", reason=msg))
        return redirect(url_for("geluiden.geluiden"))

    wi.log_event("ui", {
        "action": "upload_audio",
        "filename": filename,
        "size": size,
        "limit_mb": s.max_file_size_mb
    })
    flash(_("Upload geslaagd: %(filename)s", filename=filename))
    return redirect(url_for("geluiden.geluiden"))


@geluiden_bp.route("/geluiden/play", methods=["POST"])
@wi.tab_required("geluiden")
def geluiden_play():
    """Trigger immediate playback through the school's speakers.

    Logs the action so there's an audit trail. If someone abuses
    the button you can see who and when in the Logboek.
    """
    wi.ensure_dirs()
    name = (request.form.get("filename") or "").strip()
    # safe_audio_path validates the name against the audio-filename
    # allow-list AND confirms the resolved path stays inside
    # AUDIO_DIR. Anything else is rejected before we ever touch
    # the filesystem.
    path = safe_audio_path(name, wi.AUDIO_DIR)
    if path is None or not os.path.isfile(path):
        flash(_("Bestand niet gevonden."))
        return redirect(url_for("geluiden.geluiden"))

    try:
        v = max(0, min(100, int(Settings.load().volume_percent))) / 100.0
        _play_via_pygame(path, v)
        wi.log_event("ui", {"action": "test_bell", "filename": name})
        flash(_("Test gestart: %(name)s", name=name))
    except Exception as e:
        # Common failure modes here: ALSA can't open the device
        # (audio config wrong), or the file isn't a format pygame
        # can decode. Surface the error so the admin can debug.
        wi.log_event("ui", {"action": "test_bell_error", "filename": name, "error": str(e)})
        flash(_("Afspelen mislukt: %(err)s", err=e))
    return redirect(url_for("geluiden.geluiden"))


@geluiden_bp.route("/geluiden/delete", methods=["POST"])
@wi.tab_required("geluiden")
def geluiden_delete():
    """Delete an audio file from the server.

    First checks whether any rooster still uses the file. If so,
    deletion is blocked and the admin sees which rooster moments still
    reference it. Otherwise the file is removed and the action is logged.
    """
    wi.ensure_dirs()
    name = (request.form.get("filename") or "").strip()
    # Same safety check as in geluiden_play: only accept plain
    # filenames whose resolved path lives inside AUDIO_DIR.
    path = safe_audio_path(name, wi.AUDIO_DIR)
    if path is None or not os.path.isfile(path):
        flash(_("Bestand niet gevonden."))
        return redirect(url_for("geluiden.geluiden"))

    # Block-and-warn if the file is still used by any rooster moment.
    # Without this check, deletion would succeed silently and the
    # daemon would later log 'File not found' when that bell tried
    # to ring. The bell wouldn't go off and the user wouldn't know
    # why. Mirrors the same pattern as delete_rooster.
    roosters = wi.load_json(wi.ROOSTERS_PATH, default_roosters_obj())
    used_by = []
    for rooster_naam, momenten in roosters.items():
        for m in momenten:
            if (m.get("bestand") or "") == name:
                used_by.append(f"{rooster_naam}: {m.get('tijd','??:??')} {m.get('naam','')}".rstrip())
                break  # one mention per rooster is enough
    if used_by:
        voorb = "; ".join(used_by[:3])
        meer = "" if len(used_by) <= 3 else _(" en %(n)d meer", n=len(used_by) - 3)
        flash(_(
            "Geluid '%(name)s' wordt nog gebruikt door: %(voorb)s%(meer)s. "
            "Verwijder of vervang deze momenten eerst voordat je het bestand verwijdert.",
            name=name, voorb=voorb, meer=meer,
        ))
        return redirect(url_for("geluiden.geluiden"))

    try:
        os.remove(path)
        wi.log_event("ui", {"action": "delete_audio", "filename": name})
        flash(_("Verwijderd: %(name)s", name=name))
    except Exception as e:
        flash(_("Kon niet verwijderen: %(err)s", err=e))
    return redirect(url_for("geluiden.geluiden"))
