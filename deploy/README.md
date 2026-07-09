# Deploy-voorbeelden

**Let op:** `install.sh` gebruikt deze bestanden *niet*. Het script
genereert zijn eigen systemd-units, nginx-config en logrotate-regel,
met de paden en gebruikersnaam van jouw systeem erin ingevuld. Dat is
de aanbevolen route:

    sudo ./install.sh

De bestanden in deze map zijn naslagexemplaren voor wie handmatig
installeert of wil zien wat install.sh neerzet. De unit-bestanden
bevatten placeholders die je dan zelf moet invullen:

- `<gebruiker>`           → de accountnaam waaronder de app draait
- `/pad/naar/schoolbell`  → waar de repo staat (install.sh gaat uit
                            van `~<gebruiker>/schoolbell`)

Een unit met placeholders installeren zonder ze te vervangen faalt —
dat is expres, zo kan een half-aangepast bestand nooit stilletjes
onder de verkeerde gebruiker draaien.
