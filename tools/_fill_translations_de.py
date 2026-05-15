"""Fill translations/de/LC_MESSAGES/messages.po with German translations.

Same shape as _fill_translations_en.py — see that file for the
workflow. German plural rules differ from Dutch/English; for
strings with counts (e.g. ' und %(n)d mehr') ngettext would be
the correct path. Today we only have a handful of such strings
and they read fine with a single form, so we keep gettext until
that turns into a real complaint.

Glossary used (matches CONTRIBUTING.md):
    Rooster        -> Stundenplan
    Standaardweek  -> Standardwoche
    Agenda         -> Kalender
    Bel uit        -> Glocke aus
    Geluid         -> Klang
    Voorkeuren     -> Einstellungen
    Huisstijl      -> Hausfarben
    Waarschuwing   -> Warnung
"""

from babel.messages.pofile import read_po, write_po


TRANSLATIONS: dict[str, str] = {
    # Flash messages — webinterface.py
    "Upload te groot (controleer de ingestelde limiet bij Voorkeuren).":
        "Upload zu groß (Limit in den Einstellungen prüfen).",

    # Flash messages — blueprints/agenda.py
    "Ongeldig rooster voor %(datum)s: '%(waarde)s' bestaat niet. Overgeslagen.":
        "Ungültiger Stundenplan für %(datum)s: '%(waarde)s' existiert nicht. Übersprungen.",
    "Agenda opgeslagen.":
        "Kalender gespeichert.",
    "Vakantie-scraping is uitgeschakeld in Voorkeuren.":
        "Ferien-Abruf ist in den Einstellungen deaktiviert.",
    "Importeren mislukt: %(err)s":
        "Import fehlgeschlagen: %(err)s",
    "Geen vakantiebestand gevonden (%(path)s). Klik 'Verversen van rijksoverheid.nl' om het op te halen.":
        "Keine Feriendatei gefunden (%(path)s). Klicken Sie auf 'Von rijksoverheid.nl aktualisieren', um sie zu holen.",
    "Vakantiebestand bevat geen 'schooljaren'. Klik 'Verversen van rijksoverheid.nl' om opnieuw op te halen.":
        "Feriendatei enthält keine 'schooljaren'. Klicken Sie auf 'Von rijksoverheid.nl aktualisieren', um sie erneut zu holen.",
    "Geen schooljaren in het bestand bevatten regio '%(regio)s'. Aanwezige schooljaren: %(schooljaren)s.":
        "Keine Schuljahre in der Datei enthalten Region '%(regio)s'. Vorhandene Schuljahre: %(schooljaren)s.",
    "Geen weken om te markeren voor regio %(regio)s. Controleer het vakantiebestand (%(count)s ongeldige entries).":
        "Keine Wochen für Region %(regio)s zu markieren. Prüfen Sie die Feriendatei (%(count)s ungültige Einträge).",
    "%(weken)s week(weken) gemarkeerd als 'Bel uit' (regio %(regio)s, uit %(aantal)s schooljaar/jaren: %(schooljaren)s).":
        "%(weken)s Woche(n) als 'Glocke aus' markiert (Region %(regio)s, aus %(aantal)s Schuljahr(en): %(schooljaren)s).",
    " en %(count)s meer":
        " und %(count)s weitere",
    " Overgeslagen: %(voorb)s%(meer)s.":
        " Übersprungen: %(voorb)s%(meer)s.",
    "Bestaand vakantiebestand kon niet gelezen worden (%(err)s); wordt overschreven.":
        "Bestehende Feriendatei konnte nicht gelesen werden (%(err)s); wird überschrieben.",
    "Verversen mislukt voor alle %(n)d schooljaren. Eerste fout: %(err)s":
        "Aktualisierung für alle %(n)d Schuljahre fehlgeschlagen. Erster Fehler: %(err)s",

    # Flash messages — blueprints/auth.py
    "Onjuiste inloggegevens.":
        "Ungültige Anmeldedaten.",

    # Flash messages — blueprints/geluiden.py
    "Geen bestand ontvangen.":
        "Keine Datei empfangen.",
    "Geen bestand geselecteerd.":
        "Keine Datei ausgewählt.",
    "Alleen bestanden met deze extensies zijn toegestaan: %(exts)s.":
        "Nur Dateien mit diesen Erweiterungen sind erlaubt: %(exts)s.",
    "Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -.":
        "Ungültiger Name. Verwenden Sie 1–35 Zeichen: Buchstaben, Ziffern, Leerzeichen, _ oder -.",
    "Er bestaat al een audiobestand met deze naam. Kies een andere naam.":
        "Eine Audiodatei mit diesem Namen existiert bereits. Wählen Sie einen anderen Namen.",
    "Bestand is groter dan de ingestelde limiet van %(mb)s MB.":
        "Datei ist größer als das eingestellte Limit von %(mb)s MB.",
    "Kon bestand niet opslaan: %(err)s":
        "Datei konnte nicht gespeichert werden: %(err)s",
    "Bestand afgewezen: %(reason)s":
        "Datei abgelehnt: %(reason)s",
    "Upload geslaagd: %(filename)s":
        "Upload erfolgreich: %(filename)s",
    "Bestand niet gevonden.":
        "Datei nicht gefunden.",
    "Test gestart: %(name)s":
        "Test gestartet: %(name)s",
    "Afspelen mislukt: %(err)s":
        "Wiedergabe fehlgeschlagen: %(err)s",
    " en %(n)d meer":
        " und %(n)d weitere",
    "Geluid '%(name)s' wordt nog gebruikt door: %(voorb)s%(meer)s. Verwijder of vervang deze momenten eerst voordat je het bestand verwijdert.":
        "Klang '%(name)s' wird noch verwendet von: %(voorb)s%(meer)s. Entfernen oder ersetzen Sie diese Momente, bevor Sie die Datei löschen.",
    "Verwijderd: %(name)s":
        "Gelöscht: %(name)s",
    "Kon niet verwijderen: %(err)s":
        "Konnte nicht gelöscht werden: %(err)s",

    # Flash messages — blueprints/roosters.py
    "Naam van rooster is verplicht.":
        "Name des Stundenplans ist erforderlich.",
    "Er bestaat al een rooster met deze naam.":
        "Ein Stundenplan mit diesem Namen existiert bereits.",
    "Rooster '%(naam)s' aangemaakt.":
        "Stundenplan '%(naam)s' erstellt.",
    "Onbekend rooster.":
        "Unbekannter Stundenplan.",
    "Standaardweek (%(dagen)s)":
        "Standardwoche (%(dagen)s)",
    "Agenda (%(voorb)s%(meer)s)":
        "Kalender (%(voorb)s%(meer)s)",
    "Rooster '%(rooster)s' is nog in gebruik bij: %(delen)s. Haal deze verwijzingen eerst weg voordat je het rooster verwijdert.":
        "Stundenplan '%(rooster)s' wird noch verwendet von: %(delen)s. Entfernen Sie diese Verweise, bevor Sie den Stundenplan löschen.",
    "Rooster '%(rooster)s' verwijderd.":
        "Stundenplan '%(rooster)s' gelöscht.",
    "Tijd moet in formaat UU:MM (bijv. 8:05 of 08:05).":
        "Zeit muss im Format HH:MM sein (z. B. 8:05 oder 08:05).",
    "Naam is verplicht.":
        "Name ist erforderlich.",
    "Kies een geluidsbestand.":
        "Wählen Sie eine Audiodatei.",
    "Waarschuwing: minuten moeten een getal zijn.":
        "Warnung: Minuten müssen eine Zahl sein.",
    "Waarschuwing: minuten moeten tussen 0 en 60 liggen.":
        "Warnung: Minuten müssen zwischen 0 und 60 liegen.",
    "Kies een geluid voor de waarschuwingsbel, of zet 'minuten eerder' op 0.":
        "Wählen Sie einen Klang für die Vorwarn-Glocke oder setzen Sie 'Minuten früher' auf 0.",
    "Moment toegevoegd aan '%(rooster)s'.":
        "Moment zu '%(rooster)s' hinzugefügt.",
    "Moment '%(naam)s' verwijderd uit '%(rooster)s'.":
        "Moment '%(naam)s' aus '%(rooster)s' entfernt.",
    "Onbekende regel.":
        "Unbekannte Zeile.",
    "'%(keuze)s' bestaat niet als rooster; overslaan voor %(dag)s.":
        "'%(keuze)s' existiert nicht als Stundenplan; überspringe %(dag)s.",
    "Standaardweek opgeslagen.":
        "Standardwoche gespeichert.",

    # Navigation / brand
    "Agenda": "Kalender",
    "Vakanties": "Ferien",
    "Roosters": "Stundenpläne",
    "Standaardweek": "Standardwoche",
    "Geluiden": "Klänge",
    "Logboek": "Logbuch",
    "Voorkeuren": "Einstellungen",
    "Schoolbel": "Schulglocke",
    "IVKO Schoolbel": "IVKO Schulglocke",
    "IVKO · Schoolbel": "IVKO · Schulglocke",
    "Daemon": "Daemon",
    "Daemon actief": "Daemon aktiv",
    "Daemon: geen heartbeat": "Daemon: kein Heartbeat",
    "Daemon: %(age)ss stil": "Daemon: %(age)ss still",
    "(laatste poll: %(t)s UTC)": "(letzter Poll: %(t)s UTC)",
    "Uitloggen": "Abmelden",

    # Modal
    "Weet je het zeker?": "Sind Sie sicher?",
    "Annuleren": "Abbrechen",
    "Bevestigen": "Bestätigen",

    # Login page
    "Inloggen": "Anmelden",
    "Schoolbel — Inloggen": "Schulglocke — Anmelden",
    "Gebruikersnaam": "Benutzername",
    "Wachtwoord": "Passwort",
    "Je gebruikersnaam is meestal <code class=\"sb-code-mono\">%(admin_user)s</code>.":
        "Ihr Benutzername ist normalerweise <code class=\"sb-code-mono\">%(admin_user)s</code>.",

    # Logs page
    "Overzicht van komende belmomenten, recente bel-events (daemon) en recente UI-acties.":
        "Übersicht über bevorstehende Glockenmomente, kürzliche Glocken-Events (Daemon) und kürzliche UI-Aktionen.",
    "Eerstkomende belmomenten": "Bevorstehende Glockenmomente",
    "Datum/Tijd": "Datum/Zeit",
    "Naam": "Name",
    "Bestand": "Datei",
    "Rooster": "Stundenplan",
    "Geen komende momenten gevonden.": "Keine bevorstehenden Momente gefunden.",
    "Recente UI-acties": "Kürzliche UI-Aktionen",
    "Ts": "Ts",
    "Actie": "Aktion",
    "Details": "Details",
    "Geen UI-acties gelogd.": "Keine UI-Aktionen protokolliert.",
    "Recente bel-events (daemon)": "Kürzliche Glocken-Events (Daemon)",
    "Tijd": "Zeit",
    "Status": "Status",
    "Bericht": "Nachricht",
    "Geen bel-logregels (daemon heeft nog niet gelogd).":
        "Keine Glocken-Logeinträge (Daemon hat noch nichts protokolliert).",

    # /now page
    "Schoolbel · Volgende bel": "Schulglocke · Nächste Glocke",
    "Laatste data-fetch faalde": "Letzter Datenabruf fehlgeschlagen",
    "Volgende bel": "Nächste Glocke",
    "Geen bel meer vandaag.": "Keine Glocke mehr heute.",
    "om": "um",

    # Roosters page
    "Waarschuwing": "Warnung",
    "%(warn_min)s min eerder · %(warn_bestand)s":
        "%(warn_min)s Min früher · %(warn_bestand)s",
    "Moment %(tijd)s (%(naam)s) verwijderen?":
        "Moment %(tijd)s (%(naam)s) löschen?",
    "Verwijder dit moment": "Diesen Moment löschen",
    "Geen momenten": "Keine Momente",
    "Naam (verplicht)": "Name (erforderlich)",
    "— Kies geluid —": "— Klang wählen —",
    "Waarschuwing:": "Warnung:",
    "Minuten vóór de bel een waarschuwing afspelen (0 = uit)":
        "Minuten vor der Glocke eine Warnung abspielen (0 = aus)",
    "min. eerder met": "Min. früher mit",
    "— geen waarschuwing —": "— keine Warnung —",
    "Moment toevoegen": "Moment hinzufügen",
    "Rooster &quot;%(naam)s&quot; verwijderen? Dit verwijdert ook alle momenten in dit rooster.":
        "Stundenplan &quot;%(naam)s&quot; löschen? Damit werden auch alle Momente in diesem Stundenplan gelöscht.",
    "Rooster verwijderen": "Stundenplan löschen",
    "Er zijn nog geen roosters. Maak de eerste aan.":
        "Es gibt noch keine Stundenpläne. Erstellen Sie den ersten.",
    "Nieuw rooster": "Neuer Stundenplan",
    "Naam nieuw rooster": "Name des neuen Stundenplans",
    "Start als kopie van het eerste rooster": "Als Kopie des ersten Stundenplans starten",
    "Aanmaken": "Erstellen",
    "Na wijzigingen: herlaad de daemon met\n  <code class=\"sb-code-mono\">sudo systemctl kill -s HUP schoolbell-daemon.service</code>.":
        "Nach Änderungen: Daemon neu laden mit\n  <code class=\"sb-code-mono\">sudo systemctl kill -s HUP schoolbell-daemon.service</code>.",

    # Geluiden page
    "Beschikbare geluiden": "Verfügbare Klänge",
    "Voorbeeluister in browser": "Vorhören im Browser",
    "&quot;%(name)s&quot; nu door de hele school afspelen?":
        "&quot;%(name)s&quot; jetzt in der ganzen Schule abspielen?",
    "Afspelen": "Abspielen",
    "Speel af via omroepinstallatie school": "Über die Schul-Lautsprecheranlage abspielen",
    "Bestand &quot;%(name)s&quot; verwijderen?": "Datei &quot;%(name)s&quot; löschen?",
    "Verwijderen": "Löschen",
    "Geen audiobestanden gevonden.": "Keine Audiodateien gefunden.",
    "Nieuw geluid uploaden": "Neuen Klang hochladen",
    "Unieke naam (max 35 tekens)": "Eindeutiger Name (max. 35 Zeichen)",
    "Toegestane extensies: %(exts)s • max %(max_mb)s MB • Naam: letters/cijfers/spatie/_/-":
        "Erlaubte Erweiterungen: %(exts)s • max. %(max_mb)s MB • Name: Buchstaben/Ziffern/Leerzeichen/_/-",
    "Uploaden": "Hochladen",

    # Agenda page
    'Importeer schoolvakanties uit\n    <code class="sb-code-mono">data/vakanties.json</code>\n    voor regio <strong>%(vakantieregio)s</strong>\n    (instelbaar via <a href="%(settings_url)s">Voorkeuren</a>).\n    De getroffen weken worden automatisch op <em>Bel uit</em> gezet.\n    Bestaande markeringen blijven staan — de import voegt alleen toe.':
        'Schulferien importieren aus\n    <code class="sb-code-mono">data/vakanties.json</code>\n    für Region <strong>%(vakantieregio)s</strong>\n    (einstellbar in <a href="%(settings_url)s">Einstellungen</a>).\n    Die betroffenen Wochen werden automatisch auf <em>Glocke aus</em> gesetzt.\n    Bestehende Markierungen bleiben — der Import fügt nur hinzu.',
    "<strong>Nog geen bestand gevonden.</strong> Klik\n      <em>Verversen van rijksoverheid.nl</em> om het op te halen.":
        "<strong>Noch keine Datei gefunden.</strong> Klicken Sie\n      <em>Von rijksoverheid.nl aktualisieren</em>, um sie zu holen.",
    "Vakanties importeren voor regio %(vakantieregio)s? De getroffen weken worden op &quot;Bel uit&quot; gezet (bestaande markeringen blijven staan).":
        "Ferien für Region %(vakantieregio)s importieren? Die betroffenen Wochen werden auf &quot;Glocke aus&quot; gesetzt (bestehende Markierungen bleiben).",
    "Importeren": "Importieren",
    "Vakanties importeren": "Ferien importieren",
    "Vakantiegegevens ophalen van rijksoverheid.nl en data/vakanties.json overschrijven? Eventuele handmatige aanpassingen aan dat bestand gaan verloren.":
        "Feriendaten von rijksoverheid.nl holen und data/vakanties.json überschreiben? Manuelle Änderungen an dieser Datei gehen verloren.",
    "Verversen": "Aktualisieren",
    "Verversen van rijksoverheid.nl": "Von rijksoverheid.nl aktualisieren",
    'De daemon ververst dit automatisch ongeveer elke maand. Met de\n    knop hierboven kun je tussentijds verversen of de eerste keer\n    ophalen. Status zichtbaar in <a href="%(settings_url)s">Voorkeuren</a>.':
        'Der Daemon aktualisiert dies automatisch etwa monatlich. Mit dem\n    Knopf oben können Sie zwischendurch aktualisieren oder erstmalig\n    holen. Status sichtbar in <a href="%(settings_url)s">Einstellungen</a>.',
    "Wijzig per dag het rooster, of zet een hele week uit. Klik daarna op\n          <strong>Alles opslaan</strong>.":
        "Ändern Sie den Stundenplan pro Tag oder schalten Sie eine ganze Woche aus. Klicken Sie dann auf\n          <strong>Alles speichern</strong>.",
    "Alles opslaan": "Alles speichern",
    "Ma": "Mo",
    "Di": "Di",
    "Wo": "Mi",
    "Do": "Do",
    "Vr": "Fr",
    "Week": "Woche",
    "Bel uit": "Glocke aus",
    "Uit": "Aus",

    # Settings page
    "Pas hier algemene instellingen van de schoolbel aan, zoals volume, maximale bestandsgrootte en polling-interval.":
        "Passen Sie hier allgemeine Einstellungen der Schulglocke an, wie Lautstärke, maximale Dateigröße und Abfrageintervall.",
    "Belvolume:": "Glockenlautstärke:",
    "Standaard afspeelvolume van de bel.": "Standard-Wiedergabelautstärke der Glocke.",
    "Max. bestandsgrootte (MB)": "Max. Dateigröße (MB)",
    "Maximale grootte van geüploade audiobestanden.": "Maximale Größe hochgeladener Audiodateien.",
    "Polling-tijd (seconden)": "Abfrageintervall (Sekunden)",
    "Hoe vaak de daemon de planning controleert.": "Wie oft der Daemon den Plan prüft.",
    "Taal": "Sprache",
    "Automatisch (volgt browser)": "Automatisch (folgt dem Browser)",
    "Taal van de webinterface. Vertalingen worden in een latere update toegevoegd; voor nu blijft alle tekst Nederlands.":
        "Sprache der Weboberfläche. Übersetzungen werden in einem späteren Update hinzugefügt; vorerst bleibt der gesamte Text auf Niederländisch.",
    "Thema": "Thema",
    "Licht": "Hell",
    "Donker": "Dunkel",
    "Automatisch (volgt systeem)": "Automatisch (folgt dem System)",
    "Kies de kleurmodus voor de interface.": "Wählen Sie den Farbmodus für die Oberfläche.",
    "Huisstijl": "Hausfarben",
    "Standaard": "Standard",
    "Aangepast": "Benutzerdefiniert",
    "<strong>Standaard</strong> volgt het thema hierboven (Licht of Donker).\n        <strong>Aangepast</strong> gebruikt de drie kleuren hieronder voor\n        achtergrond, tabelvulling en navigatiebalk — staat los van Licht/Donker.":
        "<strong>Standard</strong> folgt dem Thema oben (Hell oder Dunkel).\n        <strong>Benutzerdefiniert</strong> verwendet die drei Farben unten für\n        Hintergrund, Tabellenfüllung und Navigationsleiste — unabhängig von Hell/Dunkel.",
    "Aangepaste kleuren": "Benutzerdefinierte Farben",
    "Achtergrond": "Hintergrund",
    "Tabelvulling": "Tabellenfüllung",
    "Navigatiebalk": "Navigationsleiste",
    "Klik op een kleurvak om een andere kleur te kiezen. Wijzigingen worden direct toegepast bij Opslaan; geen pagina-refresh nodig.":
        "Klicken Sie auf ein Farbfeld, um eine andere Farbe zu wählen. Änderungen werden beim Speichern sofort übernommen; kein Seiten-Refresh nötig.",
    "Schoolvakanties van rijksoverheid.nl ophalen": "Schulferien von rijksoverheid.nl holen",
    "Aan: de daemon haalt elke ~maand de officiële Nederlandse\n        schoolvakanties op (huidig schooljaar + 4 vooruit) en de\n        knoppen op de Agenda zijn beschikbaar.\n        Uit: data/vakanties.json wordt niet aangepast en de\n        Vakanties-kaart op de Agenda is verborgen — handig voor\n        installs buiten Nederland.":
        "An: Der Daemon holt die offiziellen niederländischen Schulferien\n        etwa monatlich (aktuelles Schuljahr + 4 voraus) und die Knöpfe\n        im Kalender sind verfügbar.\n        Aus: data/vakanties.json wird nicht geändert und die Ferien-Karte\n        im Kalender ist ausgeblendet — praktisch für Installationen\n        außerhalb der Niederlande.",
    "Vakantieregio": "Ferienregion",
    "Noord": "Nord",
    "Midden": "Mitte",
    "Zuid": "Süd",
    "Regio die de knop <em>Vakanties importeren</em> op de Agenda\n        gebruikt om dates uit <code class=\"sb-code-mono\">data/vakanties.json</code>\n        te kiezen.":
        "Region, die der Knopf <em>Ferien importieren</em> im Kalender\n        verwendet, um Termine aus <code class=\"sb-code-mono\">data/vakanties.json</code>\n        zu wählen.",
    "Opslaan": "Speichern",
    "Status vakantie-scrape": "Status Ferien-Abruf",
    "Opgeslagen schooljaren in\n      <code class=\"sb-code-mono\">data/vakanties.json</code>:":
        "Gespeicherte Schuljahre in\n      <code class=\"sb-code-mono\">data/vakanties.json</code>:",
    "— opgehaald %(datum)s": "— geholt %(datum)s",
    "Nog geen vakantiegegevens opgeslagen.": "Noch keine Feriendaten gespeichert.",
    "Laatste geslaagde fetch:\n      <strong>%(tijd)s UTC</strong>.":
        "Letzter erfolgreicher Abruf:\n      <strong>%(tijd)s UTC</strong>.",
    "Nog geen geslaagde fetch geregistreerd.": "Noch kein erfolgreicher Abruf registriert.",
    "Laatste fout: %(error)s": "Letzter Fehler: %(error)s",
    "(mislukt: %(jaren)s)": "(fehlgeschlagen: %(jaren)s)",
    "Laatste poging: %(tijd)s UTC.": "Letzter Versuch: %(tijd)s UTC.",

    # Standaardweek page
    "Kies per weekdag een standaardrooster. De <strong>Agenda</strong> per datum overschrijft deze keuze wanneer ingesteld.":
        "Wählen Sie pro Wochentag einen Standard-Stundenplan. Der <strong>Kalender</strong> pro Datum überschreibt diese Wahl, wenn gesetzt.",
    "Dag": "Tag",
    "Standaardrooster": "Standard-Stundenplan",
    "— geen —": "— keiner —",

    # Weekday labels.
    "Maandag": "Montag",
    "Dinsdag": "Dienstag",
    "Woensdag": "Mittwoch",
    "Donderdag": "Donnerstag",
    "Vrijdag": "Freitag",
    "Zaterdag": "Samstag",
    "Zondag": "Sonntag",

    # --- Multi-user (gebruikers page, header, 403) ---
    # Flash messages — blueprints/gebruikers.py
    "Gebruiker '%(u)s' aangemaakt.": "Benutzer '%(u)s' erstellt.",
    "Fout bij aanmaken: %(err)s": "Fehler beim Erstellen: %(err)s",
    "Wijzigingen voor '%(u)s' opgeslagen.":
        "Änderungen für '%(u)s' gespeichert.",
    "Fout bij wijzigen: %(err)s": "Fehler beim Bearbeiten: %(err)s",
    "Wachtwoord voor '%(u)s' bijgewerkt.":
        "Passwort für '%(u)s' aktualisiert.",
    "Fout: %(err)s": "Fehler: %(err)s",
    "Gebruiker '%(u)s' verwijderd.": "Benutzer '%(u)s' gelöscht.",
    "Fout bij verwijderen: %(err)s": "Fehler beim Löschen: %(err)s",

    # 403 page
    "Geen toegang": "Kein Zugriff",
    "Je account heeft geen toegang tot deze pagina. Vraag een admin om je tabbladen aan te passen.":
        "Dein Konto hat keinen Zugriff auf diese Seite. Bitte einen "
        "Administrator, deine Tabs anzupassen.",
    "Je bent ingelogd als <strong>%(u)s</strong>.":
        "Du bist als <strong>%(u)s</strong> angemeldet.",
    "Terug naar overzicht": "Zurück zur Übersicht",

    # Header indicator + nav-link
    "Gebruikers": "Benutzer",
    "Rol: %(r)s": "Rolle: %(r)s",

    # gebruikers.html — page
    "Beheer wie kan inloggen en welke tabbladen elke gebruiker mag zien. Alleen admins zien deze pagina.":
        "Verwalte, wer sich anmelden kann und welche Tabs jeder Benutzer "
        "sehen darf. Nur Administratoren sehen diese Seite.",
    "Bestaande gebruikers": "Bestehende Benutzer",
    "Rol": "Rolle",
    "Tabbladen": "Tabs",
    "Acties": "Aktionen",
    "jij": "du",
    "alle": "alle",
    "Bewerken": "Bearbeiten",
    "Gebruiker": "Benutzer",
    "Admin": "Administrator",
    "Admins krijgen automatisch toegang tot alle tabbladen; deze vinkjes worden dan genegeerd.":
        "Administratoren erhalten automatisch Zugriff auf alle Tabs; "
        "diese Kontrollkästchen werden dann ignoriert.",
    "Nieuw wachtwoord": "Neues Passwort",
    "Reset": "Zurücksetzen",
    "Gebruiker %(u)s verwijderen?": "Benutzer %(u)s löschen?",
    "Nieuwe gebruiker": "Neuer Benutzer",
    "Kleine letters, cijfers, _ en -. 2 tot 32 tekens.":
        "Kleinbuchstaben, Ziffern, _ und -. 2 bis 32 Zeichen.",
    "Minstens 8 tekens.": "Mindestens 8 Zeichen.",
    "Voor admins worden deze vinkjes genegeerd (admins krijgen altijd alles).":
        "Bei Administratoren werden diese Kontrollkästchen ignoriert "
        "(Administratoren erhalten immer alles).",
}


def main() -> None:
    path = "translations/de/LC_MESSAGES/messages.po"
    with open(path, "rb") as f:
        cat = read_po(f)

    missing: list[str] = []
    for m in cat:
        if not m.id:
            continue
        if m.id in TRANSLATIONS:
            m.string = TRANSLATIONS[m.id]
            # Clear any leftover "fuzzy" marker from pybabel's
            # guess-from-similar logic — see the en counterpart for
            # why this matters.
            if "fuzzy" in m.flags:
                m.flags.discard("fuzzy")
        else:
            missing.append(m.id)

    with open(path, "wb") as f:
        write_po(f, cat)

    print(f"Wrote {sum(1 for m in cat if m.id and m.string)} German translations.")
    if missing:
        print(f"Missing ({len(missing)}):")
        for s in missing[:10]:
            print(f"  - {s!r}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")


if __name__ == "__main__":
    main()
