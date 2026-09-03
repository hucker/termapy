"""Typed views over device bytes -- layer 3 of the memory design.

:mod:`termapy.memory` moves bytes; this module gives them meaning:
scalars (``u16`` -> 27), characters, word-grouped dump values, and --
for CPU registers -- named bit fields via the protocol format-spec
language.  The format-spec semantics are EXACTLY the protocol engine's
("byte order in the spec IS the byte order in memory"): an LE 32-bit
register's bit 15 is authored ``ON:B4-1.15``, and decoding is
:func:`termapy.protocol.core.extract_column_value` verbatim.

Bit targets (a ``.suffix`` from the address grammar) resolve to a word
plus an LSB0 slice; the masks for an atomic ``MEM.M`` -- or the host-side
read-modify-write fallback -- come from :func:`mask_pair`.  All pure
functions; no Textual, no pyserial, no I/O.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Final

from termapy.protocol.core import (
    ColumnSpec,
    extract_column_value,
    inject_column_value,
    parse_format_spec,
)
from termapy.symbols import Symbol

# Scalar type token -> (struct format char, byte size).  ``char`` is the
# odd one out: one byte rendered as a character, handled separately.
SCALARS: Final[dict[str, tuple[str, int]]] = {
    "u8": ("B", 1), "u16": ("H", 2), "u32": ("I", 4), "u64": ("Q", 8),
    "i8": ("b", 1), "i16": ("h", 2), "i32": ("i", 4), "i64": ("q", 8),
    "f32": ("f", 4), "f64": ("d", 8),
}

TYPE_TOKENS: Final[frozenset[str]] = frozenset(SCALARS) | {"char"}

_BIT_RE: Final = re.compile(r"^(?P<lo>\d+)(?:-(?P<hi>\d+))?$")


def scalar_size(type_name: str) -> int:
    """Byte size of a scalar type token (``char`` counts as one byte).

    Raises:
        ValueError: Not a known token.
    """
    if type_name == "char":
        return 1
    if type_name in SCALARS:
        return SCALARS[type_name][1]
    raise ValueError(f"Unknown type: {type_name} (types: {', '.join(sorted(TYPE_TOKENS))})")


def decode_scalar(data: bytes, type_name: str, endian: str) -> int | float:
    """Decode one scalar from bytes in memory order.

    Args:
        data: Exactly the scalar's bytes.
        type_name: A token from :data:`SCALARS` (``char`` decodes as u8).
        endian: ``le`` or ``be`` -- the device's byte order.

    Raises:
        ValueError: Unknown token or wrong byte count.
    """
    fmt, size = SCALARS["u8" if type_name == "char" else type_name] \
        if (type_name in SCALARS or type_name == "char") else (None, None)
    if fmt is None:
        raise ValueError(f"Unknown type: {type_name} (types: {', '.join(sorted(TYPE_TOKENS))})")
    if len(data) != size:
        raise ValueError(f"Type {type_name} needs {size} bytes, got {len(data)}")
    prefix = "<" if endian == "le" else ">"
    return struct.unpack(f"{prefix}{fmt}", data)[0]


def decode_words(data: bytes, type_name: str, endian: str) -> list[int | float]:
    """Decode a block into consecutive scalars (the word-dump view).

    Raises:
        ValueError: ``len(data)`` is not a multiple of the scalar size.
    """
    size = scalar_size(type_name)
    if len(data) % size:
        raise ValueError(f"Invalid length: {len(data)} (not a multiple of {size} for {type_name})")
    return [
        decode_scalar(data[offset:offset + size], type_name, endian)
        for offset in range(0, len(data), size)
    ]


def render_value(value: int | float, type_name: str) -> str:
    """One dump-cell rendering: hex words for ``u*``, decimal for ``i*``, floats.

    ``u*`` values are zero-padded hex (the dump idiom); ``i*`` signed
    decimal; ``f*`` compact floats; ``char`` as ``'A'`` or ``.``.
    """
    if type_name == "char":
        byte = int(value)
        return chr(byte) if 0x20 <= byte < 0x7F else "."
    if type_name.startswith("u"):
        return f"{int(value):0{scalar_size(type_name) * 2}X}"
    if type_name.startswith("i"):
        return str(int(value))
    return f"{value:.6g}"


def render_char(byte: int) -> str:
    """``'A' (0x41)`` -- the /mem.read rendering of a char."""
    shown = chr(byte) if 0x20 <= byte < 0x7F else "?"
    return f"'{shown}' (0x{byte:02X})"


def escape_text(text: str) -> str:
    """Display form of a device string: non-printables become escapes."""
    out: list[str] = []
    for char in text:
        if char == "\\":
            out.append("\\\\")
        elif char in ("\r", "\n", "\t"):
            out.append({"\r": "\\r", "\n": "\\n", "\t": "\\t"}[char])
        elif 0x20 <= ord(char) < 0x7F:
            out.append(char)
        else:
            out.append(f"\\x{ord(char):02x}")
    return "".join(out)


# ── Bit targets ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BitTarget:
    """A resolved bit / slice / named field within a word.

    Attributes:
        offset: Byte offset of the word from the base address.
        width: The word's byte width (the MEM.M / RMW access size).
        low: Lowest bit index, LSB0 over the logical word.
        bit_width: Number of bits.
        field: The field name when resolved from a symbol's type, else "".
    """

    offset: int
    width: int
    low: int
    bit_width: int
    field: str = ""

    @property
    def mask(self) -> int:
        return ((1 << self.bit_width) - 1) << self.low


def symbol_columns(symbol: Symbol) -> list[ColumnSpec]:
    """The parsed format-spec columns of a symbol's type (may be empty)."""
    if not symbol.type or symbol.type in SCALARS or symbol.type == "char":
        return []
    try:
        return parse_format_spec(symbol.type)
    except (ValueError, IndexError, KeyError):
        return []


def find_field(symbol: Symbol, name: str) -> ColumnSpec:
    """The named ``B`` field of a symbol's format-spec type.

    Raises:
        ValueError: The symbol has no such field (the message lists what
            it does have), or the field is not a bit field.
    """
    columns = symbol_columns(symbol)
    for column in columns:
        if column.name == name:
            if column.type_code not in ("B", "b"):
                raise ValueError(f"Not a bit field: {symbol.name}.{name}")
            return column
    fields = ", ".join(column.name for column in columns) or "none"
    raise ValueError(f"Unknown field: {symbol.name}.{name} (fields: {fields})")


def _word_width(symbol: Symbol | None, default_width: int) -> int:
    """The RMW word width for a numeric bit/slice suffix."""
    if symbol is not None:
        if symbol.type in SCALARS:
            return SCALARS[symbol.type][1]
        if symbol.size in (1, 2, 4):
            return symbol.size
    return default_width


def resolve_bit_target(
    symbol: Symbol | None, suffix: str, *, default_width: int = 4,
) -> BitTarget:
    """Interpret an address-grammar suffix against an optional symbol.

    ``.15`` is a bit, ``.8-9`` a slice (LSB0, either order), anything
    else a named field of the symbol's format-spec type.

    Args:
        symbol: The symbol the target named, or None for a bare address.
        suffix: The unconsumed text after the last dot.
        default_width: Word width when nothing else decides (the mask-op
            rule: 32-bit).

    Raises:
        ValueError: A named field without a symbol/type, an unknown
            field, or a bit index outside the word.
    """
    match = _BIT_RE.match(suffix)
    if match is None:
        if symbol is None:
            raise ValueError(f"Unknown field: .{suffix} (a bare address takes .<bit> or .<lo>-<hi>)")
        column = find_field(symbol, suffix)
        indices = column.byte_indices
        low, bit_width = _column_slice(column)
        return BitTarget(
            offset=min(indices), width=len(indices), low=low, bit_width=bit_width,
            field=column.name,
        )
    lo = int(match.group("lo"))
    hi = int(match.group("hi")) if match.group("hi") is not None else lo
    low, high = min(lo, hi), max(lo, hi)
    width = _word_width(symbol, default_width)
    if high >= width * 8:
        raise ValueError(f"Invalid bit: {suffix} (the word is {width * 8} bits)")
    return BitTarget(offset=0, width=width, low=low, bit_width=high - low + 1)


def _column_slice(column: ColumnSpec) -> tuple[int, int]:
    if isinstance(column.bit, tuple):
        start, end = column.bit
        return min(start, end), abs(start - end) + 1
    return int(column.bit or 0), 1


def mask_pair(target: BitTarget, value: int) -> tuple[int, int]:
    """The ``MEM.M`` masks that set ``target`` to ``value``.

    Returns:
        ``(and_mask, or_mask)`` over the ``target.width``-byte word.

    Raises:
        ValueError: ``value`` does not fit the field.
    """
    if not 0 <= value < (1 << target.bit_width):
        label = target.field or f"{target.bit_width} bits"
        raise ValueError(
            f"Invalid value: {value} "
            f"({label} is {target.bit_width} bit{'s' if target.bit_width != 1 else ''})"
            if target.field else
            f"Invalid value: {value} ({target.bit_width} bit{'s' if target.bit_width != 1 else ''})"
        )
    word_mask = (1 << (8 * target.width)) - 1
    and_mask = word_mask & ~target.mask
    or_mask = (value << target.low) & target.mask
    return and_mask, or_mask


def extract_bits(word: int, target: BitTarget) -> int:
    """The target's value out of a logical word."""
    return (word >> target.low) & ((1 << target.bit_width) - 1)


__all__ = [
    "SCALARS",
    "TYPE_TOKENS",
    "BitTarget",
    "decode_scalar",
    "decode_words",
    "escape_text",
    "extract_bits",
    "extract_column_value",
    "find_field",
    "inject_column_value",
    "mask_pair",
    "render_char",
    "render_value",
    "resolve_bit_target",
    "scalar_size",
    "symbol_columns",
]
