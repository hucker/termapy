"""Python CRC generator.

Emits a complete Python module string containing a single function
that computes the named CRC over a ``bytes`` input.  Verified at
build time by :class:`tests.test_crc_codegen.TestGeneratePython`,
which ``exec``-s the output and asserts the result for
``b"123456789"`` matches the reveng catalogue's ``check`` value.

No self-test function is emitted (unlike the C / Rust / VHDL
generators) because the Python output never leaves Python's verified
test boundary; pytest already executes every variant against the
reveng check value at termapy build time.
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


def _format_table_python(table: list[int], width: int) -> str:
    """Format a lookup table as a Python tuple literal named ``_TABLE``."""
    hex_w = (width + 3) // 4
    lines = ["_TABLE = ("]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append(")")
    return "\n".join(lines)


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
