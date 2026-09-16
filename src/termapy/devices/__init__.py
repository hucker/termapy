"""Device files -- the registers of a memory-mapped part, as data termapy owns.

A linker map holds every symbol the firmware DEFINES.  It does not hold the
registers of the silicon it runs on, because no linker ever sees them: they
are addresses the hardware fixes.  Without them ``/mem.dump PORTA_OUT`` has
nothing to resolve, and the addresses end up hand-copied into somewhere
they do not belong -- a per-config converter, or the symbol sidecar, where
the next ``/sym.import`` erases them.

A **device file** is that missing half: one flat JSON list of registers for
one part.  Deliberately "device", not "CPU" -- an FPGA register block on
the external bus, a memory-mapped FRAM and an MCU's own peripherals are the
same kind of fact, and a board usually has several.

Why a format of our own: there is no single standard to consume.  CMSIS-SVD
dominates (ARM, and RISC-V vendors adopted it for the tooling), IEEE 1685
IP-XACT and SystemRDL serve chip designers, Microchip PIC parts use EDC
``.PIC`` XML.  So, exactly as ``symbols/converters/xc32.py`` normalizes one
vendor's linker map into ``symbols.json``, converters normalize each of
those into THIS file and the runtime reads only this.  (``/dev.import``
with an SVD converter is the next step; this module is the format and the
loader.)

**The file** (``<cfg>/dev/<anything>.device.json``)::

    {
      "device_version": 1,
      "device": "atsame54",                    # identity; no whitespace
      "description": "Microchip SAM E54",      # optional metadata ...
      "vendor": "microchip",
      "source": "ATSAME54P20A.svd 2026-09-15", # ... where the rows came from
      "parts": ["ATSAME54P20A", "ATSAME54P19A"],
      "license": "Apache-2.0",
      "relocatable": false,                    # addr is absolute (default)
      "registers": [
        {"name": "PORTA_DIR", "addr": "0x40003000", "size": 4,
         "peripheral": "PORTA", "group": "PORT",
         "description": "Data Direction", "reset": "0x00000000",
         "access": "rw", "read_effect": false, "rmw": true,
         "fields": [
           {"name": "DIR", "bit": 0, "width": 32}
         ]}
      ]
    }

Per register, only ``name``, ``addr`` and ``size`` are required.  ``type``
(the ``/mem.read`` format spec) is DERIVED from ``fields`` when they are
given, so the viewer's structured fields and the reader's spec string can
never disagree; with neither, a scalar by size (``u32``).  ``fields`` may
carry ``values`` (``{"0": "INPUT", "1": "OUTPUT"}``) for a viewer to label.

**The safety fields are the reason the format exists in this shape.**
``access`` (``rw`` / ``ro`` / ``wo``) and ``read_effect`` (reading changes
state: a FIFO ``DATA`` register pops, a status register clears its flags)
come from the vendor's SVD for free and are what let a register viewer
auto-refresh a peripheral without eating the UART's bytes.  They ride on
``Symbol`` beside ``rmw`` because the memory commands act on them; the
human-facing extras (``peripheral``, ``description``, ``reset``, ``fields``)
live on :class:`Register` here and never reach the sidecar.

**Placement -- N of the same chip** needs a base and a distinct name, or
two ``STATUS`` registers silently collapse to one::

    {"device": "ads1256", "relocatable": true,
     "instances": [{"name": "ADC1", "base": "0x60000000"},
                   {"name": "ADC2", "base": "0x60000100"}],
     "registers": [{"name": "STATUS", "addr": "0x00", "size": 1}]}

yields ``ADC1_STATUS`` at ``0x60000000`` and ``ADC2_STATUS`` at
``0x60000100``.  A relocatable file with no instances is an error, never a
silent load at offset 0 -- that is real memory on plenty of parts, and this
is the wrong-row-wrong-address hazard the memory commands exist to avoid.
No ``instances`` means one copy, base 0, no prefix: the MCU case, whose
names are already global.

**Where files live, and why this differs from plugins.**  ``dev/`` is the
board's bill of materials: everything in ``<cfg>/dev/`` loads, a file in
``<cfg_dir>/dev/`` loads into every config here, and a per-config file
replaces a global one with the same ``device``.  A shipped catalog, when
one exists, is a place to COPY from and never a load layer -- a thousand
parts cannot load into every config, and a relocatable part cannot load
until a config says where it sits.  ``TERMAPY_TRUSTED_PLUGINS_ONLY`` does
not drop these: that switch stops arbitrary Python, and a device file is
inert data validated on load.

Two files in one folder naming the same ``device`` is an error; a register
name shared by two loaded devices is an error that skips the later one and
says which -- both are the "give it an instance name" mistake surfacing.
A build symbol sharing a name with a register is reported and the BUILD
wins: the user's own code is what they typed the name expecting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from termapy import folders
from termapy.symbols.table import Symbol, check_type, parse_addr_field

DEVICE_VERSION: Final[int] = 1

# The section every register row carries; the label /sym.info shows.
DEVICE_SECTION: Final[str] = "sfr"

SUFFIX: Final[str] = ".device.json"

ACCESS_MODES: Final[tuple[str, ...]] = ("rw", "ro", "wo")

# A register, field or instance name must be typeable at the prompt as a
# bare token and must not contain ".", which the address grammar reserves
# for bit/field access.  This also refuses an unexpanded SVD placeholder
# (``PORT%s_DIR``), which would otherwise load and be unreachable.
_IDENT_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A device name reaches ``Symbol.file`` and the ``name@file`` grammar,
# whose file matching splits on "." (stem), so dots are out; dashes are
# in because part numbers have them.
_DEVICE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Scalar type by register width when a file gives neither type nor fields.
_SCALAR_BY_SIZE: Final[dict[int, str]] = {1: "u8", 2: "u16", 4: "u32", 8: "u64"}


@dataclass(frozen=True)
class Field:
    """One named bit range inside a register.

    Attributes:
        name: Field name, an identifier.
        bit: Lowest bit, 0-based within the register's value.
        width: Bits wide; 1 = a flag.
        description: One line, for a viewer.
        values: ``(value, label)`` pairs for an enumerated field.
    """

    name: str
    bit: int
    width: int = 1
    description: str = ""
    values: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class Register:
    """One register as a viewer sees it: the resolvable symbol plus context.

    Attributes:
        symbol: What lookup and the memory commands use -- the (possibly
            instance-prefixed) name, the (possibly based) address, size,
            derived ``type``, and the safety trio ``rmw`` / ``access`` /
            ``read_effect``.  ``Symbol.file`` carries the device name.
        peripheral: The block this register belongs to (``PORTA``).
        group: The peripheral's family (``PORT``), for a two-level tree.
        description: One line, from the datasheet.
        reset: Reset value, or None when the file does not say.
        fields: Structured bit fields; the source of ``symbol.type``.
    """

    symbol: Symbol
    peripheral: str = ""
    group: str = ""
    description: str = ""
    reset: int | None = None
    fields: tuple[Field, ...] = ()


@dataclass(frozen=True)
class Instance:
    """One placed copy of a device: its registers, prefixed and based.

    Attributes:
        name: Instance name and register prefix; ``""`` for the implicit
            single instance of a non-relocatable part.
        base: Address added to every register offset.
        registers: The concrete registers.
    """

    name: str
    base: int
    registers: tuple[Register, ...]


@dataclass(frozen=True)
class Device:
    """One loaded device file, fully expanded.

    Attributes:
        name: The ``device`` field -- identity for override and collision.
        description: One line for ``/sym.info``.
        vendor: Metadata.
        source: Where the rows came from (an SVD file, a datasheet, a hand).
        parts: Exact part numbers this file covers.
        license: License of the data, for a shared file.
        relocatable: True when ``addr`` values are offsets from a base.
        instances: The placed copies (exactly one, unnamed, when not
            relocatable).
        path: File it came from.
        layer: ``"global"`` or the config name.
    """

    name: str
    description: str = ""
    vendor: str = ""
    source: str = ""
    parts: tuple[str, ...] = ()
    license: str = ""
    relocatable: bool = False
    instances: tuple[Instance, ...] = ()
    path: Path | None = None
    layer: str = ""

    def __len__(self) -> int:
        return sum(len(instance.registers) for instance in self.instances)

    def registers(self) -> list[Register]:
        """Every concrete register across every instance, in file order."""
        return [
            register for instance in self.instances for register in instance.registers
        ]


# ── Parsing ────────────────────────────────────────────────────────────────


def parse_device(data: Any, *, path: Path | None = None, layer: str = "") -> Device:
    """Validate a decoded device document and expand its instances.

    Field-qualified errors, matching ``SymbolTable.from_dict``: a handler
    renders the message and nothing else needs an error list.

    Args:
        data: The decoded JSON value.
        path: Recorded on the Device.
        layer: Layer label recorded on the Device.

    Returns:
        The Device, instances expanded.

    Raises:
        ValueError: The first problem found, field-qualified.
    """
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    version = data.get("device_version", "missing")
    if isinstance(version, bool) or version != DEVICE_VERSION:
        raise ValueError(f"device_version: expected {DEVICE_VERSION}, got {version}")
    name = data.get("device")
    if not isinstance(name, str) or not _DEVICE_RE.match(name):
        raise ValueError(
            "device: expected a name of letters, digits, '_' or '-'"
        )
    texts = {key: _text(data, key) for key in ("description", "vendor", "source", "license")}
    parts = data.get("parts", [])
    if not isinstance(parts, list) or not all(isinstance(part, str) for part in parts):
        raise ValueError("parts: expected a list of strings")
    relocatable = data.get("relocatable", False)
    if not isinstance(relocatable, bool):
        raise ValueError("relocatable: expected a boolean")
    raw_registers = data.get("registers")
    if not isinstance(raw_registers, list):
        raise ValueError("registers: expected a list")
    templates = [
        _register_template(raw, f"registers[{i}]", name)
        for i, raw in enumerate(raw_registers)
    ]
    _check_unique([register.symbol.name for register in templates], "registers", "name")
    instances = _expand(templates, data.get("instances"), relocatable)
    return Device(
        name,
        texts["description"],
        texts["vendor"],
        texts["source"],
        tuple(parts),
        texts["license"],
        relocatable,
        instances,
        path=path,
        layer=layer,
    )


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key}: expected a string")
    return value


def _ident(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise ValueError(
            f"{where}.{key}: expected an identifier (letters, digits, '_'; no '.')"
        )
    return value


def _check_unique(names: list[str], where: str, key: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"{where}: duplicate {key} {name!r}")
        seen.add(name)


def _register_template(raw: Any, where: str, device: str) -> Register:
    """One ``registers[i]`` object -> an unplaced Register (offset address).

    Narrower than ``Symbol.from_dict`` on purpose: a register has no
    ``file`` (that slot carries the device) and no ``section`` (always
    ``sfr``), so accepting those would invite files that disagree with
    the model.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected an object")
    name = _ident(raw, "name", where)
    addr = parse_addr_field(raw.get("addr"), where)
    size = raw.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError(f"{where}.size: expected a positive integer (bytes)")
    fields = _parse_fields(raw.get("fields"), size, where)
    type_spec = raw.get("type", "")
    if not isinstance(type_spec, str):
        raise ValueError(f"{where}.type: expected a string")
    if fields and type_spec:
        raise ValueError(f"{where}.type: fields are given; type is derived from them")
    if fields:
        type_spec = derive_type(size, fields)
    elif not type_spec:
        type_spec = _SCALAR_BY_SIZE.get(size, "")
    check_type(type_spec, where)
    access = raw.get("access", "rw")
    if access not in ACCESS_MODES:
        raise ValueError(f"{where}.access: expected one of {', '.join(ACCESS_MODES)}")
    flags: dict[str, bool] = {}
    for key, default in (("read_effect", False), ("rmw", True)):
        value = raw.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"{where}.{key}: expected a boolean")
        flags[key] = value
    texts = {}
    for key in ("peripheral", "group", "description", "space"):
        value = raw.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{where}.{key}: expected a string")
        texts[key] = value
    reset_raw = raw.get("reset")
    reset = None if reset_raw is None else parse_addr_field(reset_raw, where, key="reset")
    symbol = Symbol(
        name, addr, size, DEVICE_SECTION,
        file=device, type=type_spec, space=texts["space"],
        rmw=flags["rmw"], access=access, read_effect=flags["read_effect"],
    )
    return Register(
        symbol, texts["peripheral"], texts["group"], texts["description"], reset, fields,
    )


def _parse_fields(raw: Any, size: int, where: str) -> tuple[Field, ...]:
    """``fields`` -> Field tuple; absent = no fields."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{where}.fields: expected a list")
    out: list[Field] = []
    for i, item in enumerate(raw):
        here = f"{where}.fields[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{here}: expected an object")
        name = _ident(item, "name", here)
        bit = item.get("bit")
        width = item.get("width", 1)
        for key, value in (("bit", bit), ("width", width)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{here}.{key}: expected a non-negative integer")
        if width < 1 or bit + width > size * 8:
            raise ValueError(
                f"{here}: bits {bit}-{bit + width - 1} do not fit a {size}-byte register"
            )
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"{here}.description: expected a string")
        out.append(Field(name, bit, width, description, _parse_values(item.get("values"), here)))
    _check_unique([field.name for field in out], f"{where}.fields", "name")
    return tuple(out)


def _parse_values(raw: Any, where: str) -> tuple[tuple[int, str], ...]:
    """``values`` -> ``(int, label)`` pairs; JSON keys are strings."""
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{where}.values: expected an object of value -> label")
    pairs: list[tuple[int, str]] = []
    for key, label in raw.items():
        try:
            value = int(key, 0)
        except ValueError:
            raise ValueError(f"{where}.values: key {key!r} is not an integer") from None
        if not isinstance(label, str):
            raise ValueError(f"{where}.values[{key}]: expected a string label")
        pairs.append((value, label))
    return tuple(pairs)


def derive_type(size: int, fields: tuple[Field, ...]) -> str:
    """The ``/mem.read`` format spec for a register's fields.

    One direction only -- fields are the source, the spec is derived --
    so the structured view and the reader can never drift.  Uses the
    little-endian multi-byte form (``B4-1``) the format-spec language
    already reads, single-byte ``B1`` for 8-bit registers.

    Args:
        size: Register width in bytes.
        fields: The fields, in file order.

    Returns:
        A spec such as ``"EVEN:B1.0-3 ODD:B1.4-7"``.
    """
    bytes_ref = "B1" if size == 1 else f"B{size}-1"
    columns = []
    for item in fields:
        bits = str(item.bit) if item.width == 1 else f"{item.bit}-{item.bit + item.width - 1}"
        columns.append(f"{item.name}:{bytes_ref}.{bits}")
    return " ".join(columns)


def _expand(
    templates: list[Register], raw_instances: Any, relocatable: bool,
) -> tuple[Instance, ...]:
    """Place the template registers: one implicit copy, or one per instance."""
    if not relocatable:
        if raw_instances:
            raise ValueError(
                "instances: only a relocatable device has instances "
                "(set \"relocatable\": true if addr values are offsets)"
            )
        return (Instance("", 0, tuple(templates)),)
    if not isinstance(raw_instances, list) or not raw_instances:
        raise ValueError(
            "instances: a relocatable device needs at least one "
            "{\"name\": ..., \"base\": ...} -- its addr values are offsets"
        )
    instances: list[Instance] = []
    for i, raw in enumerate(raw_instances):
        where = f"instances[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: expected an object")
        name = _ident(raw, "name", where)
        base = parse_addr_field(raw.get("base"), where, key="base")
        placed = tuple(
            replace(
                template,
                symbol=replace(
                    template.symbol,
                    name=f"{name}_{template.symbol.name}",
                    addr=base + template.symbol.addr,
                ),
            )
            for template in templates
        )
        instances.append(Instance(name, base, placed))
    _check_unique([instance.name for instance in instances], "instances", "name")
    return tuple(instances)


# ── Loading ────────────────────────────────────────────────────────────────


def load_device(path: Path, layer: str = "") -> Device:
    """Read and validate one device file.

    Raises:
        OSError: The file could not be read.
        ValueError: Invalid JSON or an invalid shape.
    """
    return parse_device(
        json.loads(path.read_text(encoding="utf-8")), path=path, layer=layer,
    )


@dataclass
class DeviceLoad:
    """What one folder scan produced.

    Attributes:
        devices: Successfully loaded devices, in filename order.
        errors: ``"<file>: <problem>"`` for each file that did not load.
    """

    devices: list[Device] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_devices_from_dir(folder: Path, layer: str = "") -> DeviceLoad:
    """Load every ``*.device.json`` in one folder.

    A broken file is recorded and skipped, never fatal: one bad device
    must not cost the user every other register name.  Two files naming
    the same ``device`` is an error on the second.

    Args:
        folder: Directory to scan; a missing one yields an empty result.
        layer: Layer label recorded on each Device.

    Returns:
        The devices and the per-file errors.
    """
    result = DeviceLoad()
    if not folder.is_dir():
        return result
    seen: dict[str, str] = {}
    for file in sorted(folder.glob(f"*{SUFFIX}")):
        try:
            device = load_device(file, layer)
        except (OSError, ValueError) as e:
            result.errors.append(f"{file.name}: {e}")
            continue
        if device.name in seen:
            result.errors.append(
                f"{file.name}: device {device.name!r} is already defined by "
                f"{seen[device.name]} (N copies of a part are instances, not files)"
            )
            continue
        seen[device.name] = file.name
        result.devices.append(device)
    return result


def resolve_devices(
    config_path: str, global_root: Path | None = None,
) -> tuple[list[Device], list[str]]:
    """Every device a config loads: global then per-config, later wins.

    After layering, a register name owned by two devices is an error that
    skips the later device -- the "two ADCs without instance names"
    mistake, caught before one silently hides the other.

    Args:
        config_path: The active config file; ``""`` = no config, no devices.
        global_root: Folder holding ``termapy_cfg``; None resolves the
            real one.

    Returns:
        ``(devices, errors)`` -- devices in load order, one per name.
    """
    if not config_path:
        return [], []
    by_name: dict[str, Device] = {}
    errors: list[str] = []
    for folder, layer in _layers(config_path, global_root):
        load = load_devices_from_dir(folder, layer)
        errors.extend(load.errors)
        for device in load.devices:
            by_name[device.name] = device
    owner: dict[str, str] = {}
    kept: list[Device] = []
    for device in by_name.values():
        clash = next(
            (register.symbol.name for register in device.registers()
             if register.symbol.name in owner),
            None,
        )
        if clash is not None:
            errors.append(
                f"{_label(device)}: register {clash} is also defined by "
                f"{owner[clash]}; give one an instance name"
            )
            continue
        for register in device.registers():
            owner[register.symbol.name] = _label(device)
        kept.append(device)
    return kept, errors


def _label(device: Device) -> str:
    return device.path.name if device.path is not None else device.name


def device_dir(config_path: str) -> Path:
    """The config's own ``dev/`` folder (a path; it may not exist yet).

    ``cfg_data_dir``, not the cfg's parent: a bundled (read-only) config
    resolves its data folders elsewhere, and devices must follow.  Lazy
    import of ``config``: it pulls migration and the cfg root, and this
    module sits on the symbol load path.
    """
    from termapy.config import cfg_data_dir

    try:
        return cfg_data_dir(config_path) / folders.DEV
    except (OSError, ValueError):
        return Path(config_path).parent / folders.DEV


def _layers(config_path: str, global_root: Path | None) -> list[tuple[Path, str]]:
    """``(folder, label)`` per layer, lowest precedence first."""
    from termapy.config import global_devices_dir

    global_folder = global_devices_dir(global_root)
    per_config = device_dir(config_path)
    layers: list[tuple[Path, str]] = []
    if global_folder != per_config:
        layers.append((global_folder, "global"))
    layers.append((per_config, Path(config_path).stem))
    return layers


# ── Merging ────────────────────────────────────────────────────────────────


def merge_into(
    symbols: list[Symbol], devices: list[Device],
) -> tuple[list[Symbol], list[str]]:
    """Build symbols plus device registers; the build wins a name clash.

    The two populations barely overlap -- linker symbols live in RAM and
    flash, registers in peripheral space -- but when a name collides the
    user's own code is what they typed the name expecting, so the build
    symbol stays and the register is reported, not silently dropped.

    Args:
        symbols: Symbols from the loaded build table.
        devices: Devices in load order (already collision-free among
            themselves, per :func:`resolve_devices`).

    Returns:
        ``(merged, shadowed)`` -- a new list, and the register names the
        build shadowed.
    """
    taken = {symbol.name for symbol in symbols}
    merged = list(symbols)
    shadowed: list[str] = []
    for device in devices:
        for register in device.registers():
            if register.symbol.name in taken:
                shadowed.append(register.symbol.name)
                continue
            merged.append(register.symbol)
            taken.add(register.symbol.name)
    return merged, shadowed


# ── Peripheral space ───────────────────────────────────────────────────────


def registers_in_range(
    devices: list[Device], addr: int, length: int,
) -> list[Register]:
    """Every loaded register whose bytes fall in ``[addr, addr + length)``.

    This is the whole of termapy's knowledge of where peripheral space IS:
    an address is peripheral because a loaded device file says a register
    lives there, never because of a range table or a guess about the part.
    Nothing here needs to know what any individual register DOES, which is
    what makes it usable -- no vendor ships the per-register read-safety
    data that a smarter check would need (``readAction`` is empty in every
    SVD checked), so a bulk reader gets address containment or nothing.

    A linear scan: a big part is a few thousand registers, and every caller
    is about to pay a serial round trip that costs orders of magnitude more.

    Args:
        devices: The loaded devices (``symbols.session.get_devices``).
        addr: First byte of the range.
        length: Byte count; 0 or less matches nothing.

    Returns:
        The overlapping registers, sorted by address, then name.
    """
    if length <= 0:
        return []
    end = addr + length
    hits = [
        register
        for device in devices
        for register in device.registers()
        if register.symbol.addr < end and addr < register.symbol.end
    ]
    return sorted(hits, key=lambda register: (register.symbol.addr, register.symbol.name))


def distinct_spans(registers: list[Register]) -> int:
    """How many distinct byte ranges a set of registers actually covers.

    Not ``len(registers)``, because one register is often described several
    times.  SVD models a peripheral's operating MODES as separate register
    definitions at one address -- a SERCOM's ``CTRLA`` appears as
    ``I2CM_CTRLA``, ``I2CS_CTRLA``, ``SPIM_CTRLA``, ``SPIS_CTRLA``,
    ``USART_INT_CTRLA`` and ``USART_EXT_CTRLA``, six names for the same four
    bytes (410 such addresses on a PIC32CM5164LE00100).  Counting entries
    would call that a six-register span and refuse a read of one register.

    Reading any one of those aliases touches exactly the same silicon, so
    for anything asking "how much would this read disturb?" they are one.

    Args:
        registers: Overlapping registers, typically from
            :func:`registers_in_range`.

    Returns:
        The number of distinct ``(addr, size)`` ranges.
    """
    return len({(register.symbol.addr, register.symbol.size) for register in registers})
