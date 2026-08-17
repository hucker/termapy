"""Built-in plugin: show or change config values, project info, file listings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.builtins.commands._cfg_icon import (
    _FLAGS as _ICON_FLAGS,
    _LONG_HELP as _ICON_LONG_HELP,
    _handler as _icon_handler,
    _handler_list as _icon_handler_list,
    _handler_remove as _icon_handler_remove,
)
from termapy.config import cfg_data_dir, cfg_dir, global_plugins_dir, open_with_system
from termapy.folders import FOLDERS
from termapy.help_dynamic import cfg_status, compose
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
from termapy.plugins.params import ParamSpec
from termapy.scripting import coerce_to_type

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
    # Hand-rolled (not declarative params, unlike the /cfg.auto sibling): this
    # is a variable-arity mode dispatch -- bare /cfg opens the picker, `/cfg key`
    # queries, `/cfg key value` sets -- which params' fixed synopsis can't
    # express (see CLAUDE.md "Declarative Command Parameters").  /cfg.load is
    # likewise a trivial single positional kept hand-rolled.
    parts = args.strip().split(None, 1)
    # Bare /cfg -- TUI opens the Cfg picker dialog (matches the
    # title-bar button); CLI shows /help cfg.  Use /cfg.dump to print
    # the loaded config as JSON.
    if not parts:
        if ctx.internal.open_picker is not None:
            return ctx.internal.open_picker("cfg")
        return _handler_help(ctx, args)
    key = parts[0]
    container = _find_cfg_container(ctx.cfg, key)
    if container is None:
        return CmdResult.fail(msg=f"Unknown config key: {key}")
    # /cfg key - show value
    if len(parts) == 1:
        val = container[key]
        ctx.io.result(str(val))
        return CmdResult.ok(value=str(val))
    # /cfg key value - validate and delegate for confirmation
    value_str = parts[1]
    try:
        new_val = coerce_to_type(value_str, container[key])
    except (ValueError, TypeError) as e:
        return CmdResult.fail(msg=f"Type error: {e}")
    old_val = container[key]
    if new_val == old_val:
        ctx.io.output(f"{key} is already {old_val!r}")
        return CmdResult.ok(value=str(old_val))
    if ctx.internal.confirm_save_cfg:
        ctx.internal.confirm_save_cfg(key, new_val)
    else:
        ctx.internal.apply_cfg(key, new_val)
    return CmdResult.ok(value=str(new_val))


def _find_cfg_container(cfg, key: str):
    """Return the dict that owns ``key`` (top-level or under cfg["serial"]).

    Lets ``/cfg port`` keep working post-v22 even though port now lives
    under ``cfg["serial"]``.  ``None`` means the key isn't known.  Only
    looks one level deep (top + serial sub-dict); deeper nesting would
    require dotted-path support.

    ``cfg`` is typed loosely (``MappingProxyType | dict`` in callers)
    so the helper just relies on duck-typed ``in`` / ``get``.
    """
    if key in cfg:
        return cfg
    serial = cfg.get("serial", {})
    if isinstance(serial, dict) and key in serial:
        return serial
    return None


# ── /cfg.auto handler ─────────────────────────────────────────────────────────

# Cfg keys whose value is a filesystem path.  Setting one to an
# out-of-sandbox path turns a later read/load into an arbitrary-file
# primitive (e.g. log_file -> /log.dump), so they are contained under
# the MCP sandbox like any other caller-supplied path.
_PATH_VALUED_CFG_KEYS = frozenset({"log_file", "profile_path"})


def _handler_auto(ctx: PluginContext, args: str) -> CmdResult:
    """Set a config key immediately without confirmation dialog.

    Parameters arrive via ``ctx.arg`` (see ``params``); ``value`` is a
    positional-rest so a config value may contain spaces.
    """
    key = ctx.arg("key")
    value_str = ctx.arg("value")
    container = _find_cfg_container(ctx.cfg, key)
    if container is None:
        return CmdResult.fail(msg=f"Unknown config key: {key}")
    if key in _PATH_VALUED_CFG_KEYS and value_str:
        ctx.fs.guard_external_path(value_str, f"cfg {key}")
    try:
        new_val = coerce_to_type(value_str, container[key])
    except (ValueError, TypeError) as e:
        return CmdResult.fail(msg=f"Type error: {e}")
    ctx.internal.apply_cfg(key, new_val)
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
    for file in files:
        marker = " *" if str(file) == ctx.config_path else ""
        ctx.io.output(f"  {file.parent.name}/{file.name}{marker}")
        names.append(f"{file.parent.name}/{file.name}")
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
        ctx: Plugin context (uses ``ctx.internal.load_config``).
        args: The config name / path to load.
    """
    name = args.strip()
    if not name:
        raise UsageError()
    # A bare name / in-sandbox relative path (the MCP hot-swap case) is
    # allowed; an absolute path or ``..`` -- which load_config would read
    # AND rewrite with defaults, clobbering any JSON file -- is refused
    # under the MCP sandbox.
    ctx.fs.guard_external_path(name, "Config")
    return ctx.internal.load_config(name)


# ── /cfg.explore handler ──────────────────────────────────────────────────────


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print the config as JSON, or a cfg-folder file, to the terminal.

    Bare ``/cfg.dump`` prints the loaded config (unchanged behavior).
    With a name, prints that file from the active config folder --
    following the folder-verb vocabulary where ``.dump`` means
    "print to terminal" (``.show`` means system viewer).

    Names resolve RELATIVE TO THE CFG ROOT ONLY (subfolders allowed):
    absolute paths and ``..`` traversal are refused for every frontend,
    so this can never read outside the config folder.

    Lets `.quiet` mode capture the output for scripting
    (e.g. ``$(JSON) <- /cfg.dump.quiet``).
    """
    name = args.strip()
    if not name:
        text = json.dumps(dict(ctx.cfg), indent=4)
        ctx.io.output(text)
        return CmdResult.ok(value=text)
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    root = Path(ctx.config_path).parent.resolve()
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        return CmdResult.fail(
            msg=f"Path must be relative to the config folder: {name}"
        )
    path = (root / rel).resolve()
    if not path.is_relative_to(root):  # symlink-escape belt
        return CmdResult.fail(
            msg=f"Path must be relative to the config folder: {name}"
        )
    if not path.is_file():
        return CmdResult.fail(msg=f"File not found: {name}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    for line in text.splitlines():
        ctx.io.output(line)
    return CmdResult.ok(value=text)


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
    return CmdResult.ok(value=data_dir)


def _handler_show(ctx: PluginContext, args: str) -> CmdResult:
    """Open the current config file in the system viewer.

    Mirrors ``/edit.cfg``; named ``cfg.show`` for symmetry with the
    folder ``.show`` family on /run, /proto, /ss, etc.
    """
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    ctx.fs.open_file(Path(ctx.config_path))
    return CmdResult.ok(value=Path(ctx.config_path))


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
        active = [button for button in buttons if button.get("enabled")]
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
    return CmdResult.ok(value=report_path)


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
            help="Set immediately (no confirmation).",
            handler=_handler_auto,
            params=[
                ParamSpec("key", "str", positional=True, required=True,
                          help="config key to set"),
                ParamSpec("value", "str", positional=True, rest=True, required=True,
                          help="new value (may contain spaces)"),
            ],
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
            args="{name}",
            help="Print the config (JSON) or a cfg-folder file to the terminal.",
            handler=_handler_dump,
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
        # Desktop/menu launchers only make sense on a graphical host, and
        # creating one spawns a subprocess (PowerShell on Windows).  Gate
        # the whole family on gui_apps -- like /cfg.show / /cfg.explore --
        # so a headless MCP peer can't write a launcher or spawn a process.
        # (needs is per-command: subcommands don't inherit the parent's.)
        "icon": Command(
            args="{--force}",
            help="Create a desktop / menu launcher for the current cfg.",
            long_help=_ICON_LONG_HELP,
            handler=_icon_handler,
            flags=_ICON_FLAGS,
            needs=CapabilitySet(gui_apps=True),
            sub_commands={
                "remove": Command(
                    help="Delete the launcher for the current cfg.",
                    handler=_icon_handler_remove,
                    needs=CapabilitySet(gui_apps=True),
                ),
                "list": Command(
                    help=(
                        "List every termapy launcher this platform "
                        "can see."
                    ),
                    handler=_icon_handler_list,
                    needs=CapabilitySet(gui_apps=True),
                ),
            },
        ),
    },
)
