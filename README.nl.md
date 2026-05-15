# Schoolbel

Nederlands · [English](README.md)

Een schoolbel die draait op een Raspberry Pi. Hij speelt geluiden af
op de tijden die je instelt, heeft een webpagina waar je alles vanuit
je browser regelt, en kan een grote aftelling tot de volgende bel
tonen op een scherm in de docentenkamer.

Gebouwd voor gebruik binnen een schoolnetwerk. De interface is
beschikbaar in het Nederlands, Engels, Duits en Frans.

---

## Wat 't doet

**Roosters**: je maakt een of meer *roosters*. Een rooster is een
lijst van momenten — elk moment heeft een tijd, een naam zoals
"Begin van de les" of "Lunchpauze", en een geluidsbestand. Je kunt
een ander rooster aan elke weekdag koppelen, of voor één specifieke
datum een uitzondering maken.

**Geluiden**: je kunt eigen mp3 / wav / ogg-bestanden uploaden via
de webpagina en kiezen welk geluid bij elk moment hoort. Elk moment
kan ook een optionele **waarschuwingsbel** hebben die een paar
minuten eerder afgaat — handig voor "nog twee minuten"-signalen.

**Vakanties**: de webpagina kan met één klik de officiële Nederlandse
schoolvakanties ophalen van rijksoverheid.nl. De weken die binnen een
vakantie vallen worden dan automatisch op stil gezet.

**Publieke aftelling**: bezoek `https://<pi-ip>/now` vanuit elke
browser op school. Je ziet dan een grote weergave "Volgende bel:
Lunchpauze / 3:42" die zichzelf bijwerkt. Geen inloggen nodig.

**Gezondheidscheck**: bezoek `/healthz` voor een korte ja/nee of
het systeem werkt. Handig als je automatische monitoring wilt
opzetten.

---

## Hoe 't in elkaar zit

Een *daemon* — een programma dat constant op de achtergrond draait —
speelt de belgeluiden af op de juiste momenten. Een *webapp* laat
je de roosters bewerken vanuit je browser. De twee praten met elkaar
via een kleine interne API.

```
Jouw browser
   ↓ via het schoolnetwerk (HTTPS)
Nginx (een webserver) op poort 443
   ↓ (poort 80 stuurt automatisch door naar 443)
De Schoolbel-webapp (Flask)
   ↓ vertelt de daemon wat te bellen
Schoolbel-daemon → speaker
```

`Nginx` en `Flask` zijn de twee stukken standaard-websoftware waar
deze app op draait. Je hoeft ze niet in detail te kennen — het
installatiescript zet ze voor je op.

---

## Installeren

Wat je nodig hebt:

* Een Raspberry Pi (elk model met audio-uitgang, getest op Pi 3 en 4)
* Een verse installatie van **Raspberry Pi OS**
* Een admin-account (`sudo`)
* Internettoegang tijdens de installatie

Dan:

```bash
git clone https://github.com/<jouw-account>/schoolbell.git
cd schoolbell
sudo ./install.sh
```

Het script doet alles: installeert Python, zet de webserver op,
maakt een willekeurig admin-wachtwoord aan, en start de bel-daemon.
Het laat het wachtwoord **één keer** in een kader zien aan het
einde — schrijf het op.

Als het klaar is, open je een browser op
`https://<ip-van-je-pi>/` (of `https://schoolbell.local/`). De
eerste keer per apparaat krijg je een "verbinding niet privé"-
waarschuwing — dat hoort bij een self-signed certificaat binnen
het schoolnetwerk. Klik op *Geavanceerd → toch doorgaan*. Zie de
admin-guide voor meer uitleg.

Voor volledige installatie-details, foutopsporing, en het
herstellen van een verloren wachtwoord, zie de
[Beheerdershandleiding](docs/admin-guide.md) (Engels).

---

## Talen

De interface is beschikbaar in het **Nederlands**, **Engels**,
**Duits** en **Frans**. Kies er een in *Voorkeuren*. De standaard
is Nederlands; zet 'm op *Automatisch* om de pagina de taal van de
bezoekers browser te laten volgen.

Wil je zelf een andere taal toevoegen? Zie
[CONTRIBUTING.md](CONTRIBUTING.md) (Engels) — voor vertaalwerk hoef
je niet te kunnen programmeren, je hebt alleen een `.po`-editor
nodig.

---

## Roadmap

Geplande uitbreidingen: wachtwoord-reset via e-mail. Zie
[docs/roadmap.nl.md](docs/roadmap.nl.md) voor de volledige lijst
met scope en motivatie per onderdeel. Multi-user ondersteuning
met per-tab-rechten is er al — zie
[docs/admin-guide.md#user-management](docs/admin-guide.md#user-management)
(Engels) voor uitleg.

---

## Als er iets stuk gaat

De daemon schrijft elke paar seconden een *heartbeat-bestand* zodat
de webpagina weet dat 'ie nog leeft. Zie je een rood bolletje in
de bovenbalk van de webpagina in plaats van een groen, dan is de
daemon gestopt. Bekijk de logs met:

```bash
journalctl -u schoolbell-daemon.service
```

Meer over foutopsporing in de [Beheerdershandleiding](docs/admin-guide.md).

---

## Licentie

Schoolbel valt onder de **MIT-licentie** — zie het bestand
[LICENSE](LICENSE). Kort gezegd: je mag deze code vrij gebruiken,
aanpassen en delen, ook commercieel, zolang je de
auteursrechtvermelding meelevert in kopieën die je distribueert.
Er is geen garantie.

---

## Gebouwd met

[Flask](https://flask.palletsprojects.com/) ·
[Flask-Babel](https://python-babel.github.io/flask-babel/) ·
[pygame](https://www.pygame.org/) ·
[gunicorn](https://gunicorn.org/) ·
[nginx](https://nginx.org/) ·
[Raspberry Pi OS](https://www.raspberrypi.com/software/)

---

## Met dank aan

Het allereerste idee voor Schoolbel is geïnspireerd door
[AlarmPi](https://github.com/MckennaCisler/AlarmPi) van Mckenna
Cisler — een Raspberry Pi-wekker met een web-configuratie-
interface. Schoolbel deelt geen code met AlarmPi en is uitgegroeid
tot een ander project met een ander doel (een schoolbel op een
schoolnetwerk in plaats van een persoonlijke wekker), maar de
eerste vonk van "Pi + daemon + webinterface + geluidsbestanden"
komt daarvandaan.
