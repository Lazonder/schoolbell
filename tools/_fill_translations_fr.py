"""Fill translations/fr/LC_MESSAGES/messages.po with French translations.

Same shape as _fill_translations_en.py — see that file for the
workflow.

Glossary used (matches CONTRIBUTING.md):
    Rooster        -> Horaire
    Standaardweek  -> Semaine type
    Agenda         -> Calendrier
    Bel uit        -> Cloche désactivée
    Geluid         -> Son
    Voorkeuren     -> Paramètres
    Huisstijl      -> Charte graphique
    Waarschuwing   -> Avertissement
"""

from babel.messages.pofile import read_po, write_po


TRANSLATIONS: dict[str, str] = {
    # Flash messages — webinterface.py
    "Upload te groot (controleer de ingestelde limiet bij Voorkeuren).":
        "Téléversement trop volumineux (vérifiez la limite configurée dans Paramètres).",

    # Flash messages — blueprints/agenda.py
    "Ongeldig rooster voor %(datum)s: '%(waarde)s' bestaat niet. Overgeslagen.":
        "Horaire invalide pour %(datum)s : '%(waarde)s' n'existe pas. Ignoré.",
    "Agenda opgeslagen.":
        "Calendrier enregistré.",
    "Vakantie-scraping is uitgeschakeld in Voorkeuren.":
        "La récupération des vacances est désactivée dans Paramètres.",
    "Importeren mislukt: %(err)s":
        "Échec de l'import : %(err)s",
    "Geen vakantiebestand gevonden (%(path)s). Klik 'Verversen van rijksoverheid.nl' om het op te halen.":
        "Aucun fichier de vacances trouvé (%(path)s). Cliquez sur « Actualiser depuis rijksoverheid.nl » pour le récupérer.",
    "Vakantiebestand bevat geen 'schooljaren'. Klik 'Verversen van rijksoverheid.nl' om opnieuw op te halen.":
        "Le fichier de vacances ne contient pas « schooljaren ». Cliquez sur « Actualiser depuis rijksoverheid.nl » pour le récupérer à nouveau.",
    "Geen schooljaren in het bestand bevatten regio '%(regio)s'. Aanwezige schooljaren: %(schooljaren)s.":
        "Aucune année scolaire du fichier ne contient la région « %(regio)s ». Années scolaires présentes : %(schooljaren)s.",
    "Geen weken om te markeren voor regio %(regio)s. Controleer het vakantiebestand (%(count)s ongeldige entries).":
        "Aucune semaine à marquer pour la région %(regio)s. Vérifiez le fichier de vacances (%(count)s entrées invalides).",
    "%(weken)s week(weken) gemarkeerd als 'Bel uit' (regio %(regio)s, uit %(aantal)s schooljaar/jaren: %(schooljaren)s).":
        "%(weken)s semaine(s) marquée(s) comme « Cloche désactivée » (région %(regio)s, depuis %(aantal)s année(s) scolaire(s) : %(schooljaren)s).",
    " en %(count)s meer":
        " et %(count)s de plus",
    " Overgeslagen: %(voorb)s%(meer)s.":
        " Ignorées : %(voorb)s%(meer)s.",
    "Bestaand vakantiebestand kon niet gelezen worden (%(err)s); wordt overschreven.":
        "Le fichier de vacances existant n'a pas pu être lu (%(err)s) ; il sera écrasé.",
    "Verversen mislukt voor alle %(n)d schooljaren. Eerste fout: %(err)s":
        "Actualisation échouée pour les %(n)d années scolaires. Première erreur : %(err)s",

    # Flash messages — blueprints/auth.py
    "Onjuiste inloggegevens.":
        "Identifiants invalides.",

    # Flash messages — blueprints/geluiden.py
    "Geen bestand ontvangen.":
        "Aucun fichier reçu.",
    "Geen bestand geselecteerd.":
        "Aucun fichier sélectionné.",
    "Alleen bestanden met deze extensies zijn toegestaan: %(exts)s.":
        "Seuls les fichiers avec ces extensions sont autorisés : %(exts)s.",
    "Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -.":
        "Nom invalide. Utilisez 1 à 35 caractères : lettres, chiffres, espace, _ ou -.",
    "Er bestaat al een audiobestand met deze naam. Kies een andere naam.":
        "Un fichier audio avec ce nom existe déjà. Choisissez un autre nom.",
    "Bestand is groter dan de ingestelde limiet van %(mb)s MB.":
        "Le fichier dépasse la limite configurée de %(mb)s Mo.",
    "Kon bestand niet opslaan: %(err)s":
        "Impossible d'enregistrer le fichier : %(err)s",
    "Bestand afgewezen: %(reason)s":
        "Fichier refusé : %(reason)s",
    "Upload geslaagd: %(filename)s":
        "Téléversement réussi : %(filename)s",
    "Bestand niet gevonden.":
        "Fichier introuvable.",
    "Test gestart: %(name)s":
        "Test démarré : %(name)s",
    "Afspelen mislukt: %(err)s":
        "Lecture échouée : %(err)s",
    " en %(n)d meer":
        " et %(n)d de plus",
    "Geluid '%(name)s' wordt nog gebruikt door: %(voorb)s%(meer)s. Verwijder of vervang deze momenten eerst voordat je het bestand verwijdert.":
        "Le son « %(name)s » est encore utilisé par : %(voorb)s%(meer)s. Supprimez ou remplacez ces moments avant de supprimer le fichier.",
    "Verwijderd: %(name)s":
        "Supprimé : %(name)s",
    "Kon niet verwijderen: %(err)s":
        "Suppression impossible : %(err)s",

    # Flash messages — blueprints/roosters.py
    "Naam van rooster is verplicht.":
        "Le nom de l'horaire est obligatoire.",
    "Er bestaat al een rooster met deze naam.":
        "Un horaire avec ce nom existe déjà.",
    "Rooster '%(naam)s' aangemaakt.":
        "Horaire « %(naam)s » créé.",
    "Onbekend rooster.":
        "Horaire inconnu.",
    "Standaardweek (%(dagen)s)":
        "Semaine type (%(dagen)s)",
    "Agenda (%(voorb)s%(meer)s)":
        "Calendrier (%(voorb)s%(meer)s)",
    "Rooster '%(rooster)s' is nog in gebruik bij: %(delen)s. Haal deze verwijzingen eerst weg voordat je het rooster verwijdert.":
        "L'horaire « %(rooster)s » est encore utilisé par : %(delen)s. Supprimez ces références avant de supprimer l'horaire.",
    "Rooster '%(rooster)s' verwijderd.":
        "Horaire « %(rooster)s » supprimé.",
    "Tijd moet in formaat UU:MM (bijv. 8:05 of 08:05).":
        "L'heure doit être au format HH:MM (par ex. 8:05 ou 08:05).",
    "Naam is verplicht.":
        "Le nom est obligatoire.",
    "Kies een geluidsbestand.":
        "Choisissez un fichier audio.",
    "Waarschuwing: minuten moeten een getal zijn.":
        "Avertissement : les minutes doivent être un nombre.",
    "Waarschuwing: minuten moeten tussen 0 en 60 liggen.":
        "Avertissement : les minutes doivent être entre 0 et 60.",
    "Kies een geluid voor de waarschuwingsbel, of zet 'minuten eerder' op 0.":
        "Choisissez un son pour la cloche d'avertissement, ou réglez « minutes avant » sur 0.",
    "Moment toegevoegd aan '%(rooster)s'.":
        "Moment ajouté à « %(rooster)s ».",
    "Moment '%(naam)s' verwijderd uit '%(rooster)s'.":
        "Moment « %(naam)s » supprimé de « %(rooster)s ».",
    "Onbekende regel.":
        "Ligne inconnue.",
    "'%(keuze)s' bestaat niet als rooster; overslaan voor %(dag)s.":
        "« %(keuze)s » n'existe pas comme horaire ; ignoré pour %(dag)s.",
    "Standaardweek opgeslagen.":
        "Semaine type enregistrée.",

    # Navigation / brand
    "Agenda": "Calendrier",
    "Vakanties": "Vacances",
    "Roosters": "Horaires",
    "Standaardweek": "Semaine type",
    "Geluiden": "Sons",
    "Logboek": "Journal",
    "Voorkeuren": "Paramètres",
    "Schoolbel": "Cloche d'école",
    "IVKO Schoolbel": "Cloche d'école IVKO",
    "IVKO · Schoolbel": "IVKO · Cloche d'école",
    "Daemon": "Daemon",
    "Daemon actief": "Daemon actif",
    "Daemon: geen heartbeat": "Daemon : pas de heartbeat",
    "Daemon: %(age)ss stil": "Daemon : %(age)s s sans signe",
    "(laatste poll: %(t)s UTC)": "(dernier sondage : %(t)s UTC)",
    "Uitloggen": "Déconnexion",

    # Modal
    "Weet je het zeker?": "Êtes-vous sûr ?",
    "Annuleren": "Annuler",
    "Bevestigen": "Confirmer",

    # Login page
    "Inloggen": "Connexion",
    "Schoolbel — Inloggen": "Cloche d'école — Connexion",
    "Gebruikersnaam": "Nom d'utilisateur",
    "Wachtwoord": "Mot de passe",
    "Je gebruikersnaam is meestal <code class=\"sb-code-mono\">%(admin_user)s</code>.":
        "Votre nom d'utilisateur est généralement <code class=\"sb-code-mono\">%(admin_user)s</code>.",

    # Logs page
    "Overzicht van komende belmomenten, recente bel-events (daemon) en recente UI-acties.":
        "Vue d'ensemble des prochains moments de cloche, des événements récents (daemon) et des actions UI récentes.",
    "Eerstkomende belmomenten": "Prochains moments de cloche",
    "Datum/Tijd": "Date/Heure",
    "Naam": "Nom",
    "Bestand": "Fichier",
    "Rooster": "Horaire",
    "Geen komende momenten gevonden.": "Aucun moment à venir.",
    "Recente UI-acties": "Actions UI récentes",
    "Ts": "Ts",
    "Actie": "Action",
    "Details": "Détails",
    "Geen UI-acties gelogd.": "Aucune action UI enregistrée.",
    "Recente bel-events (daemon)": "Événements de cloche récents (daemon)",
    "Tijd": "Heure",
    "Status": "Statut",
    "Bericht": "Message",
    "Geen bel-logregels (daemon heeft nog niet gelogd).":
        "Aucun journal de cloche (le daemon n'a encore rien enregistré).",

    # /now page
    "Schoolbel · Volgende bel": "Cloche d'école · Prochaine cloche",
    "Laatste data-fetch faalde": "Dernière récupération de données échouée",
    "Volgende bel": "Prochaine cloche",
    "Geen bel meer vandaag.": "Plus de cloche aujourd'hui.",
    "om": "à",

    # Roosters page
    "Waarschuwing": "Avertissement",
    "%(warn_min)s min eerder · %(warn_bestand)s":
        "%(warn_min)s min avant · %(warn_bestand)s",
    "Moment %(tijd)s (%(naam)s) verwijderen?":
        "Supprimer le moment %(tijd)s (%(naam)s) ?",
    "Verwijder dit moment": "Supprimer ce moment",
    "Geen momenten": "Aucun moment",
    "Naam (verplicht)": "Nom (obligatoire)",
    "— Kies geluid —": "— Choisir un son —",
    "Waarschuwing:": "Avertissement :",
    "Minuten vóór de bel een waarschuwing afspelen (0 = uit)":
        "Minutes avant la cloche pour jouer un avertissement (0 = désactivé)",
    "min. eerder met": "min. avant avec",
    "— geen waarschuwing —": "— pas d'avertissement —",
    "Moment toevoegen": "Ajouter un moment",
    "Rooster &quot;%(naam)s&quot; verwijderen? Dit verwijdert ook alle momenten in dit rooster.":
        "Supprimer l'horaire &quot;%(naam)s&quot; ? Cela supprimera aussi tous les moments de cet horaire.",
    "Rooster verwijderen": "Supprimer l'horaire",
    "Er zijn nog geen roosters. Maak de eerste aan.":
        "Aucun horaire pour l'instant. Créez le premier.",
    "Nieuw rooster": "Nouvel horaire",
    "Naam nieuw rooster": "Nom du nouvel horaire",
    "Start als kopie van het eerste rooster": "Démarrer comme copie du premier horaire",
    "Aanmaken": "Créer",
    "Na wijzigingen: herlaad de daemon met\n  <code class=\"sb-code-mono\">sudo systemctl kill -s HUP schoolbell-daemon.service</code>.":
        "Après modifications : rechargez le daemon avec\n  <code class=\"sb-code-mono\">sudo systemctl kill -s HUP schoolbell-daemon.service</code>.",

    # Geluiden page
    "Beschikbare geluiden": "Sons disponibles",
    "Voorbeeluister in browser": "Aperçu dans le navigateur",
    "&quot;%(name)s&quot; nu door de hele school afspelen?":
        "Jouer &quot;%(name)s&quot; maintenant dans toute l'école ?",
    "Afspelen": "Lire",
    "Speel af via omroepinstallatie school": "Jouer via la sonorisation de l'école",
    "Bestand &quot;%(name)s&quot; verwijderen?": "Supprimer le fichier &quot;%(name)s&quot; ?",
    "Verwijderen": "Supprimer",
    "Geen audiobestanden gevonden.": "Aucun fichier audio trouvé.",
    "Nieuw geluid uploaden": "Téléverser un nouveau son",
    "Unieke naam (max 35 tekens)": "Nom unique (max. 35 caractères)",
    "Toegestane extensies: %(exts)s • max %(max_mb)s MB • Naam: letters/cijfers/spatie/_/-":
        "Extensions autorisées : %(exts)s • max %(max_mb)s Mo • Nom : lettres/chiffres/espace/_/-",
    "Uploaden": "Téléverser",

    # Agenda page
    'Importeer schoolvakanties uit\n    <code class="sb-code-mono">data/vakanties.json</code>\n    voor regio <strong>%(vakantieregio)s</strong>\n    (instelbaar via <a href="%(settings_url)s">Voorkeuren</a>).\n    De getroffen weken worden automatisch op <em>Bel uit</em> gezet.\n    Bestaande markeringen blijven staan — de import voegt alleen toe.':
        'Importer les vacances scolaires depuis\n    <code class="sb-code-mono">data/vakanties.json</code>\n    pour la région <strong>%(vakantieregio)s</strong>\n    (configurable via <a href="%(settings_url)s">Paramètres</a>).\n    Les semaines concernées sont automatiquement mises sur <em>Cloche désactivée</em>.\n    Les marquages existants restent — l\'import ne fait qu\'ajouter.',
    "<strong>Nog geen bestand gevonden.</strong> Klik\n      <em>Verversen van rijksoverheid.nl</em> om het op te halen.":
        "<strong>Aucun fichier trouvé pour l'instant.</strong> Cliquez sur\n      <em>Actualiser depuis rijksoverheid.nl</em> pour le récupérer.",
    "Vakanties importeren voor regio %(vakantieregio)s? De getroffen weken worden op &quot;Bel uit&quot; gezet (bestaande markeringen blijven staan).":
        "Importer les vacances pour la région %(vakantieregio)s ? Les semaines concernées seront mises sur &quot;Cloche désactivée&quot; (les marquages existants restent).",
    "Importeren": "Importer",
    "Vakanties importeren": "Importer les vacances",
    "Vakantiegegevens ophalen van rijksoverheid.nl en data/vakanties.json overschrijven? Eventuele handmatige aanpassingen aan dat bestand gaan verloren.":
        "Récupérer les données de vacances depuis rijksoverheid.nl et écraser data/vakanties.json ? Toute modification manuelle de ce fichier sera perdue.",
    "Verversen": "Actualiser",
    "Verversen van rijksoverheid.nl": "Actualiser depuis rijksoverheid.nl",
    'De daemon ververst dit automatisch ongeveer elke maand. Met de\n    knop hierboven kun je tussentijds verversen of de eerste keer\n    ophalen. Status zichtbaar in <a href="%(settings_url)s">Voorkeuren</a>.':
        'Le daemon actualise automatiquement environ une fois par mois. Avec le\n    bouton ci-dessus, vous pouvez actualiser entre-temps ou récupérer\n    pour la première fois. Statut visible dans <a href="%(settings_url)s">Paramètres</a>.',
    "Wijzig per dag het rooster, of zet een hele week uit. Klik daarna op\n          <strong>Alles opslaan</strong>.":
        "Modifiez l'horaire par jour, ou désactivez une semaine entière. Cliquez ensuite sur\n          <strong>Tout enregistrer</strong>.",
    "Alles opslaan": "Tout enregistrer",
    "Ma": "Lun",
    "Di": "Mar",
    "Wo": "Mer",
    "Do": "Jeu",
    "Vr": "Ven",
    "Week": "Semaine",
    "Bel uit": "Cloche désactivée",
    "Uit": "Désact.",

    # Settings page
    "Pas hier algemene instellingen van de schoolbel aan, zoals volume, maximale bestandsgrootte en polling-interval.":
        "Ajustez ici les paramètres généraux de la cloche d'école : volume, taille de fichier maximale et intervalle de sondage.",
    "Belvolume:": "Volume de la cloche :",
    "Standaard afspeelvolume van de bel.": "Volume de lecture par défaut de la cloche.",
    "Max. bestandsgrootte (MB)": "Taille de fichier max. (Mo)",
    "Maximale grootte van geüploade audiobestanden.": "Taille maximale des fichiers audio téléversés.",
    "Polling-tijd (seconden)": "Intervalle de sondage (secondes)",
    "Hoe vaak de daemon de planning controleert.": "Fréquence à laquelle le daemon vérifie le planning.",
    "Taal": "Langue",
    "Automatisch (volgt browser)": "Automatique (suit le navigateur)",
    "Taal van de webinterface. Vertalingen worden in een latere update toegevoegd; voor nu blijft alle tekst Nederlands.":
        "Langue de l'interface web. Les traductions seront ajoutées dans une mise à jour ultérieure ; pour l'instant tout le texte reste en néerlandais.",
    "Thema": "Thème",
    "Licht": "Clair",
    "Donker": "Sombre",
    "Automatisch (volgt systeem)": "Automatique (suit le système)",
    "Kies de kleurmodus voor de interface.": "Choisissez le mode de couleur de l'interface.",
    "Huisstijl": "Charte graphique",
    "Standaard": "Par défaut",
    "Aangepast": "Personnalisé",
    "<strong>Standaard</strong> volgt het thema hierboven (Licht of Donker).\n        <strong>Aangepast</strong> gebruikt de drie kleuren hieronder voor\n        achtergrond, tabelvulling en navigatiebalk — staat los van Licht/Donker.":
        "<strong>Par défaut</strong> suit le thème ci-dessus (Clair ou Sombre).\n        <strong>Personnalisé</strong> utilise les trois couleurs ci-dessous pour\n        l'arrière-plan, le remplissage des tableaux et la barre de navigation — indépendant de Clair/Sombre.",
    "Aangepaste kleuren": "Couleurs personnalisées",
    "Achtergrond": "Arrière-plan",
    "Tabelvulling": "Remplissage des tableaux",
    "Navigatiebalk": "Barre de navigation",
    "Klik op een kleurvak om een andere kleur te kiezen. Wijzigingen worden direct toegepast bij Opslaan; geen pagina-refresh nodig.":
        "Cliquez sur une case de couleur pour en choisir une autre. Les modifications sont appliquées immédiatement à l'enregistrement ; aucune actualisation de page nécessaire.",
    "Schoolvakanties van rijksoverheid.nl ophalen": "Récupérer les vacances scolaires depuis rijksoverheid.nl",
    "Aan: de daemon haalt elke ~maand de officiële Nederlandse\n        schoolvakanties op (huidig schooljaar + 4 vooruit) en de\n        knoppen op de Agenda zijn beschikbaar.\n        Uit: data/vakanties.json wordt niet aangepast en de\n        Vakanties-kaart op de Agenda is verborgen — handig voor\n        installs buiten Nederland.":
        "Activé : le daemon récupère les vacances scolaires officielles néerlandaises\n        environ une fois par mois (année scolaire actuelle + 4 à venir) et les boutons\n        du Calendrier sont disponibles.\n        Désactivé : data/vakanties.json n'est pas modifié et la carte Vacances\n        du Calendrier est masquée — pratique pour les installations\n        hors des Pays-Bas.",
    "Vakantieregio": "Région de vacances",
    "Noord": "Nord",
    "Midden": "Centre",
    "Zuid": "Sud",
    "Regio die de knop <em>Vakanties importeren</em> op de Agenda\n        gebruikt om dates uit <code class=\"sb-code-mono\">data/vakanties.json</code>\n        te kiezen.":
        "Région que le bouton <em>Importer les vacances</em> du Calendrier\n        utilise pour choisir les dates depuis <code class=\"sb-code-mono\">data/vakanties.json</code>.",
    "Opslaan": "Enregistrer",
    "Status vakantie-scrape": "Statut de la récupération des vacances",
    "Opgeslagen schooljaren in\n      <code class=\"sb-code-mono\">data/vakanties.json</code>:":
        "Années scolaires enregistrées dans\n      <code class=\"sb-code-mono\">data/vakanties.json</code> :",
    "— opgehaald %(datum)s": "— récupéré le %(datum)s",
    "Nog geen vakantiegegevens opgeslagen.": "Aucune donnée de vacances enregistrée.",
    "Laatste geslaagde fetch:\n      <strong>%(tijd)s UTC</strong>.":
        "Dernière récupération réussie :\n      <strong>%(tijd)s UTC</strong>.",
    "Nog geen geslaagde fetch geregistreerd.": "Aucune récupération réussie enregistrée.",
    "Laatste fout: %(error)s": "Dernière erreur : %(error)s",
    "(mislukt: %(jaren)s)": "(échoué : %(jaren)s)",
    "Laatste poging: %(tijd)s UTC.": "Dernière tentative : %(tijd)s UTC.",

    # Standaardweek page
    "Kies per weekdag een standaardrooster. De <strong>Agenda</strong> per datum overschrijft deze keuze wanneer ingesteld.":
        "Choisissez un horaire par défaut pour chaque jour de la semaine. Le <strong>Calendrier</strong> par date remplace ce choix quand il est défini.",
    "Dag": "Jour",
    "Standaardrooster": "Horaire par défaut",
    "— geen —": "— aucun —",

    # Weekday labels.
    "Maandag": "Lundi",
    "Dinsdag": "Mardi",
    "Woensdag": "Mercredi",
    "Donderdag": "Jeudi",
    "Vrijdag": "Vendredi",
    "Zaterdag": "Samedi",
    "Zondag": "Dimanche",

    # --- Multi-user (gebruikers page, header, 403) ---
    # Flash messages — blueprints/gebruikers.py
    "Gebruiker '%(u)s' aangemaakt.": "Utilisateur « %(u)s » créé.",
    "Fout bij aanmaken: %(err)s":
        "Erreur lors de la création : %(err)s",
    "Wijzigingen voor '%(u)s' opgeslagen.":
        "Modifications de « %(u)s » enregistrées.",
    "Fout bij wijzigen: %(err)s":
        "Erreur lors de la modification : %(err)s",
    "Wachtwoord voor '%(u)s' bijgewerkt.":
        "Mot de passe de « %(u)s » mis à jour.",
    "Fout: %(err)s": "Erreur : %(err)s",
    "Gebruiker '%(u)s' verwijderd.":
        "Utilisateur « %(u)s » supprimé.",
    "Fout bij verwijderen: %(err)s":
        "Erreur lors de la suppression : %(err)s",

    # 403 page
    "Geen toegang": "Accès refusé",
    "Je account heeft geen toegang tot deze pagina. Vraag een admin om je tabbladen aan te passen.":
        "Votre compte n'a pas accès à cette page. Demandez à un "
        "administrateur d'ajuster vos onglets.",
    "Je bent ingelogd als <strong>%(u)s</strong>.":
        "Vous êtes connecté en tant que <strong>%(u)s</strong>.",
    "Terug naar overzicht": "Retour à l'accueil",

    # Header indicator + nav-link
    "Gebruikers": "Utilisateurs",
    "Rol: %(r)s": "Rôle : %(r)s",

    # gebruikers.html — page
    "Beheer wie kan inloggen en welke tabbladen elke gebruiker mag zien. Alleen admins zien deze pagina.":
        "Gérez qui peut se connecter et quels onglets chaque utilisateur "
        "peut voir. Seuls les administrateurs voient cette page.",
    "Bestaande gebruikers": "Utilisateurs existants",
    "Rol": "Rôle",
    "Tabbladen": "Onglets",
    "Acties": "Actions",
    "jij": "vous",
    "alle": "tous",
    "Bewerken": "Modifier",
    "Gebruiker": "Utilisateur",
    "Admin": "Administrateur",
    "Admins krijgen automatisch toegang tot alle tabbladen; deze vinkjes worden dan genegeerd.":
        "Les administrateurs ont automatiquement accès à tous les "
        "onglets ; ces cases à cocher sont alors ignorées.",
    "Nieuw wachtwoord": "Nouveau mot de passe",
    "Reset": "Réinitialiser",
    "Gebruiker %(u)s verwijderen?": "Supprimer l'utilisateur %(u)s ?",
    "Nieuwe gebruiker": "Nouvel utilisateur",
    "Kleine letters, cijfers, _ en -. 2 tot 32 tekens.":
        "Minuscules, chiffres, _ et -. De 2 à 32 caractères.",
    "Minstens 8 tekens.": "Au moins 8 caractères.",
    "Voor admins worden deze vinkjes genegeerd (admins krijgen altijd alles).":
        "Pour les administrateurs, ces cases sont ignorées (les "
        "administrateurs obtiennent toujours tout).",
}


def main() -> None:
    path = "translations/fr/LC_MESSAGES/messages.po"
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

    print(f"Wrote {sum(1 for m in cat if m.id and m.string)} French translations.")
    if missing:
        print(f"Missing ({len(missing)}):")
        for s in missing[:10]:
            print(f"  - {s!r}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")


if __name__ == "__main__":
    main()
