"""The one reader for a typed name: every name filter and every did-you-mean shares its rules.

A pattern holding a wildcard (``*``, ``?`` or ``[``) is a glob, anchored at
both ends: ``pic32*`` finds the names that start with pic32.  Plain text in
a list filter means "contains" (``/dev.lib pic32``).  A command that acts on
what it picks (``/cap.delete``) reads plain text as one exact name instead,
so it asks :func:`is_glob` first.  Case never matters, on any platform:
``fnmatch.fnmatch`` follows the OS, and one pattern must answer the same on
Windows and Linux.  A filter that matches nothing is an empty answer, not a
failure (decision 2026-10-19).

A name that names nothing is answered with the names it most likely
meant: :func:`closest` picks them (case ignored, edit distance, a limit
that grows with the name) and :func:`did_you_mean` phrases them, so
every suggestion is found and worded the same way (decision 2026-10-30).
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase

WILDCARDS = "*?["

# What a suggestion's names follow, at the end of an error (``did_you_mean``).
DID_YOU_MEAN = " -- did you mean "


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


def edit_distance(a: str, b: str) -> int:
    """The edits that turn ``a`` into ``b``: insert, delete, substitute, or swap two neighbors.

    The optimal-string-alignment form of Damerau-Levenshtein: a swap of
    adjacent characters is one edit (``hlep`` -> ``help``), the commonest
    typo, and no stretch of characters is edited twice.  Case counts here;
    :func:`closest` folds it first.
    """
    rows = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        rows[i][0] = i
    for j in range(len(b) + 1):
        rows[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            rows[i][j] = min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                rows[i][j] = min(rows[i][j], rows[i - 2][j - 2] + 1)
    return rows[len(a)][len(b)]


def _typo_reach(name: str) -> int:
    """How many edits still read as a typo of ``name``: 1 up to 4 characters, 2 up to 10, 3 beyond.

    A flat limit suggests junk for a short word (``foo`` is two edits from
    ``log``) and misses a long one; scaled to the length, neither happens.
    """
    return 1 if len(name) <= 4 else 2 if len(name) <= 10 else 3


def closest(name: str, candidates: Iterable[str], *, limit: int = 3) -> list[str]:
    """The names a mistyped ``name`` most likely meant, nearest first (registry ``did-you-mean-one``).

    Case is ignored, so ``sercom0_ctrla`` finds ``SERCOM0_CTRLA``.  A
    candidate qualifies within :func:`_typo_reach` edits of ``name``; ties
    go to the earlier name alphabetically, and ``name`` itself never comes
    back.  A candidate whose length alone puts it out of reach is skipped
    before its distance is computed, which keeps a lookup over a
    2500-register part to a fraction of a second.

    Args:
        name: The name as typed.
        candidates: The names that exist; duplicates are fine.
        limit: At most this many come back.

    Returns:
        Up to ``limit`` candidates, nearest first; empty when none is close.
    """
    key = name.casefold()
    reach = _typo_reach(name)
    scored: list[tuple[int, str]] = []
    for candidate in set(candidates):
        folded = candidate.casefold()
        if candidate == name or abs(len(folded) - len(key)) > reach:
            continue
        distance = edit_distance(key, folded)
        if distance <= reach:
            scored.append((distance, candidate))
    return [candidate for _, candidate in sorted(scored)][:limit]


def did_you_mean(suggestions: Iterable[str], *, prefix: str = "") -> str:
    """The one phrasing of a suggestion: `` -- did you mean a, b?``, or ``""`` for none.

    Appended to the error that names the unknown thing, so every
    suggestion reads the same: ``Unknown command: hlep -- did you mean
    /help?``.  ``prefix`` goes before each name (the REPL prefix, for a
    command).
    """
    names = [f"{prefix}{suggestion}" for suggestion in suggestions]
    return f"{DID_YOU_MEAN}{', '.join(names)}?" if names else ""


def split_did_you_mean(text: str) -> tuple[str, list[str], str]:
    """``text`` around its did-you-mean: what comes before it, the names it offers, what follows.

    The inverse of :func:`did_you_mean`, so a frontend that can click
    makes each offered name a link while the error stays one string.  A
    suggestion ends its line, and a line may follow it (the ``Usage:``
    line of a parameter error), which comes back as the third part.
    ``(text, [], "")`` when no line ends with a suggestion.
    """
    at = text.rfind(DID_YOU_MEAN)
    end = text.find("?", at) if at >= 0 else -1
    after = text[end + 1 :] if end >= 0 else ""
    if end < 0 or (after and not after.startswith("\n")):
        return text, [], ""
    return text[:at], text[at + len(DID_YOU_MEAN) : end].split(", "), after
