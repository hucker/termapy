"""Built-in plugin: show or change config values, project info, file listings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_data_dir, cfg_dir, global_plugins_dir, open_with_system
from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── /cfg handler ───────────────────────────────────────────────────────────────


def _handler(ctx: PluginContext, args: str) -> None:
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
    # /cfg — show all
    if not parts:
        for k, v in ctx.cfg.items():
            ctx.write(f"  {k}: {v!r}")
        return
    key = parts[0]
    if key not in ctx.cfg:
        ctx.write(f"Unknown config key: {key}", "red")
        return
    # /cfg key — show value
    if len(parts) == 1:
        ctx.write(f"  {key}: {ctx.cfg[key]!r}")
        return
    # /cfg key value — validate and delegate for confirmation
    value_str = parts[1]
    try:
        new_val = ctx.engine.coerce_type(value_str, ctx.cfg[key])
    except (ValueError, TypeError) as e:
        ctx.write(f"Type error: {e}", "red")
        return
    old_val = ctx.cfg[key]
    if new_val == old_val:
        ctx.write(f"{key} is already {old_val!r}", "dim")
        return
    if ctx.engine.save_cfg:
        ctx.engine.save_cfg(key, new_val)
    else:
        ctx.engine.apply_cfg(key, new_val)


# ── /cfg.auto handler ─────────────────────────────────────────────────────────


def _handler_auto(ctx: PluginContext, args: str) -> None:
    """Set a config key immediately without confirmation dialog.

    Args:
        ctx: Plugin context for config access and output.
        args: ``"key value"`` string (both required).
    """
    parts = args.strip().split(None, 1)
    if not parts or len(parts) < 2:
        ctx.write("Usage: /cfg.auto <key> <value>", "red")
        return
    key, value_str = parts[0], parts[1]
    if key not in ctx.cfg:
        ctx.write(f"Unknown config key: {key}", "red")
        return
    try:
        new_val = ctx.engine.coerce_type(value_str, ctx.cfg[key])
    except (ValueError, TypeError) as e:
        ctx.write(f"Type error: {e}", "red")
        return
    ctx.engine.apply_cfg(key, new_val)


# ── /cfg.configs handler ──────────────────────────────────────────────────────


def _handler_configs(ctx: PluginContext, args: str) -> None:
    """List all config files in the config directory.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    d = cfg_dir()
    files = sorted(d.glob("*/*.cfg"))
    if not files:
        ctx.write("  (no config files)", "dim")
        return
    for f in files:
        marker = " *" if str(f) == ctx.config_path else ""
        ctx.write(f"  {f.parent.name}/{f.name}{marker}")


# ── /cfg.explore handler ──────────────────────────────────────────────────────


def _handler_explore(ctx: PluginContext, args: str) -> None:
    """Open the config data directory in the system file explorer.

    Args:
        ctx: Plugin context.
        args: Unused.
    """
    if not ctx.config_path:
        ctx.write("No config loaded.", "red")
        return
    data_dir = Path(ctx.config_path).parent
    open_with_system(str(data_dir))


# ── Tree-building helpers (shared by info and folder listings) ────────────────


# Directory sections: (folder_name, glob_pattern)
_SECTIONS = [
    ("scripts", "*.run"),
    ("proto", "*.pro"),
    ("plugins", "*.py"),
    ("ss", "*"),
    ("viz", "*.py"),
    ("cap", "*"),
]

# Folders that support .clear (generated output, not user-authored)
_CLEARABLE = {"ss", "cap"}


def _names(directory: Path, pattern: str) -> list[str]:
    """Return sorted filenames matching pattern in directory."""
    if pattern == "*":
        return sorted(f.name for f in directory.glob(pattern) if f.is_file())
    return sorted(f.name for f in directory.glob(pattern))


def _build_tree(config_path: str, sections: list[tuple[str, list[str]]],
                global_names: list[str] | None = None) -> tuple[str, str]:
    """Build plain and Rich-colored directory trees.

    Args:
        config_path: Path to the config file.
        sections: List of (name, file_list) tuples.
        global_names: Optional global plugin filenames.

    Returns:
        Tuple of (colored tree for terminal, plain tree for markdown).
    """
    config_name = Path(config_path).stem

    _DIR = "cyan"
    _TREE = "dim"
    _FILE = "blue"

    plain_lines: list[str] = [f"{config_name}/"]
    color_lines: list[str] = [f"[{_DIR}]{config_name}/[/]"]

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
        plain_lines.append("plugins/ (global)")
        color_lines.append("")
        color_lines.append(f"[{_DIR}]plugins/ (global)[/]")
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
    for folder, pattern in _SECTIONS:
        sections.append((f"{folder}/", _names(data_dir / folder, pattern)))
    return sections


# ── /cfg.info handler ──────────────────────────────────────────────────────────


def _handler_info(ctx: PluginContext, args: str) -> None:
    """Generate project info report and print summary to output.

    Writes ``<config_name>.md`` to the config data directory
    and prints the directory tree to the output window.
    With ``--display``, opens the full report in the system viewer.

    Args:
        ctx: Plugin context.
        args: ``"--display"`` to open report externally.
    """
    if not ctx.config_path:
        ctx.write("No config loaded.", "red")
        return

    try:
        sections = _all_sections(ctx.config_path)
        global_names = _names(global_plugins_dir(), "*.py")
        colored_tree, plain_tree = _build_tree(
            ctx.config_path, sections, global_names
        )

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
            md_lines.extend([
                f"## Custom Buttons ({len(active)} active)",
                "",
                "```json",
                json.dumps(active, indent=4),
                "```",
                "",
            ])

        data_dir = cfg_data_dir(ctx.config_path)
        report_path = data_dir / f"{config_name}.md"
        report_path.write_text("\n".join(md_lines), encoding="utf-8")

        ctx.write_markup(colored_tree)

        if "--display" in args.lower():
            open_with_system(str(report_path))
    except Exception as e:
        ctx.write(f"Info error: {e}", "red")


# ── Per-folder subcommand factories ──────────────────────────────────────────


def _make_folder_handler(folder: str, pattern: str):
    """Create a handler that lists files in a single folder."""
    def handler(ctx: PluginContext, args: str) -> None:
        if not ctx.config_path:
            ctx.write("No config loaded.", "red")
            return
        data_dir = Path(ctx.config_path).parent
        files = _names(data_dir / folder, pattern)
        if not files:
            ctx.write(f"  {folder}/ (empty)", "dim")
            return
        ctx.write(f"  {folder}/")
        for fname in files:
            ctx.write(f"    {fname}")
    return handler


def _make_explore_handler(folder: str):
    """Create a handler that opens a folder in the system file explorer."""
    def handler(ctx: PluginContext, args: str) -> None:
        if not ctx.config_path:
            ctx.write("No config loaded.", "red")
            return
        path = Path(ctx.config_path).parent / folder
        open_with_system(str(path))
    return handler


def _make_clear_handler(folder: str, pattern: str):
    """Create a handler that deletes all files in a folder."""
    def handler(ctx: PluginContext, args: str) -> None:
        if not ctx.config_path:
            ctx.write("No config loaded.", "red")
            return
        data_dir = Path(ctx.config_path).parent / folder
        if pattern == "*":
            files = [f for f in data_dir.glob(pattern) if f.is_file()]
        else:
            files = list(data_dir.glob(pattern))
        if not files:
            ctx.write(f"  {folder}/ is already empty.", "dim")
            return
        for f in files:
            f.unlink()
        ctx.write(f"  Deleted {len(files)} file(s) from {folder}/.")
    return handler


# ── Build per-folder subcommand dicts ────────────────────────────────────────


def _build_folder_subs() -> dict[str, Command]:
    """Build top-level subcommands for each folder in _SECTIONS."""
    subs = {}
    for folder, pattern in _SECTIONS:
        nested: dict[str, Command] = {
            "explore": Command(
                help=f"Open {folder}/ in file explorer.",
                handler=_make_explore_handler(folder),
            ),
        }
        if folder in _CLEARABLE:
            nested["clear"] = Command(
                help=f"Delete all files in {folder}/.",
                handler=_make_clear_handler(folder, pattern),
            )
        subs[folder] = Command(
            help=f"List files in {folder}/.",
            handler=_make_folder_handler(folder, pattern),
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
  /cfg              — show all config key/value pairs
  /cfg baud_rate    — show current value of 'baud_rate'
  /cfg baud_rate 115200 — change with confirmation dialog

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
        **_build_folder_subs(),
    },
)
