"""Built-in plugin: /dev.* -- device files: import a vendor register description.

The command surface only.  The format, the loader, instances and the
layered ``dev/`` folders live in :mod:`termapy.devices`; the converters in
:mod:`termapy.devices.converters`; the session merge in
:mod:`termapy.symbols.session`.  Core needs all of that at config load, so
none of it lives here (CLAUDE.md: infrastructure never lives under
``builtins/``).

- ``/dev.import <file> {format=...} {category=...} {use=on|off}`` -- convert
  a vendor register description (CMSIS-SVD) into the LIBRARY and reference
  it from this config.  A converted part is a fact about silicon, so it is
  stored once; the config gets three lines saying it uses it.
- ``/dev.use <part> {at=NAME@ADDR ...}`` -- add a library part to this
  config, with where it sits on THIS board.
- ``/dev.list`` -- the devices THIS config loaded, with layer and placement.
- ``/dev.lib {pattern}`` -- the parts the library holds.  A different
  question from ``/dev.list``: available, not installed.
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Final

from termapy.config import cfg_relative_path, library_dir, not_found_message
from termapy.converters import DEVICE
from termapy.devices import (
    SUFFIX,
    device_dir,
    library_path,
    load_devices_from_dir,
    parse_device,
    scan_library,
    write_library_part,
)
from termapy.devices.converters import FORMATS, find_converter
from termapy.folders import ensure_folder
from termapy.help_dynamic import compose, state_line
from termapy.plugins import BoundaryException, CmdResult, Command, UsageError
from termapy.plugins.params import ParamSpec
from termapy.symbols import get_devices, install_devices, parse_number
from termapy.symbols.format import (
    device_list_rows,
    device_records,
    hex_addr,
    library_records,
)

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
        return CmdResult.fail(msg=not_found_message("Source file", raw, ctx.config_path))
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
    # The part goes in the LIBRARY, once; the config gets a reference.  A
    # converted part is a fact about silicon, not about this board, and a
    # 2517-register MCU copied per config is megabytes duplicated to
    # customize nothing.  `use=off` keeps the old self-contained-copy
    # behavior for a part that is genuinely this board's alone.
    library = _library_root(ctx)
    dest = library_path(library, doc, str(ctx.arg("category") or ""))
    existing = _already_defined(library, device.name, dest)
    if existing is not None:
        return CmdResult.fail(
            msg=f"Device {device.name} is already defined by {existing}; "
            f"remove it first"
        )
    try:
        write_library_part(dest, doc)
    except OSError as e:
        return CmdResult.fail(msg=f"Write error: {e}")
    relative = dest.relative_to(library).as_posix()
    peripherals = len({register.peripheral for register in device.registers()})
    ctx.io.result(
        f"Imported {len(device)} registers for {device.name} from {path.name} "
        f"({spec.format}) -> lib/{relative}",
        "green",
    )
    ref_path = ""
    if bool(ctx.arg("use")):
        written = _write_reference(ctx, device.name, ())
        if isinstance(written, CmdResult):
            return written
        ref_path = str(written)
        _reload(ctx)
        ctx.io.output(
            f"  {peripherals} peripherals; referenced by dev/{written.name}"
        )
    else:
        ctx.io.output(
            f"  {peripherals} peripherals; {ctx.prefix}dev.use {device.name} "
            f"to add it to this config"
        )
    data = device_records([device])[0] | {
        "format": spec.format, "path": str(dest),
        "relative": relative, "reference": ref_path,
    }
    return CmdResult.ok(value=str(len(device)), data=data)


def _already_defined(library: Path, name: str, dest: Path) -> str | None:
    """The library path already holding ``name``, when it is not ``dest``."""
    for part in scan_library(library)[0]:
        if part.device == name and part.path != dest:
            return part.path.relative_to(library).as_posix()
    return None


def _write_reference(
    ctx: PluginContext, part: str, instances: tuple[dict[str, object], ...],
) -> Path | CmdResult:
    """Write ``dev/<part>.device.json`` as a reference to a library part."""
    folder = device_dir(ctx.config_path)
    dest = folder / f"{part}{SUFFIX}"
    for existing in load_devices_from_dir(folder, library=_library_root(ctx)).devices:
        if existing.name == part and existing.path != dest:
            return CmdResult.fail(
                msg=f"Device {part} is already defined by "
                f"{existing.path.name if existing.path else '?'}; remove it first"
            )
    doc: dict[str, object] = {"device_version": 1, "ref": part}
    if instances:
        doc["instances"] = list(instances)
    try:
        ensure_folder(folder)
        dest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return CmdResult.fail(msg=f"Write error: {e}")
    return dest


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


def _handler_use(ctx: PluginContext, args: str) -> CmdResult:
    """Add a library part to THIS config, with where it sits on this board."""
    part = str(ctx.arg("part") or "").strip()
    if not part:
        raise UsageError()
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    library = _library_root(ctx)
    match = next((p for p in scan_library(library)[0] if p.device == part), None)
    if match is None:
        return CmdResult.fail(
            msg=f"No library part named {part} "
            f"(try {ctx.prefix}dev.lib to see what there is)"
        )
    placements = str(ctx.arg("at") or "").split()
    instances = _parse_placements(placements)
    if isinstance(instances, CmdResult):
        return instances
    written = _write_reference(ctx, part, instances)
    if isinstance(written, CmdResult):
        return written
    _reload(ctx)
    loaded = next((d for d in get_devices(ctx) if d.name == part), None)
    if loaded is None:
        # The reference wrote but would not load -- a relocatable part with
        # no placement is the likely cause, and leaving the file behind
        # would make every later load report the same error.
        written.unlink(missing_ok=True)
        _reload(ctx)
        return CmdResult.fail(
            msg=f"{part} needs placement: it is relocatable, so give at least "
            f"one NAME@ADDRESS ({ctx.prefix}dev.use {part} at=ADC1@0x60000000)"
        )
    where = " ".join(
        f"{instance.name}@{hex_addr(instance.base)}"
        for instance in loaded.instances if instance.name
    )
    ctx.io.result(
        f"Using {part}: {len(loaded)} registers"
        f"{' at ' + where if where else ''} -> dev/{written.name}",
        "green",
    )
    return CmdResult.ok(
        value=str(len(loaded)),
        data=device_records([loaded])[0] | {"reference": str(written)},
    )


def _parse_placements(
    tokens: list[str],
) -> tuple[dict[str, object], ...] | CmdResult:
    """``NAME@ADDR`` tokens -> instance objects, or the failure to return."""
    instances: list[dict[str, object]] = []
    for token in tokens:
        name, _, addr = token.partition("@")
        if not name or not addr:
            return CmdResult.fail(
                msg=f"Invalid placement: {token} (expected NAME@ADDRESS)"
            )
        base = parse_number(addr)
        if base is None:
            return CmdResult.fail(msg=f"Invalid address: {addr}")
        instances.append({"name": name, "base": base})
    return tuple(instances)


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
    "A device file is a part's registers as data, merged over the build's symbols\n"
    "at load and untouched by /sym.import.  Parts live ONCE in the library\n"
    "(lib/<vendor>/...); a config says which it uses with a three-line dev/ file:\n"
    "\n"
    "  /dev.import ATSAME54P20A.svd          - convert into lib/, use it here\n"
    "  /dev.import p.svd category=mcu/pic32  - file it under lib/<vendor>/mcu/pic32\n"
    "  /dev.import p.svd use=off             - library only, do not add it here\n"
    "  /dev.use <part>                       - add a library part to this config\n"
    "  /dev.use <part> at=ADC1@0x60000000    - ... and say where it sits\n"
    "  /dev.list                             - what this config has loaded\n"
    "  /dev.lib {pattern}                    - what the library offers\n"
    "\n"
    "Storing a part once is the point: a 2500-register MCU is a fact about the\n"
    "silicon, not about one board, and the reference keeps this board's addresses\n"
    "so re-importing a newer conversion cannot lose them.  A self-contained\n"
    "dev/*.device.json still works for a one-off part.  /sym.info lists loaded\n"
    "devices, /sym.search finds registers, /mem.* reads them by name.  Vendor SVDs\n"
    "come from the chip vendor's CMSIS pack or the cmsis-svd-data repository.\n"
    "See /help symbols, \"Parts: device files\"."
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
                ParamSpec(
                    "category", "str",
                    help="sub-path under the vendor in lib/ (mcu/pic32cm)",
                ),
                ParamSpec(
                    "use", "bool", default=True,
                    help="also add it to this config (off = library only)",
                ),
            ],
            help="Convert a vendor register description to dev/<device>.device.json and load it.",
            handler=_handler_import,
        ),
        "list": Command(
            help="List the devices this config has loaded, with their layer and placement.",
            handler=_handler_list,
        ),
        "use": Command(
            params=[
                ParamSpec(
                    "part", "str", positional=True, required=True,
                    help="library part name (see /dev.lib)",
                ),
                ParamSpec(
                    "at", "str", rest=True,
                    help="placement for a relocatable part: NAME@ADDR ...",
                ),
            ],
            help="Add a library part to this config, with where it sits on this board.",
            handler=_handler_use,
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
