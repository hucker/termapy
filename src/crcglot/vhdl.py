"""VHDL CRC generator.

Emits a single ``.vhd`` file containing a package with five functions:

  - ``<fname>_init`` -- return the starting state (std_logic_vector)
  - ``<fname>_update(state, data)`` -- feed bytes, return new state
  - ``<fname>_finalize(state)`` -- apply output reflection + xorout
  - ``<fname>(data)`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test`` -- runs the algorithm against the canonical
    reveng check string ``"123456789"`` and reports whether the
    result matches.  Designed to be called from a testbench process
    via ``assert`` (crcglot's pytest harness synthesizes that
    testbench to verify the generator).

The streaming primitives let callers process a byte stream that
arrives in chunks (any-length packed std_logic_vector per update
call).  The one-shot wrapper preserves the simple API for the
common case.

Scope note: simulator-friendly VHDL (pure functions over
``std_logic_vector``).  Compiles and simulates under GHDL and is
enough to verify correctness against the reveng catalogue.
Synthesizable FPGA hardware (pipelined entity / architecture) is a
future enhancement -- the function-in-package form is the right
shape for a "verified reference implementation"; a hardware
designer will typically want to wrap a synthesizable wrapper
around it.

Verified at build time by ``tests.test_crc_codegen_exec
.TestGeneratedVhdlExecutes`` (one-shot path) and
``TestGeneratedVhdlStreaming`` (streaming splittability invariant).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from crcglot._helpers import _func_name, _hex
from crcglot.catalogue import CRC_CATALOGUE, _reflect


def _vhdl_lit(value: int, width: int) -> str:
    """Format an integer constant as a VHDL ``unsigned``-compatible literal.

    Hex bit-string literals (``x"..."``) are used when ``width`` is a
    multiple of 4 because they are width-explicit (no need to pass the
    size separately) and don't suffer from ``to_unsigned``'s ``natural``
    range limit -- which silently rejects values >= 2^31 with a
    runtime bound-check failure.  That bites every 32-bit CRC whose
    init / poly / check value happens to exceed 0x7FFFFFFF
    (most of them: 0xFFFFFFFF is a common init).

    For widths that aren't a multiple of 4 (e.g. CRC-5, CRC-12), the
    max possible value fits in ``natural``, so ``to_unsigned`` is
    safe and we keep using it.
    """
    if width % 4 == 0:
        hex_w = width // 4
        return f'x"{value:0{hex_w}X}"'
    return f"to_unsigned({value}, {width})"


def _self_test_vhdl(fname: str, check: int, width: int) -> str:
    """Emit a VHDL self-test function returning ``true`` / ``false``.

    Designed to be called from a testbench process via ``assert ...
    severity failure`` -- crcglot's pytest harness synthesizes that
    testbench at test time (see ``test_crc_codegen_exec.py``).
    """
    lines = [
        f"    -- Run the canonical reveng check value; returns true on success.",
        f"    function {fname}_self_test return boolean is",
        f'        constant kCheckInput: std_logic_vector(71 downto 0) :=',
        f'            x"313233343536373839";  -- ASCII "123456789"',
        f"    begin",
        f"        return unsigned({fname}(kCheckInput)) = "
        f"{_vhdl_lit(check, width)};",
        f"    end function;",
    ]
    return "\n".join(lines)


def generate_vhdl(
    name: str, table: bool = False, symbol: str | None = None,
) -> str | None:
    """Look up a CRC algorithm by name and generate a VHDL package.

    Thin wrapper around :func:`generate_vhdl_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None
    return generate_vhdl_from_entry(name, entry, table=table, symbol=symbol)


def generate_vhdl_from_entry(
    name: str,
    entry: dict,
    table: bool = False,
    symbol: str | None = None,
) -> str:
    """Generate a VHDL package from a catalogue-shaped entry dict.

    Args:
        name: Algorithm name (used in comments).
        entry: Catalogue dict with ``width`` / ``poly`` / ``init`` /
            ``refin`` / ``refout`` / ``xorout`` / ``check`` (required)
            and ``desc`` (optional).
        table: Accepted for API symmetry with the other generators
            but ignored -- bit-by-bit only (table-driven VHDL deferred).
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).  Package name derives
            from the symbol so include references match.

    Returns:
        VHDL source string.
    """
    _ = table  # currently unused (see scope note in module docstring)
    w = entry["width"]
    poly = entry["poly"]
    init = entry["init"]
    refin = entry["refin"]
    refout = entry["refout"]
    xorout = entry["xorout"]
    check = entry["check"]
    desc = entry.get("desc", "")
    fname = symbol if symbol else _func_name(name)
    pkg = f"{fname}_pkg"

    # Pre-loaded init state and (for reflected algorithms) the reflected
    # polynomial used in the right-shift loop.
    if refin:
        init_state = _reflect(init, w)
        poly_val = _reflect(poly, w)
    else:
        init_state = init
        poly_val = poly

    # ---- package declaration (forward declarations only) ----
    lines: list[str] = [
        f"-- {fname}.vhd -- generated by crcglot from reveng/{name}",
        f"-- {desc}",
        f"-- check: crc(\"123456789\") == {_hex(check, w)}",
        f"--",
        f"-- Streaming: init -> update (any number of times) -> finalize.",
        f"-- One-shot:  {fname}(data).",
        f"-- Verify:    call {fname}_self_test from a testbench's assert.",
        f"",
        f"library ieee;",
        f"use ieee.std_logic_1164.all;",
        f"use ieee.numeric_std.all;",
        f"",
        f"package {pkg} is",
        f"    -- Streaming triple.  ``state`` is a {w}-bit std_logic_vector",
        f"    -- carrying the in-progress CRC value between updates.",
        f"    function {fname}_init return std_logic_vector;",
        f"    function {fname}_update(state: std_logic_vector; "
        f"data: std_logic_vector) return std_logic_vector;",
        f"    function {fname}_finalize(state: std_logic_vector) "
        f"return std_logic_vector;",
        f"",
        f"    -- One-shot convenience: init + single update + finalize.",
        f"    -- Input ``data`` is a packed byte vector "
        f"(length must be a multiple of 8).",
        f"    function {fname}(data: std_logic_vector) "
        f"return std_logic_vector;",
        f"",
        f"    -- Self-test (returns true if algorithm matches reveng check).",
        f"    function {fname}_self_test return boolean;",
        f"end package;",
        f"",
        f"package body {pkg} is",
    ]

    # ---- <fname>_init ----
    lines += [
        f"",
        f"    function {fname}_init return std_logic_vector is",
        f"        variable s: unsigned({w - 1} downto 0) := "
        f"{_vhdl_lit(init_state, w)};",
        f"    begin",
        f"        return std_logic_vector(s);",
        f"    end function;",
    ]

    # ---- <fname>_update(state, data) ----
    lines += [
        f"",
        f"    function {fname}_update(state: std_logic_vector; "
        f"data: std_logic_vector) return std_logic_vector is",
        f"        variable crc: unsigned({w - 1} downto 0) := unsigned(state);",
        f"        variable byte: unsigned(7 downto 0);",
        f"        -- Normalize indexing regardless of caller's slice direction.",
        f"        constant d: std_logic_vector(data'length - 1 downto 0) := data;",
        f"        constant n: natural := data'length / 8;",
        f"    begin",
        f"        for i in 0 to n - 1 loop",
        f"            byte := unsigned("
        f"d((n - i)*8 - 1 downto (n - i - 1)*8));",
    ]
    if refin:
        lines += [
            f"            crc := crc xor resize(byte, {w});",
            f"            for j in 0 to 7 loop",
            f"                if crc(0) = '1' then",
            f"                    crc := shift_right(crc, 1) xor "
            f"{_vhdl_lit(poly_val, w)};",
            f"                else",
            f"                    crc := shift_right(crc, 1);",
            f"                end if;",
            f"            end loop;",
        ]
    else:
        lines += [
            f"            crc := crc xor shift_left(resize(byte, {w}), {w - 8});",
            f"            for j in 0 to 7 loop",
            f"                if crc({w - 1}) = '1' then",
            f"                    crc := shift_left(crc, 1) xor "
            f"{_vhdl_lit(poly_val, w)};",
            f"                else",
            f"                    crc := shift_left(crc, 1);",
            f"                end if;",
            f"            end loop;",
        ]
    lines += [
        f"        end loop;",
        f"        return std_logic_vector(crc);",
        f"    end function;",
    ]

    # ---- <fname>_finalize(state) ----
    finalize_lines: list[str] = [
        f"",
        f"    function {fname}_finalize(state: std_logic_vector) "
        f"return std_logic_vector is",
        f"        variable crc: unsigned({w - 1} downto 0) := unsigned(state);",
    ]
    if refout != refin:
        finalize_lines.append(
            f"        variable reflected: unsigned({w - 1} downto 0);"
        )
    finalize_lines.append(f"    begin")
    if refout != refin:
        finalize_lines += [
            f"        -- reflect output (refout != refin)",
            f"        reflected := (others => '0');",
            f"        for k in 0 to {w - 1} loop",
            f"            reflected(k) := crc({w - 1} - k);",
            f"        end loop;",
            f"        crc := reflected;",
        ]
    if xorout:
        finalize_lines.append(
            f"        return std_logic_vector(crc xor "
            f"{_vhdl_lit(xorout, w)});"
        )
    else:
        finalize_lines.append(f"        return std_logic_vector(crc);")
    finalize_lines.append(f"    end function;")
    lines += finalize_lines

    # ---- <fname> one-shot wrapper ----
    lines += [
        f"",
        f"    function {fname}(data: std_logic_vector) "
        f"return std_logic_vector is",
        f"    begin",
        f"        return {fname}_finalize("
        f"{fname}_update({fname}_init, data));",
        f"    end function;",
    ]

    # ---- self-test ----
    lines.append("")
    lines.append(_self_test_vhdl(fname, check, w))

    lines.append(f"end package body;")

    return "\n".join(lines)
