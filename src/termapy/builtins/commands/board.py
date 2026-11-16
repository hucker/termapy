"""Built-in plugin: /board -- starter projects for real dev boards.

``/board`` lists the boards ``termapy.boards.BOARDS`` holds and whether one
is plugged in; ``.new`` creates a board's config and profile, connecting by
the attached board's USB serial number; ``.firmware`` copies the code that
runs on the board into a folder for flashing; ``.update`` replaces a board
config's profile and device code with what the installed termapy ships,
after a yes.  The table, the detection and
the files are core (``termapy.boards``); this file is the thin surface over
them (registry ``infra-not-under-builtins``).  The simulator is ``/demo``;
a board starter talks to real hardware.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.boards import (
    BOARDS,
    AlreadyConfigured,
    Board,
    Stale,
    attached,
    config_profile_path,
    copy_firmware,
    create_board_config,
    find_board,
    firmware_paths,
    install_command,
    name_profile,
    profile_name,
    profile_revision,
    replace_stale,
    stale_files,
)
from termapy.config import ConfigExistsError, cfg_dir
from termapy.defaults import cfg_value
from termapy.folder_ops import write_destination
from termapy.folders import FIRMWARE
from termapy.help_dynamic import compose
from termapy.plugins import CapabilitySet, CmdResult, Column, Command
from termapy.plugins.params import ParamSpec

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _record(board: Board) -> dict:
    """One listing row with real JSON types: the board and where it is plugged in now."""
    found = attached(board)
    if found.running:
        state = "running"
    elif found.without_runtime:
        state = "attached"
    else:
        state = "not attached"
    return {
        "name": board.name,
        "title": board.title,
        "runtime": board.runtime.name,
        "usb": list(board.usb),
        "state": state,
        "ports": [device for device, _ in found.running],
        "firmware": [path.name for path in firmware_paths(board)],
    }


def _attached_cell(record: dict) -> str:
    """``running on COM5``, ``attached, no MicroPython``, or ``not attached``."""
    if record["ports"]:
        return f"{record['state']} on {', '.join(record['ports'])}"
    if record["state"] == "attached":
        return f"attached, no {record['runtime']}"
    return record["state"]


# A click on a name fills the input with its .new line and runs nothing
# (registry ``links-are-data``): creating a config is a write.
_COLUMNS = (
    Column(
        "NAME", lambda record: record["name"], prefill=lambda record: f"board.new {record['name']}"
    ),
    Column("BOARD", lambda record: record["title"]),
    Column("RUNS", lambda record: record["runtime"]),
    Column("ATTACHED", _attached_cell),
)

_BOARD = ParamSpec(
    "board", "str", required=True, positional=True, hint="<board>", help="the board, e.g. pico"
)


def _long_help(ctx: PluginContext) -> str:
    """The boards, live from the table, then the workflow."""
    rows = [f"{board.name}: {board.title}, {board.runtime.name}. {board.flash}" for board in BOARDS]
    return compose(
        "Boards:",
        "\n".join(rows),
        "{prefix}board.firmware <board> copies the code that runs on the board "
        "into the config's firmware/ folder (folder= names another; a relative "
        "one is under the config's folder) and prints the command that puts it "
        "on the board.  Flash it, plug the "
        "board in, then {prefix}board.new <board> creates its config and "
        "profile, connecting by the board's USB serial number so a changed COM "
        "number does not matter; {prefix}cfg.load <board> switches to it.  "
        "Nothing that is already there is replaced: not a config by that name, "
        "and not a second config for a board one already connects to.  The "
        "profile puts the board's commands in {prefix}help and gives an MCP "
        "agent typed tools; the config loads it at every connect.  After "
        "termapy is upgraded, {prefix}board.update brings the active board "
        "config's profile and device code up to date, after a yes.  The "
        "simulator is {prefix}demo.",
    )


def _stale_line(item: Stale, folder: Path) -> str:
    """One file ``/board.update`` replaces: its name in the config folder, and what changes."""
    name = item.destination.relative_to(folder).as_posix()
    if item.destination.parent == folder:
        was = profile_revision(item.destination) or "none"
        return f"{name}  {was} -> {profile_revision(item.source)}"
    return name if item.destination.is_file() else f"{name}  (new)"


def _board_or_fail(ctx: PluginContext) -> Board | CmdResult:
    """The board the bound ``board`` names, or the failure that answers it."""
    try:
        return find_board(ctx.arg("board"))
    except ValueError as e:
        return CmdResult.fail(msg=str(e))


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """``/board``: the boards termapy ships starters for, and which is plugged in."""
    return CmdResult.table("boards", "board", _COLUMNS, [_record(board) for board in BOARDS])


def _handler_new(ctx: PluginContext, args: str) -> CmdResult:
    """``/board.new <board>``: create the board's config and profile for the attached board."""
    board = _board_or_fail(ctx)
    if isinstance(board, CmdResult):
        return board
    name = ctx.arg("cfg") or board.name
    try:
        created = create_board_config(board, cfg_dir(), name)
    except LookupError as e:
        return CmdResult.fail(msg=str(e))
    except AlreadyConfigured as e:
        return CmdResult.fail(
            msg=f"Config exists for this board: {e.path.stem} (serial number {e.serial}); "
            f"{ctx.prefix}cfg.load {e.path.stem} switches to it"
        )
    except ConfigExistsError:
        return CmdResult.fail(msg=f"Config exists: {name} (load it, or pass cfg=<new name>)")
    except (ValueError, OSError) as e:
        return CmdResult.fail(msg=f"Config error: {e}")
    others = f"; {created.others} more attached, not chosen" if created.others else ""
    ctx.io.result(
        f"Created {name}: {board.title} on {created.device}, serial number "
        f"{created.port}{others}.  {ctx.prefix}cfg.load {name} switches to it."
    )
    if created.firmware:
        ctx.io.output(f"Its device code is in {created.firmware[0].parent}")
    data = {"config": str(created.path), "port": created.port, "device": created.device}
    return CmdResult.ok(value=str(created.path), data=data)


def _handler_firmware(ctx: PluginContext, args: str) -> CmdResult:
    """``/board.firmware <board>``: copy the board's device code into a folder for flashing."""
    board = _board_or_fail(ctx)
    if isinstance(board, CmdResult):
        return board
    folder = write_destination(ctx, ctx.arg("folder") or "", FIRMWARE)
    try:
        written = copy_firmware(board, folder)
    except FileExistsError as e:
        return CmdResult.fail(msg=f"File exists: {e} (delete or rename it first)")
    except OSError as e:
        return CmdResult.fail(msg=f"Write error: {e}")
    names = ", ".join(path.name for path in written)
    ctx.io.result(f"Copied {names} to {folder}")
    found = attached(board)
    port = found.running[0][0] if found.running else "<port>"
    command = install_command(board, written, port)
    if command:
        ctx.io.output(f"Put them on the running board: {command}")
    paths = [str(path) for path in written]
    return CmdResult.ok(value="\n".join(paths), data={"files": paths, "install": command})


def _handler_update(ctx: PluginContext, args: str) -> CmdResult:
    """``/board.update {board}``: replace the config's profile and device code with the shipped ones, after a yes.

    The board defaults to the one the active config is named for.  Only
    copies that differ are replaced, and a config naming no profile gets
    ``profile_path`` set so the profile loads at every connect.  The
    profile is loaded at the end either way, and when device code changed
    the reply ends with the line that puts it on the board: termapy never
    writes to the board itself.
    """
    # Resolved, so the reply and the install line name full paths that work from any folder.
    config_path = Path(ctx.config_path).resolve()
    try:
        board = find_board(ctx.arg("board") or config_path.stem)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    folder = config_path.parent
    stale = stale_files(board, config_path)
    try:
        written = config_profile_path(config_path)
    except (OSError, ValueError) as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    # The file decides, as it is what the next load reads; the session follows it.
    names_profile = not written
    session = cfg_value(ctx.cfg, "profile_path")
    profile = session or written or profile_name(config_path.stem)
    lines = [_stale_line(item, folder) for item in stale]
    if names_profile:
        lines.append(f"{config_path.name}  profile_path = {profile}")
    if lines:
        question = f"Update {config_path.stem} from termapy's {board.title} starter?\n" + "\n".join(
            f"  {line}" for line in lines
        )
        if ctx.capabilities.confirm_dialog and not ctx.ui.confirm(question):
            return CmdResult.fail(msg=f"Config not updated: {config_path.stem}")
        try:
            replace_stale(stale)
            named = name_profile(config_path) if names_profile else ""
        except (OSError, ValueError) as e:
            return CmdResult.fail(msg=f"Write error: {e}")
        if named and not session:
            ctx.internal.apply_cfg("profile_path", named)
        ctx.io.result(f"Updated {config_path.stem}:")
        for line in lines:
            ctx.io.output(f"  {line}")
    else:
        ctx.io.result(f"{config_path.stem} is up to date.")
    loaded = ctx.internal.dispatch(f"profile.load {profile}")
    if not loaded.success:
        return loaded
    command = ""
    if any(item.destination.parent.name == FIRMWARE for item in stale):
        found = attached(board)
        port = found.running[0][0] if found.running else "<port>"
        code = [folder / FIRMWARE / path.name for path in firmware_paths(board)]
        command = install_command(board, code, port)
        if command:
            ctx.io.output(f"Put the device code on the board: {command}")
    replaced = [str(item.destination) for item in stale]
    data = {"replaced": replaced, "profile_path": profile, "install": command}
    return CmdResult.ok(value=len(replaced), data=data)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    group="devices",
    name="board",
    maturity="prototype",
    help="Starter projects for real dev boards: list, new, firmware, update.",
    intro=(
        "Starter projects for real dev boards: the code that runs on the board, a "
        "host config and a device profile, so a new board answers a REPL and an MCP "
        "agent on day one."
    ),
    long_help=_long_help,
    bare_sub="list",
    sub_commands={
        "list": Command(
            help="List the boards termapy ships starters for, and which is plugged in.",
            long_help=_long_help,
            handler=_handler_list,
            shows=True,
        ),
        "new": Command(
            help="Create a board's config and profile, connecting by its USB serial number.",
            handler=_handler_new,
            params=[
                _BOARD,
                ParamSpec(
                    "cfg", "str", hint="<name>", help="the config's name (default: the board's)"
                ),
            ],
        ),
        "firmware": Command(
            help="Copy the code that runs on a board into the config's firmware/ folder, for flashing.",
            handler=_handler_firmware,
            needs=CapabilitySet.CONFIG_LOADED,
            params=[
                _BOARD,
                ParamSpec(
                    "folder",
                    "path",
                    help="where to write instead, created if missing; a relative one is under the config's folder",
                ),
            ],
        ),
        "update": Command(
            help="Update the active board config's profile and device code to this termapy's (asks first).",
            long_help=(
                "After termapy is upgraded, a board config still holds the profile and "
                "device code it was made with.  This replaces the copies that differ "
                "from what termapy ships now, the profile in the config's folder and the "
                "code in its firmware/, after listing them and asking.  A file you edited "
                "is replaced too, so the list is the one place to see it.  Then it loads "
                "the profile, and when device code changed it prints the command that "
                "puts it on the board; termapy never writes to the board itself.  The "
                "board defaults to the one the config is named for."
            ),
            handler=_handler_update,
            needs=CapabilitySet.MANAGE_FILES,
            safety="destructive",
            cfg={"profile_path": "set to the config's profile when empty, so it loads at connect"},
            params=[
                ParamSpec(
                    "board",
                    "str",
                    positional=True,
                    help="the board, e.g. pico (default: the config's name)",
                ),
            ],
        ),
    },
)
