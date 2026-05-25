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
framework, boot self-check, factory burn-in, or crcglot's CI runner
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

from crcglot._helpers import (
    _build_slice8_tables,
    _build_table,
    _func_name,
    _hex,
    _mask,
)
from crcglot.catalogue import CRC_CATALOGUE, _reflect


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


def _format_slice8_tables_c(
    tables: list[list[int]], width: int, ctype: str,
) -> str:
    """Format the 8 slice-by-8 tables as a 2D C ``static const`` array."""
    hex_w = (width + 3) // 4
    lines = [f"static const {ctype} crc_slice_tables[8][256] = {{"]
    for t_idx, table in enumerate(tables):
        lines.append(f"    {{ /* T{t_idx} */")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}"
                for i in range(row, min(row + 8, 256))
            )
            comma = "," if row + 8 < 256 else ""
            lines.append(f"        {vals}{comma}")
        comma = "," if t_idx < 7 else ""
        lines.append(f"    }}{comma}")
    lines.append("};")
    return "\n".join(lines)


def _update_loop_c_slice8(w: int, refin: bool, ctype: str) -> list[str]:
    """Emit the per-8-byte slice-by-8 main loop + byte-by-byte tail.

    Variable ``crc`` (of type ``ctype``) is assumed to already hold
    the incoming state.  Processes ``data[0..len-1]`` 8 bytes at a
    time via 8 chained table lookups (the slice tables), then falls
    back to single-byte table-driven via ``crc_slice_tables[0]`` for
    any 1-7 trailing bytes.

    Only valid for w == 32 or w == 64 (only widths where slice-by-8
    has a meaningful equivalent of "fits in a uint64_t chunk").
    """
    if w == 32:
        if refin:
            # Reflected: input loaded little-endian, low byte of state
            # XOR'd with first 4 input bytes, table indices walk from
            # least-significant byte upward (T7..T0).
            return [
                "    while (len >= 8) {",
                "        uint32_t b03 = (uint32_t)data[0]"
                " | (uint32_t)data[1] << 8"
                " | (uint32_t)data[2] << 16"
                " | (uint32_t)data[3] << 24;",
                "        uint32_t b47 = (uint32_t)data[4]"
                " | (uint32_t)data[5] << 8"
                " | (uint32_t)data[6] << 16"
                " | (uint32_t)data[7] << 24;",
                "        uint32_t xored = crc ^ b03;",
                "        crc = crc_slice_tables[7][ xored        & 0xFF]"
                " ^ crc_slice_tables[6][(xored >>  8) & 0xFF]",
                "            ^ crc_slice_tables[5][(xored >> 16) & 0xFF]"
                " ^ crc_slice_tables[4][(xored >> 24) & 0xFF]",
                "            ^ crc_slice_tables[3][ b47          & 0xFF]"
                " ^ crc_slice_tables[2][(b47   >>  8) & 0xFF]",
                "            ^ crc_slice_tables[1][(b47   >> 16) & 0xFF]"
                " ^ crc_slice_tables[0][(b47   >> 24) & 0xFF];",
                "        data += 8;",
                "        len -= 8;",
                "    }",
                "    while (len--) {",
                "        crc = crc_slice_tables[0][(crc ^ *data++) & 0xFF]"
                " ^ (crc >> 8);",
                "    }",
            ]
        # Non-reflected w=32: load big-endian, state's top XOR'd with
        # first 4 input bytes' top.  Table-index convention: byte at
        # position k in the chunk uses T[7-k] (k=0 is "most delayed",
        # i.e. the byte that has the most zero-bytes processed after
        # it to reach the end of the chunk).
        return [
            "    while (len >= 8) {",
            "        uint32_t b03 = (uint32_t)data[0] << 24"
            " | (uint32_t)data[1] << 16"
            " | (uint32_t)data[2] << 8"
            " | (uint32_t)data[3];",
            "        uint32_t b47 = (uint32_t)data[4] << 24"
            " | (uint32_t)data[5] << 16"
            " | (uint32_t)data[6] << 8"
            " | (uint32_t)data[7];",
            "        uint32_t xored = crc ^ b03;",
            "        crc = crc_slice_tables[7][(xored >> 24) & 0xFF]"
            " ^ crc_slice_tables[6][(xored >> 16) & 0xFF]",
            "            ^ crc_slice_tables[5][(xored >>  8) & 0xFF]"
            " ^ crc_slice_tables[4][ xored        & 0xFF]",
            "            ^ crc_slice_tables[3][(b47   >> 24) & 0xFF]"
            " ^ crc_slice_tables[2][(b47   >> 16) & 0xFF]",
            "            ^ crc_slice_tables[1][(b47   >>  8) & 0xFF]"
            " ^ crc_slice_tables[0][ b47          & 0xFF];",
            "        data += 8;",
            "        len -= 8;",
            "    }",
            "    while (len--) {",
            "        uint32_t top = crc >> 24;",
            "        crc = crc_slice_tables[0][(top ^ *data++) & 0xFF]"
            " ^ (crc << 8);",
            "    }",
        ]
    # w == 64
    if refin:
        return [
            "    while (len >= 8) {",
            "        uint64_t b = (uint64_t)data[0]"
            " | (uint64_t)data[1] << 8"
            " | (uint64_t)data[2] << 16"
            " | (uint64_t)data[3] << 24",
            "                   | (uint64_t)data[4] << 32"
            " | (uint64_t)data[5] << 40"
            " | (uint64_t)data[6] << 48"
            " | (uint64_t)data[7] << 56;",
            "        uint64_t xored = crc ^ b;",
            "        crc = crc_slice_tables[7][ xored        & 0xFF]"
            " ^ crc_slice_tables[6][(xored >>  8) & 0xFF]",
            "            ^ crc_slice_tables[5][(xored >> 16) & 0xFF]"
            " ^ crc_slice_tables[4][(xored >> 24) & 0xFF]",
            "            ^ crc_slice_tables[3][(xored >> 32) & 0xFF]"
            " ^ crc_slice_tables[2][(xored >> 40) & 0xFF]",
            "            ^ crc_slice_tables[1][(xored >> 48) & 0xFF]"
            " ^ crc_slice_tables[0][(xored >> 56) & 0xFF];",
            "        data += 8;",
            "        len -= 8;",
            "    }",
            "    while (len--) {",
            "        crc = crc_slice_tables[0][(crc ^ *data++) & 0xFF]"
            " ^ (crc >> 8);",
            "    }",
        ]
    # Non-reflected w=64.  Same index convention as w=32: byte at
    # position k uses T[7-k].
    return [
        "    while (len >= 8) {",
        "        uint64_t b = (uint64_t)data[0] << 56"
        " | (uint64_t)data[1] << 48"
        " | (uint64_t)data[2] << 40"
        " | (uint64_t)data[3] << 32",
        "                   | (uint64_t)data[4] << 24"
        " | (uint64_t)data[5] << 16"
        " | (uint64_t)data[6] << 8"
        " | (uint64_t)data[7];",
        "        uint64_t xored = crc ^ b;",
        "        crc = crc_slice_tables[7][(xored >> 56) & 0xFF]"
        " ^ crc_slice_tables[6][(xored >> 48) & 0xFF]",
        "            ^ crc_slice_tables[5][(xored >> 40) & 0xFF]"
        " ^ crc_slice_tables[4][(xored >> 32) & 0xFF]",
        "            ^ crc_slice_tables[3][(xored >> 24) & 0xFF]"
        " ^ crc_slice_tables[2][(xored >> 16) & 0xFF]",
        "            ^ crc_slice_tables[1][(xored >>  8) & 0xFF]"
        " ^ crc_slice_tables[0][ xored        & 0xFF];",
        "        data += 8;",
        "        len -= 8;",
        "    }",
        "    while (len--) {",
        "        uint64_t top = crc >> 56;",
        "        crc = crc_slice_tables[0][(top ^ *data++) & 0xFF]"
        " ^ (crc << 8);",
        "    }",
    ]


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
    boot self-check, or crcglot's CI runner harness.  We deliberately
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
    crcglot's pytest runner) don't have to know which headers the
    function body needs.  Declares the streaming triple (init / update
    / finalize), the one-shot convenience wrapper, and the self-test
    function so all five are part of the public surface.
    """
    guard = f"{fname.upper()}_H"
    lines = [
        f"/* {fname}.h -- generated by crcglot from reveng/{name}",
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


def generate_c(
    name: str,
    table: bool = False,
    symbol: str | None = None,
    slice8: bool = False,
) -> tuple[str, str] | None:
    """Look up a CRC algorithm by name and generate a C .h + .c pair.

    Thin wrapper around :func:`generate_c_from_entry`; use the latter
    directly when generating from a custom (non-catalogue) algorithm spec.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None
    return generate_c_from_entry(
        name, entry, table=table, symbol=symbol, slice8=slice8,
    )


def generate_c_from_entry(
    name: str,
    entry: dict,
    table: bool = False,
    symbol: str | None = None,
    slice8: bool = False,
) -> tuple[str, str]:
    """Generate a C ``.h`` + ``.c`` pair from a catalogue-shaped entry dict.

    Returns a ``(header, source)`` tuple of complete, compilable files.
    The source emits the streaming triple (init / update / finalize),
    a one-shot wrapper, and a self-test -- see module docstring for
    details.

    Args:
        name: Algorithm name (used in comments + ``_self_test`` input
            data; pass a meaningful descriptor when generating from
            custom params).
        entry: Catalogue dict with ``width`` / ``poly`` / ``init`` /
            ``refin`` / ``refout`` / ``xorout`` / ``check`` (required)
            and ``desc`` (optional).
        table: If True, emit the table-driven implementation instead
            of the bit-by-bit form.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).  Header filename and
            include guard derive from the symbol so the generated
            header references match.

    Returns:
        ``(header_source, impl_source)`` tuple of strings.
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
        ctype = "uint8_t"
    elif w <= 16:
        ctype = "uint16_t"
    elif w <= 32:
        ctype = "uint32_t"
    else:
        ctype = "uint64_t"

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"slice8=True requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    # Pre-loaded init state: matches the value the main loop expects
    # on entry.  Reflected algorithms enter the loop with the reflection
    # of the textbook init; non-reflected use the textbook init directly.
    init_state = _reflect(init, w) if refin else init

    lines: list[str] = []
    lines.append(f'/* {fname}.c -- generated by crcglot from reveng/{name}')
    lines.append(f' * {desc}')
    lines.append(f' * check: crc("123456789") == {_hex(check, w)}')
    lines.append(f' *')
    lines.append(f' * Streaming: init -> update (any number of times) -> finalize.')
    lines.append(f' * One-shot:  call {fname}(data, len).')
    lines.append(f' * Verify:    call {fname}_self_test() (returns 0 on success).')
    if slice8:
        lines.append(
            f' * Variant:   slice-by-8 (8 tables, ~10x throughput vs '
            f'plain table for large buffers).'
        )
    lines.append(f' */')
    lines.append(f'#include "{fname}.h"')
    lines.append(f'')
    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_c(slice_tables, w, ctype))
        lines.append("")
    elif table:
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
    if slice8:
        lines.extend(_update_loop_c_slice8(w, refin, ctype))
    else:
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
