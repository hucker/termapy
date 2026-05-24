"""Rust CRC generator.

Emits a complete ``.rs`` file with the CRC function plus an idiomatic
``#[cfg(test)] mod tests`` block containing a single ``#[test]``
that asserts the implementation against the reveng catalogue's
``check`` value.  ``cargo test`` discovers and runs it; termapy's
pytest harness uses ``rustc --test file.rs -o bin && ./bin`` for
the same verification.

Verified at build time by ``tests.test_crc_codegen_exec
.TestGeneratedRustExecutes``.
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


def generate_rust(name: str, table: bool = False) -> str | None:
    """Generate a Rust function for a CRC algorithm.

    Args:
        name: Algorithm name from CRC_CATALOGUE.
        table: If True, generate table-driven implementation.

    Returns:
        Rust source code string, or None if algorithm not found.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None

    w = entry["width"]
    poly = entry["poly"]
    init = entry["init"]
    refin = entry["refin"]
    refout = entry["refout"]
    xorout = entry["xorout"]
    check = entry["check"]
    desc = entry.get("desc", "")
    fname = _func_name(name)
    mask = _mask(w)

    if w <= 8:
        rtype = "u8"
    elif w <= 16:
        rtype = "u16"
    elif w <= 32:
        rtype = "u32"
    else:
        rtype = "u64"

    lines = []
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_rust(tbl, w, rtype))
        lines.append("")
    lines.append(f"/// {name} - {desc}")
    lines.append(f'/// check: crc(b"123456789") == {_hex(check, w)}')
    lines.append(f"fn {fname}(data: &[u8]) -> {rtype} {{")

    if table:
        if w == 8:
            # For 8-bit CRC, table lookup IS the complete algorithm --
            # no shifts or masks needed.  Rust rejects ``u8 << 8`` as
            # arithmetic_overflow (correctly -- u8 has 8 bits), so the
            # generic shift-and-xor formula below would fail to compile
            # for w=8.  C silently widens the operand via integer
            # promotion and produces the same result, but emitting the
            # simplified form is cleaner output for both languages.
            init_val = _reflect(init, w) if refin else init
            lines.append(f"    let mut crc: {rtype} = {_hex(init_val, w)};")
            lines.append(f"    for &byte in data {{")
            lines.append(f"        crc = CRC_TABLE[(crc ^ byte) as usize];")
            lines.append(f"    }}")
        elif refin:
            ref_init = _reflect(init, w)
            lines.append(f"    let mut crc: {rtype} = {_hex(ref_init, w)};")
            lines.append(f"    for &byte in data {{")
            lines.append(
                f"        crc = CRC_TABLE[(crc ^ byte as {rtype}) as usize & 0xFF] ^ (crc >> 8);"
            )
            lines.append(f"    }}")
        else:
            lines.append(f"    let mut crc: {rtype} = {_hex(init, w)};")
            lines.append(f"    for &byte in data {{")
            lines.append(
                f"        crc = CRC_TABLE[((crc >> {w - 8}) ^ byte as {rtype}) as usize & 0xFF] ^ (crc << 8) & {mask};"
            )
            lines.append(f"    }}")
    elif refin:
        ref_poly = _reflect(poly, w)
        ref_init = _reflect(init, w)
        lines.append(f"    let mut crc: {rtype} = {_hex(ref_init, w)};")
        lines.append(f"    for &byte in data {{")
        lines.append(f"        crc ^= byte as {rtype};")
        lines.append(f"        for _ in 0..8 {{")
        lines.append(f"            if crc & 1 != 0 {{")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};")
        lines.append(f"            }} else {{")
        lines.append(f"                crc >>= 1;")
        lines.append(f"            }}")
        lines.append(f"        }}")
        lines.append(f"    }}")
    else:
        lines.append(f"    let mut crc: {rtype} = {_hex(init, w)};")
        lines.append(f"    for &byte in data {{")
        lines.append(f"        crc ^= (byte as {rtype}) << {w - 8};")
        lines.append(f"        for _ in 0..8 {{")
        lines.append(f"            if crc & {_hex(1 << (w - 1), w)} != 0 {{")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)};")
        lines.append(f"            }} else {{")
        lines.append(f"                crc <<= 1;")
        lines.append(f"            }}")
        lines.append(f"            crc &= {mask};")
        lines.append(f"        }}")
        lines.append(f"    }}")

    if refout != refin:
        lines.append(f"    // reflect output")
        lines.append(f"    let mut reflected: {rtype} = 0;")
        lines.append(f"    for k in 0..{w} {{")
        lines.append(f"        reflected |= ((crc >> k) & 1) << ({w - 1} - k);")
        lines.append(f"    }}")
        lines.append(f"    crc = reflected;")

    if xorout:
        lines.append(f"    crc ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    crc")
    lines.append(f"}}")
    lines.append(_self_test_rust(fname, check, w))

    return "\n".join(lines)
