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


def _self_test_vhdl(fname: str, check: int, width: int) -> str:
    """Emit a VHDL self-test function.

    Returns ``true`` on success, ``false`` on failure.  Designed to
    be called from a testbench process via ``assert`` -- termapy's
    pytest harness synthesizes that testbench to verify the generator
    output (see ``test_crc_codegen_exec.py``).
    """
    lines = [
        f"    -- Run the canonical reveng check value; returns true on success.",
        f"    function {fname}_self_test return boolean is",
        f'        constant kCheckInput: std_logic_vector(71 downto 0) :=',
        f'            x"313233343536373839";  -- ASCII "123456789"',
        f"    begin",
        f"        return unsigned({fname}(kCheckInput)) = "
        f"to_unsigned({check}, {width});",
        f"    end function;",
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


def generate_vhdl(name: str, table: bool = False) -> str | None:
    """Generate a VHDL package implementing a CRC algorithm.

    Emits a single ``.vhd`` file containing a package with two
    functions:

    - ``<fname>(data: std_logic_vector) return std_logic_vector`` --
      the algorithm; input is a packed byte vector whose length must
      be a multiple of 8.
    - ``<fname>_self_test return boolean`` -- runs the algorithm
      against the canonical reveng check string ``"123456789"`` and
      reports whether the result matches.  Designed to be called
      from a testbench process via ``assert`` (termapy's pytest
      harness synthesizes that testbench to verify the generator).

    Scope note: this is **simulator-friendly VHDL** (pure functions
    over ``std_logic_vector``).  It compiles and simulates under GHDL
    and is enough to verify correctness against the reveng catalogue.
    Synthesizable FPGA hardware (pipelined entity/architecture) is a
    future enhancement -- the function-in-package form is right for
    a "verified reference implementation" but a hardware designer
    will typically want to wrap a synthesizable wrapper around it.

    Args:
        name: Algorithm name from CRC_CATALOGUE.
        table: Accepted for API symmetry with the other generators
            but ignored -- the bit-by-bit form is always emitted.
            Table-driven VHDL adds nontrivial code-gen for the
            256-entry constant array without earning much in
            simulator mode; deferred to a future pass.

    Returns:
        VHDL source string, or None if the algorithm is unknown.
    """
    _ = table  # currently unused (see scope note above)
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
    pkg = f"{fname}_pkg"

    if refin:
        init_val = _reflect(init, w)
        poly_val = _reflect(poly, w)
    else:
        init_val = init
        poly_val = poly

    header = [
        f"-- {fname}.vhd -- generated by termapy from reveng/{name}",
        f"-- {desc}",
        f"-- check: crc(\"123456789\") == {_hex(check, w)}",
        f"--",
        f"-- Call {fname}_self_test from a testbench to verify "
        f"against the reveng check value.",
        f"",
        f"library ieee;",
        f"use ieee.std_logic_1164.all;",
        f"use ieee.numeric_std.all;",
        f"",
        f"package {pkg} is",
        f"    -- Compute CRC over a packed byte vector "
        f"(length must be a multiple of 8).",
        f"    function {fname}(data: std_logic_vector) "
        f"return std_logic_vector;",
        f"    function {fname}_self_test return boolean;",
        f"end package;",
        f"",
        f"package body {pkg} is",
    ]

    body = [
        f"    function {fname}(data: std_logic_vector) "
        f"return std_logic_vector is",
        f"        variable crc: unsigned({w - 1} downto 0) := "
        f"to_unsigned({init_val}, {w});",
        f"        variable byte: unsigned(7 downto 0);",
        f"        -- Normalize indexing regardless of caller's slice direction.",
        f"        constant d: std_logic_vector(data'length - 1 downto 0) := data;",
        f"        constant n: natural := data'length / 8;",
    ]

    if refout != refin:
        body.append(
            f"        variable reflected: unsigned({w - 1} downto 0);"
        )

    body += [
        f"    begin",
        f"        for i in 0 to n - 1 loop",
        f"            byte := unsigned("
        f"d((n - i)*8 - 1 downto (n - i - 1)*8));",
    ]

    if refin:
        body += [
            f"            crc := crc xor resize(byte, {w});",
            f"            for j in 0 to 7 loop",
            f"                if crc(0) = '1' then",
            f"                    crc := shift_right(crc, 1) xor "
            f"to_unsigned({poly_val}, {w});",
            f"                else",
            f"                    crc := shift_right(crc, 1);",
            f"                end if;",
            f"            end loop;",
        ]
    else:
        body += [
            f"            crc := crc xor shift_left(resize(byte, {w}), {w - 8});",
            f"            for j in 0 to 7 loop",
            f"                if crc({w - 1}) = '1' then",
            f"                    crc := shift_left(crc, 1) xor "
            f"to_unsigned({poly_val}, {w});",
            f"                else",
            f"                    crc := shift_left(crc, 1);",
            f"                end if;",
            f"            end loop;",
        ]

    body.append(f"        end loop;")

    if refout != refin:
        body += [
            f"        -- reflect output (refout != refin)",
            f"        reflected := (others => '0');",
            f"        for k in 0 to {w - 1} loop",
            f"            reflected(k) := crc({w - 1} - k);",
            f"        end loop;",
            f"        crc := reflected;",
        ]

    if xorout:
        body.append(
            f"        return std_logic_vector(crc xor "
            f"to_unsigned({xorout}, {w}));"
        )
    else:
        body.append(f"        return std_logic_vector(crc);")
    body.append(f"    end function;")
    body.append("")

    body.append(_self_test_vhdl(fname, check, w))

    body.append(f"end package body;")

    return "\n".join(header + body)


GENERATORS: dict[str, Callable] = {
    "c": generate_c,
    "python": generate_python,
    "rust": generate_rust,
    "vhdl": generate_vhdl,
}
