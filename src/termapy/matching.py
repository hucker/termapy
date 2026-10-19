"""The one reader for a typed name pattern: every name filter shares its rules.

A pattern holding a wildcard (``*``, ``?`` or ``[``) is a glob, anchored at
both ends: ``pic32*`` finds the names that start with pic32.  Plain text in
a list filter means "contains" (``/dev.lib pic32``).  A command that acts on
what it picks (``/cap.delete``) reads plain text as one exact name instead,
so it asks :func:`is_glob` first.  Case never matters, on any platform:
``fnmatch.fnmatch`` follows the OS, and one pattern must answer the same on
Windows and Linux.  A filter that matches nothing is an empty answer, not a
failure (decision 2026-10-19).
"""

from __future__ import annotations

from fnmatch import fnmatchcase

WILDCARDS = "*?["


def is_glob(pattern: str) -> bool:
    """True when ``pattern`` holds a wildcard, so it is a glob rather than a name."""
    return any(char in pattern for char in WILDCARDS)


def matches(name: str, pattern: str) -> bool:
    """Whether ``name`` matches ``pattern``: an anchored glob, else contains, case ignored.

    An empty pattern matches every name, so a bare filter lists everything.
    """
    folded = pattern.casefold()
    if is_glob(pattern):
        return fnmatchcase(name.casefold(), folded)
    return folded in name.casefold()
