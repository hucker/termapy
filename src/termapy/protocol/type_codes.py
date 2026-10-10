"""The format-spec type codes -- one table, every consumer iterates it.

A format-spec column is ``Name:<code><bytes>`` (``Temp:U1-2``).  The codes
used to be listed by hand in four places -- the parser's two code-set
strings, the Protocol Testing help page, and two README tables -- and the
README drifted (it called ``H``/``h`` upper/lowercase hex, which they are
not).  ``TYPE_CODES`` is now the single source:

- ``protocol.core.parse_format_spec`` takes its code sets from
  ``BYTE_CODES`` / ``BIT_CODES``;
- ``scripts/sync_format_tables.py`` renders the markdown tables between
  the ``format-types:start`` / ``format-types:end`` markers, computing each
  row's Output column by running its examples over ``sample`` through the
  real decoder, so a documented output cannot disagree with the code;
- ``tests/test_format_tables_sync.py`` runs the script's ``--check``.

Pure data, no imports from the rest of termapy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["bytes", "bits", "crc"]


@dataclass(frozen=True)
class TypeCode:
    """One format-spec type code and how the docs show it.

    Attributes:
        code: The code as written in a spec (``"H"``), or the display form
            for a family the parser recognizes by shape (``"crc*"``).
        kind: ``bytes`` (``<code><byte refs>``), ``bits``
            (``<code><byte refs>.<bit refs>``), or ``crc`` (an algorithm
            name).
        meaning: The table's one-phrase description.
        examples: Spec bodies, without the ``Name:``, shown in order.
        sample: Bytes the examples are decoded from to fill the Output
            column.  Unused when ``output`` is set.
        output: Literal Output text for a row the decoder does not render
            as a value (padding, CRC verify).
    """

    code: str
    kind: Kind
    meaning: str
    examples: tuple[str, ...]
    sample: bytes = b""
    output: str = ""


TYPE_CODES: tuple[TypeCode, ...] = (
    TypeCode("H", "bytes", "Hex bytes", ("H1", "H3-4"), bytes([0x0A, 0x00, 0x01, 0xFF])),
    TypeCode("h", "bytes", "Hex, per byte", ("h3-4",), bytes([0x0A, 0x00, 0x01, 0xFF])),
    TypeCode("U", "bytes", "Unsigned integer", ("U1", "U3-4"), bytes([0x0A, 0x00, 0x01, 0x00])),
    TypeCode("I", "bytes", "Signed integer", ("I1", "I3-4"), bytes([0xFF, 0x00, 0x00, 0x7F])),
    TypeCode("S", "bytes", "ASCII string", ("S1-5",), b"Hello"),
    TypeCode("F", "bytes", "IEEE 754 float", ("F1-4",), bytes([0x40, 0x48, 0xF5, 0xC3])),
    TypeCode("B", "bits", "Bit field", ("B1.3", "B1-2.7-9"), bytes([0x0A, 0x80])),
    TypeCode("b", "bits", "Bit field, bits", ("b1.0-7",), bytes([0x06])),
    TypeCode("_", "bytes", "Padding (hidden)", ("_3-4",), output="*(skipped)*"),
    TypeCode("crc*", "crc", "CRC verify", ("crc16-modbus",), output="pass/fail"),
)

BYTE_CODES = "".join(entry.code for entry in TYPE_CODES if entry.kind == "bytes")
"""Codes the parser reads as ``<code><byte refs>``."""

BIT_CODES = "".join(entry.code for entry in TYPE_CODES if entry.kind == "bits")
"""Codes the parser reads as ``<code><byte refs>.<bit refs>``."""
