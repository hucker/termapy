"""CommandSuggester -- input type-ahead from REPL commands + history.

Pure matching logic (no widget tree), so it can be tested directly:
``update()`` builds the candidate list and ``get_suggestion()`` returns a
prefix match.  Extracted from ``app.py`` so that contract has coverage.
"""

from __future__ import annotations

from textual.suggester import Suggester

from termapy.defaults import DEFAULT_CMD_PREFIX


class CommandSuggester(Suggester):
    """Type-ahead from REPL commands + device command history.

    Combines REPL command names (e.g. ``/help``, ``/cfg``) with non-REPL
    history entries (device commands like ``AT+CSQ``). Updated dynamically
    as new commands are entered.
    """

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=False)
        self._suggestions: list[str] = []

    def update(
        self,
        commands: list[str],
        history: list[str],
        prefix: str = DEFAULT_CMD_PREFIX,
    ) -> None:
        """Rebuild suggestions: REPL commands + non-REPL history (deduped)."""
        device_cmds = [device_cmd for device_cmd in history if not device_cmd.startswith(prefix)]
        self._suggestions = commands + device_cmds

    async def get_suggestion(self, value: str) -> str | None:
        """Return the first prefix match (case-insensitive)."""
        for suggestion in self._suggestions:
            if suggestion.casefold().startswith(value):
                return suggestion
        return None
