"""Built-in plugin: show or change config values, project info, file listings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_data_dir, cfg_dir, global_plugins_dir, open_with_system
from termapy.folders import FOLDERS
from termapy.plugins import CmdResult, Command

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
    # /cfg - show all
    if not parts:
        ctx.write(json.dumps(dict(ctx.cfg), indent=4))
        return CmdResult.ok()
    key = parts[0]
    if key not in ctx.cfg:
        return CmdResult.fail(msg=f"Unknown config key: {key}")
    # /cfg key - show value
    if len(parts) == 1:
        val = ctx.cfg[key]
        ctx.result(str(val))
        return CmdResult.ok(value=str(val))
    # /cfg key value - validate and delegate for confirmation
    value_str = parts[1]
    try:
        new_val = ctx.engine.coerce_type(value_str, ctx.cfg[key])
    except (ValueError, TypeError) as e:
        return CmdResult.fail(msg=f"Type error: {e}")
    old_val = ctx.cfg[key]
    if new_val == old_val:
        ctx.output(f"{key} is already {old_val!r}")
        return CmdResult.ok()
    if ctx.engine.save_cfg:
        ctx.engine.save_cfg(key, new_val)
    else:
        ctx.engine.apply_cfg(key, new_val)
    return CmdResult.ok()


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
    return CmdResult.ok()


# ── /cfg.configs handler ──────────────────────────────────────────────────────


def _handler_configs(ctx: PluginContext, args: str) -> CmdResult:
    """List all config files in the config directory.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    d = cfg_dir()
    files = sorted(d.glob("*/*.cfg"))
    if not files:
        ctx.output("  (no config files)")
        return CmdResult.ok()
    for f in files:
        marker = " *" if str(f) == ctx.config_path else ""
        ctx.write(f"  {f.parent.name}/{f.name}{marker}")
    return CmdResult.ok()


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
    return CmdResult.ok()


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

    Args:
        config_path: Path to the config file.
        sections: List of (name, file_list) tuples.
        global_names: Optional global plugin filenames.

    Returns:
        Tuple of (colored tree for terminal, plain tree for markdown).
    """
    Path(config_path).stem
    abs_root = Path(config_path).parent.resolve().as_posix() + "/"

    _DIR = "cyan"
    _TREE = "dim"
    _FILE = "blue"

    plain_lines: list[str] = [abs_root]
    color_lines: list[str] = [f"[{_DIR}]{abs_root}[/]"]

    data_dir = Path(config_path).parent

    entries: list[tuple[str, list[str]]] = []
    for name, files in sections:
        if name.endswith("/"):
            entries.append((name, files))
        elif (data_dir / name).exists():
            entries.append((name, []))

    for i, (name, files) in enumerate(entries):
        is_last_entry = i == len(entries) - 1
        connector = "└── " if is_last_entry else "├── "
        child_prefix = "    " if is_last_entry else "│   "

        if not name.endswith("/"):
            plain_lines.append(f"{connector}{name}")
            color_lines.append(f"[{_TREE}]{connector}[/][{_FILE}]{name}[/]")
        elif files:
            plain_lines.append(f"{connector}{name}")
            color_lines.append(f"[{_TREE}]{connector}[/][{_DIR}]{name}[/]")
            for j, fname in enumerate(files):
                child_conn = "└── " if j == len(files) - 1 else "├── "
                plain_lines.append(f"{child_prefix}{child_conn}{fname}")
                color_lines.append(
                    f"[{_TREE}]{child_prefix}{child_conn}[/][{_FILE}]{fname}[/]"
                )
        else:
            plain_lines.append(f"{connector}{name}")
            color_lines.append(f"[{_TREE}]{connector}[/][{_DIR}]{name}[/]")

    if global_names:
        plain_lines.append("")
        plain_lines.append("plugin/ (global)")
        color_lines.append("")
        color_lines.append(f"[{_DIR}]plugin/ (global)[/]")
        for i, fname in enumerate(global_names):
            connector = "└── " if i == len(global_names) - 1 else "├── "
            plain_lines.append(f"{connector}{fname}")
            color_lines.append(f"[{_TREE}]{connector}[/][{_FILE}]{fname}[/]")

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

        ctx.write_markup(colored_tree)

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

        if "--display" in args.lower():
            open_with_system(str(report_path))
    except Exception as e:
        return CmdResult.fail(msg=f"Info error: {e}")
    return CmdResult.ok()


# ── Per-folder subcommand factories ──────────────────────────────────────────


def _make_folder_handler(folder: str, pattern: str):
    """Create a handler that lists files in a single folder."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        if not ctx.config_path:
            return CmdResult.fail(msg="No config loaded.")
        data_dir = Path(ctx.config_path).parent
        files = _names(data_dir / folder, pattern)
        if not files:
            ctx.output(f"  {folder}/ (empty)")
            return CmdResult.ok()
        ctx.write(f"  {folder}/")
        for fname in files:
            ctx.write(f"    {fname}")
        return CmdResult.ok()

    return handler


def _make_explore_handler(folder: str):
    """Create a handler that opens a folder in the system file explorer."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        if not ctx.config_path:
            return CmdResult.fail(msg="No config loaded.")
        path = Path(ctx.config_path).parent / folder
        open_with_system(str(path))
        return CmdResult.ok()

    return handler


def _make_clear_handler(folder: str, pattern: str):
    """Create a handler that deletes all files in a folder."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        if not ctx.config_path:
            return CmdResult.fail(msg="No config loaded.")
        data_dir = Path(ctx.config_path).parent / folder
        if pattern == "*":
            files = [f for f in data_dir.glob(pattern) if f.is_file()]
        else:
            files = list(data_dir.glob(pattern))
        if not files:
            ctx.output(f"  {folder}/ is already empty.")
            return CmdResult.ok()
        for f in files:
            f.unlink()
        ctx.write(f"  Deleted {len(files)} file(s) from {folder}/.")
        return CmdResult.ok()

    return handler


def _make_show_handler(folder: str, pattern: str):
    """Create a handler that opens the newest file in system viewer."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        if not ctx.config_path:
            return CmdResult.fail(msg="No config loaded.")
        data_dir = Path(ctx.config_path).parent / folder
        if not data_dir.exists():
            ctx.output(f"  {folder}/ is empty.")
            return CmdResult.ok()
        if pattern == "*":
            files = [f for f in data_dir.glob(pattern) if f.is_file()]
        else:
            files = list(data_dir.glob(pattern))
        if not files:
            ctx.output(f"  {folder}/ is empty.")
            return CmdResult.ok()
        newest = max(files, key=lambda f: f.stat().st_mtime)
        ctx.write(f"Opening {newest.name}")
        open_with_system(str(newest))
        return CmdResult.ok()

    return handler


def _make_dump_handler(folder: str, pattern: str):
    """Create a handler that prints the newest (or named) file to the terminal."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        if not ctx.config_path:
            return CmdResult.fail(msg="No config loaded.")
        data_dir = Path(ctx.config_path).parent / folder
        name = args.strip()
        if name:
            # Named file
            path = data_dir / name
            if not path.exists():
                return CmdResult.fail(msg=f"File not found: {name}")
        else:
            # Newest file
            if not data_dir.exists():
                ctx.output(f"  {folder}/ is empty.")
                return CmdResult.ok()
            if pattern == "*":
                files = [f for f in data_dir.glob(pattern) if f.is_file()]
            else:
                files = list(data_dir.glob(pattern))
            if not files:
                ctx.output(f"  {folder}/ is empty.")
                return CmdResult.ok()
            path = max(files, key=lambda f: f.stat().st_mtime)
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                ctx.output(line)
        except OSError as e:
            return CmdResult.fail(msg=f"Read error: {e}")
        return CmdResult.ok()

    return handler


# ── Build per-folder subcommand dicts ────────────────────────────────────────


def _build_folder_subs() -> dict[str, Command]:
    """Build top-level subcommands for each folder in FOLDERS."""
    subs = {}
    for spec in FOLDERS:
        nested: dict[str, Command] = {
            "explore": Command(
                help=f"Open {spec.name}/ in file explorer.",
                handler=_make_explore_handler(spec.name),
            ),
        }
        if spec.showable:
            nested["show"] = Command(
                help=f"Open newest file in {spec.name}/.",
                handler=_make_show_handler(spec.name, spec.pattern),
            )
        if spec.dumpable:
            nested["dump"] = Command(
                args="{filename}",
                help=f"Print newest (or named) file from {spec.name}/ to terminal.",
                handler=_make_dump_handler(spec.name, spec.pattern),
            )
        if spec.clearable:
            nested["clear"] = Command(
                help=f"Delete all files in {spec.name}/.",
                handler=_make_clear_handler(spec.name, spec.pattern),
            )
        subs[spec.name] = Command(
            help=f"List files in {spec.name}/.",
            handler=_make_folder_handler(spec.name, spec.pattern),
            sub_commands=nested,
        )
    return subs


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="cfg",
    args="{key {value}}",
    help="Show or change config values.",
    long_help="""\
Three modes:
  /cfg              - show all config key/value pairs
  /cfg baud_rate    - show current value of 'baud_rate'
  /cfg baud_rate 115200 - change with confirmation dialog

Type is auto-detected from the existing value (int, float,
bool, string). Bool accepts: true/false, yes/no, on/off, 1/0.
Changes are saved to the JSON config file.

Use /cfg.auto to set values without confirmation (for scripts).""",
    handler=_handler,
    sub_commands={
        "auto": Command(
            args="<key> <value>",
            help="Set immediately (no confirmation).",
            handler=_handler_auto,
        ),
        "configs": Command(
            help="List all config files.",
            handler=_handler_configs,
        ),
        "info": Command(
            args="{--display}",
            help="Show project summary. --display opens full report.",
            handler=_handler_info,
        ),
        "explore": Command(
            help="Open config directory in file explorer.",
            handler=_handler_explore,
        ),
        "dump": Command(
            help="Print current config as JSON to the terminal.",
            handler=lambda ctx, args: (
                ctx.output(json.dumps(dict(ctx.cfg), indent=4)),
                CmdResult.ok(),
            )[-1],
        ),
        **_build_folder_subs(),
    },
)
