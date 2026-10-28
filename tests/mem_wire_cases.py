"""The MEM wire spec's refusals and reply forms, one source for every implementation that answers it.

termapy_mem.c (``tests/test_firmware_mem.py``, through its host harness)
and MicroPython's termapy_mem.py (``tests/test_boards.py``, through the
board monitor) answer each line of ``WIRE_ERRORS`` with the same one
``ERR`` line, and form rows and masked words as ``row`` and ``masked`` do.  A
line is the verb and its arguments as the C dispatcher takes them; the
board's tests send it with the ``MEM.`` prefix termapy sends.  Neither a
dispatcher nor a memory map decides any of these, so a case that one of
those decides stays with its own implementation's tests.
"""

from __future__ import annotations

import sys

# (line, the reason after ERR)
WIRE_ERRORS: list[tuple[str, str]] = [
    ("R", "usage"),
    ("R 0", "usage"),
    ("R 0 0", "length"),
    ("R 0 65", "length"),
    ("R FFFFFFFF 2", "range"),  # wraps past the top of the address space
    ("R 0 16 junk", "usage"),  # a verb takes exactly its arguments
    ("INFO junk", "usage"),
    ("R 100000000 1", "usage"),  # nine hex digits: wider than an address
    ("R 0 4294967296", "usage"),  # past uint32_t, refused rather than wrapped
    ("W 0", "usage"),
    ("W 0 ABC", "usage"),  # odd digit count
    ("W 0 " + "00" * 65, "length"),
    ("M 3 FFFF 0000", "usage"),  # misaligned for a 16-bit word
    ("M 0 FF 0000", "usage"),  # mask widths disagree
    ("M 0 FF 00 junk", "usage"),
]


def row(addr: int, data: bytes) -> str:
    """One MEM.R reply row as it reaches the wire: eight address digits, the bytes in hex, CRLF."""
    return f"{addr:08X}: " + " ".join(f"{byte:02X}" for byte in data) + "\r\n"


def masked(old: bytes, and_mask: int, or_mask: int) -> bytes:
    """What MEM.M leaves behind, computed the way the core sees the word."""
    value = int.from_bytes(old, sys.byteorder)
    return ((value & and_mask) | or_mask).to_bytes(len(old), sys.byteorder)
