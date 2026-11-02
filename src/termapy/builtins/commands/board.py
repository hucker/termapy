"""Built-in plugin: /board -- starter projects for real dev boards.

``/board`` lists the boards ``termapy.boards.BOARDS`` holds and whether one
is plugged in; ``.new`` creates a board's config and profile, connecting by
the attached board's USB serial number; ``.firmware`` copies the code that
runs on the board into a folder for flashing.  The table, the detection and
the files are core (``termapy.boards``); this file is the thin surface over
them (registry ``infra-not-under-builtins``).  The simulator is ``/demo``;
a board starter talks to real hardware.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.boards import (
    BOARDS,
    AlreadyConfigured,
    Board,
    attached,
    copy_firmware,
    create_board_config,
    find_board,
    firmware_paths,
    install_command,
)
from termapy.config import ConfigExistsError, cfg_dir
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
        "agent typed tools.  The simulator is {prefix}demo.",
    )


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
    records = [_record(board) for board in BOARDS]
    names = "\n".join(record["name"] for record in records)
    return CmdResult.table("boards", "board", _COLUMNS, records, value=names)


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


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    group="devices",
    name="board",
    maturity="prototype",
    help="Starter projects for real dev boards: list, new, firmware.",
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
    },
)
