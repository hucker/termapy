"""Rust CRC generator.

Emits a complete ``.rs`` file with four module-level functions:

  - ``<fname>_init() -> rtype`` -- return the starting state
  - ``<fname>_update(state, data) -> rtype`` -- feed bytes, return new state
  - ``<fname>_finalize(state) -> rtype`` -- apply output reflection + xorout
  - ``<fname>(data) -> rtype`` -- one-shot wrapper (init + update + finalize)

Plus an idiomatic ``#[cfg(test)] mod tests`` block containing a
``#[test]`` that asserts the one-shot path against the reveng
catalogue's ``check`` value.  ``cargo test`` discovers and runs it;
crcglot's pytest harness uses ``rustc --test file.rs -o bin && ./bin``
for the same verification.

The streaming primitives (init / update / finalize) let callers
compute a CRC over data that arrives in chunks (large files, network
streams) without buffering everything.  The one-shot wrapper preserves
the simple API for the common case.

Verified at build time by ``tests.test_crc_codegen_exec
.TestGeneratedRustExecutes`` (one-shot path via the cfg(test) module)
and ``TestGeneratedRustStreaming`` (streaming splittability
invariant via a synthesized runner).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from crcglot._helpers import (
    _build_slice8_tables,
    _build_table,
    _func_name,
    _hex,
    _mask,
)
from crcglot.catalogue import CRC_CATALOGUE, _reflect


def _format_table_rust(table: list[int], width: int, rtype: str) -> str:
    """Format a lookup table as a Rust ``const`` array."""
    hex_w = (width + 3) // 4
    lines = [f"const CRC_TABLE: [{rtype}; 256] = ["]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("];")
    return "\n".join(lines)


def _format_slice8_tables_rust(
    tables: list[list[int]], width: int, rtype: str,
) -> str:
    """Format the 8 slice-by-8 tables as a Rust 2D ``const`` array."""
    hex_w = (width + 3) // 4
    lines = [f"const CRC_SLICE_TABLES: [[{rtype}; 256]; 8] = ["]
    for t_idx, table in enumerate(tables):
        lines.append(f"    // T{t_idx}")
        lines.append(f"    [")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}"
                for i in range(row, min(row + 8, 256))
            )
            lines.append(f"        {vals},")
        lines.append(f"    ],")
    lines.append("];")
    return "\n".join(lines)


def _update_loop_rust_slice8(w: int, refin: bool) -> list[str]:
    """Emit the per-8-byte slice-by-8 main loop + byte-by-byte tail.

    Variable ``crc`` (mutable, of the appropriate width type) is assumed
    to already hold the incoming state.  Processes ``data[0..n-1]`` 8
    bytes at a time via 8 chained table lookups (the slice tables),
    then falls back to single-byte table-driven via
    ``CRC_SLICE_TABLES[0]`` for any 1-7 trailing bytes.

    Only valid for w == 32 or w == 64 (the only widths where slice-by-8
    has a meaningful "fits in a u64 chunk" equivalent).
    """
    if w == 32:
        if refin:
            # Reflected: input loaded little-endian, low byte of state
            # XOR'd with first 4 input bytes, table indices walk from
            # least-significant byte upward (T7..T0).
            return [
                "    let n = data.len();",
                "    let mut i: usize = 0;",
                "    while i + 8 <= n {",
                "        let b03 = data[i] as u32"
                " | (data[i+1] as u32) << 8"
                " | (data[i+2] as u32) << 16"
                " | (data[i+3] as u32) << 24;",
                "        let b47 = data[i+4] as u32"
                " | (data[i+5] as u32) << 8"
                " | (data[i+6] as u32) << 16"
                " | (data[i+7] as u32) << 24;",
                "        let xored = crc ^ b03;",
                "        crc = CRC_SLICE_TABLES[7][ (xored        & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[6][((xored >>  8) & 0xFF) as usize]",
                "            ^ CRC_SLICE_TABLES[5][((xored >> 16) & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[4][((xored >> 24) & 0xFF) as usize]",
                "            ^ CRC_SLICE_TABLES[3][ (b47          & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[2][((b47   >>  8) & 0xFF) as usize]",
                "            ^ CRC_SLICE_TABLES[1][((b47   >> 16) & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[0][((b47   >> 24) & 0xFF) as usize];",
                "        i += 8;",
                "    }",
                "    while i < n {",
                "        crc = CRC_SLICE_TABLES[0]"
                "[((crc ^ data[i] as u32) & 0xFF) as usize] ^ (crc >> 8);",
                "        i += 1;",
                "    }",
            ]
        # Non-reflected w=32: load big-endian, state's top XOR'd with
        # first 4 input bytes' top.  Table-index convention: byte at
        # position k in the chunk uses T[7-k] (k=0 is "most delayed",
        # i.e. the byte that has the most zero-bytes processed after
        # it to reach the end of the chunk).
        return [
            "    let n = data.len();",
            "    let mut i: usize = 0;",
            "    while i + 8 <= n {",
            "        let b03 = (data[i] as u32) << 24"
            " | (data[i+1] as u32) << 16"
            " | (data[i+2] as u32) << 8"
            " | data[i+3] as u32;",
            "        let b47 = (data[i+4] as u32) << 24"
            " | (data[i+5] as u32) << 16"
            " | (data[i+6] as u32) << 8"
            " | data[i+7] as u32;",
            "        let xored = crc ^ b03;",
            "        crc = CRC_SLICE_TABLES[7][((xored >> 24) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[6][((xored >> 16) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[5][((xored >>  8) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[4][ (xored        & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[3][((b47   >> 24) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[2][((b47   >> 16) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[1][((b47   >>  8) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[0][ (b47          & 0xFF) as usize];",
            "        i += 8;",
            "    }",
            "    while i < n {",
            "        let top = crc >> 24;",
            "        crc = CRC_SLICE_TABLES[0]"
            "[((top ^ data[i] as u32) & 0xFF) as usize] ^ (crc << 8);",
            "        i += 1;",
            "    }",
        ]
    # w == 64
    if refin:
        return [
            "    let n = data.len();",
            "    let mut i: usize = 0;",
            "    while i + 8 <= n {",
            "        let b = data[i] as u64"
            " | (data[i+1] as u64) << 8"
            " | (data[i+2] as u64) << 16"
            " | (data[i+3] as u64) << 24",
            "              | (data[i+4] as u64) << 32"
            " | (data[i+5] as u64) << 40"
            " | (data[i+6] as u64) << 48"
            " | (data[i+7] as u64) << 56;",
            "        let xored = crc ^ b;",
            "        crc = CRC_SLICE_TABLES[7][ (xored        & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[6][((xored >>  8) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[5][((xored >> 16) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[4][((xored >> 24) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[3][((xored >> 32) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[2][((xored >> 40) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[1][((xored >> 48) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[0][((xored >> 56) & 0xFF) as usize];",
            "        i += 8;",
            "    }",
            "    while i < n {",
            "        crc = CRC_SLICE_TABLES[0]"
            "[((crc ^ data[i] as u64) & 0xFF) as usize] ^ (crc >> 8);",
            "        i += 1;",
            "    }",
        ]
    # Non-reflected w=64.  Same index convention as w=32: byte at
    # position k uses T[7-k].
    return [
        "    let n = data.len();",
        "    let mut i: usize = 0;",
        "    while i + 8 <= n {",
        "        let b = (data[i] as u64) << 56"
        " | (data[i+1] as u64) << 48"
        " | (data[i+2] as u64) << 40"
        " | (data[i+3] as u64) << 32",
        "              | (data[i+4] as u64) << 24"
        " | (data[i+5] as u64) << 16"
        " | (data[i+6] as u64) << 8"
        " | data[i+7] as u64;",
        "        let xored = crc ^ b;",
        "        crc = CRC_SLICE_TABLES[7][((xored >> 56) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[6][((xored >> 48) & 0xFF) as usize]",
        "            ^ CRC_SLICE_TABLES[5][((xored >> 40) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[4][((xored >> 32) & 0xFF) as usize]",
        "            ^ CRC_SLICE_TABLES[3][((xored >> 24) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[2][((xored >> 16) & 0xFF) as usize]",
        "            ^ CRC_SLICE_TABLES[1][((xored >>  8) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[0][ (xored        & 0xFF) as usize];",
        "        i += 8;",
        "    }",
        "    while i < n {",
        "        let top = crc >> 56;",
        "        crc = CRC_SLICE_TABLES[0]"
        "[((top ^ data[i] as u64) & 0xFF) as usize] ^ (crc << 8);",
        "        i += 1;",
        "    }",
    ]


def _update_loop_rust(
    w: int,
    poly: int,
    refin: bool,
    mask: str,
    rtype: str,
    table: bool,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Variable ``crc`` (of type ``rtype``) is assumed to already hold
    the incoming state; this returns only the for-loop that consumes
    ``data`` and updates ``crc`` in place.
    """
    if table:
        if w == 8:
            # For 8-bit CRC, table lookup IS the complete algorithm --
            # no shifts or masks needed.  Rust rejects ``u8 << 8`` as
            # arithmetic_overflow (correctly -- u8 has 8 bits), so the
            # generic shift-and-xor formula below would fail to compile
            # for w=8.  C silently widens the operand via integer
            # promotion and produces the same result, but emitting the
            # simplified form is cleaner output for both languages.
            return [
                "    for &byte in data {",
                "        crc = CRC_TABLE[(crc ^ byte) as usize];",
                "    }",
            ]
        if refin:
            return [
                "    for &byte in data {",
                f"        crc = CRC_TABLE[(crc ^ byte as {rtype}) as usize & 0xFF] ^ (crc >> 8);",
                "    }",
            ]
        return [
            "    for &byte in data {",
            f"        crc = CRC_TABLE[((crc >> {w - 8}) ^ byte as {rtype}) as usize & 0xFF] ^ (crc << 8) & {mask};",
            "    }",
        ]
    if refin:
        ref_poly = _reflect(poly, w)
        return [
            "    for &byte in data {",
            f"        crc ^= byte as {rtype};",
            "        for _ in 0..8 {",
            "            if crc & 1 != 0 {",
            f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};",
            "            } else {",
            "                crc >>= 1;",
            "            }",
            "        }",
            "    }",
        ]
    return [
        "    for &byte in data {",
        f"        crc ^= (byte as {rtype}) << {w - 8};",
        "        for _ in 0..8 {",
        f"            if crc & {_hex(1 << (w - 1), w)} != 0 {{",
        f"                crc = (crc << 1) ^ {_hex(poly, w)};",
        "            } else {",
        "                crc <<= 1;",
        "            }",
        f"            crc &= {mask};",
        "        }",
        "    }",
    ]


def _self_test_rust(fname: str, check: int, width: int) -> str:
    """Emit a Rust ``#[cfg(test)] mod tests`` block.

    Idiomatic: ``cargo test`` discovers it automatically and it's
    compiled out of release builds via ``#[cfg(test)]``.  Termapy's
    pytest harness invokes ``rustc --test file.rs`` to build a test
    binary and runs it -- exit 0 means the assertion passed.
    """
    lines = [
        f"",
        f"#[cfg(test)]",
        f"mod tests {{",
        f"    use super::*;",
        f"",
        f"    #[test]",
        f"    fn check_value_matches_reveng() {{",
        f'        assert_eq!({fname}(b"123456789"), {_hex(check, width)});',
        f"    }}",
        f"}}",
    ]
    return "\n".join(lines)


def generate_rust(
    name: str,
    table: bool = False,
    symbol: str | None = None,
    slice8: bool = False,
) -> str | None:
    """Look up a CRC algorithm by name and generate Rust source for it.

    Thin wrapper around :func:`generate_rust_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None
    return generate_rust_from_entry(
        name, entry, table=table, symbol=symbol, slice8=slice8,
    )


def generate_rust_from_entry(
    name: str,
    entry: dict,
    table: bool = False,
    symbol: str | None = None,
    slice8: bool = False,
) -> str:
    """Generate Rust source from a catalogue-shaped entry dict.

    Args:
        name: Algorithm name (used in comments).
        entry: Catalogue dict with ``width`` / ``poly`` / ``init`` /
            ``refin`` / ``refout`` / ``xorout`` / ``check`` (required)
            and ``desc`` (optional).
        table: If True, generate table-driven implementation.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        slice8: If True, emit the slice-by-8 implementation (8 tables,
            ~5-10x throughput vs plain table-driven for CRC-32 / CRC-64
            on large buffers).  Only valid for width 32 or 64;
            ``ValueError`` otherwise.

    Returns:
        Rust source code string.
    """
    w = entry["width"]
    poly = entry["poly"]
    init = entry["init"]
    refin = entry["refin"]
    refout = entry["refout"]
    xorout = entry["xorout"]
    check = entry["check"]
    desc = entry.get("desc", "")
    fname = symbol if symbol else _func_name(name)
    mask = _mask(w)

    if w <= 8:
        rtype = "u8"
    elif w <= 16:
        rtype = "u16"
    elif w <= 32:
        rtype = "u32"
    else:
        rtype = "u64"

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"slice8=True requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    # Pre-loaded init state for streaming entry.
    init_state = _reflect(init, w) if refin else init

    lines: list[str] = []
    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_rust(slice_tables, w, rtype))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_rust(tbl, w, rtype))
        lines.append("")
    lines.append(f"/// {name} - {desc}")
    lines.append(f'/// check: crc(b"123456789") == {_hex(check, w)}')
    lines.append(f"///")
    lines.append(f"/// Streaming: init -> update (any number of times) -> finalize.")
    lines.append(f"/// One-shot:  call {fname}(data).")
    if slice8:
        lines.append(
            f"/// Variant:   slice-by-8 (8 tables, ~10x throughput vs "
            f"plain table for large buffers)."
        )

    # ``pub fn`` (not plain ``fn``) so the same file works equally well
    # as a standalone crate (where ``fn`` would suffice) and as a module
    # included into a parent crate via ``include!`` / ``mod`` (where
    # the caller needs the symbol to cross the mod boundary).  Plain
    # ``fn`` is private-to-mod by default in Rust.

    # ----- <fname>_init() -----
    lines.append(f"pub fn {fname}_init() -> {rtype} {{")
    lines.append(f"    {_hex(init_state, w)}")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines.append(
        f"pub fn {fname}_update(state: {rtype}, data: &[u8]) -> {rtype} {{"
    )
    lines.append(f"    let mut crc: {rtype} = state;")
    if slice8:
        lines.extend(_update_loop_rust_slice8(w, refin))
    else:
        lines.extend(_update_loop_rust(w, poly, refin, mask, rtype, table))
    lines.append(f"    crc")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines.append(f"pub fn {fname}_finalize(state: {rtype}) -> {rtype} {{")
    if refout != refin:
        lines.append(f"    // reflect output (refout != refin)")
        lines.append(f"    let mut reflected: {rtype} = 0;")
        lines.append(f"    for k in 0..{w} {{")
        lines.append(f"        reflected |= ((state >> k) & 1) << ({w - 1} - k);")
        lines.append(f"    }}")
        lines.append(f"    let state = reflected;")
    if xorout:
        lines.append(f"    state ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    state")
    lines.append(f"}}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines.append(f"pub fn {fname}(data: &[u8]) -> {rtype} {{")
    lines.append(
        f"    {fname}_finalize({fname}_update({fname}_init(), data))"
    )
    lines.append(f"}}")
    lines.append(_self_test_rust(fname, check, w))

    return "\n".join(lines)
