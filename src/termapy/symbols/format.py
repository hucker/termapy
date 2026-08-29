"""Prose renderers and their structured ``data=`` twins, side by side.

The ``port_format.row_from_facts`` / ``facts_to_json_record`` pattern: every
record a ``/sym.*`` handler returns as ``CmdResult.data`` is produced HERE,
never inline in the handler.  Records have a FIXED shape -- every key
present, ``null`` / ``""`` / ``0`` for unknowns, numeric ``addr`` beside
``addr_hex`` -- so an agent can filter numerically or match strings, and a
hit and a miss carry the same keys.
"""

from __future__ import annotations

from typing import Any

from termapy.symbols.address import Address
from termapy.symbols.table import Symbol, SymbolTable, hex_digits, section_label


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


def table_record(table: SymbolTable, *, file: str = "") -> dict[str, Any]:
    """The ``/sym.info`` / ``/sym.load`` / ``/sym.import`` record."""
    span = table.span()
    bits = table.address_bits
    return {
        "path": file,
        "source": table.source,
        "imported": table.imported,
        "address_bits": bits,
        "endian": table.endian,
        "count": len(table),
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
    return [
        ("file", file or "(in-memory)"),
        ("source", table.source or "(none)"),
        ("imported", table.imported or "(none)"),
        ("address_bits", str(bits)),
        ("endian", table.endian),
        ("symbols", symbols),
        ("range", span_text),
        ("regions", str(len(table.regions))),
    ]
