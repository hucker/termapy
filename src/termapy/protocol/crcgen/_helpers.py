"""Language-agnostic helpers shared by every target generator.

Function-name sanitization, hex formatting, bit masks, and CRC table
pre-computation are identical across Python / C / Rust / VHDL output
because they're math, not syntax.  Each language module imports what
it needs from here; per-language helpers (table formatters,
self-test scaffolds, header builders) stay local to their target.

Underscore-prefixed; the package's public API is ``__init__.py``.
"""

from __future__ import annotations

from termapy.protocol.crc import _reflect


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
