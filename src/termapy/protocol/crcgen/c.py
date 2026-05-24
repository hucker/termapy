"""C / C++ CRC generator.

Emits a ``(header, source)`` tuple of complete, compilable files.
The header uses the standard ``#ifdef __cplusplus`` ``extern "C"``
guard so the same code drops into both C and C++ projects without
manual name-mangling fix-ups.  The source ``#include``s the header
and emits five functions:

  - ``<fname>_init(void)`` -- return the starting state
  - ``<fname>_update(state, data, len)`` -- feed bytes, return new state
  - ``<fname>_finalize(state)`` -- apply output reflection + xorout
  - ``<fname>(data, len)`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test(void)`` -- returns 0 if check matches reveng, 1 otherwise

The streaming API (init / update / finalize) lets embedded firmware
compute a CRC over data that arrives in chunks (large files, network
streams, sensor logs over UART) without buffering everything in
memory.  The one-shot wrapper preserves the simple API for the
common case.  ``_self_test()`` is callable from a downstream test
framework, boot self-check, factory burn-in, or termapy's CI runner
harness; no ``main()`` is emitted so the file links cleanly alongside
the user's own entry point.

Verified at build time by ``tests.test_crc_codegen_exec
.TestGeneratedCExecutes`` (one-shot path) and ``TestGeneratedCStreaming``
(streaming splittability invariant) -- write the pair to a tmpdir,
synthesize a runner, compile with ``gcc -std=c99 -Wall -Werror``,
assert exit 0.
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


def _format_table_c(table: list[int], width: int, ctype: str) -> str:
    """Format a lookup table as a C ``static const`` array."""
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


def _update_loop_c(
    w: int,
    poly: int,
    refin: bool,
    mask: str,
    table: bool,
    ctype: str,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Variable ``crc`` (of the appropriate width type) is assumed to
    already hold the incoming state; this returns only the for-loop
    that consumes ``data[0..len-1]`` and updates ``crc`` in place.
    """
    if table:
        if refin:
            return [
                "    for (size_t i = 0; i < len; i++)",
                "        crc = crc_table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);",
            ]
        # Parenthesize the second operand fully -- gcc's -Wparentheses
        # (in -Wall) rejects ``a ^ b & c`` as ambiguous even though
        # C's precedence rules give the same result.  Embedded devs
        # routinely build with -Wall -Werror, so the generator must
        # produce code that survives that.  (Caught by the execution
        # tests in test_crc_codegen_exec.py via the -Werror gcc flag.)
        return [
            "    for (size_t i = 0; i < len; i++)",
            f"        crc = crc_table[((crc >> {w - 8}) ^ data[i]) & 0xFF] ^ ((crc << 8) & {mask});",
        ]
    if refin:
        ref_poly = _reflect(poly, w)
        return [
            "    for (size_t i = 0; i < len; i++) {",
            "        crc ^= data[i];",
            "        for (int j = 0; j < 8; j++) {",
            "            if (crc & 1)",
            f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};",
            "            else",
            "                crc >>= 1;",
            "        }",
            "    }",
        ]
    # Cast to ``ctype`` (not uint8_t) before shifting: for w=64, shifting
    # a uint8_t (promoted to int) by 56 is undefined behaviour because
    # int is only 32 bits.  Casting to the destination type keeps the
    # promotion wide enough to be defined.
    return [
        "    for (size_t i = 0; i < len; i++) {",
        f"        crc ^= ({ctype})data[i] << {w - 8};",
        "        for (int j = 0; j < 8; j++) {",
        f"            if (crc & {_hex(1 << (w - 1), w)})",
        f"                crc = (crc << 1) ^ {_hex(poly, w)};",
        "            else",
        "                crc <<= 1;",
        f"            crc &= {mask};",
        "        }",
        "    }",
    ]


def _self_test_c(fname: str, check: int, width: int) -> str:
    """Emit a C self-test function returning 0 on success, 1 on failure.

    Designed to be called from a downstream test framework, firmware
    boot self-check, or termapy's CI runner harness.  We deliberately
    do NOT emit a ``main()`` so the file drops into firmware without
    a symbol collision.
    """
    lines = [
        f"int {fname}_self_test(void) {{",
        f'    static const uint8_t kCheckInput[] = "123456789";',
        f"    return {fname}(kCheckInput, 9) == {_hex(check, width)} ? 0 : 1;",
        f"}}",
    ]
    return "\n".join(lines)


def _header_c(name: str, fname: str, ctype: str, desc: str) -> str:
    """Emit the ``.h`` header with ``extern "C"`` guard for C++ interop.

    Pulls in ``<stdint.h>`` and ``<stddef.h>`` so the implementation
    ``.c`` only needs ``#include "<fname>.h"`` -- callers (and
    termapy's pytest runner) don't have to know which headers the
    function body needs.  Declares the streaming triple (init / update
    / finalize), the one-shot convenience wrapper, and the self-test
    function so all five are part of the public surface.
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
        f"/* Streaming API: init -> update (any number of times) -> finalize. */",
        f"{ctype} {fname}_init(void);",
        f"{ctype} {fname}_update({ctype} state, const uint8_t *data, size_t len);",
        f"{ctype} {fname}_finalize({ctype} state);",
        f"",
        f"/* One-shot convenience: init + single update + finalize. */",
        f"{ctype} {fname}(const uint8_t *data, size_t len);",
        f"",
        f"/* Self-test: returns 0 if check value matches reveng catalogue, 1 otherwise. */",
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
    The source emits the streaming triple (init / update / finalize),
    a one-shot wrapper, and a self-test -- see module docstring for
    details.

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
    elif w <= 32:
        ctype = "uint32_t"
    else:
        ctype = "uint64_t"

    # Pre-loaded init state: matches the value the main loop expects
    # on entry.  Reflected algorithms enter the loop with the reflection
    # of the textbook init; non-reflected use the textbook init directly.
    init_state = _reflect(init, w) if refin else init

    lines: list[str] = []
    lines.append(f'/* {fname}.c -- generated by termapy from reveng/{name}')
    lines.append(f' * {desc}')
    lines.append(f' * check: crc("123456789") == {_hex(check, w)}')
    lines.append(f' *')
    lines.append(f' * Streaming: init -> update (any number of times) -> finalize.')
    lines.append(f' * One-shot:  call {fname}(data, len).')
    lines.append(f' * Verify:    call {fname}_self_test() (returns 0 on success).')
    lines.append(f' */')
    lines.append(f'#include "{fname}.h"')
    lines.append(f'')
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_c(tbl, w, ctype))
        lines.append("")

    # ----- <fname>_init() -----
    lines.append(f"{ctype} {fname}_init(void) {{")
    lines.append(f"    return {_hex(init_state, w)};")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data, len) -----
    lines.append(
        f"{ctype} {fname}_update({ctype} state, const uint8_t *data, size_t len) {{"
    )
    lines.append(f"    {ctype} crc = state;")
    lines.extend(_update_loop_c(w, poly, refin, mask, table, ctype))
    lines.append(f"    return crc;")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines.append(f"{ctype} {fname}_finalize({ctype} state) {{")
    if refout != refin:
        lines.append(f"    /* reflect output (refout != refin) */")
        lines.append(f"    {ctype} reflected = 0;")
        lines.append(f"    for (int k = 0; k < {w}; k++)")
        lines.append(f"        reflected |= ((state >> k) & 1) << ({w - 1} - k);")
        lines.append(f"    state = reflected;")
    if xorout:
        lines.append(f"    return state ^ {_hex(xorout, w)};")
    else:
        lines.append(f"    return state;")
    lines.append(f"}}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines.append(f"{ctype} {fname}(const uint8_t *data, size_t len) {{")
    lines.append(
        f"    return {fname}_finalize({fname}_update({fname}_init(), data, len));"
    )
    lines.append(f"}}")
    lines.append("")

    # ----- self-test -----
    lines.append(_self_test_c(fname, check, w))

    header = _header_c(name, fname, ctype, desc)
    source = "\n".join(lines)
    return header, source
