"""Built-in plugin: open project files in the system editor.

Provides a uniform /edit tree for all file types: run, proto,
plugin, config, log, and info report. Each folder type gets the
same subcommands: edit by name, list files, open folder.

In the TUI, hooks override /edit, /edit.cfg, /edit.run, and
/edit.proto to use Textual modal editors. Everything else (list,
explore, log, info) works the same in both frontends via ctx.fs.open_file().
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from termapy.config import cfg_log_path
from termapy.folders import EXT_TO_FOLDER
from termapy.help_dynamic import folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── File resolution ──────────────────────────────────────────────────────────


def _resolve_file(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a filename to a project file path.

    Checks run/, proto/, plugin/ dirs by prefix or extension.
    """
    dir_map = {
        "run": (ctx.fs.scripts_dir, ".run"),
        "proto": (ctx.fs.proto_dir, ".pro"),
    }
    parts = Path(name).parts
    if len(parts) == 2:
        entry = dir_map.get(parts[0].lower())
        if entry:
            path = entry[0] / parts[1]
            return path if path.exists() else None

    ext = Path(name).suffix.lower()
    _folder_dirs = {
        "run": ctx.fs.scripts_dir,
        "proto": ctx.fs.proto_dir,
        "plugin": ctx.fs.scripts_dir.parent / "plugin",
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
        raise UsageError()
    path = _resolve_file(ctx, name)
    if path is None:
        return CmdResult.fail(msg=f"File not found: {name}")
    ctx.fs.open_file(path)
    return CmdResult.ok(value=path)


def _handler_cfg(ctx: PluginContext, args: str) -> CmdResult:
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    ctx.fs.open_file(Path(ctx.config_path))
    return CmdResult.ok(value=Path(ctx.config_path))


def _handler_log(ctx: PluginContext, args: str) -> CmdResult:
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    configured = ctx.cfg.get("log_file", "")
    if configured:
        path = Path(configured).resolve()
    else:
        path = Path(cfg_log_path(ctx.config_path))
    ctx.fs.open_file(path)
    return CmdResult.ok(value=path)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    stem = Path(ctx.config_path).stem
    path = Path(ctx.config_path).parent / f"{stem}.md"
    if path.exists():
        ctx.fs.open_file(path)
        return CmdResult.ok(value=path)
    else:
        return CmdResult.fail(msg="No info report yet. Run /cfg.info first.")


# ── Folder subcommand factories ──────────────────────────────────────────────


def _make_edit_handler(get_dir, ext, pattern):
    """Create a handler that opens a file by name, or lists files if no name given.

    Returns the opened file path as ``CmdResult.value`` (or the list of
    file names if no name was given and we delegate to the list handler).
    """
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        name = args.strip()
        if not name:
            return _make_list_handler(get_dir, pattern)(ctx, args)
        folder = get_dir(ctx)
        if not name.endswith(ext):
            name += ext
        path = folder / name
        if not path.exists():
            return CmdResult.fail(msg=f"File not found: {name}")
        ctx.fs.open_file(path)
        return CmdResult.ok(value=path)
    return handler


def _make_list_handler(get_dir, pattern):
    """Create a handler that lists files in a folder.

    Returns the newline-joined list of file names as ``CmdResult.value``
    so scripts can capture it; empty string for empty/missing folders.
    """
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        folder = get_dir(ctx)
        if not folder.is_dir():
            ctx.io.output("  (no directory)")
            return CmdResult.ok(value="")
        files = sorted(folder.glob(pattern))
        if not files:
            ctx.io.output("  (empty)")
            return CmdResult.ok(value="")
        ctx.io.output("  Available file(s):")
        names = []
        for f in files:
            ctx.io.output(f"    {f.name}")
            names.append(f.name)
        return CmdResult.ok(value="\n".join(names))
    return handler


def _make_explore_handler(get_dir):
    """Create a handler that opens a folder in the system file explorer.

    Returns the opened folder path as ``CmdResult.value``.
    """
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        folder = get_dir(ctx)
        folder.mkdir(parents=True, exist_ok=True)
        ctx.fs.open_file(folder)
        return CmdResult.ok(value=folder)
    return handler


def _build_folder_sub(get_dir, ext, pattern, kind=None, noun=None):
    """Build a folder subcommand with edit, list, and explore.

    ``kind`` is the ``folders.py`` folder name (``run``, ``proto``, etc.).
    When supplied, every subcommand gets a dynamic ``long_help`` showing
    the current file count in that folder, so ``/help edit.run`` prints
    "42 scripts in run/" in green.
    """
    long_help: str | Callable = ""
    if kind is not None:
        def long_help(ctx):
            return folder_line(ctx, kind, noun=noun)
    return Command(
        args="{filename}",
        help=f"Open a {ext} file in the system editor.",
        long_help=long_help,
        handler=_make_edit_handler(get_dir, ext, pattern),
        needs=CapabilitySet(gui_apps=True),
        sub_commands={
            "list": Command(
                help=f"List {ext} files.",
                long_help=long_help,
                handler=_make_list_handler(get_dir, pattern),
            ),
            "explore": Command(
                help="Open folder in file explorer.",
                long_help=long_help,
                handler=_make_explore_handler(get_dir),
                needs=CapabilitySet(gui_apps=True),
            ),
        },
    )


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    name="edit",
    args="<filename>",
    help="Open a project file in the system editor.",
    handler=_handler_root,
    needs=CapabilitySet(gui_apps=True),
    sub_commands={
        "run": _build_folder_sub(
            lambda ctx: ctx.fs.scripts_dir, ".run", "*.run",
            kind="run", noun="script",
        ),
        "proto": _build_folder_sub(
            lambda ctx: ctx.fs.proto_dir, ".pro", "*.pro",
            kind="proto", noun="script",
        ),
        "plugin": _build_folder_sub(
            lambda ctx: Path(ctx.config_path).parent / "plugin" if ctx.config_path else Path("."),
            ".py", "*.py",
            kind="plugin",
        ),
        "cfg": Command(
            help="Open the config file in the system editor.",
            handler=_handler_cfg,
            needs=CapabilitySet(gui_apps=True),
        ),
        "log": Command(
            help="Open the session log in the system viewer.",
            handler=_handler_log,
            needs=CapabilitySet(gui_apps=True),
        ),
        "info": Command(
            help="Open the info report in the system viewer.",
            handler=_handler_info,
            needs=CapabilitySet(gui_apps=True),
        ),
    },
)
