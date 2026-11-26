"""Read-only info views for the TUI.

Three passive renders over app state -- the full command landscape,
the config-info dialog, and the port picker.  None of these mutate
state; they just present what the app already knows.

Previously lived as ``_show_*`` methods on ``SerialTerminal``.
Extracted here so the read-only display surface is a named subsystem.

Each function takes the app as first argument.  ``SerialTerminal``
keeps thin stubs (``self._show_commands`` etc.) so existing call sites
-- direct method calls and button-dict getattr-by-name lookups --
keep working unchanged.

The curated command palette is no longer in this module: it moved
to ``palette_provider.PaletteProvider`` to feed Textual's built-in
``CommandPalette`` modal directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from termapy.defaults import cfg_value, cmd_prefix
from termapy.dialogs import PortPicker
from termapy.plugins import interpolate_help

if TYPE_CHECKING:
    from termapy.app import SerialTerminal


def show_config_info(app: SerialTerminal, path: str) -> None:
    """Print config dir, file, and log file paths (verbose only)."""
    if not getattr(app.repl, "ctx", None):
        return
    if app.repl.ctx.output_level != "verbose":
        return
    resolved = Path(path).resolve()
    app._status(f"Config dir:  {resolved.parent}", "green")
    app._status(f"Config file: {resolved}", "green")
    xfer_root = cfg_value(app.cfg, "file_xfer_root")
    if xfer_root:
        app._status(f"Xfer root:   {Path(xfer_root).resolve()}", "green")
    else:
        app._status(f"Xfer root:   {resolved.parent / 'cap'}", "green")
    log_path = app.session_log_path()
    if log_path is not None:
        app._status(f"Log file:    {log_path}", "green")


def show_port_picker(app: SerialTerminal) -> None:
    """Open the PortPicker modal, every time.

    It opens even when the machine has one port: the dialog also takes a
    typed network port (Network Port...), and Enter picks the one row.

    Args:
        app: The SerialTerminal instance.
    """
    app.push_screen(PortPicker(test_port=app._test_port), callback=app._on_port_picked)


def show_commands(app: SerialTerminal) -> None:
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

    # Build the filter from the current input.  Users can have the
    # prefix typed (``/po``) or not (``po``) -- strip once so the
    # match is on the command name only.
    raw = app.query_one("#cmd", Input).value.strip()
    if raw.startswith(prefix):
        raw = raw[len(prefix) :]
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
                _add_row(f"{prefix}{name}", interpolate_help(plugin.help, prefix), f"run:{name}")
            if plugin.args:
                _add_row(
                    f"{prefix}{name} {plugin.args}",
                    interpolate_help(plugin.help, prefix),
                    f"repl:{name}",
                )
    popup.add_class("visible")
    popup.focus()
    popup.highlighted = 1 if popup.option_count > 1 else 0
    app._popup_mode = "commands"
