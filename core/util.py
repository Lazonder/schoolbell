"""Small generic helpers that don't fit anywhere else."""

import os


def _env_bool(name: str, default: bool) -> bool:
    """Read an environment variable as a True/False value.

    Why this helper exists: writing ``bool(os.environ.get("FOO"))`` looks
    fine but has a famous trap. ``bool("0")`` is ``True`` in Python,
    because the string "0" is not empty. So an env var set to "0" would
    flip on instead of off. This helper checks the actual text of the
    value and only returns True for things that really mean "yes".

    Rules:
      - variable not set            -> the ``default`` you passed in
      - "0", "false", "no", "off"   -> False (case does not matter)
      - empty string                -> False
      - anything else (e.g. "1")    -> True

    Example::

        DEBUG = _env_bool("SCHOOLBELL_DEBUG", default=False)
    """
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")
