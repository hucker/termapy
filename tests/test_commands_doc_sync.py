"""Freshness guard: every command listed in help/commands.md must exist.

commands.md is a *curated* overview (a subset of the ~250-command registry,
with hand-written descriptions), so we can't generate it -- but we can catch
the drift that ships stale docs: a command that was renamed or removed while
its row lingered, or a phantom command that never existed.

We check command-NAME existence, not synopsis text: the doc deliberately
abbreviates (`/cap.bin <f> ...`), so a strict synopsis compare would false-
positive.  The live synopsis is single-sourced on `Command.args` and shown by
`/help`; this test just keeps the doc's command SET honest.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.legacy import LEGACY_COMMANDS

_COMMANDS_MD = Path(__file__).parent.parent / "src" / "termapy" / "help" / "commands.md"
# A command token in a code-span: `/name` -- dotted segments, no trailing dot
# (so `/term.*` in prose yields nothing, not "term.").
_CMD_RE = re.compile(r"`/([a-z0-9_]+(?:\.[a-z0-9_]+)*)")

# Commands registered ONLY by the Textual TUI host (not by the CLI host this
# test builds), so they can't be seen via the CLI registry but are real and
# fine to document.  Keep this list tiny and specific.
_TUI_ONLY = {"proto.load"}


def _documented_commands() -> set[str]:
    """Every `/command` name in the FIRST (Command) column of the table.

    Only table rows (lines starting with ``|``) and only their first cell --
    so a `/foo` in prose or a `/term.*` inside a Description doesn't count.
    """
    names: set[str] = set()
    for line in _COMMANDS_MD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|")[1]  # text between the 1st and 2nd pipe
        names.update(m.group(1) for m in _CMD_RE.finditer(first_cell))
    return names


def _registered_commands() -> set[str]:
    """Every command name a real CLI host registers (builtins + hooks)."""
    tmp = Path(tempfile.mkdtemp())
    cp = tmp / "c" / "c.cfg"
    cp.parent.mkdir()
    cfg = {
        **DEFAULT_CFG,
        "serial": {**DEFAULT_CFG["serial"], "port": "", "baud_rate": 115200},
    }
    cp.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "proto", "cap", "prof"):
        (cp.parent / sub).mkdir(exist_ok=True)
    cli = CLITerminal(cfg, str(cp), no_color=True, term_width=80)
    return set(cli.repl._plugins)


def test_every_documented_command_exists():
    # Arrange -- known real commands are builtins + CLI/TUI hooks; legacy
    # aliases (/echo -> /term.echo) are valid to document too.
    known = _registered_commands() | set(LEGACY_COMMANDS) | _TUI_ONLY

    # Act -- commands in the doc that no host registers and aren't aliases
    documented = _documented_commands()
    phantom = sorted(documented - known)

    # Assert
    assert phantom == [], (
        "commands.md lists commands that don't exist (renamed/removed?): "
        f"{phantom}"
    )
