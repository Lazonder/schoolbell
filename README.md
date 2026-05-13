# Schoolbell

[Nederlands](README.nl.md) · English

A school bell that runs on a Raspberry Pi. It plays sounds at the
times you set, has a web page where you manage everything from your
browser, and can show a big countdown to the next bell on a screen
in the staff room.

Built for use inside a school network. The interface speaks Dutch
and English (German and French are planned).

---

## What it does

**Schedules**: you make one or more *schedules* (the Dutch word
"rooster"). A schedule is a list of moments — each moment has a
time, a name like "Start of class" or "Lunch break", and a sound
file. You can assign a different schedule to every weekday, or
override one specific date.

**Sounds**: upload your own mp3 / wav / ogg files through the web
page and pick which one rings at each moment. Each moment can also
have an optional **warning bell** that plays a few minutes before
the main one — handy for "two minutes left" cues.

**Holidays**: the web page can fetch the official Dutch school
holidays from rijksoverheid.nl with one click. The weeks that fall
inside a holiday are then automatically silenced.

**Public countdown**: visit `http://<pi-ip>/now` from any browser
in the school. You'll see a big "Next bell: Lunch break / 3:42"
display that updates by itself. Nothing to log into.

**Health page**: visit `/healthz` to get a quick yes/no on whether
the system is working. Useful if you want to set up automatic
monitoring.

---

## How it's put together

A *daemon* — a program that runs all the time in the background —
plays the bell sounds at the right moments. A *web app* lets you
edit the schedules from your browser. The two talk to each other
through a small internal API.

```
Your browser
   ↓ over the school network
Nginx (a web server) on port 80
   ↓
The Schoolbell web app (Flask)
   ↓ tells the daemon what to ring
Schoolbell daemon → speaker
```

`Nginx` and `Flask` are the two pieces of standard web software
this app runs on. You don't need to know them in detail — the
install script sets them up for you.

---

## Installing it

You need:

* A Raspberry Pi (any model with audio output, tested on Pi 3 and 4)
* A fresh install of **Raspberry Pi OS**
* An admin (`sudo`) account
* Internet access for the install

Then:

```bash
git clone https://github.com/<your-account>/schoolbell.git
cd schoolbell
sudo ./install.sh
```

The script does everything: installs Python, sets up the web server,
creates a random admin password, and starts the bell daemon. It
prints the password **once** in a box at the end — write it down.

When it's done, open a browser to `http://<your-pi's-ip>/`.

For full installation details, troubleshooting, and how to recover
a lost password, see the [Admin guide](docs/admin-guide.md).

---

## Languages

The interface is available in **Dutch** and **English** today.
Pick one in *Settings* (or *Voorkeuren* in Dutch). The default is
Dutch; setting *Automatic* makes the page follow the language of
the visitor's browser.

**German** and **French** translations are planned. Want to add
a language yourself? See [CONTRIBUTING.md](CONTRIBUTING.md) — no
programming needed for translation work, just a `.po` file editor.

---

## What if something breaks

The daemon writes a *heartbeat file* every couple of seconds so
the web page knows it's alive. If you see a red dot in the top
bar of the web page instead of a green one, the daemon stopped.
Check the logs with:

```bash
journalctl -u schoolbell-daemon.service
```

For more on debugging, see the [Admin guide](docs/admin-guide.md).

---

## License

Schoolbell is released under the **MIT license** — see the
[LICENSE](LICENSE) file. In short: you can use, modify and share
this code freely, including in commercial settings, as long as you
keep the copyright notice in copies you distribute. There is no
warranty.

---

## Built with

[Flask](https://flask.palletsprojects.com/) ·
[Flask-Babel](https://python-babel.github.io/flask-babel/) ·
[pygame](https://www.pygame.org/) ·
[gunicorn](https://gunicorn.org/) ·
[nginx](https://nginx.org/) ·
[Raspberry Pi OS](https://www.raspberrypi.com/software/)

---

## Acknowledgements

The earliest idea for Schoolbell was inspired by
[AlarmPi](https://github.com/MckennaCisler/AlarmPi) by Mckenna
Cisler — a Raspberry Pi alarm clock with a web configuration
interface. Schoolbell shares no code with AlarmPi and has grown
into a different project for a different purpose (a school bell on
a school network rather than a personal alarm clock), but the
original spark of "Pi + daemon + web interface + sound files" came
from there.
