"""Built-in plugin: open project files in the system editor.

Provides a uniform /edit tree for all file types: run, proto,
plugin, config, log, and info report. Each folder type gets the
same subcommands: edit by name, list files, open folder.

In the TUI, hooks override /edit, /edit.cfg, /edit.run, and
/edit.proto to use Textual modal editors. Everything else (list,
explore, log, info) works the same in both frontends via ctx.open_file().
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_log_path, open_with_system
from termapy.folders import EXT_TO_FOLDER
from termapy.plugins import Command
from termapy.scripting import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── File resolution ──────────────────────────────────────────────────────────


def _resolve_file(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a filename to a project file path.

    Checks run/, proto/, plugin/ dirs by prefix or extension.
    """
    dir_map = {
        "run": (ctx.scripts_dir, ".run"),
        "proto": (ctx.proto_dir, ".pro"),
    }
    parts = Path(name).parts
    if len(parts) == 2:
        entry = dir_map.get(parts[0].lower())
        if entry:
            path = entry[0] / parts[1]
            return path if path.exists() else None

    ext = Path(name).suffix.lower()
    _folder_dirs = {
        "run": ctx.scripts_dir,
        "proto": ctx.proto_dir,
        "plugin": ctx.scripts_dir.parent / "plugin",
    }
    base = _folder_dirs.get(EXT_TO_FOLDER.get(ext, ""))
    if base:
        path = base / name
        return path if path.exists() else None
    return None


# ── Handlers ─────────────────────────────────────────────────────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    name = args.strip()
    if not name:
        return CmdResult.fail(msg="Usage: /edit <filename>")
    path = _resolve_file(ctx, name)
    if path is None:
        return CmdResult.fail(msg=f"File not found: {name}")
    ctx.open_file(path)
    return CmdResult.ok()


def _handler_cfg(ctx: PluginContext, args: str) -> CmdResult:
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    ctx.open_file(Path(ctx.config_path))
    return CmdResult.ok()


def _handler_log(ctx: PluginContext, args: str) -> CmdResult:
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    configured = ctx.cfg.get("log_file", "")
    if configured:
        ctx.open_file(Path(configured).resolve())
    else:
        ctx.open_file(Path(cfg_log_path(ctx.config_path)))
    return CmdResult.ok()


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    stem = Path(ctx.config_path).stem
    path = Path(ctx.config_path).parent / f"{stem}.md"
    if path.exists():
        ctx.open_file(path)
        return CmdResult.ok()
    else:
        return CmdResult.fail(msg="No info report yet. Run /cfg.info first.")


# ── Folder subcommand factories ──────────────────────────────────────────────


def _make_edit_handler(get_dir, ext):
    """Create a handler that opens a file by name from a folder."""
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        name = args.strip()
        if not name:
            return CmdResult.fail(msg=f"Usage: /edit.<folder> <filename>")
        folder = get_dir(ctx)
        if not name.endswith(ext):
            name += ext
        path = folder / name
        if not path.exists():
            return CmdResult.fail(msg=f"File not found: {name}")
        ctx.open_file(path)
        return CmdResult.ok()
    return handler


def _make_list_handler(get_dir, pattern):
    """Create a handler that lists files in a folder."""
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        folder = get_dir(ctx)
        if not folder.is_dir():
            ctx.write(f"  (no directory)", "dim")
            return CmdResult.ok()
        files = sorted(folder.glob(pattern))
        if not files:
            ctx.write(f"  (empty)", "dim")
            return CmdResult.ok()
        for f in files:
            ctx.write(f"  {f.name}")
        return CmdResult.ok()
    return handler


def _make_explore_handler(get_dir):
    """Create a handler that opens a folder in the system file explorer."""
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        folder = get_dir(ctx)
        folder.mkdir(parents=True, exist_ok=True)
        ctx.open_file(folder)
        return CmdResult.ok()
    return handler


def _build_folder_sub(get_dir, ext, pattern):
    """Build a folder subcommand with edit, list, and explore."""
    return Command(
        args="{filename}",
        help=f"Open a {ext} file in the system editor.",
        handler=_make_edit_handler(get_dir, ext),
        sub_commands={
            "list": Command(
                help=f"List {ext} files.",
                handler=_make_list_handler(get_dir, pattern),
            ),
            "explore": Command(
                help=f"Open folder in file explorer.",
                handler=_make_explore_handler(get_dir),
            ),
        },
    )


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    name="edit",
    args="<filename>",
    help="Open a project file in the system editor.",
    handler=_handler_root,
    sub_commands={
        "run": _build_folder_sub(
            lambda ctx: ctx.scripts_dir, ".run", "*.run",
        ),
        "proto": _build_folder_sub(
            lambda ctx: ctx.proto_dir, ".pro", "*.pro",
        ),
        "plugin": _build_folder_sub(
            lambda ctx: Path(ctx.config_path).parent / "plugin" if ctx.config_path else Path("."),
            ".py", "*.py",
        ),
        "cfg": Command(
            help="Open the config file in the system editor.",
            handler=_handler_cfg,
        ),
        "log": Command(
            help="Open the session log in the system viewer.",
            handler=_handler_log,
        ),
        "info": Command(
            help="Open the info report in the system viewer.",
            handler=_handler_info,
        ),
    },
)
