"""Built-in plugin: /firmware -- the device-side sources termapy ships, one command away.

``/firmware`` lists the targets ``termapy.firmware.TARGETS`` holds (the MEM
wire-spec module and the JSON reply envelope, in C); ``.copy`` writes every
module of a language, or one module by name, into a folder; ``.show`` opens
a module's main source in the system viewer; ``.dump`` prints one file,
which is also how an agent fetches it over MCP.  The tables and the file
access are core (``termapy.firmware``); this file is the thin surface over
them (registry ``infra-not-under-builtins``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.firmware import (
    NAMES,
    TARGETS,
    Target,
    copy_targets,
    find_target,
    select_targets,
    source_path,
    source_size,
    source_text,
)
from termapy.folder_ops import write_destination
from termapy.folders import FIRMWARE
from termapy.help_dynamic import compose
from termapy.plugins import CapabilitySet, CmdResult, Column, Command
from termapy.plugins.params import ParamSpec
from termapy.scripting import format_size

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _copy_command(target: Target) -> str:
    """The ``.copy`` line for one module, naming the language only when it is needed."""
    several = sum(1 for other in TARGETS if other.name == target.name) > 1
    return f"firmware.copy {target.name}" + (f" lang={target.language}" if several else "")


def _record(target: Target) -> dict:
    """One listing row with real JSON types; the table's cells are derived from it."""
    return {
        "name": target.name,
        "language": target.language,
        "platform": target.platform,
        "files": list(target.sources),
        "bytes": source_size(target),
        "guide": target.guide,
        "copy": _copy_command(target),
    }


# A click on a name fills the input with its .copy line and runs nothing
# (registry ``links-are-data``): copying is a write, so it waits for Enter.
_COLUMNS = (
    Column("NAME", lambda record: record["name"], prefill=lambda record: record["copy"]),
    Column("LANGUAGE", lambda record: record["language"]),
    Column("FILES", lambda record: ", ".join(record["files"])),
    Column("SIZE", lambda record: format_size(record["bytes"]), right=True),
)

_NAME = ParamSpec("name", "str", required=True, positional=True, hint="<name>", help="mem or json")
_LANG = ParamSpec(
    "lang",
    "str",
    hint="<language>",
    help="the language; needed only when the sources ship in several",
)


def _long_help(ctx: PluginContext) -> str:
    """The targets, live from the table, then the verbs."""
    rows = [
        f"{target.name} ({target.language}): {', '.join(target.sources)} -- {NAMES[target.name]}"
        for target in TARGETS
    ]
    return compose(
        "\n".join(["Shipped with this termapy:", *rows]),
        "{prefix}firmware.copy writes every module of a language into the "
        "config's firmware/ folder and never overwrites a file: lang= picks "
        "the language when several ship, a name copies one module, folder= "
        "names another folder (a relative one is under the config's folder).  "
        "{prefix}firmware.show opens a module's main source; "
        "{prefix}firmware.dump prints one file, which over MCP is how an "
        "agent reads it.",
    )


def _target_or_fail(ctx: PluginContext) -> Target | CmdResult:
    """The one target the bound ``name`` and ``lang`` address, or the failure that answers them."""
    try:
        return find_target(ctx.arg("name"), ctx.arg("lang"))
    except ValueError as e:
        return CmdResult.fail(msg=str(e))


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """``/firmware``: what ships, one row per target."""
    records = [_record(target) for target in TARGETS]
    names = "\n".join(f"{record['name']} {record['language']}" for record in records)
    return CmdResult.table("firmware", "target", _COLUMNS, records, value=names)


def _handler_copy(ctx: PluginContext, args: str) -> CmdResult:
    """``/firmware.copy {name}``: write a language's modules, or one, into a folder; never over existing files."""
    try:
        targets = select_targets(ctx.arg("name"), ctx.arg("lang"))
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    folder = write_destination(ctx, ctx.arg("folder") or "", FIRMWARE)
    try:
        written = copy_targets(targets, folder)
    except FileExistsError as e:
        return CmdResult.fail(msg=f"File exists: {e} (delete or rename it first)")
    except OSError as e:
        return CmdResult.fail(msg=f"Write error: {e}")
    names = ", ".join(path.name for path in written)
    ctx.io.result(f"Copied {names} to {folder}")
    paths = [str(path) for path in written]
    return CmdResult.ok(value="\n".join(paths), data={"files": paths})


def _handler_show(ctx: PluginContext, args: str) -> CmdResult:
    """``/firmware.show <name>``: open the module's main source in the system viewer."""
    target = _target_or_fail(ctx)
    if isinstance(target, CmdResult):
        return target
    path = source_path(target, target.sources[0])
    ctx.fs.open_file(path)
    return CmdResult.ok(value=path)


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """``/firmware.dump <name> {file}``: print one shipped file; its text is the value."""
    target = _target_or_fail(ctx)
    if isinstance(target, CmdResult):
        return target
    name = ctx.arg("file")
    if not name:
        if len(target.sources) != 1:
            return CmdResult.fail(msg=f"Specify a file: {', '.join(target.sources)}")
        name = target.sources[0]
    try:
        text = source_text(target, name)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    if not ctx.wants_data:
        for line in text.splitlines():
            ctx.io.output(line)
    facts = {
        "name": target.name,
        "language": target.language,
        "file": name,
        "bytes": len(text.encode("utf-8")),
    }
    return CmdResult.ok(value=text, data=facts)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    group="devices",
    name="firmware",
    help="Device-side sources termapy ships: list, copy, show, dump.",
    long_help=_long_help,
    bare_sub="list",
    sub_commands={
        "list": Command(
            help="List the shipped targets: name, language, files, size.",
            long_help=_long_help,
            handler=_handler_list,
        ),
        "copy": Command(
            help="Write the shipped sources into the config's firmware/ folder: every module of a language, or one by name; never overwrites.",
            handler=_handler_copy,
            needs=CapabilitySet.CONFIG_LOADED,
            params=[
                ParamSpec(
                    "name",
                    "str",
                    positional=True,
                    hint="<name>",
                    help="one module (mem or json); omitted, every module of the language",
                ),
                _LANG,
                ParamSpec(
                    "folder",
                    "path",
                    help="where to write instead, created if missing; a relative one is under the config's folder",
                ),
            ],
        ),
        "show": Command(
            help="Open a module's main source in the system viewer.",
            handler=_handler_show,
            needs=CapabilitySet.GUI_APPS,
            params=[_NAME, _LANG],
        ),
        "dump": Command(
            help="Print one shipped source file to the terminal.",
            handler=_handler_dump,
            params=[
                _NAME,
                ParamSpec(
                    "file",
                    "str",
                    positional=True,
                    hint="<file>",
                    help="one of the module's files; needed when it ships several",
                ),
                _LANG,
            ],
        ),
    },
)
