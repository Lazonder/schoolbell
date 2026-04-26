#!/usr/bin/env python3
"""
Quick speaker / pygame sanity check.

Plays an audio file via pygame.mixer and waits for it to finish.
Use this during install to verify that audio output works on the
Pi (correct ALSA device, libasound present, speaker plugged in)
*before* configuring the daemon.

Usage:
    python3 tools/belltest.py path/to/file.mp3
    python3 tools/belltest.py static/geluiden/1\\ ivko\\ schoolbel.mp3

Not part of the runtime: this script lives outside the systemd
units and isn't imported by the web or daemon code. Safe to run
while the services are up — pygame opens its own audio handle.
"""

import argparse
import sys
import time
from pathlib import Path

import pygame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "audio_file",
        type=Path,
        help="Path to an audio file (.mp3, .wav, .ogg).",
    )
    args = parser.parse_args(argv)

    if not args.audio_file.is_file():
        print(f"ERROR: file not found: {args.audio_file}", file=sys.stderr)
        return 1

    pygame.mixer.init()
    try:
        pygame.mixer.music.load(str(args.audio_file))
        pygame.mixer.music.play()
        print(f"Playing {args.audio_file} — Ctrl+C to stop.")
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except KeyboardInterrupt:
        pygame.mixer.music.stop()
        print("\nStopped.")
    finally:
        pygame.mixer.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
