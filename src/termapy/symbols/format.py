"""Prose renderers and their structured ``data=`` twins, side by side.

The ``port_format.row_from_facts`` / ``facts_to_json_record`` pattern: every
record a ``/sym.*`` handler returns as ``CmdResult.data`` is produced HERE,
never inline in the handler.  Records have a FIXED shape -- every key
present, ``null`` / ``""`` / ``0`` for unknowns, numeric ``addr`` beside
``addr_hex`` -- so an agent can filter numerically or match strings, and a
hit and a miss carry the same keys.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from termapy.plugins.views import Column
from termapy.symbols.address import Address
from termapy.symbols.provenance import IN_SYNC, UNKNOWN, check_staleness
from termapy.symbols.table import Region, Symbol, SymbolTable, hex_digits, section_label

if TYPE_CHECKING:
    from termapy.devices import Device, LibraryPart, Register


def hex_addr(addr: int, address_bits: int = 32) -> str:
    """``0x``-prefixed, zero-padded to the table's address width."""
    return f"0x{addr:0{hex_digits(address_bits)}X}"


def symbolic_name(symbol: Symbol, addr: int) -> str:
    """``main`` at the start, ``main+0x10`` inside or above, ``main-0x4`` below."""
    if addr == symbol.addr:
        return symbol.name
    if addr > symbol.addr:
        return f"{symbol.name}+0x{addr - symbol.addr:X}"
    return f"{symbol.name}-0x{symbol.addr - addr:X}"


def format_symbol(
    symbol: Symbol,
    query_addr: int | None = None,
    *,
    address_bits: int = 32,
) -> str:
    """One display line: ``0x00002010  main+0x10  [code 442 bytes main.c]``.

    A constant ends with its value (``= 4``; a pointer ``-> 0x41008080``),
    an alias with the register it names (``= PORTC_DIR``).

    Args:
        symbol: The symbol to render.
        query_addr: The address that was asked for; when given, the line
            shows it and the symbolic offset from ``symbol``.
        address_bits: Hex width of the printed address.

    Returns:
        The line.  Size is omitted when 0, file when empty.
    """
    addr = symbol.addr if query_addr is None else query_addr
    name = symbol.name if query_addr is None else symbolic_name(symbol, query_addr)
    size_str = f" {symbol.size} bytes" if symbol.size else ""
    file_str = f" {symbol.file}" if symbol.file else ""
    tail = _value_text(symbol, address_bits)
    return f"{hex_addr(addr, address_bits)}  {name}  [{symbol.section_label}{size_str}{file_str}]{tail}"


def _value_text(symbol: Symbol, address_bits: int) -> str:
    """The display tail of a line: two spaces and :func:`symbol_note`, or nothing."""
    note = symbol_note(symbol_record(symbol, address_bits=address_bits), address_bits=address_bits)
    return f"  {note}" if note else ""


def symbol_note(record: dict[str, Any], *, address_bits: int = 32) -> str:
    """What a symbol stands for: ``= PORTC_DIR`` (alias), ``-> 0x...`` (pointer), ``= 4`` (value), ``""`` for none.

    The one phrasing: :func:`format_symbol`'s tail and the ``/sym.search``
    table's NOTE column both read it, so the line and the table cannot drift.
    """
    if record["alias_of"]:
        return f"= {record['alias_of']}"
    if record["value"] is None:
        return ""
    if record["pointee"] and isinstance(record["value"], int):
        return f"-> {hex_addr(record['value'], address_bits)}"
    return f"= {record['value']}"


def symbol_columns(*, address_bits: int = 32) -> tuple[Column, ...]:
    """The ``/sym.search`` table over :func:`symbol_record`: every fact :func:`format_symbol` shows, a column each.

    Clicking a name runs ``/sym <name>``, a lookup that changes nothing.

    Args:
        address_bits: Address width for a pointer's target in NOTE.
    """
    return (
        Column("ADDRESS", lambda record: record["addr_hex"]),
        Column("NAME", lambda record: record["name"], run=lambda record: f"sym {record['name']}"),
        Column("SECTION", lambda record: section_label(record["section"])),
        Column("SIZE", lambda record: str(record["size"]) if record["size"] else "", right=True),
        Column("FILE", lambda record: record["file"]),
        Column(
            "NOTE",
            lambda record: symbol_note(record, address_bits=address_bits),
            hide_empty=True,
        ),
    )


def symbol_record(symbol: Symbol, *, address_bits: int = 32) -> dict[str, Any]:
    """The agent record for one symbol (int and hex addresses; every key)."""
    return {
        "name": symbol.name,
        "addr": symbol.addr,
        "addr_hex": hex_addr(symbol.addr, address_bits),
        "end": symbol.end,
        "size": symbol.size,
        "section": symbol.section,
        "file": symbol.file,
        "type": symbol.type,
        "space": symbol.space,
        "rmw": symbol.rmw,
        "access": symbol.access,
        "read_effect": symbol.read_effect,
        "value": symbol.value,
        "pointee": symbol.pointee,
        "alias_of": symbol.alias_of,
    }


def region_record(region: Region, *, address_bits: int = 32) -> dict[str, Any]:
    """The agent record for one memory region (int and hex start; every key)."""
    return {
        "name": region.name,
        "start": region.start,
        "start_hex": hex_addr(region.start, address_bits),
        "end": region.end,
        "size": region.size,
        "access": region.access,
        "space": region.space,
    }


def format_region(region: Region, *, address_bits: int = 32) -> str:
    """``rom 0x00000000+0x80000 ro`` -- the linker script's ORIGIN and LENGTH."""
    access = f" {region.access}" if region.access else ""
    return f"{region.name} {hex_addr(region.start, address_bits)}+0x{region.size:X}{access}"


def lookup_record(
    parsed: Address,
    at: Symbol | None,
    *,
    address_bits: int = 32,
) -> dict[str, Any]:
    """The ``/sym`` record: what was asked, what it resolved to, what is there.

    Args:
        parsed: The resolved address.
        at: The symbol at ``parsed.addr`` per ``table.lookup``, or None.
        address_bits: Hex width for ``addr_hex``.

    Returns:
        A record with the same keys on hit and miss (``symbol`` is null on
        a miss).
    """
    return {
        "query": parsed.text,
        "addr": parsed.addr,
        "addr_hex": hex_addr(parsed.addr, address_bits),
        "symbolic": symbolic_name(at, parsed.addr) if at is not None else "",
        "symbol": symbol_record(at, address_bits=address_bits) if at is not None else None,
        "offset": parsed.offset,
        "suffix": parsed.suffix,
    }


def device_columns(*, address_bits: int = 32) -> tuple[Column, ...]:
    """The ``/dev.list`` table over :func:`device_records`: one row per loaded device.

    The answer to "what is on this board": the part, its register count,
    the LAYER it came from (a part loaded globally and one this config
    loaded are different facts a flat list would hide), and its placement
    -- the instance names and bases of a relocatable part, empty for a
    fixed-address one, since only a placed part can collide with another
    copy.

    Args:
        address_bits: Address width for rendering instance bases.
    """

    def placement(record: dict[str, Any]) -> str:
        return " ".join(
            f"{instance['name']}@{hex_addr(instance['base'], address_bits)}"
            for instance in record["instances"]
            if instance["name"]
        )

    return (
        Column("DEVICE", lambda record: record["device"]),
        Column("REGISTERS", lambda record: str(record["registers"]), right=True),
        Column("LAYER", lambda record: record["layer"] or "-"),
        Column("PLACEMENT", placement),
    )


def library_columns() -> tuple[Column, ...]:
    """The ``/dev.lib`` table over :func:`library_records`: one row per available part.

    LOADED says whether this config already has the part, in the same
    glance as the part; clicking a part puts ``/dev.use <part>`` in the
    input line rather than running it, since using a part changes the
    config.
    """
    return (
        Column(
            "PART",
            lambda record: record["device"],
            prefill=lambda record: f"dev.use {record['device']}",
        ),
        Column("LOADED", lambda record: "yes" if record["loaded"] else ""),
        Column(
            "REGISTERS",
            lambda record: str(record["registers"]) if record["registers"] >= 0 else "?",
            right=True,
        ),
        Column("LAYER", lambda record: record["layer"]),
        Column("CATEGORY", lambda record: record["category"] or "-"),
    )


def register_flags(record: dict[str, Any]) -> str:
    """The hazards a register record carries, spelled as its device file spells them.

    ``read_effect`` (reading it changes device state) and ``rmw: false``
    (a masked write would write back, and so clear, flags nobody named):
    the two facts that make an innocent-looking command touch the chip
    in a way the user did not ask for.  ``""`` for a plain register.
    """
    flags = []
    if record["read_effect"]:
        flags.append("read_effect")
    if not record["rmw"]:
        flags.append("rmw: false")
    return ", ".join(flags)


def register_columns() -> tuple[Column, ...]:
    """The ``/dev.regs`` table over :func:`register_records`: one row per register.

    FLAGS is drawn in yellow so a hazard is seen before anyone reads the
    register.  No cell is clickable: a click that prefilled a read would
    put the hazard one Enter away.
    """
    return (
        Column("ADDRESS", lambda record: record["addr_hex"]),
        Column("NAME", lambda record: record["name"]),
        Column("SIZE", lambda record: str(record["size"]), right=True),
        Column("ACCESS", lambda record: record["access"]),
        Column("FLAGS", register_flags, style=lambda record: "yellow"),
        Column("PERIPHERAL", lambda record: record["peripheral"] or "-"),
        Column("DESCRIPTION", lambda record: record["description"]),
    )


def register_records(registers: list[Register], *, address_bits: int = 32) -> list[dict[str, Any]]:
    """The ``data=`` twin for ``/dev.regs``: one record per register, from the device file.

    Everything the file says about a register: where it is, the safety
    trio the memory commands act on (``access``, ``read_effect``,
    ``rmw``), its context, its reset value (None when the file does not
    say) and its bit fields -- what a caller needs to decide whether a
    read is safe before making one.

    Args:
        registers: The registers to describe (``Device.registers``).
        address_bits: Address width for ``addr_hex``.
    """
    return [
        {
            "name": register.symbol.name,
            "addr": register.symbol.addr,
            "addr_hex": hex_addr(register.symbol.addr, address_bits),
            "size": register.symbol.size,
            "access": register.symbol.access,
            "read_effect": register.symbol.read_effect,
            "rmw": register.symbol.rmw,
            "peripheral": register.peripheral,
            "group": register.group,
            "description": register.description,
            "reset": register.reset,
            "fields": [
                {
                    "name": field.name,
                    "bit": field.bit,
                    "width": field.width,
                    "description": field.description,
                    "values": {str(value): label for value, label in field.values},
                }
                for field in register.fields
            ],
        }
        for register in registers
    ]


def library_records(
    parts: list[LibraryPart], loaded: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """The ``data=`` twin for a library listing: one record per available part.

    Distinct from :func:`device_records` because these are parts a config
    COULD use, not devices it has loaded -- there is no placement, and
    ``registers`` is what the file claims rather than what a parse
    produced.  ``layer`` says which library holds the part (the per-config
    one shadows the global), so an agent can tell a shared part from a
    board-local one.  ``loaded`` says whether this config already has
    the part.

    Args:
        parts: The library parts to describe.
        loaded: Names of the devices this config has loaded.
    """
    return [
        {
            "device": part.device,
            "loaded": part.device in loaded,
            "description": part.description,
            "vendor": part.vendor,
            "category": part.category,
            "registers": part.registers,
            "layer": part.layer,
            "path": str(part.path),
            "relative": part.relative,
        }
        for part in parts
    ]


def device_records(devices: list[Device]) -> list[dict[str, Any]]:
    """The ``data=`` twin for loaded device files: one fixed-shape record each.

    Lives here, not in ``termapy.devices``, so that module never has to be
    imported by this one: ``devices`` imports ``symbols.table``, and a
    module-level import back would make the package's import order decide
    whether it loads.
    """
    return [
        {
            "device": device.name,
            "description": device.description,
            "vendor": device.vendor,
            "relocatable": device.relocatable,
            "instances": [
                {"name": instance.name, "base": instance.base} for instance in device.instances
            ],
            "registers": len(device),
            "layer": device.layer,
            "path": str(device.path) if device.path else "",
        }
        for device in devices
    ]


def table_record(table: SymbolTable, *, file: str = "") -> dict[str, Any]:
    """The ``/sym.info`` / ``/sym.load`` / ``/sym.import`` record.

    Carries the staleness verdict so an agent reading ``data=`` learns the
    table is out of date at the same moment it learns the table exists --
    the one consumer able to act on it never has to ask twice.
    """
    span = table.span()
    bits = table.address_bits
    verdict = check_staleness(table)
    return {
        "path": file,
        "source": table.source,
        "imported": table.imported,
        "recipe": table.recipe,
        "witness": table.witness,
        "status": verdict.status,
        "status_reason": verdict.reason,
        "fixable": verdict.fixable,
        "rebuild_command": verdict.command,
        "address_bits": bits,
        "endian": table.endian,
        "count": len(table),
        "devices": device_records(table.devices),
        "aliases": [
            {
                "name": alias.name,
                "block": alias.block,
                "device": alias.device,
                "base": alias.base,
                "base_hex": hex_addr(alias.base, bits),
                "count": alias.count,
            }
            for alias in table.aliases
        ],
        "sections": table.stats(),
        "range": None
        if span is None
        else {
            "start": span[0],
            "end": span[1],
            "start_hex": hex_addr(span[0], bits),
            "end_hex": hex_addr(span[1], bits),
        },
        "regions": [region_record(region, address_bits=bits) for region in table.regions],
    }


def info_rows(table: SymbolTable, *, file: str = "") -> list[tuple[str, str]]:
    """``(label, value)`` rows for ``format_kv_lines`` -- same facts as ``table_record``."""
    counts: dict[str, int] = {}
    for section, count in table.stats().items():
        label = section_label(section)
        counts[label] = counts.get(label, 0) + count
    sections = ", ".join(f"{label} {counts[label]}" for label in sorted(counts))
    symbols = f"{len(table)}  ({sections})" if sections else str(len(table))
    span = table.span()
    bits = table.address_bits
    span_text = (
        "(empty)" if span is None else f"{hex_addr(span[0], bits)} - {hex_addr(span[1], bits)}"
    )
    region_list = ", ".join(format_region(region, address_bits=bits) for region in table.regions)
    regions = f"{len(table.regions)}  ({region_list})" if region_list else "0"
    rows = [
        ("file", file or "(in-memory)"),
        ("source", table.source or "(none)"),
        ("imported", table.imported or "(none)"),
    ]
    # Only a POSITIVE stale verdict earns a row.  "unknown" is the normal
    # state of every hand-written table and every sidecar written before
    # witnesses existed -- a row there would be permanent noise on a file
    # that is not wrong, and would put a warning in the CLI gold.
    verdict = check_staleness(table)
    if verdict.status not in (IN_SYNC, UNKNOWN):
        rows.append(("status", f"STALE - {verdict.reason}"))
        if verdict.command:
            rows.append(("rebuild", verdict.command))
    rows += [
        ("address_bits", str(bits)),
        ("endian", table.endian),
        ("symbols", symbols),
        ("range", span_text),
        ("regions", regions),
    ]
    # Devices are a separate layer, so name them separately: the build's
    # symbol count and the part's register count answer different
    # questions, and one merged number hides which half is missing.
    for device in table.devices:
        placed = " ".join(
            f"{instance.name}@{hex_addr(instance.base, bits)}"
            for instance in device.instances
            if instance.name
        )
        detail = f"  {placed}" if placed else ""
        rows.append(("device", f"{device.name}{detail}  ({len(device)} registers)"))
    # A block the build named: the pointer constant, the peripheral it
    # points at, and how many of its registers answer to the new prefix.
    for alias in table.aliases:
        rows.append(
            (
                "alias",
                f"{alias.name} -> {alias.block}@{hex_addr(alias.base, bits)}  ({alias.count} registers)",
            )
        )
    return rows
