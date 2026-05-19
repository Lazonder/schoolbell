"""Fill translations/en/LC_MESSAGES/messages.po with English translations.

Run after `pybabel extract -F babel.cfg -o messages.pot .` and
`pybabel update -i messages.pot -d translations -l en`. This script
walks the catalog, looks up each Dutch msgid in the TRANSLATIONS
dict below, and writes the matching English msgstr.

Strings not in the dict get an empty msgstr (Babel will then fall
back to the msgid, i.e. show the Dutch source). When extracting new
strings later, run this script again — only the new ones need to be
added to TRANSLATIONS.
"""

from babel.messages.pofile import read_po, write_po


TRANSLATIONS: dict[str, str] = {
    # Flash messages — webinterface.py
    "Upload te groot (controleer de ingestelde limiet bij Voorkeuren).":
        "Upload too large (check the limit configured in Settings).",

    # Flash messages — blueprints/agenda.py
    "Ongeldig rooster voor %(datum)s: '%(waarde)s' bestaat niet. Overgeslagen.":
        "Invalid schedule for %(datum)s: '%(waarde)s' does not exist. Skipped.",
    "Agenda opgeslagen.":
        "Calendar saved.",
    "Vakantie-scraping is uitgeschakeld in Voorkeuren.":
        "Holiday scraping is disabled in Settings.",
    "Importeren mislukt: %(err)s":
        "Import failed: %(err)s",
    "Geen vakantiebestand gevonden (%(path)s). Klik 'Verversen van rijksoverheid.nl' om het op te halen.":
        "No holiday file found (%(path)s). Click 'Refresh from rijksoverheid.nl' to fetch it.",
    "Vakantiebestand bevat geen 'schooljaren'. Klik 'Verversen van rijksoverheid.nl' om opnieuw op te halen.":
        "Holiday file contains no 'schooljaren'. Click 'Refresh from rijksoverheid.nl' to fetch again.",
    "Geen schooljaren in het bestand bevatten regio '%(regio)s'. Aanwezige schooljaren: %(schooljaren)s.":
        "No school years in the file contain region '%(regio)s'. School years available: %(schooljaren)s.",
    "Geen weken om te markeren voor regio %(regio)s. Controleer het vakantiebestand (%(count)s ongeldige entries).":
        "No weeks to mark for region %(regio)s. Check the holiday file (%(count)s invalid entries).",
    "%(weken)s week(weken) gemarkeerd als 'Bel uit' (regio %(regio)s, uit %(aantal)s schooljaar/jaren: %(schooljaren)s).":
        "%(weken)s week(s) marked as 'Bell off' (region %(regio)s, from %(aantal)s school year(s): %(schooljaren)s).",
    " en %(count)s meer":
        " and %(count)s more",
    " Overgeslagen: %(voorb)s%(meer)s.":
        " Skipped: %(voorb)s%(meer)s.",
    "Bestaand vakantiebestand kon niet gelezen worden (%(err)s); wordt overschreven.":
        "Existing holiday file could not be read (%(err)s); will be overwritten.",
    "Verversen mislukt voor alle %(n)d schooljaren. Eerste fout: %(err)s":
        "Refresh failed for all %(n)d school years. First error: %(err)s",

    # Flash messages — blueprints/auth.py
    "Onjuiste inloggegevens.":
        "Invalid credentials.",

    # Flash messages — blueprints/geluiden.py
    "Geen bestand ontvangen.":
        "No file received.",
    "Geen bestand geselecteerd.":
        "No file selected.",
    "Alleen bestanden met deze extensies zijn toegestaan: %(exts)s.":
        "Only files with these extensions are allowed: %(exts)s.",
    "Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -.":
        "Invalid name. Use 1–35 characters: letters, digits, space, _ or -.",
    "Er bestaat al een audiobestand met deze naam. Kies een andere naam.":
        "An audio file with this name already exists. Choose a different name.",
    "Bestand is groter dan de ingestelde limiet van %(mb)s MB.":
        "File is larger than the configured limit of %(mb)s MB.",
    "Kon bestand niet opslaan: %(err)s":
        "Could not save file: %(err)s",
    "Bestand afgewezen: %(reason)s":
        "File rejected: %(reason)s",
    "Upload geslaagd: %(filename)s":
        "Upload succeeded: %(filename)s",
    "Bestand niet gevonden.":
        "File not found.",
    "Test gestart: %(name)s":
        "Test started: %(name)s",
    "Afspelen mislukt: %(err)s":
        "Playback failed: %(err)s",
    " en %(n)d meer":
        " and %(n)d more",
    "Geluid '%(name)s' wordt nog gebruikt door: %(voorb)s%(meer)s. Verwijder of vervang deze momenten eerst voordat je het bestand verwijdert.":
        "Sound '%(name)s' is still used by: %(voorb)s%(meer)s. Remove or replace those moments first before deleting the file.",
    "Verwijderd: %(name)s":
        "Deleted: %(name)s",
    "Kon niet verwijderen: %(err)s":
        "Could not delete: %(err)s",

    # Flash messages — blueprints/roosters.py
    "Naam van rooster is verplicht.":
        "Schedule name is required.",
    "Er bestaat al een rooster met deze naam.":
        "A schedule with this name already exists.",
    "Rooster '%(naam)s' aangemaakt.":
        "Schedule '%(naam)s' created.",
    "Onbekend rooster.":
        "Unknown schedule.",
    "Standaardweek (%(dagen)s)":
        "Default week (%(dagen)s)",
    "Agenda (%(voorb)s%(meer)s)":
        "Calendar (%(voorb)s%(meer)s)",
    "Rooster '%(rooster)s' is nog in gebruik bij: %(delen)s. Haal deze verwijzingen eerst weg voordat je het rooster verwijdert.":
        "Schedule '%(rooster)s' is still in use by: %(delen)s. Remove those references first before deleting the schedule.",
    "Rooster '%(rooster)s' verwijderd.":
        "Schedule '%(rooster)s' deleted.",
    "Tijd moet in formaat UU:MM (bijv. 8:05 of 08:05).":
        "Time must be in format HH:MM (e.g. 8:05 or 08:05).",
    "Naam is verplicht.":
        "Name is required.",
    "Kies een geluidsbestand.":
        "Pick an audio file.",
    "Waarschuwing: minuten moeten een getal zijn.":
        "Warning: minutes must be a number.",
    "Waarschuwing: minuten moeten tussen 0 en 60 liggen.":
        "Warning: minutes must be between 0 and 60.",
    "Kies een geluid voor de waarschuwingsbel, of zet 'minuten eerder' op 0.":
        "Pick a sound for the warning bell, or set 'minutes earlier' to 0.",
    "Moment toegevoegd aan '%(rooster)s'.":
        "Moment added to '%(rooster)s'.",
    "Moment '%(naam)s' verwijderd uit '%(rooster)s'.":
        "Moment '%(naam)s' deleted from '%(rooster)s'.",
    "Moment '%(naam)s' bijgewerkt in '%(rooster)s'.":
        "Moment '%(naam)s' updated in '%(rooster)s'.",
    "Onbekende regel.":
        "Unknown row.",
    "'%(keuze)s' bestaat niet als rooster; overslaan voor %(dag)s.":
        "'%(keuze)s' does not exist as a schedule; skipping for %(dag)s.",
    "Standaardweek opgeslagen.":
        "Default week saved.",

    # Navigation / brand
    "Agenda": "Calendar",
    "Vakanties": "Holidays",
    "Roosters": "Schedules",
    "Standaardweek": "Default week",
    "Geluiden": "Sounds",
    "Logboek": "Log",
    "Voorkeuren": "Settings",
    "Schoolbel": "School bell",
    "IVKO Schoolbel": "IVKO School bell",
    "IVKO · Schoolbel": "IVKO · School bell",
    "Daemon": "Daemon",
    "Daemon actief": "Daemon active",
    "Daemon: geen heartbeat": "Daemon: no heartbeat",
    "Daemon: %(age)ss stil": "Daemon: %(age)ss silent",
    "(laatste poll: %(t)s UTC)": "(last poll: %(t)s UTC)",
    "Uitloggen": "Sign out",

    # Modal
    "Weet je het zeker?": "Are you sure?",
    "Annuleren": "Cancel",
    "Bevestigen": "Confirm",

    # Login page
    "Inloggen": "Sign in",
    "Schoolbel — Inloggen": "School bell — Sign in",
    "Gebruikersnaam": "Username",
    "Wachtwoord": "Password",
    "Je gebruikersnaam is meestal <code class=\"sb-code-mono\">%(admin_user)s</code>.":
        "Your username is usually <code class=\"sb-code-mono\">%(admin_user)s</code>.",

    # Logs page
    "Overzicht van komende belmomenten, recente bel-events (daemon) en recente UI-acties.":
        "Overview of upcoming bell moments, recent bell events (daemon) and recent UI actions.",
    "Eerstkomende belmomenten": "Upcoming bell moments",
    "Datum/Tijd": "Date/Time",
    "Naam": "Name",
    "Bestand": "File",
    "Rooster": "Schedule",
    "Geen komende momenten gevonden.": "No upcoming moments found.",
    "Recente UI-acties": "Recent UI actions",
    "Ts": "Ts",
    "Actie": "Action",
    "Details": "Details",
    "Geen UI-acties gelogd.": "No UI actions logged.",
    "Recente bel-events (daemon)": "Recent bell events (daemon)",
    "Tijd": "Time",
    "Status": "Status",
    "Bericht": "Message",
    "Geen bel-logregels (daemon heeft nog niet gelogd).":
        "No bell log entries (daemon hasn't logged yet).",

    # /now page
    "Schoolbel · Volgende bel": "School bell · Next bell",
    "Laatste data-fetch faalde": "Last data fetch failed",
    "Volgende bel": "Next bell",
    "Geen bel meer vandaag.": "No more bells today.",
    "om": "at",

    # Roosters page
    "Waarschuwing": "Warning",
    "%(warn_min)s min eerder · %(warn_bestand)s":
        "%(warn_min)s min earlier · %(warn_bestand)s",
    "Moment %(tijd)s (%(naam)s) verwijderen?":
        "Delete moment %(tijd)s (%(naam)s)?",
    "Verwijder dit moment": "Delete this moment",
    "Bewerk dit moment": "Edit this moment",
    "Geen momenten": "No moments",
    "Naam (verplicht)": "Name (required)",
    "— Kies geluid —": "— Pick sound —",
    "Waarschuwing:": "Warning:",
    "Minuten vóór de bel een waarschuwing afspelen (0 = uit)":
        "Minutes before the bell to play a warning (0 = off)",
    "min. eerder met": "min. earlier with",
    "— geen waarschuwing —": "— no warning —",
    "Moment toevoegen": "Add moment",
    "Rooster &quot;%(naam)s&quot; verwijderen? Dit verwijdert ook alle momenten in dit rooster.":
        "Delete schedule &quot;%(naam)s&quot;? This also deletes all moments in this schedule.",
    "Rooster verwijderen": "Delete schedule",
    "Er zijn nog geen roosters. Maak de eerste aan.":
        "There are no schedules yet. Create the first one.",
    "Nieuw rooster": "New schedule",
    "Naam nieuw rooster": "Name of new schedule",
    "Start als kopie van het eerste rooster": "Start as a copy of the first schedule",
    "Aanmaken": "Create",
    # Geluiden page
    "Beschikbare geluiden": "Available sounds",
    "Voorbeeluister in browser": "Preview in browser",
    "&quot;%(name)s&quot; nu door de hele school afspelen?":
        "Play &quot;%(name)s&quot; now through the whole school?",
    "Afspelen": "Play",
    "Speel af via omroepinstallatie school": "Play via school PA system",
    "Bestand &quot;%(name)s&quot; verwijderen?": "Delete file &quot;%(name)s&quot;?",
    "Verwijderen": "Delete",
    "Geen audiobestanden gevonden.": "No audio files found.",
    "Nieuw geluid uploaden": "Upload new sound",
    "Unieke naam (max 35 tekens)": "Unique name (max 35 characters)",
    "Toegestane extensies: %(exts)s • max %(max_mb)s MB • Naam: letters/cijfers/spatie/_/-":
        "Allowed extensions: %(exts)s • max %(max_mb)s MB • Name: letters/digits/space/_/-",
    "Uploaden": "Upload",

    # Agenda page
    'Importeer schoolvakanties uit\n    <code class="sb-code-mono">data/vakanties.json</code>\n    voor regio <strong>%(vakantieregio)s</strong>\n    (instelbaar via <a href="%(settings_url)s">Voorkeuren</a>).\n    De getroffen weken worden automatisch op <em>Bel uit</em> gezet.\n    Bestaande markeringen blijven staan — de import voegt alleen toe.':
        'Import school holidays from\n    <code class="sb-code-mono">data/vakanties.json</code>\n    for region <strong>%(vakantieregio)s</strong>\n    (configurable via <a href="%(settings_url)s">Settings</a>).\n    Affected weeks are automatically set to <em>Bell off</em>.\n    Existing marks stay — import only adds.',
    "<strong>Nog geen bestand gevonden.</strong> Klik\n      <em>Verversen van rijksoverheid.nl</em> om het op te halen.":
        "<strong>No file found yet.</strong> Click\n      <em>Refresh from rijksoverheid.nl</em> to fetch it.",
    "Vakanties importeren voor regio %(vakantieregio)s? De getroffen weken worden op &quot;Bel uit&quot; gezet (bestaande markeringen blijven staan).":
        "Import holidays for region %(vakantieregio)s? Affected weeks will be set to &quot;Bell off&quot; (existing marks stay).",
    "Importeren": "Import",
    "Vakanties importeren": "Import holidays",
    "Vakantiegegevens ophalen van rijksoverheid.nl en data/vakanties.json overschrijven? Eventuele handmatige aanpassingen aan dat bestand gaan verloren.":
        "Fetch holiday data from rijksoverheid.nl and overwrite data/vakanties.json? Any manual edits to that file will be lost.",
    "Verversen": "Refresh",
    "Verversen van rijksoverheid.nl": "Refresh from rijksoverheid.nl",
    'De daemon ververst dit automatisch ongeveer elke maand. Met de\n    knop hierboven kun je tussentijds verversen of de eerste keer\n    ophalen. Status zichtbaar in <a href="%(settings_url)s">Voorkeuren</a>.':
        'The daemon refreshes this automatically about once a month. Use\n    the button above to refresh in between or to fetch for the first\n    time. Status visible in <a href="%(settings_url)s">Settings</a>.',
    "Wijzig per dag het rooster, of zet een hele week uit. Klik daarna op\n          <strong>Alles opslaan</strong>.":
        "Change the schedule per day, or turn a whole week off. Then click\n          <strong>Save all</strong>.",
    "Alles opslaan": "Save all",
    "Ma": "Mon",
    "Di": "Tue",
    "Wo": "Wed",
    "Do": "Thu",
    "Vr": "Fri",
    "Week": "Week",
    "Bel uit": "Bell off",
    "Uit": "Off",

    # Settings page
    "Pas hier algemene instellingen van de schoolbel aan, zoals volume, maximale bestandsgrootte en polling-interval.":
        "Adjust general settings of the school bell here, like volume, maximum file size and polling interval.",
    "Belvolume:": "Bell volume:",
    "Standaard afspeelvolume van de bel.": "Default playback volume of the bell.",
    "Max. bestandsgrootte (MB)": "Max. file size (MB)",
    "Maximale grootte van geüploade audiobestanden.": "Maximum size of uploaded audio files.",
    "Polling-tijd (seconden)": "Polling time (seconds)",
    "Hoe vaak de daemon de planning controleert.": "How often the daemon checks the schedule.",
    "Taal": "Language",
    "Automatisch (volgt browser)": "Automatic (follow browser)",
    "Taal van de webinterface. Vertalingen worden in een latere update toegevoegd; voor nu blijft alle tekst Nederlands.":
        "Language of the web interface. Translations will be added in a later update; for now all text stays in Dutch.",
    "Thema": "Theme",
    "Licht": "Light",
    "Donker": "Dark",
    "Automatisch (volgt systeem)": "Automatic (follow system)",
    "Kies de kleurmodus voor de interface.": "Pick the color mode for the interface.",
    "Huisstijl": "Branding",
    "Standaard": "Default",
    "Aangepast": "Custom",
    "<strong>Standaard</strong> volgt het thema hierboven (Licht of Donker).\n        <strong>Aangepast</strong> gebruikt de drie kleuren hieronder voor\n        achtergrond, tabelvulling en navigatiebalk — staat los van Licht/Donker.":
        "<strong>Default</strong> follows the theme above (Light or Dark).\n        <strong>Custom</strong> uses the three colors below for\n        background, table fill and navigation bar — independent of Light/Dark.",
    "Aangepaste kleuren": "Custom colors",
    "Achtergrond": "Background",
    "Tabelvulling": "Table fill",
    "Navigatiebalk": "Navigation bar",
    "Klik op een kleurvak om een andere kleur te kiezen. Wijzigingen worden direct toegepast bij Opslaan; geen pagina-refresh nodig.":
        "Click a color box to pick a different color. Changes apply immediately on Save; no page refresh needed.",
    "Schoolvakanties van rijksoverheid.nl ophalen": "Fetch school holidays from rijksoverheid.nl",
    "Aan: de daemon haalt elke ~maand de officiële Nederlandse\n        schoolvakanties op (huidig schooljaar + 4 vooruit) en de\n        knoppen op de Agenda zijn beschikbaar.\n        Uit: data/vakanties.json wordt niet aangepast en de\n        Vakanties-kaart op de Agenda is verborgen — handig voor\n        installs buiten Nederland.":
        "On: the daemon fetches the official Dutch school holidays about\n        once a month (current school year + 4 ahead) and the buttons\n        on the Calendar are available.\n        Off: data/vakanties.json is not touched and the Holidays card\n        on the Calendar is hidden — handy for installs outside the\n        Netherlands.",
    "Vakantieregio": "Holiday region",
    "Noord": "North",
    "Midden": "Central",
    "Zuid": "South",
    "Regio die de knop <em>Vakanties importeren</em> op de Agenda\n        gebruikt om dates uit <code class=\"sb-code-mono\">data/vakanties.json</code>\n        te kiezen.":
        "Region the <em>Import holidays</em> button on the Calendar uses\n        to pick dates from <code class=\"sb-code-mono\">data/vakanties.json</code>.",
    "Opslaan": "Save",
    "Status vakantie-scrape": "Holiday-scrape status",
    "Opgeslagen schooljaren in\n      <code class=\"sb-code-mono\">data/vakanties.json</code>:":
        "Stored school years in\n      <code class=\"sb-code-mono\">data/vakanties.json</code>:",
    "— opgehaald %(datum)s": "— fetched %(datum)s",
    "Nog geen vakantiegegevens opgeslagen.": "No holiday data stored yet.",
    "Laatste geslaagde fetch:\n      <strong>%(tijd)s UTC</strong>.":
        "Last successful fetch:\n      <strong>%(tijd)s UTC</strong>.",
    "Nog geen geslaagde fetch geregistreerd.": "No successful fetch recorded yet.",
    "Laatste fout: %(error)s": "Last error: %(error)s",
    "(mislukt: %(jaren)s)": "(failed: %(jaren)s)",
    "Laatste poging: %(tijd)s UTC.": "Last attempt: %(tijd)s UTC.",

    # Standaardweek page
    "Kies per weekdag een standaardrooster. De <strong>Agenda</strong> per datum overschrijft deze keuze wanneer ingesteld.":
        "Pick a default schedule per weekday. The <strong>Calendar</strong> per date overrides this choice when set.",
    "Dag": "Day",
    "Standaardrooster": "Default schedule",
    "— geen —": "— none —",

    # Weekday labels passed through _() in templates (from WEEKDAYS list).
    "Maandag": "Monday",
    "Dinsdag": "Tuesday",
    "Woensdag": "Wednesday",
    "Donderdag": "Thursday",
    "Vrijdag": "Friday",
    "Zaterdag": "Saturday",
    "Zondag": "Sunday",

    # --- Multi-user (gebruikers page, header, 403) ---
    # Flash messages — blueprints/gebruikers.py
    "Gebruiker '%(u)s' aangemaakt.": "User '%(u)s' created.",
    "Fout bij aanmaken: %(err)s": "Error creating user: %(err)s",
    "Wijzigingen voor '%(u)s' opgeslagen.": "Changes for '%(u)s' saved.",
    "Fout bij wijzigen: %(err)s": "Error updating user: %(err)s",
    "Wachtwoord voor '%(u)s' bijgewerkt.": "Password for '%(u)s' updated.",
    "Fout: %(err)s": "Error: %(err)s",
    "Gebruiker '%(u)s' verwijderd.": "User '%(u)s' deleted.",
    "Fout bij verwijderen: %(err)s": "Error deleting user: %(err)s",

    # 403 page
    "Geen toegang": "No access",
    "Je account heeft geen toegang tot deze pagina. Vraag een admin om je tabbladen aan te passen.":
        "Your account doesn't have access to this page. Ask an admin to adjust your tabs.",
    "Je bent ingelogd als <strong>%(u)s</strong>.":
        "You're signed in as <strong>%(u)s</strong>.",
    "Terug naar overzicht": "Back to overview",

    # Header indicator + nav-link
    "Gebruikers": "Users",
    "Rol: %(r)s": "Role: %(r)s",

    # gebruikers.html — page
    "Beheer wie kan inloggen en welke tabbladen elke gebruiker mag zien. Alleen admins zien deze pagina.":
        "Manage who can sign in and which tabs each user is allowed to see. Only admins see this page.",
    "Bestaande gebruikers": "Existing users",
    "Rol": "Role",
    "Tabbladen": "Tabs",
    "Acties": "Actions",
    "jij": "you",
    "alle": "all",
    "Bewerken": "Edit",
    "Gebruiker": "User",
    "Admin": "Admin",
    "Admins krijgen automatisch toegang tot alle tabbladen; deze vinkjes worden dan genegeerd.":
        "Admins automatically get access to all tabs; these checkboxes are then ignored.",
    "Nieuw wachtwoord": "New password",
    "Reset": "Reset",
    "Gebruiker %(u)s verwijderen?": "Delete user %(u)s?",
    "Nieuwe gebruiker": "New user",
    "Kleine letters, cijfers, _ en -. 2 tot 32 tekens.":
        "Lowercase letters, digits, _ and -. 2 to 32 characters.",
    "Minstens 8 tekens.": "At least 8 characters.",
    "Voor admins worden deze vinkjes genegeerd (admins krijgen altijd alles).":
        "These checkboxes are ignored for admins (admins always get everything).",
}


def main() -> None:
    path = "translations/en/LC_MESSAGES/messages.po"
    with open(path, "rb") as f:
        cat = read_po(f)

    missing: list[str] = []
    for m in cat:
        if not m.id:
            continue
        if m.id in TRANSLATIONS:
            m.string = TRANSLATIONS[m.id]
            # pybabel's `update` step can mark entries as "fuzzy"
            # when it guesses a new string from a similar old one
            # (e.g. "Gebruiker '...' aangemaakt." was guessed from
            # "Rooster '...' aangemaakt."). Once we provide an
            # explicit translation here, clear the flag so the
            # entry is treated as a confirmed match.
            if "fuzzy" in m.flags:
                m.flags.discard("fuzzy")
        else:
            missing.append(m.id)

    with open(path, "wb") as f:
        write_po(f, cat)

    print(f"Wrote {sum(1 for m in cat if m.id and m.string)} translations.")
    if missing:
        print(f"Missing translations ({len(missing)}):")
        for s in missing[:10]:
            print(f"  - {s!r}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")


if __name__ == "__main__":
    main()
