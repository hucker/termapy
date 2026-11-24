"""Built-in plugin: /firmware -- the device-side sources termapy ships, one command away.

``/firmware.lib`` (bare ``/firmware``) lists the modules
``termapy.firmware.TARGETS`` holds (the MEM wire-spec module, the JSON reply
envelope and the monitor, in C); ``.copy`` writes every module of a language,
or one module by name, into a folder; ``.lib.show`` opens a module's main
source in the system viewer; ``.lib.dump`` prints one file, which is also how
an agent fetches it over MCP.  The config's own ``firmware/`` folder, where
the copies land, has the family every data folder has (``/firmware.list``,
``.show``, ``.dump``, ``.edit``, ``.rename``, ``.delete``; registry
``folder-family-from-table``), so the plain verbs mean the config's files
and the shipped catalog is the noun ``lib``, as ``/dev.lib`` is beside
``dev/``.  The tables and the file access are core (``termapy.firmware``);
this file is the thin surface over them (registry
``infra-not-under-builtins``).
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
from termapy.folder_ops import build_folder_subcommands, edit_command, write_destination
from termapy.folders import FIRMWARE
from termapy.help_dynamic import compose
from termapy.plugins import CapabilitySet, CmdResult, Column, Command, TextLine
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
        "{prefix}firmware.lib.show opens a shipped module's main source; "
        "{prefix}firmware.lib.dump prints one of its files, which over MCP is "
        "how an agent reads it.  The copies are this config's: "
        "{prefix}firmware.list, .show, .dump, .edit, .rename and .delete work "
        "on firmware/ as {prefix}run.list and its family do on run/.",
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


def _handler_lib(ctx: PluginContext, args: str) -> CmdResult:
    """``/firmware.lib`` (bare ``/firmware``): what ships, one row per target."""
    return CmdResult.table("firmware", "target", _COLUMNS, [_record(target) for target in TARGETS])


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
    return CmdResult.ok(value=len(paths), data={"files": paths})


def _handler_lib_show(ctx: PluginContext, args: str) -> CmdResult:
    """``/firmware.lib.show <name>``: open the shipped module's main source in the system viewer."""
    target = _target_or_fail(ctx)
    if isinstance(target, CmdResult):
        return target
    path = source_path(target, target.sources[0])
    ctx.fs.open_file(path)
    return CmdResult.ok(value=path)


def _handler_lib_dump(ctx: PluginContext, args: str) -> CmdResult:
    """``/firmware.lib.dump <name> {file}``: one shipped file as a page; its text is the value, its facts the data."""
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
    facts = {
        "name": target.name,
        "language": target.language,
        "file": name,
        "bytes": len(text.encode("utf-8")),
    }
    # The file is the answer: a record page dispatch prints at quiet and up,
    # and never beside the data (registry record-one-format).
    return CmdResult.record(facts, [TextLine(line) for line in text.splitlines()], value=text)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    group="devices",
    name="firmware",
    maturity="prototype",
    help="Device-side sources: what termapy ships (.lib) and this config's firmware/ folder.",
    intro=(
        "Source code termapy ships for your device's firmware, so the device can "
        "answer termapy's memory and JSON commands. Copy it into your project and "
        "build it."
    ),
    long_help=_long_help,
    bare_sub="lib",
    help_list="lib",
    sub_commands={
        # What ships, under a noun as /dev.lib is beside dev/, so the plain
        # verbs below mean this config's firmware/ folder (registry
        # folder-family-from-table).
        "lib": Command(
            help="List the shipped modules: name, language, files, size.",
            intro=(
                "The device code termapy ships, that {prefix}firmware.copy copies into "
                "this config's firmware/ folder."
            ),
            long_help=_long_help,
            handler=_handler_lib,
            shows=True,
            state=True,
            sub_commands={
                "show": Command(
                    help="Open a shipped module's main source in the system viewer.",
                    handler=_handler_lib_show,
                    shows=True,
                    needs=CapabilitySet.GUI_APPS,
                    params=[_NAME, _LANG],
                ),
                "dump": Command(
                    help="Print one shipped source file to the terminal.",
                    handler=_handler_lib_dump,
                    shows=True,
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
        "edit": edit_command("firmware"),
        **build_folder_subcommands("firmware"),
    },
)
