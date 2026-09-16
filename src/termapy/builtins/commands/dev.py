"""Built-in plugin: /dev.* -- device files: import a vendor register description.

The command surface only.  The format, the loader, instances and the
layered ``dev/`` folders live in :mod:`termapy.devices`; the converters in
:mod:`termapy.devices.converters`; the session merge in
:mod:`termapy.symbols.session`.  Core needs all of that at config load, so
none of it lives here (CLAUDE.md: infrastructure never lives under
``builtins/``).

- ``/dev.import <file> {format=...}`` -- convert a vendor register
  description (CMSIS-SVD) to ``dev/<device>.device.json`` and load it.
- ``/dev.list`` -- the devices THIS config loaded, with layer and placement.
- ``/dev.lib {pattern}`` -- the parts the library holds.  A different
  question from ``/dev.list``: available, not installed.
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Final

from termapy.config import cfg_relative_path, library_dir
from termapy.converters import DEVICE
from termapy.devices import (
    SUFFIX,
    device_dir,
    load_devices_from_dir,
    parse_device,
    scan_library,
)
from termapy.devices.converters import FORMATS, find_converter
from termapy.folders import ensure_folder
from termapy.help_dynamic import compose, state_line
from termapy.plugins import BoundaryException, CmdResult, Command, UsageError
from termapy.plugins.params import ParamSpec
from termapy.symbols import get_devices, install_devices
from termapy.symbols.format import device_list_rows, device_records, library_records

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _plugin_converters(ctx: PluginContext) -> tuple:
    """Device converters the active config's plugin folders loaded.

    Filtered to ``KIND = "device"``: the engine holds every kind in one
    list, and a symbol converter must never be offered as a device format.
    """
    internal = getattr(ctx, "internal", None)
    specs = getattr(internal, "converters", ()) or ()
    return tuple(spec for spec in specs if spec.kind == DEVICE)


def _known_formats(ctx: PluginContext) -> list[str]:
    extra = [spec.format for spec in _plugin_converters(ctx)]
    return list(FORMATS) + [name for name in extra if name not in FORMATS]


def _handler_import(ctx: PluginContext, args: str) -> CmdResult:
    """Convert a vendor file, write ``dev/<device>.device.json``, reload devices."""
    raw = str(ctx.arg("file") or "")
    fmt = ctx.arg("format") or ""
    if not raw:
        raise UsageError()
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    # Reading an arbitrary path is a parse/existence oracle under MCP;
    # contain to the sandbox unless the operator opted out.
    ctx.fs.guard_external_path(raw, "Source path")
    path = cfg_relative_path(ctx.config_path, raw)
    if not path.is_file():
        return CmdResult.fail(msg=f"Source file not found: {raw}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    spec = find_converter(fmt, text, extra=_plugin_converters(ctx))
    if spec is None:
        subject = fmt if fmt else path.name
        return CmdResult.fail(
            msg=f"Unknown device format: {subject} "
            f"(formats: {', '.join(_known_formats(ctx))})"
        )
    try:
        doc = spec.convert(text)
    # A plugin converter is third-party code on the dispatch path.
    except BoundaryException as e:
        return CmdResult.fail(msg=f"Converter error: {spec.format}: {e}")
    if not isinstance(doc, dict) or not doc.get("registers"):
        return CmdResult.fail(msg=f"No registers found in {path.name} ({spec.format})")
    doc = dict(doc)
    doc["source"] = f"{path.name}: {doc.get('source', '')}".rstrip(": ")
    # The converter's output goes through the same validation as a
    # hand-written file: a converter that emits a bad document is a
    # converter bug, and it is named as one.
    try:
        device = parse_device(doc)
    except ValueError as e:
        return CmdResult.fail(msg=f"Converter error: {spec.format}: {e}")
    folder = device_dir(ctx.config_path)
    dest = folder / f"{device.name}{SUFFIX}"
    for existing in load_devices_from_dir(folder).devices:
        if existing.name == device.name and existing.path != dest:
            return CmdResult.fail(
                msg=f"Device {device.name} is already defined by "
                f"{existing.path.name if existing.path else '?'}; remove it first"
            )
    try:
        ensure_folder(folder)
        dest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return CmdResult.fail(msg=f"Write error: {e}")
    _reload(ctx)
    peripherals = len({register.peripheral for register in device.registers()})
    ctx.io.result(
        f"Imported {len(device)} registers for {device.name} from {path.name} "
        f"({spec.format}) -> {dest.name}",
        "green",
    )
    ctx.io.output(f"  {peripherals} peripherals; /sym.info lists the device")
    data = device_records([device])[0] | {"format": spec.format, "path": str(dest)}
    return CmdResult.ok(value=str(len(device)), data=data)


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """What THIS config loaded: one row per device, with its layer."""
    devices = get_devices(ctx)
    if not devices:
        ctx.io.output("  (no devices loaded)")
        return CmdResult.ok(value="", data=[] if ctx.wants_data else None)
    if ctx.wants_data:
        return CmdResult.ok(
            value="\n".join(device.name for device in devices),
            data=device_records(devices),
        )
    rows = device_list_rows(devices)
    headers = ("DEVICE", "REGISTERS", "LAYER", "PLACEMENT")
    # Placement is last and often empty, so it needs no padding.
    widths = [
        max(len(header), *(len(row[i]) for row in rows))
        for i, header in enumerate(headers[:-1])
    ]
    ctx.io.output("  " + "  ".join(
        f"{header:<{width}}" for header, width in zip(headers, widths)
    ) + f"  {headers[-1]}")
    for row in rows:
        line = "  ".join(f"{cell:<{width}}" for cell, width in zip(row, widths))
        ctx.io.output(f"  {line}  {row[-1]}".rstrip())
    return CmdResult.ok(value="\n".join(device.name for device in devices))


def _handler_lib(ctx: PluginContext, args: str) -> CmdResult:
    """What the LIBRARY holds -- parts available to use, none of them loaded."""
    pattern = str(ctx.arg("pattern") or "").strip()
    root = _library_root(ctx)
    parts, errors = scan_library(root)
    for error in errors:
        ctx.io.output(f"Library: {error}", "yellow")
    # Glob if the user typed a wildcard, else a case-insensitive substring
    # over name AND category, so "microchip" finds a vendor folder and
    # "pic32cm*" finds a family.  Deliberately not the symbol-search
    # vocabulary (exact -> regex -> substring): a library listing is
    # browsing, and a half-copy of that ladder would be two grammars to
    # learn for one job.
    matched = [
        part for part in parts
        if _matches(pattern, part.device) or _matches(pattern, part.category)
    ] if pattern else parts
    if not matched:
        where = f" matching '{pattern}'" if pattern else ""
        ctx.io.output(f"  (no library parts{where})")
        return CmdResult.ok(value="", data=[] if ctx.wants_data else None)
    if ctx.wants_data:
        return CmdResult.ok(
            value="\n".join(part.device for part in matched),
            data=library_records(matched, root),
        )
    loaded = {device.name for device in get_devices(ctx)}
    width = max(len(part.device) for part in matched)
    for part in matched:
        count = f"{part.registers}" if part.registers >= 0 else "?"
        # The marker answers "do I already have this?" in the same glance.
        mark = " *" if part.device in loaded else "  "
        detail = part.category or "-"
        ctx.io.output(
            f"  {part.device:<{width}}{mark}  {count:>6}  {detail}"
        )
    ctx.io.output(f"  ({len(matched)} of {len(parts)} parts; * = loaded here)")
    return CmdResult.ok(value="\n".join(part.device for part in matched))


def _matches(pattern: str, text: str) -> bool:
    """Glob when the pattern has a wildcard, else case-insensitive substring."""
    if not text:
        return False
    if "*" in pattern or "?" in pattern:
        return fnmatch(text.lower(), pattern.lower())
    return pattern.lower() in text.lower()


def _library_root(ctx: PluginContext) -> Path:
    """The library folder, via the engine (it owns the global root).

    A hand-built test context may have no forwarder; then resolve the real
    one, which is what the engine would do unconfigured.
    """
    forward = getattr(getattr(ctx, "internal", None), "library_root", None)
    return forward() if callable(forward) else library_dir()


def _reload(ctx: PluginContext) -> None:
    """Re-scan the dev/ folders through the engine (it knows the global root).

    A hand-built test context may have no forwarder; then re-resolve with
    the real global root, which is what the engine would do unconfigured.
    """
    reload = getattr(getattr(ctx, "internal", None), "reload_devices", None)
    if callable(reload):
        reload()
        return
    from termapy.devices import resolve_devices

    devices, errors = resolve_devices(ctx.config_path)
    for error in errors:
        ctx.io.output(f"Device: {error}", "yellow")
    install_devices(ctx, devices)


# ── Dynamic long_help ─────────────────────────────────────────────────────────


_GRAMMAR_HELP: Final[str] = (
    "A device file is a part's registers as data: dev/<name>.device.json in the\n"
    "config folder, merged over the build's symbols at load and untouched by\n"
    "/sym.import.  /dev.import converts a vendor's register description into one:\n"
    "\n"
    "  /dev.import ATSAME54P20A.svd          - CMSIS-SVD, sniffed\n"
    "  /dev.import part.xml format=<fmt>     - explicit format (built-in or plugin)\n"
    "  /dev.list                             - what this config has loaded\n"
    "  /dev.lib {pattern}                    - what the library offers\n"
    "\n"
    "The file lands as dev/<device>.device.json and loads at once; /sym.info\n"
    "lists it, /sym.search finds its registers, /mem.* reads them by name.\n"
    "Vendor SVDs come from the chip vendor's CMSIS pack or the cmsis-svd-data\n"
    "repository.  See /help symbols, \"Parts: device files\"."
)


def _long_help(ctx: PluginContext) -> str:
    devices = get_devices(ctx)
    state = ", ".join(f"{device.name} ({len(device)})" for device in devices) or "none"
    return compose(state_line("devices", state), _GRAMMAR_HELP)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="dev",
    help="Device files: a part's registers as data (see /help symbols).",
    long_help=_long_help,
    sub_commands={
        "import": Command(
            params=[
                ParamSpec(
                    "file", "path", positional=True, required=True, rest=True,
                    help="vendor register description to convert (CMSIS-SVD)",
                ),
                # A str, not an enum: plugin folders add converters at
                # runtime, so the valid set is not knowable at import.
                ParamSpec(
                    "format", "str",
                    help=f"file format ({', '.join(FORMATS)}, or a plugin "
                         "converter); omitted = sniff the file",
                ),
            ],
            help="Convert a vendor register description to dev/<device>.device.json and load it.",
            handler=_handler_import,
        ),
        "list": Command(
            help="List the devices this config has loaded, with their layer and placement.",
            handler=_handler_list,
        ),
        "lib": Command(
            params=[
                ParamSpec(
                    "pattern", "str", positional=True, rest=True,
                    help="filter by part or category; glob (pic32*) or substring",
                ),
            ],
            help="List the parts the device library holds (available, not loaded).",
            handler=_handler_lib,
        ),
    },
)
