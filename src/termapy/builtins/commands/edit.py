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
file (over SSH and in a browser tab too), the system editor otherwise,
which needs ``gui_apps``: ``/edit <name>`` checks that itself for a file of
a type termapy has no editor for, since its declared need is only
``file_editor``.  Until 2026-10-16 the TUI registered its own /edit
commands over these, with its own file lookup, so ``/edit foo.py`` worked
in the CLI and failed in the TUI (review 2026-10-15, D5).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.folder_ops import TERMAPY_EDITS, edit_cfg_command, edit_folder_command
from termapy.folders import EXT_TO_FOLDER, file_suggestion
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError, refusal_text

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── File resolution ──────────────────────────────────────────────────────────


def _search_place(ctx: PluginContext, name: str) -> tuple[Path, str] | None:
    """Where ``/edit`` looks for ``name``: the folder, and the name to look for in it.

    ``run/x.run`` names its folder; otherwise the extension picks run/,
    proto/ or plugin/.  None for a kind of file no folder holds.
    """
    named = {"run": ctx.fs.scripts_dir, "proto": ctx.fs.proto_dir}
    parts = Path(name).parts
    if len(parts) == 2 and parts[0].lower() in named:
        return named[parts[0].lower()], parts[1]
    by_ext = {
        "run": ctx.fs.scripts_dir,
        "proto": ctx.fs.proto_dir,
        "plugin": ctx.fs.scripts_dir.parent / "plugin",
    }
    base = by_ext.get(EXT_TO_FOLDER.get(Path(name).suffix.lower(), ""))
    return (base, name) if base else None


def _resolve_file(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a filename to a project file path, or None (``_search_place`` says where)."""
    place = _search_place(ctx, name)
    if place is None:
        return None
    path = place[0] / place[1]
    return path if path.exists() else None


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Open a project file by name in the editor, searching the config's folders (``_resolve_file``)."""
    name = args.strip()
    if not name:
        raise UsageError()
    path = _resolve_file(ctx, name)
    if path is None:
        place = _search_place(ctx, name)
        hint = file_suggestion(place[0], name) if place else ""
        return CmdResult.fail(msg=f"File not found: {name}{hint}")
    # A type termapy has no editor of its own for (a plugin's .py) opens in
    # the system editor, which needs a desktop the user sees; a browser tab
    # and SSH without a display have none.  The need depends on the file, so
    # the handler checks it here (registry gated-call-declares-need).
    if path.suffix.lower() not in TERMAPY_EDITS and not ctx.capabilities.gui_apps:
        return CmdResult.fail(
            msg=f"{path.name} opens only in the system editor: "
            + refusal_text("edit", ["gui_apps"], ctx.prefix)
        )
    ctx.internal.edit_file(path)
    return CmdResult.ok(value=path)


def _handler_log(ctx: PluginContext, args: str) -> CmdResult:
    """Open the session log in the editor."""
    path = ctx.internal.session_log_path()
    assert path is not None, "edit.log declares session_log"
    ctx.internal.edit_file(path)
    return CmdResult.ok(value=path)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Open the config's info report (``<cfg>.md``) in the editor; ``cfg.info`` writes it."""
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
    intro=(
        "Open this config's files in an editor: its scripts, protocol tests and "
        "plugins, the config itself, its session log and its project report."
    ),
    handler=_handler_root,
    shows=True,
    needs=CapabilitySet.FILE_EDITOR,
    sub_commands={
        "run": edit_folder_command("run"),
        "proto": edit_folder_command("proto"),
        "plugin": edit_folder_command("plugin"),
        "cfg": edit_cfg_command(),
        "log": Command(
            help="Open the session log in the system viewer.",
            handler=_handler_log,
            shows=True,
            needs=CapabilitySet(session_log=True, config_loaded=True, gui_apps=True),
        ),
        "info": Command(
            help="Open the info report in the system viewer.",
            handler=_handler_info,
            shows=True,
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
    },
)
