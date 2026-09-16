"""Built-in plugin: /dev.* -- device files: import a vendor register description.

The command surface only.  The format, the loader, instances and the
layered ``dev/`` folders live in :mod:`termapy.devices`; the converters in
:mod:`termapy.devices.converters`; the session merge in
:mod:`termapy.symbols.session`.  Core needs all of that at config load, so
none of it lives here (CLAUDE.md: infrastructure never lives under
``builtins/``).

- ``/dev.import <file> {format=...}`` -- convert a vendor register
  description (CMSIS-SVD) to ``dev/<device>.device.json`` and load it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from termapy.config import cfg_relative_path
from termapy.converters import DEVICE
from termapy.devices import SUFFIX, device_dir, load_devices_from_dir, parse_device
from termapy.devices.converters import FORMATS, find_converter
from termapy.folders import ensure_folder
from termapy.help_dynamic import compose, state_line
from termapy.plugins import BoundaryException, CmdResult, Command, UsageError
from termapy.plugins.params import ParamSpec
from termapy.symbols import get_devices, install_devices
from termapy.symbols.format import device_records

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
    },
)
