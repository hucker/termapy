"""Rust CRC generator.

Emits a complete ``.rs`` file with four module-level functions:

  - ``<fname>_init() -> rtype`` -- return the starting state
  - ``<fname>_update(state, data) -> rtype`` -- feed bytes, return new state
  - ``<fname>_finalize(state) -> rtype`` -- apply output reflection + xorout
  - ``<fname>(data) -> rtype`` -- one-shot wrapper (init + update + finalize)

Plus an idiomatic ``#[cfg(test)] mod tests`` block containing a
``#[test]`` that asserts the one-shot path against the reveng
catalogue's ``check`` value.  ``cargo test`` discovers and runs it;
termapy's pytest harness uses ``rustc --test file.rs -o bin && ./bin``
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

from termapy.protocol.crc import CRC_CATALOGUE, _reflect
from termapy.protocol.crcgen._helpers import (
    _build_table,
    _func_name,
    _hex,
    _mask,
)


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
    name: str, table: bool = False, symbol: str | None = None,
) -> str | None:
    """Look up a CRC algorithm by name and generate Rust source for it.

    Thin wrapper around :func:`generate_rust_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None
    return generate_rust_from_entry(name, entry, table=table, symbol=symbol)


def generate_rust_from_entry(
    name: str,
    entry: dict,
    table: bool = False,
    symbol: str | None = None,
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

    # Pre-loaded init state for streaming entry.
    init_state = _reflect(init, w) if refin else init

    lines: list[str] = []
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_rust(tbl, w, rtype))
        lines.append("")
    lines.append(f"/// {name} - {desc}")
    lines.append(f'/// check: crc(b"123456789") == {_hex(check, w)}')
    lines.append(f"///")
    lines.append(f"/// Streaming: init -> update (any number of times) -> finalize.")
    lines.append(f"/// One-shot:  call {fname}(data).")

    # ----- <fname>_init() -----
    lines.append(f"fn {fname}_init() -> {rtype} {{")
    lines.append(f"    {_hex(init_state, w)}")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines.append(f"fn {fname}_update(state: {rtype}, data: &[u8]) -> {rtype} {{")
    lines.append(f"    let mut crc: {rtype} = state;")
    lines.extend(_update_loop_rust(w, poly, refin, mask, rtype, table))
    lines.append(f"    crc")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines.append(f"fn {fname}_finalize(state: {rtype}) -> {rtype} {{")
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
    lines.append(f"fn {fname}(data: &[u8]) -> {rtype} {{")
    lines.append(
        f"    {fname}_finalize({fname}_update({fname}_init(), data))"
    )
    lines.append(f"}}")
    lines.append(_self_test_rust(fname, check, w))

    return "\n".join(lines)
