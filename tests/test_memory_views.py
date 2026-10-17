"""Unit tests for ``termapy.memory_views`` -- scalars, chars, bit targets.

Pure functions only: the codec, the dump-value renderer, the suffix
resolver and the mask builder.  Register fields are word-relative
(``ON:B.15``): the word's width is the symbol's size and its byte order
is the device's, supplied by the caller, so the demo's U1MODE spec decodes
its LE bytes correctly and the same spec would read a BE core right.
"""

from __future__ import annotations

import pytest

from termapy.memory_views import (
    BitTarget,
    decode_scalar,
    decode_words,
    escape_text,
    extract_bits,
    find_field,
    mask_pair,
    render_char,
    render_value,
    resolve_bit_target,
    scalar_size,
)
from termapy.protocol.core import extract_column_value
from termapy.symbols import Symbol

# The demo's U1MODE: LE u32 register, seeded ON | BRGH = 0x8008.
U1MODE = Symbol(
    "U1MODE",
    0xBF806000,
    4,
    "sfr",
    type="ON:B.15 UEN:B.8-9 BRGH:B.3",
)
U1MODE_BYTES = (0x8008).to_bytes(4, "little")
GFLAGS = Symbol("gFlags", 0x1008, 4, "data", type="u32")


class TestScalars:
    @pytest.mark.parametrize(
        "type_name, data, endian, expected",
        [
            ("u8", b"\x1b", "le", 27),
            ("u16", b"\x1b\x00", "le", 27),
            ("u16", b"\x00\x1b", "be", 27),
            ("i16", b"\xff\xff", "le", -1),
            ("u32", b"\x08\x80\x00\x00", "le", 0x8008),
            ("i8", b"\x80", "le", -128),
            ("char", b"A", "le", 65),
        ],
    )
    def test_decode(self, type_name, data, endian, expected):
        assert decode_scalar(data, type_name, endian) == expected

    def test_decode_f32(self):
        import struct

        assert decode_scalar(struct.pack("<f", 1013.25), "f32", "le") == pytest.approx(1013.25)

    def test_wrong_size(self):
        with pytest.raises(ValueError, match="Type u16 needs 2 bytes, got 1"):
            decode_scalar(b"\x00", "u16", "le")

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown type: u128"):
            scalar_size("u128")

    def test_decode_words(self):
        values = decode_words(b"\x1b\x00\x00\x50", "u16", "le")
        assert values == [0x001B, 0x5000], "consecutive LE u16 words"

    def test_decode_words_misaligned(self):
        with pytest.raises(ValueError, match="Invalid length: 3 \\(not a multiple of 2 for u16\\)"):
            decode_words(b"\x00\x01\x02", "u16", "le")


class TestRendering:
    @pytest.mark.parametrize(
        "value, type_name, expected",
        [
            (0x1B, "u16", "001B"),
            (0x8008, "u32", "00008008"),
            (-2, "i16", "-2"),
            (65, "char", "A"),
            (7, "char", "."),
        ],
    )
    def test_render_value(self, value, type_name, expected):
        assert render_value(value, type_name) == expected, "u* hex, i* decimal, char printable"

    def test_render_char(self):
        assert render_char(0x41) == "'A' (0x41)"
        assert render_char(0x07) == "'?' (0x07)", "non-printables show as ?"

    def test_escape_text(self):
        assert escape_text("hi\r\n\x07") == "hi\\r\\n\\x07", "control bytes become escapes"


class TestBitTargets:
    def test_named_field(self):
        # Act -- ON is bit 15 of the register word; the device's endian binds it
        target = resolve_bit_target(U1MODE, "ON", endian="le")

        # Assert
        assert (target.offset, target.width) == (0, 4), "the whole 4-byte word"
        assert (target.low, target.bit_width) == (15, 1), "LSB0 over the logical word"
        assert target.field == "ON"

    def test_field_slice(self):
        target = resolve_bit_target(U1MODE, "UEN", endian="le")
        assert (target.low, target.bit_width) == (8, 2), "an 8-9 range is two bits from 8"

    def test_field_columns_follow_the_device_byte_order(self):
        # Arrange -- the same four bytes, read by a little- and a big-endian core
        on_le = find_field(U1MODE, "ON", "le")
        on_be = find_field(U1MODE, "ON", "be")

        # Act
        actual_le = extract_column_value(U1MODE_BYTES, on_le)
        actual_be = extract_column_value(U1MODE_BYTES, on_be)

        # Assert
        assert actual_le == 1, "LE: 08 80 00 00 is 0x00008008, bit 15 set"
        assert actual_be == 0, "BE: the same bytes are 0x08800000, bit 15 clear"

    def test_numeric_bit_width_from_symbol_type(self):
        target = resolve_bit_target(GFLAGS, "4", endian="le")
        assert (target.width, target.low, target.bit_width) == (4, 4, 1), (
            "u32 symbol -> 4-byte word"
        )

    def test_slice_normalizes_order(self):
        assert resolve_bit_target(GFLAGS, "6-4", endian="le").low == 4, (
            "hi-lo and lo-hi mean the same slice"
        )
        assert resolve_bit_target(GFLAGS, "4-6", endian="le").bit_width == 3

    def test_bare_address_defaults_to_32_bits(self):
        target = resolve_bit_target(None, "15", endian="le")
        assert target.width == 4, "the mask-op default word"

    def test_bit_outside_the_word(self):
        with pytest.raises(ValueError, match="Invalid bit: 32 \\(the word is 32 bits\\)"):
            resolve_bit_target(GFLAGS, "32", endian="le")

    def test_unknown_field(self):
        with pytest.raises(
            ValueError, match="Unknown field: U1MODE.BOGUS \\(fields: ON, UEN, BRGH\\)"
        ):
            find_field(U1MODE, "BOGUS", "le")

    def test_field_on_a_bare_address(self):
        with pytest.raises(ValueError, match="Unknown field: .ON"):
            resolve_bit_target(None, "ON", endian="le")

    def test_extract_from_the_demo_word(self):
        # Arrange -- the seeded U1MODE word through each field
        word = int.from_bytes(U1MODE_BYTES, "little")

        # Assert
        assert extract_bits(word, resolve_bit_target(U1MODE, "ON", endian="le")) == 1, "ON set"
        assert extract_bits(word, resolve_bit_target(U1MODE, "UEN", endian="le")) == 0, "UEN clear"
        assert extract_bits(word, resolve_bit_target(U1MODE, "BRGH", endian="le")) == 1, "BRGH set"


class TestMaskPair:
    def test_set_one_bit(self):
        target = resolve_bit_target(U1MODE, "ON", endian="le")
        and_mask, or_mask = mask_pair(target, 1)
        assert and_mask == 0xFFFF7FFF, "everything but bit 15 kept"
        assert or_mask == 0x00008000, "bit 15 set"

    def test_clear_one_bit(self):
        and_mask, or_mask = mask_pair(resolve_bit_target(U1MODE, "ON", endian="le"), 0)
        assert (and_mask, or_mask) == (0xFFFF7FFF, 0), "clear = keep others, or nothing"

    def test_field_value(self):
        and_mask, or_mask = mask_pair(resolve_bit_target(U1MODE, "UEN", endian="le"), 2)
        assert (and_mask, or_mask) == (0xFFFFFCFF, 0x0200), "two-bit field at 8"

    def test_value_too_big(self):
        with pytest.raises(ValueError, match="Invalid value: 2 \\(ON is 1 bit\\)"):
            mask_pair(resolve_bit_target(U1MODE, "ON", endian="le"), 2)

    def test_round_trip(self):
        target = BitTarget(offset=0, width=2, low=4, bit_width=3)
        and_mask, or_mask = mask_pair(target, 5)
        word = (0xFFFF & and_mask) | or_mask
        assert extract_bits(word, target) == 5, "inject then extract is identity"
