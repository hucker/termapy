"""The CLI's interactive prompt: prompt_toolkit history, completion and session.

Everything in the plain-text CLI that needs prompt_toolkit lives here, and
``CLITerminal`` imports this module only when it builds an interactive
prompt.  prompt_toolkit costs ~0.2 s to import, so keeping it out of
``termapy.cli`` means a one-shot (``termapy --cli -e ...`` or ``--run``),
which never shows a prompt, does not pay for one.  No Textual dependency.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import History

from termapy.folders import ensure_folder

if TYPE_CHECKING:
    from termapy.repl import ReplEngine


class PlainFileHistory(History):
    """Prompt history stored as plain text lines -- one command per line.

    The TUI reads and writes the per-config history file as plain lines
    (app.py _load_history/_save_history), so the CLI must speak the same
    format for the one-file-per-cfg contract to hold.  prompt_toolkit's
    own FileHistory writes a timestamped ``+``-prefixed format that
    corrupts the shared file -- hence this subclass.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__()

    def load_history_strings(self):
        """Yield stored commands, newest first (prompt_toolkit contract)."""
        try:
            lines = Path(self.filename).read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            return
        for line in reversed(lines):
            # Skip legacy prompt_toolkit FileHistory timestamp comments so
            # pre-fix files clean up on their own.  Old "+cmd" entries are
            # left as-is (a user can legitimately type "+++"); they age out
            # via the TUI's history-limit rewrite.
            if line.startswith("# ") or not line.strip():
                continue
            yield line

    def store_string(self, string: str) -> None:
        """Append one command as one plain line (newlines flattened)."""
        line = " ".join(string.splitlines())
        try:
            # With no config loaded the file sits in the cfg root, which
            # the first write creates.
            ensure_folder(Path(self.filename).parent)
            with open(self.filename, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # non-critical file I/O: history loss is not an error


def _menu_rows_for_terminal() -> int:
    """Scale the completion-dropdown row reservation to terminal height.

    Prompt-toolkit reserves rows below the prompt for the completion
    dropdown, which appears as a visible empty band when idle.  Tall
    terminals can afford a generous dropdown; very short ones should
    give up completion entirely before the band eats half the
    screen.

      - height >= 40 rows  -> 8 rows  (the prompt-toolkit default)
      - height >= 10 rows  -> 4 rows  (still scannable, smaller gap)
      - height < 10 rows   -> 0 rows  (no dropdown, no gap)

    Read once at PromptSession construction; prompt-toolkit caches the
    value internally.
    """
    try:
        lines = shutil.get_terminal_size().lines
    except (OSError, ValueError):
        return 4
    if lines >= 40:
        return 8
    if lines >= 10:
        return 4
    return 0


class _TermapyCompleter(Completer):
    """Tab completer for REPL commands, device commands, and data-folder files."""

    def __init__(self, repl: ReplEngine, prefix: str) -> None:
        self._repl = repl
        self._prefix = prefix

    def get_completions(self, document, complete_event):  # noqa: ARG002 -- prompt_toolkit interface
        """Yield completions for the current input."""
        line = document.text

        # The first argument of a command that declares its folder
        # (Command.files): the folder's files, newest first.
        file_arg = self._repl.complete_file_arg(line)
        if file_arg is not None:
            start, names = file_arg
            for name in names:
                yield Completion(name, start_position=start - len(line))
            return

        # REPL command completion
        if line.startswith(self._prefix):
            for name in sorted(self._repl._plugins):
                full = f"{self._prefix}{name}"
                if full.startswith(line):
                    yield Completion(full, start_position=-len(line))
            return

        # Device command completion -- pulls from the active profile's
        # commands dict.
        active = self._repl.ctx.ns("active_profile")
        profile_cmds = active.get("commands") if isinstance(active, dict) else None
        if isinstance(profile_cmds, dict) and profile_cmds:
            for name in sorted(profile_cmds):
                if name.lower().startswith(line.lower()):
                    yield Completion(name, start_position=-len(line))


def build_session(repl: ReplEngine, prefix: str, history_path: str) -> PromptSession:
    """The CLI's prompt: shared plain-text history, tab completion, suggestions.

    Args:
        repl: The engine whose commands, profile and folders complete.
        prefix: The REPL command prefix (``/``).
        history_path: The per-config history file, shared with the TUI.
    """
    return PromptSession(
        history=PlainFileHistory(history_path),
        completer=_TermapyCompleter(repl, prefix),
        auto_suggest=AutoSuggestFromHistory(),
        # Prompt-toolkit reserves space below the prompt for the
        # completion dropdown, which renders as a visible empty
        # band when nothing's being typed.  Scale the reservation
        # to the terminal height so tall windows get a roomy
        # dropdown and short windows aren't dominated by dead
        # space (or give up completion entirely when there's
        # simply no room).  Read once at startup -- prompt_toolkit
        # captures this value at PromptSession construction.
        reserve_space_for_menu=_menu_rows_for_terminal(),
    )
