"""Word-relative bit fields: ``NAME:B.<bits>`` binds to a register word, not to packet bytes.

The packet form (``B4-1.15``) spells its own byte order; the register
form leaves that to the reader -- the symbol's size and the device's
endian -- so one spec reads right on either kind of core, and a packet
spec, which has no word, is refused rather than silently read as zero.
"""

from __future__ import annotations

import pytest

from termapy.protocol.core import (
    apply_format,
    extract_column_value,
    inject_column_value,
    parse_format_spec,
    resolve_word_columns,
)


class TestParse:

    def test_no_byte_range_parses_as_word_relative(self):
        on, mode = parse_format_spec("ON:B.15 MODE:B.8-9")
        assert (on.type_code, on.byte_indices, on.bit) == ("B", [], 15)
        assert (mode.byte_indices, mode.bit) == ([], (8, 9))

    def test_explicit_byte_range_is_untouched(self):
        (on,) = parse_format_spec("ON:B4-1.15")
        assert on.byte_indices == [3, 2, 1, 0], "the packet form keeps its own order"


class TestResolve:

    @pytest.mark.parametrize(("size", "endian", "expected"), [
        (4, "le", [3, 2, 1, 0]),
        (4, "be", [0, 1, 2, 3]),
        (2, "le", [1, 0]),
        (1, "le", [0]),
        (1, "be", [0]),
    ])
    def test_binds_the_word(self, size, endian, expected):
        (column,) = resolve_word_columns(parse_format_spec("F:B.0"), size, endian)
        assert column.byte_indices == expected

    def test_explicit_columns_pass_through(self):
        # Arrange
        columns = parse_format_spec("A:B.0 Len:U1 C:B1-2.3")

        # Act
        resolved = resolve_word_columns(columns, 4, "le")

        # Assert
        actual = [column.byte_indices for column in resolved]
        expected = [[3, 2, 1, 0], [0], [0, 1]]
        assert actual == expected, "only the word-relative column is bound"
        assert resolved[1] is columns[1] and resolved[2] is columns[2], "the others are untouched"

    def test_bit_outside_the_word_is_refused(self):
        with pytest.raises(ValueError, match=r"Invalid bit: HI \(the word is 16 bits\)"):
            resolve_word_columns(parse_format_spec("HI:B.16"), 2, "le")

    def test_same_bytes_read_in_the_core_byte_order(self):
        # Arrange -- 0x8008 stored little-endian: 08 80 00 00
        data = (0x8008).to_bytes(4, "little")
        (on_le,) = resolve_word_columns(parse_format_spec("ON:B.15"), 4, "le")
        (on_be,) = resolve_word_columns(parse_format_spec("ON:B.15"), 4, "be")

        # Act
        actual_le = extract_column_value(data, on_le)
        actual_be = extract_column_value(data, on_be)

        # Assert
        assert actual_le == 1, "a little-endian core sees 0x00008008: bit 15 set"
        assert actual_be == 0, "a big-endian core sees 0x08800000: bit 15 clear"

    def test_inject_follows_the_same_binding(self):
        (on,) = resolve_word_columns(parse_format_spec("ON:B.15"), 4, "le")
        actual = inject_column_value(bytes(4), on, 1)
        assert actual == (0x8000).to_bytes(4, "little"), "bit 15 lands in the LE word's second byte"


class TestUnboundIsAnError:
    """A packet has no register word, so the primitives refuse an unbound field."""

    def test_extract(self):
        (column,) = parse_format_spec("ON:B.15")
        with pytest.raises(ValueError, match="no byte range"):
            extract_column_value(bytes(4), column)

    def test_inject(self):
        (column,) = parse_format_spec("ON:B.15")
        with pytest.raises(ValueError, match="no byte range"):
            inject_column_value(bytes(4), column, 1)

    def test_apply_format(self):
        with pytest.raises(ValueError, match="no byte range"):
            apply_format(bytes(4), parse_format_spec("ON:B.15"))
