"""Command-history browsing cursor and file merge for the REPL input.

Pure, no Textual.  :class:`HistoryNavigator` is the browsing cursor: given
the history list and the current draft, ``up`` / ``down`` return the text
to place in the input, and it remembers where in history the user is (and
the draft they were typing before they started browsing, so walking back
off the newest entry restores it).

:func:`merge_history` is the other half: the history FILE is shared state,
and a session that only ever overwrote it with its own in-memory list
would lose whatever else wrote there -- another session on the same
config, or an editor appending commands to run.  Merging keeps both.

Owned by the app; driven from ``on_key`` (Up / Down) and the load/save
pair.  Kept Textual-free so the transition logic -- floor/ceiling
wrapping, empty-history guard, draft save/restore, merge ordering -- is
unit-testable without a running UI.
"""

from __future__ import annotations

from pathlib import Path


def read_history(path: str | Path, limit: int) -> list[str]:
    """The file's last ``limit`` non-empty lines; ``[]`` when unreadable.

    Never raises: a missing, unreadable or undecodable history file means
    "no history", which must not stop the app from starting.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def merge_history(on_disk: list[str], in_memory: list[str], limit: int) -> list[str]:
    """Combine a history file with a session's own list, newest last.

    The file is split at the last line this session already knows.  What
    precedes that point is shared past and sorts BEFORE this session's
    list; what follows it arrived after this session last read the file --
    another instance's commands, or an append meant to be recalled -- and
    sorts AFTER, so the first Up reaches it.

    A file with nothing in common (a different session entirely) is all
    "before": its lines are history this session simply never had, and
    they must not displace what the user just typed.

    Args:
        on_disk: Lines read from the file, oldest first.
        in_memory: This session's history, oldest first.
        limit: Keep at most this many, dropping the oldest.

    Returns:
        The merged list, oldest first.
    """
    known = set(in_memory)
    split = 0
    for i, line in enumerate(on_disk):
        if line in known:
            split = i + 1
    before = [line for line in on_disk[:split] if line not in known]
    after = [line for line in on_disk[split:] if line not in known]
    if not split:
        before, after = before + after, []
    merged = before + in_memory + after
    # Dedupe keeping the LAST occurrence, so a repeated command sits at
    # its most recent position (dict preserves insertion order).
    deduped = list(dict.fromkeys(reversed(merged)))
    deduped.reverse()
    return deduped[-limit:]


class HistoryNavigator:
    """Tracks the Up/Down browsing position through command history.

    ``_idx == -1`` means "not browsing" -- the input holds the user's live
    draft, not a recalled entry.  The first Up stashes that draft in
    ``_saved`` and jumps to the newest history entry; walking Down back off
    the newest entry restores the stashed draft and returns to not-browsing.
    """

    def __init__(self) -> None:
        self._idx: int = -1  # -1 = not browsing history
        self._saved: str = ""  # draft stashed when browsing began

    @property
    def browsing(self) -> bool:
        """True while the input shows a recalled entry, not the live draft."""
        return self._idx != -1

    def reset(self) -> None:
        """Stop browsing (e.g. after a submit, config switch, or Escape)."""
        self._idx = -1

    def up(self, history: list[str], draft: str) -> str | None:
        """Step toward older entries; return the text to show, or None.

        Returns None (caller leaves the input untouched) only when there is
        no history to browse.  The first Up stashes ``draft`` so Down can
        restore it.  At the oldest entry, further Up stays put.
        """
        if not history:
            return None
        if self._idx == -1:
            self._saved = draft
            self._idx = len(history) - 1
        elif self._idx > 0:
            self._idx -= 1
        return history[self._idx]

    def down(self, history: list[str]) -> str | None:
        """Step toward newer entries; return the text to show, or None.

        Returns None (caller leaves the input untouched) when not browsing.
        Walking down past the newest entry restores the stashed draft and
        returns to not-browsing.
        """
        if self._idx == -1:
            return None
        self._idx += 1
        if self._idx >= len(history):
            self._idx = -1
            return self._saved
        return history[self._idx]
