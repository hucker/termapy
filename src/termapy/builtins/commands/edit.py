"""Built-in plugin: open project files in the system editor.

Provides a uniform /edit tree for all file types: run, proto,
plugin, config, log, and info report. Each folder type gets the
same subcommands: edit by name, list files, open folder.

``edit_command`` / ``edit_cfg_command`` also build the noun-first twins
(``/run.edit``, ``/proto.edit``, ``/plugin.edit``, ``/cfg.edit``) that
the run, proto, plugin and cfg plugins mount, so both spellings are one
declaration.

In the TUI, hooks override /edit, /edit.cfg, /edit.run, and
/edit.proto (and their twins) to use Textual modal editors. Everything
else (list, explore, log, info) works the same in both frontends via
ctx.fs.open_file().
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from termapy.config import cfg_log_path
from termapy.folders import EXT_TO_FOLDER, FOLDERS, ensure_folder
from termapy.help_dynamic import compose, folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
from termapy.plugins.command import PICKERS

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


def make_edit_handler(folder: str, get_dir, ext):
    """Create a handler that opens a file by name, or lists the folder if no name given.

    Returns the opened file path as ``CmdResult.value``; bare, the
    ``/<folder>.list`` result (a picker host never gets here bare -- the
    dispatcher opens the picker first).
    """
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        name = args.strip()
        if not name:
            return make_list_handler(folder)(ctx, args)
        if not name.endswith(ext):
            name += ext
        path = get_dir(ctx) / name
        if not path.exists():
            return CmdResult.fail(msg=f"File not found: {name}")
        ctx.fs.open_file(path)
        return CmdResult.ok(value=path)
    return handler


def make_list_handler(folder: str):
    """``/edit.<folder>.list`` is ``/<folder>.list``: one listing per folder.

    Forwarded through the engine, so the listing (format, order, data
    records, ``/run.list``'s SUMMARY) is the folder command's own.
    """
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        return ctx.internal.dispatch(f"{folder}.list")
    return handler


def make_explore_handler(get_dir):
    """Create a handler that opens a folder in the system file explorer.

    Returns the opened folder path as ``CmdResult.value``.
    """
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        folder = get_dir(ctx)
        ctx.fs.open_file(ensure_folder(folder))
        return CmdResult.ok(value=folder)
    return handler


def _folder_dir(folder: str) -> Callable[[PluginContext], Path]:
    """Getter for a per-config data folder; the cwd when no config is loaded."""
    def get_dir(ctx: PluginContext) -> Path:
        return Path(ctx.config_path).parent / folder if ctx.config_path else Path(".")
    return get_dir


# The word ``folder_line`` counts files in, where "file" would read wrong.
_NOUNS = {"run": "script", "proto": "script"}


def _folder_long_help(folder: str, text: str = "") -> Callable[[PluginContext], str]:
    """Dynamic long_help: the live file count ("42 scripts in run/"), then ``text``."""
    noun = _NOUNS.get(folder)

    def long_help(ctx: PluginContext) -> str:
        return compose(folder_line(ctx, folder, noun=noun), text)
    return long_help


def edit_command(folder: str) -> Command:
    """``/edit.<folder>`` and its noun-first twin ``/<folder>.edit``.

    One builder for both spellings, so they cannot drift: open an existing
    file from ``folder`` by name, or list the folder when bare.  The TUI
    replaces both with a hook that opens Textual's editor and, bare, the
    folder's picker.

    Args:
        folder: A ``folders.FOLDERS`` name holding editable text files.
    """
    spec = next(spec for spec in FOLDERS if spec.name == folder)
    # A folder with a picker opens it on a bare call in the TUI (SYNOPSIS
    # says so); the listing is what a host without pickers falls back to.
    picker = folder if folder in PICKERS else ""
    bare = f"Without a picker (CLI, MCP), bare lists {folder}/." if picker else f"Bare lists {folder}/."
    text = (
        f"With a name, opens that existing file from {folder}/ in the system "
        f"editor (the {spec.ext} is optional); a missing name fails.  {bare}  "
        f"/{folder}.edit and /edit.{folder} are the same command."
    )
    return Command(
        args="{filename}",
        help=f"Open a {spec.ext} file in the system editor.",
        long_help=_folder_long_help(folder, text),
        handler=make_edit_handler(folder, _folder_dir(folder), spec.ext),
        needs=CapabilitySet.GUI_APPS,
        files=folder,
        picker=picker,
    )


def edit_cfg_command() -> Command:
    """``/edit.cfg`` and its noun-first twin ``/cfg.edit``: open the current config."""
    return Command(
        help="Open the config file in the system editor.",
        long_help=(
            "Opens the loaded config's .cfg file in the system editor.  "
            "/cfg.edit and /edit.cfg are the same command."
        ),
        handler=_handler_cfg,
        needs=CapabilitySet.GUI_APPS,
    )


def _build_folder_sub(folder: str) -> Command:
    """``/edit.<folder>``: the shared edit leaf plus ``.list`` and ``.explore``."""
    spec = next(spec for spec in FOLDERS if spec.name == folder)
    get_dir = _folder_dir(folder)
    long_help = _folder_long_help(folder)  # the subs: the count line alone
    return replace(
        edit_command(folder),
        sub_commands={
            "list": Command(
                help=f"List {spec.ext} files.",
                long_help=long_help,
                handler=make_list_handler(folder),
            ),
            "explore": Command(
                help="Open folder in file explorer.",
                long_help=long_help,
                handler=make_explore_handler(get_dir),
                needs=CapabilitySet.GUI_APPS,
            ),
        },
    )


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    name="edit",
    args="<filename>",
    help="Open a project file in the system editor.",
    handler=_handler_root,
    needs=CapabilitySet.GUI_APPS,
    sub_commands={
        "run": _build_folder_sub("run"),
        "proto": _build_folder_sub("proto"),
        "plugin": _build_folder_sub("plugin"),
        "cfg": edit_cfg_command(),
        "log": Command(
            help="Open the session log in the system viewer.",
            handler=_handler_log,
            needs=CapabilitySet.GUI_APPS,
        ),
        "info": Command(
            help="Open the info report in the system viewer.",
            handler=_handler_info,
            needs=CapabilitySet.GUI_APPS,
        ),
    },
)
