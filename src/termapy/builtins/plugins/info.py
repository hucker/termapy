"""Built-in plugin: project info report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_data_dir, global_plugins_dir, open_with_system
from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _names(directory: Path, pattern: str) -> list[str]:
    """Return sorted filenames matching pattern in directory."""
    if pattern == "*":
        return sorted(f.name for f in directory.glob(pattern) if f.is_file())
    return sorted(f.name for f in directory.glob(pattern))


def _build_info_tree(config_path: str, cfg: dict) -> tuple[str, str]:
    """Generate a directory-tree view of the project.

    Args:
        config_path: Path to the config file.
        cfg: Loaded config dict.

    Returns:
        Tuple of (colored tree for terminal, markdown report).
    """
    config_p = Path(config_path)
    data_dir = config_p.parent if config_p.is_absolute() else config_p.resolve().parent
    config_name = config_p.stem

    # Gather file lists
    sections: list[tuple[str, list[str]]] = [
        (f"{config_name}.cfg", []),
        (f"{config_name}.log", []),
        ("scripts/", _names(data_dir / "scripts", "*.run")),
        ("proto/", _names(data_dir / "proto", "*.pro")),
        ("plugins/", _names(data_dir / "plugins", "*.py")),
        ("ss/", _names(data_dir / "ss", "*")),
        ("viz/", _names(data_dir / "viz", "*.py")),
    ]

    # Global plugins
    global_names = _names(global_plugins_dir(), "*.py")

    # Colors for Rich markup (terminal output)
    _DIR = "cyan"
    _TREE = "dim"
    _FILE = "blue"

    # Build tree — plain lines for markdown, colored for terminal
    plain_lines: list[str] = [f"{config_name}/"]
    color_lines: list[str] = [f"[{_DIR}]{config_name}/[/]"]

    # Filter to non-empty entries and standalone files
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
            # Standalone file (config json, log)
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
            # Empty directory
            plain_lines.append(f"{connector}{name}")
            color_lines.append(f"[{_TREE}]{connector}[/][{_DIR}]{name}[/]")

    # Global plugins section (separate root)
    if global_names:
        plain_lines.append("")
        plain_lines.append("plugins/ (global)")
        color_lines.append("")
        color_lines.append(f"[{_DIR}]plugins/ (global)[/]")
        for i, fname in enumerate(global_names):
            connector = "└── " if i == len(global_names) - 1 else "├── "
            plain_lines.append(f"{connector}{fname}")
            color_lines.append(f"[{_TREE}]{connector}[/][{_FILE}]{fname}[/]")

    tree = "\n".join(plain_lines)
    colored_tree = "\n".join(color_lines)

    # Build markdown report with tree + config JSON
    cfg_display = {k: v for k, v in cfg.items() if k != "custom_buttons"}
    buttons = cfg.get("custom_buttons", [])
    active = [b for b in buttons if b.get("enabled")]

    md_lines: list[str] = [
        f"# Project: {config_name}",
        "",
        "```text",
        tree,
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

    return colored_tree, "\n".join(md_lines)


def _handler(ctx: PluginContext, args: str) -> None:
    """Generate project info report and print summary to output.

    Writes ``<config_name>.md`` to the config data directory
    and prints non-zero file counts to the output window.
    With ``--display``, opens the full report in the system viewer.

    Args:
        ctx: Plugin context.
        args: ``"--display"`` to open report externally.
    """
    if not ctx.config_path:
        ctx.write("No config loaded.", "red")
        return

    try:
        tree, markdown = _build_info_tree(ctx.config_path, ctx.cfg)
        data_dir = cfg_data_dir(ctx.config_path)
        config_name = Path(ctx.config_path).stem
        report_path = data_dir / f"{config_name}.md"
        report_path.write_text(markdown, encoding="utf-8")

        ctx.write_markup(tree)

        if "--display" in args.lower():
            open_with_system(str(report_path))
    except Exception as e:
        ctx.write(f"Info error: {e}", "red")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="info",
    args="{--display}",
    help="Show project summary. --display opens full report in system viewer.",
    handler=_handler,
)
