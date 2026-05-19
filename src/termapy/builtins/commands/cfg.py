"""Built-in plugin: show or change config values, project info, file listings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.builtins.commands._cfg_icon import (
    _FLAGS as _ICON_FLAGS,
)
from termapy.builtins.commands._cfg_icon import (
    _LONG_HELP as _ICON_LONG_HELP,
)
from termapy.builtins.commands._cfg_icon import _handler as _icon_handler
from termapy.builtins.commands._cfg_icon import (
    _handler_list as _icon_handler_list,
)
from termapy.builtins.commands._cfg_icon import (
    _handler_remove as _icon_handler_remove,
)
from termapy.config import cfg_data_dir, cfg_dir, global_plugins_dir, open_with_system
from termapy.folders import FOLDERS
from termapy.help_dynamic import cfg_status, compose
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── /cfg handler ───────────────────────────────────────────────────────────────


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Show all config, a single key, or set a key with confirmation.

    With no arguments, prints every key/value pair. With a key only,
    prints that key's current value. With key and value, validates the
    type against the existing value and delegates to the confirmation
    dialog (or applies directly if no dialog is configured).

    Args:
        ctx: Plugin context for config access and output.
        args: Optional ``"key"`` or ``"key value"`` string.
    """
    parts = args.strip().split(None, 1)
    # Bare /cfg -- TUI opens the Cfg picker dialog (matches the
    # title-bar button); CLI shows /help cfg.  Use /cfg.dump to print
    # the loaded config as JSON.
    if not parts:
        if ctx.engine.open_picker is not None:
            return ctx.engine.open_picker("cfg")
        return _handler_help(ctx, args)
    key = parts[0]
    if key not in ctx.cfg:
        return CmdResult.fail(msg=f"Unknown config key: {key}")
    # /cfg key - show value
    if len(parts) == 1:
        val = ctx.cfg[key]
        ctx.io.result(str(val))
        return CmdResult.ok(value=str(val))
    # /cfg key value - validate and delegate for confirmation
    value_str = parts[1]
    try:
        new_val = ctx.engine.coerce_type(value_str, ctx.cfg[key])
    except (ValueError, TypeError) as e:
        return CmdResult.fail(msg=f"Type error: {e}")
    old_val = ctx.cfg[key]
    if new_val == old_val:
        ctx.io.output(f"{key} is already {old_val!r}")
        return CmdResult.ok(value=str(old_val))
    if ctx.engine.save_cfg:
        ctx.engine.save_cfg(key, new_val)
    else:
        ctx.engine.apply_cfg(key, new_val)
    return CmdResult.ok(value=str(new_val))


# ── /cfg.auto handler ─────────────────────────────────────────────────────────


def _handler_auto(ctx: PluginContext, args: str) -> CmdResult:
    """Set a config key immediately without confirmation dialog.

    Args:
        ctx: Plugin context for config access and output.
        args: ``"key value"`` string (both required).
    """
    parts = args.strip().split(None, 1)
    if not parts or len(parts) < 2:
        return CmdResult.fail(msg="Usage: /cfg.auto <key> <value>")
    key, value_str = parts[0], parts[1]
    if key not in ctx.cfg:
        return CmdResult.fail(msg=f"Unknown config key: {key}")
    try:
        new_val = ctx.engine.coerce_type(value_str, ctx.cfg[key])
    except (ValueError, TypeError) as e:
        return CmdResult.fail(msg=f"Type error: {e}")
    ctx.engine.apply_cfg(key, new_val)
    return CmdResult.ok(value=str(new_val))


# ── /cfg.list handler ─────────────────────────────────────────────────────────


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List all config files in the config directory.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    d = cfg_dir()
    files = sorted(d.glob("*/*.cfg"))
    if not files:
        ctx.io.output("  (no config files)")
        return CmdResult.ok(value="")
    names: list[str] = []
    for f in files:
        marker = " *" if str(f) == ctx.config_path else ""
        ctx.io.output(f"  {f.parent.name}/{f.name}{marker}")
        names.append(f"{f.parent.name}/{f.name}")
    return CmdResult.ok(value="\n".join(names))


# ── /cfg.load handler ──────────────────────────────────────────────────────


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Load a different config in the current session.

    Accepts a bare config name (``myproj``), a relative path, or an
    absolute path -- same resolution as the ``[config]`` positional
    command-line argument.  Disconnects any currently-open port,
    replaces the in-memory config, and reconnects if the new cfg has
    ``auto_connect: true``.

    Available in CLI, TUI, and MCP frontends.  Particularly useful in
    MCP mode where the model can hot-swap between device configs
    without spawning a new server process for each one.

    Args:
        ctx: Plugin context (uses ``ctx.engine.load_config``).
        args: The config name / path to load.
    """
    name = args.strip()
    if not name:
        return CmdResult.fail(
            msg=f"Usage: {ctx.engine.prefix}cfg.load <name>"
        )
    return ctx.engine.load_config(name)


# ── /cfg.explore handler ──────────────────────────────────────────────────────


def _handler_explore(ctx: PluginContext, args: str) -> CmdResult:
    """Open the config data directory in the system file explorer.

    Args:
        ctx: Plugin context.
        args: Unused.
    """
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    data_dir = Path(ctx.config_path).parent
    open_with_system(str(data_dir))
    return CmdResult.ok(value=str(data_dir))


def _handler_show(ctx: PluginContext, args: str) -> CmdResult:
    """Open the current config file in the system viewer.

    Mirrors ``/edit.cfg``; named ``cfg.show`` for symmetry with the
    folder ``.show`` family on /run, /proto, /ss, etc.
    """
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    ctx.fs.open_file(Path(ctx.config_path))
    return CmdResult.ok(value=ctx.config_path)


def _handler_help(ctx: PluginContext, args: str) -> CmdResult:
    """Same as ``/help cfg``, plus an AVAILABLE CONFIGS file list."""
    from termapy.builtins.commands.help import (
        _show_command_help,
        append_files_section,
    )

    result = _show_command_help(ctx, "cfg")
    files = sorted(
        f"{f.parent.name}/{f.name}" for f in cfg_dir().glob("*/*.cfg")
    )
    append_files_section(ctx, "AVAILABLE CONFIGS", files)
    return result


# ── Tree-building helpers (shared by info and folder listings) ────────────────


def _names(directory: Path, pattern: str) -> list[str]:
    """Return sorted filenames matching pattern in directory."""
    if pattern == "*":
        return sorted(f.name for f in directory.glob(pattern) if f.is_file())
    return sorted(f.name for f in directory.glob(pattern))


def _build_tree(
    config_path: str,
    sections: list[tuple[str, list[str]]],
    global_names: list[str] | None = None,
) -> tuple[str, str]:
    """Build plain and Rich-colored directory trees.

    The colored tree is for the terminal output and contains names
    only.  The plain tree is for the markdown report and appends
    size / created / modified columns to each file line; folders
    appear bare.  Columns are space-padded so they align in a
    monospace ``text`` code-fence.

    Args:
        config_path: Path to the config file.
        sections: List of (name, file_list) tuples.
        global_names: Optional global plugin filenames.

    Returns:
        Tuple of (colored tree for terminal, plain tree for markdown).
    """
    from termapy.tree_render import FileTree

    abs_root = Path(config_path).parent.resolve().as_posix() + "/"
    data_dir = Path(config_path).parent

    # Filter to entries that exist on disk; bare files that don't
    # exist are skipped so the tree never claims a missing artifact.
    entries: list[tuple[str, list[str]]] = []
    for name, files in sections:
        if name.endswith("/"):
            entries.append((name, files))
        elif (data_dir / name).exists():
            entries.append((name, []))

    # Pad all file names to the longest one (across both top-level and
    # nested files, plus any global plugins) so the metadata columns
    # line up in the monospace markdown fence.
    name_width = 0
    for name, files in entries:
        if not name.endswith("/"):
            name_width = max(name_width, len(name))
        else:
            for fname in files:
                name_width = max(name_width, len(fname))
    for fname in global_names or []:
        name_width = max(name_width, len(fname))

    color_lines = [f"[cyan]{abs_root}[/]"] + FileTree(
        entries, base_dir=data_dir, color=True,
    ).render()
    plain_lines = [abs_root] + FileTree(
        entries,
        base_dir=data_dir,
        file_dates=True,
        color=False,
        name_width=name_width,
    ).render()

    if global_names:
        global_sections = [(fname, []) for fname in global_names]
        color_lines.append("")
        color_lines.append("[cyan]plugin/ (global)[/]")
        color_lines.extend(FileTree(
            global_sections, base_dir=global_plugins_dir(), color=True,
        ).render())

        plain_lines.append("")
        plain_lines.append("plugin/ (global)")
        plain_lines.extend(FileTree(
            global_sections,
            base_dir=global_plugins_dir(),
            file_dates=True,
            color=False,
            name_width=name_width,
        ).render())

    return "\n".join(color_lines), "\n".join(plain_lines)


def _all_sections(config_path: str) -> list[tuple[str, list[str]]]:
    """Build the full sections list for a config."""
    data_dir = Path(config_path).parent
    config_name = Path(config_path).stem
    sections: list[tuple[str, list[str]]] = [
        (f"{config_name}.cfg", []),
        (f"{config_name}.log", []),
    ]
    for spec in FOLDERS:
        sections.append((f"{spec.name}/", _names(data_dir / spec.name, spec.pattern)))
    return sections


# ── /cfg.info handler ──────────────────────────────────────────────────────────


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Generate project info report and print summary to output.

    Writes ``<config_name>.md`` to the config data directory
    and prints the directory tree to the output window.
    With ``--display``, opens the full report in the system viewer.

    Args:
        ctx: Plugin context.
        args: ``"--display"`` to open report externally.
    """
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")

    try:
        sections = _all_sections(ctx.config_path)
        global_names = _names(global_plugins_dir(), "*.py")
        colored_tree, plain_tree = _build_tree(ctx.config_path, sections, global_names)

        ctx.io.output_markup(colored_tree)

        # Build markdown report
        cfg_display = {k: v for k, v in ctx.cfg.items() if k != "custom_buttons"}
        buttons = ctx.cfg.get("custom_buttons", [])
        active = [b for b in buttons if b.get("enabled")]
        config_name = Path(ctx.config_path).stem

        md_lines: list[str] = [
            f"# Project: {config_name}",
            "",
            "```text",
            plain_tree,
            "```",
            "",
            "## Config",
            "",
            "```json",
            json.dumps(cfg_display, indent=4),
            "```",
            "",
        ]
        if active:
            md_lines.extend(
                [
                    f"## Custom Buttons ({len(active)} active)",
                    "",
                    "```json",
                    json.dumps(active, indent=4),
                    "```",
                    "",
                ]
            )

        data_dir = cfg_data_dir(ctx.config_path)
        report_path = data_dir / f"{config_name}.md"
        report_path.write_text("\n".join(md_lines), encoding="utf-8")

        if ctx.flag("--display"):
            open_with_system(str(report_path))
    except OSError as e:
        # File write / directory scan failure -- most likely real
        # failure mode.  Reports the underlying OS message ("Permission
        # denied", "No such file or directory") to the user.
        return CmdResult.fail(msg=f"Info error: {e}")
    except TypeError as e:
        # json.dumps() hit a non-serializable value in the config.
        # Worth a specific message so the user knows what to fix.
        return CmdResult.fail(msg=f"Config has non-JSON value: {e}")
    return CmdResult.ok(value=str(report_path))


# ── Dynamic long_help ─────────────────────────────────────────────────────────

_CFG_PROSE = """\
Three modes:
  {prefix}cfg                  - TUI: open Cfg picker.  CLI: show this help.
  {prefix}cfg baud_rate        - show current value of 'baud_rate'
  {prefix}cfg baud_rate 115200 - change with confirmation dialog

Type is auto-detected from the existing value (int, float,
bool, string). Bool accepts: true/false, yes/no, on/off, 1/0.
Changes are saved to the JSON config file.

Use {prefix}cfg.dump to print every key/value pair as JSON.
Use {prefix}cfg.auto to set values without confirmation (for scripts)."""


def _cfg_long_help(ctx: PluginContext) -> str:
    """Green status line (active cfg + total count) prepended to the prose."""
    return compose(cfg_status(ctx), _CFG_PROSE)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="cfg",
    args="{key {value}}",
    help="Show or set config values.",
    long_help=_cfg_long_help,
    handler=_handler,
    sub_commands={
        "auto": Command(
            args="<key> <value>",
            help="Set immediately (no confirmation).",
            handler=_handler_auto,
        ),
        "list": Command(
            help="List all config files.",
            handler=_handler_list,
        ),
        "load": Command(
            args="<name>",
            help="Load a different config in this session (hot-swap).",
            handler=_handler_load,
        ),
        "info": Command(
            args="",
            flags={"--display": "Open full report in the system viewer."},
            help="Print project summary (tree + config + buttons).",
            handler=_handler_info,
        ),
        "explore": Command(
            help="Open config directory in file explorer.",
            handler=_handler_explore,
            needs=CapabilitySet(gui_apps=True),
        ),
        "dump": Command(
            help="Print current config as JSON to the terminal.",
            handler=lambda ctx, args: (
                ctx.io.output(json.dumps(dict(ctx.cfg), indent=4)),
                CmdResult.ok(),
            )[-1],
        ),
        "show": Command(
            help="Open the current config file in the system viewer.",
            handler=_handler_show,
            needs=CapabilitySet(gui_apps=True),
        ),
        "help": Command(
            help="Show /cfg help.",
            handler=_handler_help,
        ),
        "icon": Command(
            args="{--force}",
            help="Create a desktop / menu launcher for the current cfg.",
            long_help=_ICON_LONG_HELP,
            handler=_icon_handler,
            flags=_ICON_FLAGS,
            sub_commands={
                "remove": Command(
                    help="Delete the launcher for the current cfg.",
                    handler=_icon_handler_remove,
                ),
                "list": Command(
                    help=(
                        "List every termapy launcher this platform "
                        "can see."
                    ),
                    handler=_icon_handler_list,
                ),
            },
        ),
    },
)
