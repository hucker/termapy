"""Command-history browsing cursor for the REPL input.

Pure state machine, no Textual: given the history list and the current
draft, ``up`` / ``down`` return the text to place in the input, and the
navigator remembers where in history the user is (and the draft they were
typing before they started browsing, so walking back off the newest entry
restores it).

Owned by the app; driven from ``on_key`` (Up / Down).  Kept Textual-free so
the transition logic -- floor/ceiling wrapping, empty-history guard, draft
save/restore -- is unit-testable without a running UI.
"""

from __future__ import annotations


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
