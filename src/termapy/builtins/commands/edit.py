"""Built-in plugin: open project files for editing.

Provides a uniform /edit tree for all file types: run, proto,
plugin, config, log, and info report. Each folder type gets the
same subcommands: edit by name, list files, open folder.

The folder leaves and the config leaf are ``folder_ops``'
(``edit_folder_command``, ``edit_cfg_command``), which also builds the
noun-first twins (``/run.edit``, ``/proto.edit``, ``/plugin.edit``,
``/cfg.edit``) that the run, proto, plugin and cfg plugins mount, so
both spellings are one declaration.

These handlers decide WHICH file; ``ctx.internal.edit_file`` is how the
frontend opens it: the TUI's own editor for the config, a .run or a .pro
file (over SSH too), the system editor otherwise.  Until 2026-10-16 the
TUI registered its own /edit commands over these, with its own file
lookup, so ``/edit foo.py`` worked in the CLI and failed in the TUI
(review 2026-10-15, D5).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.folder_ops import edit_cfg_command, edit_folder_command
from termapy.folders import EXT_TO_FOLDER, FOLDER_EXTS, PROTO, RUN
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── File resolution ──────────────────────────────────────────────────────────


def _resolve_file(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a filename to a project file path.

    Checks run/, proto/, plugin/ dirs by prefix or extension.
    """
    dir_map = {
        "run": (ctx.fs.scripts_dir, FOLDER_EXTS[RUN]),
        "proto": (ctx.fs.proto_dir, FOLDER_EXTS[PROTO]),
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
    ctx.internal.edit_file(path)
    return CmdResult.ok(value=path)


def _handler_log(ctx: PluginContext, args: str) -> CmdResult:
    path = ctx.internal.session_log_path()
    assert path is not None, "edit.log declares session_log"
    ctx.internal.edit_file(path)
    return CmdResult.ok(value=path)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    stem = Path(ctx.config_path).stem
    path = Path(ctx.config_path).parent / f"{stem}.md"
    if path.exists():
        ctx.internal.edit_file(path)
        return CmdResult.ok(value=path)
    else:
        return CmdResult.fail(msg=f"No info report yet. Run {ctx.prefix}cfg.info first.")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    group="advanced",
    name="edit",
    args="<filename>",
    help="Open a project file for editing (run/, proto/ or plugin/).",
    handler=_handler_root,
    needs=CapabilitySet.FILE_EDITOR,
    sub_commands={
        "run": edit_folder_command("run"),
        "proto": edit_folder_command("proto"),
        "plugin": edit_folder_command("plugin"),
        "cfg": edit_cfg_command(),
        "log": Command(
            help="Open the session log in the system viewer.",
            handler=_handler_log,
            needs=CapabilitySet(session_log=True, config_loaded=True, gui_apps=True),
        ),
        "info": Command(
            help="Open the info report in the system viewer.",
            handler=_handler_info,
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
    },
)
