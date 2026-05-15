"""Read-only info views for the TUI.

Four passive renders over app state -- the full command landscape,
the config-info dialog, the command palette, and the port picker.
None of these mutate state; they just present what the app already
knows.

Previously lived as four ``_show_*`` methods on ``SerialTerminal``.
Extracted here so the read-only display surface is a named
subsystem.

Each function takes the app as first argument.  ``SerialTerminal``
keeps thin stubs (``self._show_commands`` etc.) so existing call
sites -- direct method calls, button-dict getattr-by-name lookups,
and action_show_palette -- keep working unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from termapy.defaults import cmd_prefix
from termapy.dialogs import PortPicker

if TYPE_CHECKING:
    from termapy.app import SerialTerminal  # noqa: F401


def show_config_info(app, path: str) -> None:
    """Print config dir, file, and log file paths (verbose only)."""
    if not getattr(app.repl, "ctx", None):
        return
    if app.repl.ctx.output_level != "verbose":
        return
    resolved = Path(path).resolve()
    app._status(f"Config dir:  {resolved.parent}", "green")
    app._status(f"Config file: {resolved}", "green")
    xfer_root = app.cfg.get("file_xfer_root", "")
    if xfer_root:
        app._status(f"Xfer root:   {Path(xfer_root).resolve()}", "green")
    else:
        app._status(f"Xfer root:   {resolved.parent / 'cap'}", "green")
    log_path = app._log_path()
    if log_path:
        app._status(f"Log file:    {Path(log_path).resolve()}", "green")


def show_port_picker(app) -> None:
    """Open the PortPicker modal, or short-circuit to the only port.

    When the system has exactly one serial port, skips the modal and
    immediately calls ``app._on_port_picked`` with that port -- there's
    nothing to pick.  With zero or multiple ports, push the modal.

    Args:
        app: The SerialTerminal instance.
    """
    from serial.tools.list_ports import comports

    ports = sorted(comports(), key=lambda p: p.device)
    if len(ports) == 1:
        app._on_port_picked(ports[0].device)
        return
    app.push_screen(PortPicker(), callback=app._on_port_picked)


def show_commands(app) -> None:
    """Show the REPL command picker with smart arg handling.

    Filters to commands whose name contains the substring currently
    in the ``#cmd`` input (case-insensitive).  Any leading prefix
    character in the input is stripped first, so typing ``/po`` and
    ``po`` behave identically.  Empty input shows everything.

    Hides Linux-only commands on non-Linux platforms (detected by
    scanning each plugin's help text for "linux ... only").
    Right-pads the command+args portion so the "# help text"
    comment starts at a consistent column; if the prefix is too
    long to fit, the comment wraps to a new continuation line
    indented to the same column.
    """
    popup = app.query_one("#history-popup", OptionList)
    popup.clear_options()
    prefix = cmd_prefix(app.cfg)
    skip_linux_only = sys.platform != "linux"

    # Build the filter from the current input.  Users can have the
    # prefix typed (``/po``) or not (``po``) -- strip once so the
    # match is on the command name only.
    raw = app.query_one("#cmd", Input).value.strip()
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
    # If the user typed args (``/port open COM3``), only match on
    # the first token -- the name portion.
    filter_term = raw.split(None, 1)[0].lower() if raw else ""

    def _add_row(cmd_text: str, help_text: str, option_id: str) -> None:
        """Build a Text row with the help column aligned to _CMDS_HELP_COL.

        If ``cmd_text`` is shorter than the target column, pad
        with spaces.  If it's longer, break and indent the
        help text on the next line so the comment column
        stays aligned.
        """
        label = Text(cmd_text)
        pad = app._CMDS_HELP_COL - len(cmd_text)
        if pad > 0:
            label.append(" " * pad)
            label.append(f"# {help_text}", style="dim")
        else:
            label.append("\n")
            label.append(" " * app._CMDS_HELP_COL)
            label.append(f"# {help_text}", style="dim")
        popup.add_option(Option(label, id=option_id))

    groups: dict[str, list] = {}
    for name, plugin in app.repl._plugins.items():
        if getattr(plugin, "hidden", False):
            continue
        if skip_linux_only and app._LINUX_ONLY_RE.search(plugin.help or ""):
            continue
        if filter_term and filter_term not in name.lower():
            continue
        groups.setdefault(plugin.source, []).append((name, plugin))
    if not groups and filter_term:
        popup.add_option(
            Option(
                Text(f"  (no commands match '{filter_term}')", style="dim"),
                disabled=True,
            )
        )
    for source, plugins in groups.items():
        popup.add_option(Option(f"── {source} ──", disabled=True))
        for name, plugin in sorted(plugins, key=lambda p: p[0]):
            has_required = "<" in plugin.args if plugin.args else False
            has_optional = "{" in plugin.args if plugin.args else False
            if not plugin.args or (has_optional and not has_required):
                _add_row(f"{prefix}{name}", plugin.help, f"run:{name}")
            if plugin.args:
                _add_row(
                    f"{prefix}{name} {plugin.args}",
                    plugin.help,
                    f"repl:{name}",
                )
    popup.add_class("visible")
    popup.focus()
    popup.highlighted = 1 if popup.option_count > 1 else 0
    app._popup_mode = "commands"


def show_palette(app) -> None:
    """Show the command palette popup."""
    popup = app.query_one("#history-popup", OptionList)
    popup.clear_options()
    for i, (label, _) in enumerate(app.PALETTE_CMDS):
        popup.add_option(Option(label, id=f"palette:{i}"))
    popup.add_class("visible")
    popup.focus()
    if popup.option_count > 0:
        popup.highlighted = 0
    app._popup_mode = "palette"


