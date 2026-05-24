"""CRC source code generation for C, Python, and Rust.

Generates standalone CRC functions from catalogue parameters.
Pure functions, no dependencies beyond protocol_crc.

Note: the generated code is not optimized for speed or size; it's meant
to be readable and straightforward, and to serve as a reference
implementation for each algorithm.  For production use, consider using a
well-vetted library like zlib, crcmod, or crc-anywhere instead.

You may be inclined to just ask Claude to write these for you, but
be aware that every crc algorithm created here comes with a test vector
that this code is verified against.  If you ask Claude to write code
for you make sure you check against the test vector to verify correctness.

"""

# Allowing this makes code lineup nicely
# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from typing import Callable

from termapy.protocol.crc import CRC_CATALOGUE, _reflect


def _func_name(algo_name: str) -> str:
    """Convert algorithm name to a valid function name."""
    return algo_name.replace("-", "_").replace(".", "_")


def _hex(value: int, width: int) -> str:
    """Format an integer as a hex literal with appropriate width."""
    hex_w = (width + 3) // 4  # hex digits needed
    return f"0x{value:0{hex_w}X}"


def _mask(width: int) -> str:
    """Format the bit mask for a given width."""
    return _hex((1 << width) - 1, width)


def _build_table(width: int, poly: int, refin: bool) -> list[int]:
    """Pre-compute 256-entry CRC lookup table."""
    table = []
    if refin:
        ref_poly = _reflect(poly, width)
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ ref_poly
                else:
                    crc >>= 1
            table.append(crc & ((1 << width) - 1))
    else:
        for i in range(256):
            crc = i << (width - 8)
            for _ in range(8):
                if crc & (1 << (width - 1)):
                    crc = (crc << 1) ^ poly
                else:
                    crc <<= 1
                crc &= (1 << width) - 1
            table.append(crc)
    return table


def _format_table_c(table: list[int], width: int, ctype: str) -> str:
    """Format a lookup table as a C array."""
    hex_w = (width + 3) // 4
    lines = [f"static const {ctype} crc_table[256] = {{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        comma = "," if row + 8 < 256 else ""
        lines.append(f"    {vals}{comma}")
    lines.append("};")
    return "\n".join(lines)


def _format_table_python(table: list[int], width: int) -> str:
    """Format a lookup table as a Python tuple."""
    hex_w = (width + 3) // 4
    lines = ["_TABLE = ("]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append(")")
    return "\n".join(lines)


def _format_table_rust(table: list[int], width: int, rtype: str) -> str:
    """Format a lookup table as a Rust const array."""
    hex_w = (width + 3) // 4
    lines = [f"const CRC_TABLE: [{rtype}; 256] = ["]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("];")
    return "\n".join(lines)


def _self_test_c(fname: str, ctype: str, check: int, width: int) -> str:
    """Emit a C self-test function.

    Returns 0 on success, 1 on failure.  Caller (firmware test
    framework, boot self-check, or termapy's CI runner harness)
    invokes it; we deliberately do NOT emit a ``main()`` so the
    file drops into firmware without symbol collision.
    """
    lines = [
        f"int {fname}_self_test(void) {{",
        f'    static const uint8_t kCheckInput[] = "123456789";',
        f"    return {fname}(kCheckInput, 9) == {_hex(check, width)} ? 0 : 1;",
        f"}}",
    ]
    return "\n".join(lines)


def _self_test_rust(fname: str, check: int, width: int) -> str:
    """Emit a Rust ``#[cfg(test)]`` test module.

    Idiomatic Rust: ``cargo test`` discovers and runs it.  Compiled
    out of release builds automatically via ``#[cfg(test)]``.  Used
    by termapy's pytest harness via ``rustc --test file.rs``.
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


def _header_c(name: str, fname: str, ctype: str, desc: str) -> str:
    """Emit the ``.h`` header for a CRC algorithm.

    Uses the standard ``#ifdef __cplusplus`` ``extern "C"`` guard so
    C++ consumers can include the header and link without manual
    name-mangling workarounds.  Header pulls in ``<stdint.h>`` and
    ``<stddef.h>`` so the implementation .c file only needs to
    ``#include "<fname>.h"``.
    """
    guard = f"{fname.upper()}_H"
    lines = [
        f"/* {fname}.h -- generated by termapy from reveng/{name}",
        f" * {desc}",
        f" */",
        f"#ifndef {guard}",
        f"#define {guard}",
        f"",
        f"#include <stdint.h>",
        f"#include <stddef.h>",
        f"",
        f"#ifdef __cplusplus",
        f'extern "C" {{',
        f"#endif",
        f"",
        f"{ctype} {fname}(const uint8_t *data, size_t len);",
        f"int {fname}_self_test(void);",
        f"",
        f"#ifdef __cplusplus",
        f"}}",
        f"#endif",
        f"",
        f"#endif /* {guard} */",
    ]
    return "\n".join(lines)


def generate_c(name: str, table: bool = False) -> tuple[str, str] | None:
    """Generate a C ``.h`` + ``.c`` pair for a CRC algorithm.

    Returns a ``(header, source)`` tuple of complete, compilable files.
    The header uses the standard ``#ifdef __cplusplus`` ``extern "C"``
    guard so the same code drops into C and C++ projects with no
    name-mangling workarounds.  The source ``#include``s the header
    and additionally emits an ``<fname>_self_test()`` function that
    returns 0 on success / 1 on failure -- call from your test
    framework, boot self-check, factory burn-in, or termapy's CI
    runner harness.  No ``main()`` is emitted, so the file links
    cleanly alongside your firmware's own entry point.

    Args:
        name: Algorithm name from CRC_CATALOGUE.
        table: If True, emit the table-driven implementation instead
            of the bit-by-bit form.

    Returns:
        ``(header_source, impl_source)`` tuple of strings, or None if
        the algorithm name is unknown.
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
        ctype = "uint8_t"
    elif w <= 16:
        ctype = "uint16_t"
    else:
        ctype = "uint32_t"

    lines = []
    lines.append(f'/* {fname}.c -- generated by termapy from reveng/{name}')
    lines.append(f' * {desc}')
    lines.append(f' * check: crc("123456789") == {_hex(check, w)}')
    lines.append(f' *')
    lines.append(f' * Verify by calling {fname}_self_test() (returns 0 on success).')
    lines.append(f' */')
    lines.append(f'#include "{fname}.h"')
    lines.append(f'')
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_c(tbl, w, ctype))
        lines.append("")
    lines.append(f"{ctype} {fname}(const uint8_t *data, size_t len) {{")

    if table:
        if refin:
            ref_init = _reflect(init, w)
            lines.append(f"    {ctype} crc = {_hex(ref_init, w)};")
            lines.append("    for (size_t i = 0; i < len; i++)")
            lines.append(
                "        crc = crc_table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);"
            )
        else:
            lines.append(f"    {ctype} crc = {_hex(init, w)};")
            lines.append("    for (size_t i = 0; i < len; i++)")
            # Parenthesize the second operand fully -- gcc's -Wparentheses
            # (in -Wall) rejects ``a ^ b & c`` as ambiguous even though
            # C's precedence rules give the same result.  Embedded devs
            # routinely build with -Wall -Werror, so the generator must
            # produce code that survives that.  (Caught by the execution
            # tests in test_crc_codegen_exec.py via the -Werror gcc flag.)
            lines.append(
                f"        crc = crc_table[((crc >> {w - 8}) ^ data[i]) & 0xFF] ^ ((crc << 8) & {mask});"
            )
    elif refin:
        ref_poly = _reflect(poly, w)
        ref_init = _reflect(init, w)
        lines.append(f"    {ctype} crc = {_hex(ref_init, w)};")
        lines.append(f"    for (size_t i = 0; i < len; i++) {{")
        lines.append(f"        crc ^= data[i];")
        lines.append(f"        for (int j = 0; j < 8; j++) {{")
        lines.append(f"            if (crc & 1)")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};")
        lines.append(f"            else")
        lines.append(f"                crc >>= 1;")
        lines.append(f"        }}")
        lines.append(f"    }}")
    else:
        lines.append(f"    {ctype} crc = {_hex(init, w)};")
        lines.append(f"    for (size_t i = 0; i < len; i++) {{")
        lines.append(f"        crc ^= ({ctype})data[i] << {w - 8};")
        lines.append(f"        for (int j = 0; j < 8; j++) {{")
        lines.append(f"            if (crc & {_hex(1 << (w - 1), w)})")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)};")
        lines.append(f"            else")
        lines.append(f"                crc <<= 1;")
        lines.append(f"            crc &= {mask};")
        lines.append(f"        }}")
        lines.append(f"    }}")

    if refout != refin:
        lines.append(f"    /* reflect output */")
        lines.append(f"    {ctype} reflected = 0;")
        lines.append(f"    for (int k = 0; k < {w}; k++)")
        lines.append(f"        reflected |= ((crc >> k) & 1) << ({w - 1} - k);")
        lines.append(f"    crc = reflected;")

    if xorout:
        lines.append(f"    return crc ^ {_hex(xorout, w)};")
    else:
        lines.append(f"    return crc;")
    lines.append(f"}}")
    lines.append("")
    lines.append(_self_test_c(fname, ctype, check, w))

    header = _header_c(name, fname, ctype, desc)
    source = "\n".join(lines)
    return header, source


def generate_python(name: str, table: bool = False) -> str | None:
    """Generate a Python function for a CRC algorithm.

    Args:
        name: Algorithm name from CRC_CATALOGUE.
        table: If True, generate table-driven implementation.

    Returns:
        Python source code string, or None if algorithm not found.
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

    lines = []
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_python(tbl, w))
        lines.append("")
        lines.append("")

    lines.append(f"def {fname}(data: bytes) -> int:")
    lines.append(f'    """{name} - {desc}')
    lines.append(f"")
    lines.append(f"    check: crc(b'123456789') == {_hex(check, w)}")
    lines.append(f'    """')

    if table:
        if refin:
            ref_init = _reflect(init, w)
            lines.append(f"    crc = {_hex(ref_init, w)}")
            lines.append(f"    for byte in data:")
            lines.append(f"        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)")
        else:
            lines.append(f"    crc = {_hex(init, w)}")
            lines.append(f"    for byte in data:")
            lines.append(
                f"        crc = _TABLE[((crc >> {w - 8}) ^ byte) & 0xFF] ^ (crc << 8) & {mask}"
            )
    elif refin:
        ref_poly = _reflect(poly, w)
        ref_init = _reflect(init, w)
        lines.append(f"    crc = {_hex(ref_init, w)}")
        lines.append(f"    for byte in data:")
        lines.append(f"        crc ^= byte")
        lines.append(f"        for _ in range(8):")
        lines.append(f"            if crc & 1:")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)}")
        lines.append(f"            else:")
        lines.append(f"                crc >>= 1")
    else:
        lines.append(f"    crc = {_hex(init, w)}")
        lines.append(f"    for byte in data:")
        lines.append(f"        crc ^= byte << {w - 8}")
        lines.append(f"        for _ in range(8):")
        lines.append(f"            if crc & {_hex(1 << (w - 1), w)}:")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)}")
        lines.append(f"            else:")
        lines.append(f"                crc <<= 1")
        lines.append(f"            crc &= {mask}")

    if refout != refin:
        lines.append(f"    # reflect output")
        lines.append(
            f"    crc = sum(((crc >> k) & 1) << ({w - 1} - k) for k in range({w}))"
        )

    if xorout:
        lines.append(f"    return crc ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    return crc")

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
    else:
        rtype = "u32"

    lines = []
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_rust(tbl, w, rtype))
        lines.append("")
    lines.append(f"/// {name} - {desc}")
    lines.append(f'/// check: crc(b"123456789") == {_hex(check, w)}')
    lines.append(f"fn {fname}(data: &[u8]) -> {rtype} {{")

    if table:
        if refin:
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


GENERATORS: dict[str, Callable] = {
    "c": generate_c,
    "python": generate_python,
    "rust": generate_rust,
}
