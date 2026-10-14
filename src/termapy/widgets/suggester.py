"""CommandSuggester -- input type-ahead from REPL commands + history.

Pure matching logic (no widget tree), so it can be tested directly:
``update()`` builds the candidate list and ``get_suggestion()`` returns a
prefix match.  Extracted from ``app.py`` so that contract has coverage.
"""

from __future__ import annotations

from typing import Callable

from textual.suggester import Suggester

from termapy.defaults import DEFAULT_CMD_PREFIX

# ``ReplEngine.complete_file_arg``: a line -> (start, names) or None.
FileCompleter = Callable[[str], tuple[int, list[str]] | None]


class CommandSuggester(Suggester):
    """Type-ahead from REPL commands + device command history.

    Combines REPL command names (e.g. ``/help``, ``/cfg``) with non-REPL
    history entries (device commands like ``AT+CSQ``). Updated dynamically
    as new commands are entered.  With a ``complete_file`` callable, a
    partly typed file argument of a command that declares its folder
    (``Command.files``) is completed to the newest matching file instead.

    Args:
        complete_file: Returns ``(start, names)`` for a line in a file
            argument, else None.  Omitted, only the static list is used.
    """

    def __init__(self, complete_file: FileCompleter | None = None) -> None:
        super().__init__(use_cache=False, case_sensitive=False)
        self._suggestions: list[str] = []
        self._complete_file = complete_file

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
        """Return the newest matching file, else the first prefix match.

        A file is offered only once a character of the argument is typed:
        on a bare ``/run `` the static signature (``/run {filename}``)
        still shows what the command takes.
        """
        if self._complete_file is not None:
            completion = self._complete_file(value)
            if completion is not None:
                start, names = completion
                if value[start:] and names:
                    return value[:start] + names[0]
        for suggestion in self._suggestions:
            if suggestion.casefold().startswith(value):
                return suggestion
        return None
