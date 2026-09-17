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

from termapy.symbols.address import Address
from termapy.symbols.provenance import IN_SYNC, UNKNOWN, check_staleness
from termapy.symbols.table import Symbol, SymbolTable, hex_digits, section_label

if TYPE_CHECKING:
    from termapy.devices import Device, LibraryPart


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
    symbol: Symbol, query_addr: int | None = None, *, address_bits: int = 32,
) -> str:
    """One display line: ``0x00002010  main+0x10  [code 442 bytes main.c]``.

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
    return (
        f"{hex_addr(addr, address_bits)}  {name}"
        f"  [{symbol.section_label}{size_str}{file_str}]"
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
    }


def lookup_record(
    parsed: Address, at: Symbol | None, *, address_bits: int = 32,
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


def device_list_rows(
    devices: list[Device], *, address_bits: int = 32,
) -> list[tuple[str, str, str, str]]:
    """``(device, registers, layer, placement)`` per loaded device, for a table.

    The prose twin of :func:`device_records`, and the answer to "what is on
    this board": one row per part, with the LAYER it came from, because a
    part loaded globally and a part loaded by this config are different
    facts that a single flat list would hide.

    Placement is the instance names and bases for a relocatable part, empty
    for a fixed-address one -- the distinction a viewer and a reader both
    need, since only a placed part can collide with another copy.

    Args:
        devices: Loaded devices, in load order.
        address_bits: Address width for rendering instance bases.

    Returns:
        One tuple per device; the caller lays out the columns.
    """
    rows: list[tuple[str, str, str, str]] = []
    for device in devices:
        placed = " ".join(
            f"{instance.name}@{hex_addr(instance.base, address_bits)}"
            for instance in device.instances if instance.name
        )
        rows.append((device.name, str(len(device)), device.layer or "-", placed))
    return rows


def library_records(parts: list[LibraryPart]) -> list[dict[str, Any]]:
    """The ``data=`` twin for a library listing: one record per available part.

    Distinct from :func:`device_records` because these are parts a config
    COULD use, not devices it has loaded -- there is no placement, and
    ``registers`` is what the file claims rather than what a parse
    produced.  ``layer`` says which library holds the part (the per-config
    one shadows the global), so an agent can tell a shared part from a
    board-local one.
    """
    return [
        {
            "device": part.device,
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
                {"name": instance.name, "base": instance.base}
                for instance in device.instances
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
        "sections": table.stats(),
        "range": None if span is None else {
            "start": span[0],
            "end": span[1],
            "start_hex": hex_addr(span[0], bits),
            "end_hex": hex_addr(span[1], bits),
        },
        "regions": len(table.regions),
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
        "(empty)" if span is None
        else f"{hex_addr(span[0], bits)} - {hex_addr(span[1], bits)}"
    )
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
        ("regions", str(len(table.regions))),
    ]
    # Devices are a separate layer, so name them separately: the build's
    # symbol count and the part's register count answer different
    # questions, and one merged number hides which half is missing.
    for device in table.devices:
        placed = " ".join(
            f"{instance.name}@{hex_addr(instance.base, bits)}"
            for instance in device.instances if instance.name
        )
        detail = f"  {placed}" if placed else ""
        rows.append(("device", f"{device.name}{detail}  ({len(device)} registers)"))
    return rows
