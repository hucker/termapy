"""Language-agnostic helpers shared by every target generator.

Function-name sanitization, hex formatting, bit masks, and CRC table
pre-computation are identical across Python / C / Rust / VHDL output
because they're math, not syntax.  Each language module imports what
it needs from here; per-language helpers (table formatters,
self-test scaffolds, header builders) stay local to their target.

Underscore-prefixed; the package's public API is ``__init__.py``.
"""

from __future__ import annotations

from crcglot.catalogue import _reflect


def _func_name(algo_name: str) -> str:
    """Convert a CRC algorithm name into a valid identifier.

    Algorithm names from the reveng catalogue use ``-`` and ``.``
    which aren't valid in C / Python / Rust / VHDL identifiers;
    swap them for underscores.  Same mangling is applied
    consistently across all four target languages.
    """
    return algo_name.replace("-", "_").replace(".", "_")


def _hex(value: int, width: int) -> str:
    """Format an integer as a ``0xHEX`` literal sized for ``width`` bits.

    The ``0x``-prefixed form is identical in C / Python / Rust source
    (and acceptable in VHDL comments), so callers across all target
    languages share this helper.  VHDL *code* uses :func:`_vhdl_lit`
    from the vhdl module because hex literals there have a different
    syntax for arithmetic contexts.
    """
    hex_w = (width + 3) // 4
    return f"0x{value:0{hex_w}X}"


def _mask(width: int) -> str:
    """Format ``(1 << width) - 1`` as a hex literal of matching width."""
    return _hex((1 << width) - 1, width)


def _build_table(width: int, poly: int, refin: bool) -> list[int]:
    """Pre-compute the 256-entry CRC lookup table for an algorithm.

    Returns the table as a list of ``width``-bit integers, one per
    possible byte value.  Caller renders this list to its target
    language's array syntax via per-language formatters.

    The reflected-input case uses the reflected polynomial and
    right-shifts; the normal case left-shifts.  Both are textbook
    Sarwate's algorithm.
    """
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


def _build_slice8_tables(
    width: int, poly: int, refin: bool,
) -> list[list[int]]:
    """Pre-compute the 8 tables used by slice-by-8 CRC.

    Returns ``[T0, T1, ..., T7]``: each Tk is a 256-entry list of
    width-bit ints.  Tk[i] is the CRC of ``[i] + [0] * k`` -- i.e.
    the contribution to the running CRC of a byte at position k.

    The recurrence: ``T(k+1)[i] = T0[low_byte(Tk[i])] ^ shifted_rest(Tk[i])``
    where the shift direction matches the polynomial direction.  This
    is what powers Intel's slice-by-8 (5-10x throughput over standard
    table-driven for CRC-32 / CRC-64 on big buffers).
    """
    mask = (1 << width) - 1
    t0 = _build_table(width, poly, refin)
    tables = [t0]
    for _ in range(7):
        prev = tables[-1]
        nxt: list[int] = []
        if refin:
            # Reflected: low byte feeds next lookup, rest shifts right.
            for i in range(256):
                v = prev[i]
                nxt.append((t0[v & 0xFF] ^ (v >> 8)) & mask)
        else:
            # Normal: high byte feeds next lookup, rest shifts left.
            for i in range(256):
                v = prev[i]
                top = (v >> (width - 8)) & 0xFF
                nxt.append((t0[top] ^ ((v << 8) & mask)) & mask)
        tables.append(nxt)
    return tables


# Note: a Python reference for slice-by-8 was considered but dropped.
# It would have served as a test oracle for the generated C / Rust
# code, but Python doesn't benefit from slice-by-N at runtime (per-int
# overhead eats the win), and using it as an oracle adds a third
# implementation that itself needs verification.  Better verification:
# generate both bit-by-bit and slice-by-8 in the target language,
# compile both, run both on the same inputs, assert they agree.
# Bit-by-bit is reveng-verified, so equivalence means slice-by-8 is
# correct.  Tests live in tests/test_crc_codegen_exec.py.
