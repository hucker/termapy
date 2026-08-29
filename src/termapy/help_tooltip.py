"""The Help-button tooltip: version status + open-source attributions.

A pure Rich renderable builder -- no Textual, no widget tree -- so it can
be rendered to a string and asserted directly (see
``tests/test_help_tooltip.py``).  Extracted from ``app.py`` so the
version-status line and the attribution block have coverage; the caller
passes the hotkey hint (``app.py`` owns ``_hotkey_label``).
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from termapy.update_check import cached_status


def build_help_tooltip(ver: str, hint: str = "") -> Group:
    """Build the Help-button tooltip as a Rich renderable.

    ``hint`` is the hotkey label (e.g. ``"F1"``) shown in parentheses next
    to "Show help guide"; empty for none.  Lays the attribution block out
    as a three-column ``Table.grid`` (name / role / author) so it stays
    scannable.  The version-status line reads the cached PyPI check
    (network-free); ``None`` = never checked, so we say nothing.
    """
    hint_str = f" ({hint})" if hint else ""

    # Ambient version status from the cached background check -- a
    # network-free state read, so building the tooltip never blocks or
    # fails.  ``None`` latest = we've never successfully checked, so we
    # say nothing rather than guess.
    latest_seen, outdated = cached_status(ver)
    if latest_seen is None:
        status_line = None
    elif outdated:
        status_line = Text.from_markup(
            f"[yellow]Update available: v{latest_seen}"
            "  (uv tool upgrade termapy)[/]"
        )
    else:
        status_line = Text.from_markup("[green]You have the latest version.[/]")

    # crcglot (the CRC engine termapy calls) is built on the reveng
    # catalogue (the algorithm source), so both are credited, adjacent,
    # to show the lineage -- dropping either would erase a link.  The
    # reveng URL sits on a second line inside its "role" cell so it
    # aligns under "CRC algorithms"; Rich renders ``\n`` in a cell as
    # multi-line and column widths still line up.
    reveng_role = Text("CRC algorithms\n", style="white")
    reveng_role.append("reveng.sourceforge.io", style="dim")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="cyan")
    grid.add_column(style="white")
    grid.add_column(style="green")
    grid.add_row("pyserial",         "serial I/O",     "Chris Liechti")
    grid.add_row("Textual / Rich",   "TUI + output",   "Will McGugan")
    grid.add_row("prompt_toolkit",   "CLI",            "Jonathan Slenders")
    grid.add_row("crcglot",          "CRC engine",     "Chuck Bass")
    grid.add_row("frist",            "ages, durations", "Chuck Bass")
    grid.add_row("reveng catalog", reveng_role,      "Greg Cook")
    grid.add_row("xmodem",           "file transfer",
                 "Wijnand Modderman, Jeff Quast, Andrew Leech")
    grid.add_row("ymodem",           "file transfer",  "alexwoo")

    parts: list[RenderableType] = [
        Text.from_markup(f"[bold]Termapy v{ver}[/]  [dim]Show help guide{hint_str}.[/]"),
    ]
    if status_line is not None:
        parts.append(status_line)
    parts += [
        Text(""),
        Text.from_markup("[bold]Built on open source:[/]"),
        grid,
        Text(""),
        Text.from_markup("Type [bold cyan]/credits[/] for full attribution."),
    ]
    return Group(*parts)
