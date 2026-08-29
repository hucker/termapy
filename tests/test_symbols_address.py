"""Tests for ``termapy.symbols.address`` -- the address grammar.

Replaces the ``TestParseAddress`` cases of the retired ``tests/test_pic_map.py``
with DELIBERATE inversions: the example plugin guessed any 4+ hex-looking
token as hex (``CAFE`` -> 0xCAFE, ``20000000`` -> 0x20000000, ``12345`` ->
0x12345).  The grammar here never guesses: ``1000`` is decimal, ``CAFE`` is
a name, and only ``0x..`` / ``..h`` spell hex.  ``FFFFh`` -> 0xFFFF is kept.

Every error message is asserted verbatim -- they are the agent-facing
contract a ``/sym`` caller sees.
"""

from __future__ import annotations

import pytest

from termapy.symbols import Address, Symbol, SymbolTable, parse_address, parse_number


def _table() -> SymbolTable:
    """A table shaped like the demo file plus the grammar's edge cases."""
    return SymbolTable([
        Symbol("gTemp", 0x1000, 2, "bss", file="sensor.c", type="u16"),
        Symbol("gFlags", 0x1008, 4, "data", file="main.c", type="u32"),
        Symbol("count.12", 0x100C, 4, "bss", file="main.c", type="u32"),
        Symbol("tick", 0x1010, 4, "bss", file="mon.c", type="u32"),
        Symbol("tick", 0x1014, 4, "bss", file="adc.c", type="u32"),
        Symbol("sState", 0x1030, 1, "bss", file="my-module.c"),
        Symbol("__func__.3", 0x1040, 8, "rodata", file="main.c"),
        Symbol("cafeh", 0x1050, 4, "bss", file="main.c"),
        Symbol("dup", 0x1060, 4, "bss"),
        Symbol("dup", 0x1064, 4, "bss"),
        Symbol("main", 0x2000, 442, "text", file="main.c"),
        Symbol("U1MODE", 0xBF806000, 4, "sfr", type="ON:B1-4.15 UEN:B1-4.8-9 BRGH:B1-4.3"),
    ])


@pytest.fixture
def table() -> SymbolTable:
    return _table()


# ── parse_number ────────────────────────────────────────────────────────────


class TestParseNumber:

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("0x1F", 31),
            ("0X1f", 31),
            ("1000h", 0x1000),
            ("FFFFh", 0xFFFF),
            ("0FFFFh", 0xFFFF),
            ("1000", 1000),
            ("12345", 12345),
            ("20000000", 20000000),
            ("100", 100),
            ("CAFE", None),
            ("0x", None),
            ("", None),
            ("-4", None),
            ("12g4", None),
            ("hello", None),
            (" 0x10 ", 16),
        ],
        ids=[
            "0x-upper", "0X-lower", "h-suffix", "FFFFh", "0FFFFh", "decimal-1000",
            "decimal-12345", "decimal-8-digits", "decimal-100", "bare-hex-is-not-a-number",
            "0x-alone", "empty", "negative", "garbage-digits", "word", "whitespace",
        ],
    )
    def test_parse_number(self, text, expected):
        # Act
        actual = parse_number(text)

        # Assert
        assert actual == expected


# ── parse_address: literals ─────────────────────────────────────────────────


class TestParseAddressLiterals:

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("0xFFFF", 0xFFFF), ("0X10FFA", 0x10FFA), ("FFFFh", 0xFFFF), ("4096", 4096), ("1000", 1000)],
        ids=["0x", "0X", "h", "decimal-4096", "decimal-1000"],
    )
    def test_literal_without_table(self, text, expected):
        # Act
        actual = parse_address(text)

        # Assert
        assert actual == Address(text, expected), "literals parse with no table at all"

    def test_literal_with_offset(self, table):
        # Act
        actual = parse_address("0x1000+4", table)

        # Assert
        assert actual == Address("0x1000+4", 0x1004, None, 4), "offset applies to a literal base"

    def test_literal_with_negative_offset(self, table):
        # Act
        actual = parse_address("0x1000-4", table)

        # Assert
        assert actual == Address("0x1000-4", 0xFFC, None, -4), "a negative offset above zero is fine"

    def test_whitespace_stripped(self):
        # Act
        actual = parse_address("  0xFF  ")

        # Assert
        assert actual.addr == 0xFF and actual.text == "0xFF", "surrounding whitespace is stripped"

    def test_literal_with_suffix_and_no_table(self):
        # Act
        actual = parse_address("0xBF806000.15")

        # Assert
        assert actual == Address("0xBF806000.15", 0xBF806000, None, 0, "15"), (
            "the dot fallback works for a literal head even with table None"
        )


# ── parse_address: symbols ──────────────────────────────────────────────────


class TestParseAddressSymbols:

    def test_name(self, table):
        # Act
        actual = parse_address("main", table)

        # Assert
        assert actual.addr == 0x2000, "name resolves to its address"
        assert actual.symbol is not None and actual.symbol.name == "main", "symbol is what was named"
        assert (actual.offset, actual.suffix) == (0, ""), "no offset, no suffix"

    @pytest.mark.parametrize("text", ["main+0x10", "main+16"], ids=["hex-offset", "decimal-offset"])
    def test_name_plus_offset(self, table, text):
        # Act
        actual = parse_address(text, table)

        # Assert
        assert (actual.addr, actual.offset) == (0x2010, 16), "offset is applied and recorded"
        assert actual.symbol is not None and actual.symbol.name == "main", "symbol is the base"

    def test_name_minus_offset(self, table):
        # Act
        actual = parse_address("main-4", table)

        # Assert
        assert (actual.addr, actual.offset) == (0x1FFC, -4), "negative offset"

    def test_bad_plus_offset(self, table):
        # Act / Assert
        with pytest.raises(ValueError, match=r"^Invalid offset: zz$"):
            parse_address("main+zz", table)

    def test_minus_non_number_is_a_name(self, table):
        # Act / Assert -- a '-' splits only when a number follows
        with pytest.raises(ValueError, match=r"^Unknown symbol: main-zz$"):
            parse_address("main-zz", table)

    def test_dash_in_file_is_not_an_offset(self, table):
        # Act
        actual = parse_address("sState@my-module.c", table)

        # Assert
        assert (actual.addr, actual.offset) == (0x1030, 0), "my-module.c is a file, not an offset"

    def test_ambiguous_lists_addresses_and_files(self, table):
        # Act / Assert
        expected = (
            "Ambiguous symbol: tick (0x00001010 mon.c, 0x00001014 adc.c; "
            "use tick@mon.c or the address)"
        )
        with pytest.raises(ValueError) as excinfo:
            parse_address("tick", table)
        assert str(excinfo.value) == expected

    def test_ambiguous_without_files(self, table):
        # Act / Assert -- a converter that left file empty still gets a usable message
        expected = "Ambiguous symbol: dup (0x00001060 ?, 0x00001064 ?; use the address)"
        with pytest.raises(ValueError) as excinfo:
            parse_address("dup", table)
        assert str(excinfo.value) == expected

    @pytest.mark.parametrize("text", ["tick@adc.c", "tick@adc"], ids=["basename", "stem"])
    def test_file_qualifier(self, table, text):
        # Act
        actual = parse_address(text, table)

        # Assert
        assert actual.addr == 0x1014, f"{text!r} reaches the adc.c static"

    def test_file_qualifier_by_path(self):
        # Arrange
        table = SymbolTable([
            Symbol("tick", 0x1010, 4, "bss", file="src/mon.c"),
            Symbol("tick", 0x1014, 4, "bss", file="src/adc.c"),
        ])

        # Act
        actual = parse_address("tick@src/adc.c", table)

        # Assert
        assert actual.addr == 0x1014, "an exact file path qualifies too"

    def test_file_qualifier_with_offset(self, table):
        # Act
        actual = parse_address("tick@adc.c+4", table)

        # Assert
        assert (actual.addr, actual.offset) == (0x1018, 4), "qualifier and offset compose"

    def test_unknown_file(self, table):
        # Act / Assert
        with pytest.raises(ValueError, match=r"^Unknown symbol: tick@usb.c$"):
            parse_address("tick@usb.c", table)

    def test_unknown_symbol(self, table):
        # Act / Assert
        with pytest.raises(ValueError, match=r"^Unknown symbol: nosuch$"):
            parse_address("nosuch", table)

    def test_bare_hex_is_a_name(self, table):
        # Act / Assert -- the deliberate inversion of the old hex guess
        with pytest.raises(ValueError, match=r"^Unknown symbol: CAFE$"):
            parse_address("CAFE", table)

    def test_name_without_table(self):
        # Act / Assert
        with pytest.raises(ValueError, match=r"^No symbols loaded\.$"):
            parse_address("CAFE")

    def test_name_with_empty_table(self):
        # Act / Assert
        with pytest.raises(ValueError, match=r"^No symbols loaded\.$"):
            parse_address("main", SymbolTable([]))

    def test_exact_name_beats_number_rule(self, table):
        # Act -- cafeh parses as 0xCAFE, but the table has a symbol by that name
        actual = parse_address("cafeh", table)

        # Assert
        assert actual.addr == 0x1050, "an exact table match wins over the ..h spelling"
        assert actual.symbol is not None and actual.symbol.name == "cafeh"

    def test_exact_name_beats_number_rule_with_offset(self, table):
        # Act -- the base of name+off gets the same exact-match-first treatment
        actual = parse_address("cafeh+4", table)

        # Assert
        assert (actual.addr, actual.offset) == (0x1054, 4), "cafeh+4 is the symbol plus 4, not 0xCAFE+4"
        assert actual.symbol is not None and actual.symbol.name == "cafeh", "symbol is the base"

    def test_h_spelling_without_a_matching_symbol(self, table):
        # Act
        actual = parse_address("beefh", table)

        # Assert
        assert actual == Address("beefh", 0xBEEF), "no symbol named beefh, so ..h is hex"


# ── parse_address: dots and the reserved suffix ─────────────────────────────


class TestParseAddressSuffix:

    def test_dotted_symbol_exact_wins(self, table):
        # Act
        actual = parse_address("count.12", table)

        # Assert
        assert (actual.addr, actual.suffix) == (0x100C, ""), "count.12 is the symbol, not bit 12"

    def test_dotted_symbol_with_offset(self, table):
        # Act
        actual = parse_address("count.12+4", table)

        # Assert
        assert (actual.addr, actual.offset, actual.suffix) == (0x1010, 4, ""), (
            "the offset split runs before the dot fallback"
        )

    def test_bit_suffix(self, table):
        # Act
        actual = parse_address("gFlags.4", table)

        # Assert
        assert (actual.addr, actual.suffix) == (0x1008, "4"), "unconsumed suffix comes back"
        assert actual.text == "gFlags.4", "text is what was typed"

    def test_slice_suffix(self, table):
        # Act
        actual = parse_address("gFlags.4-6", table)

        # Assert
        assert (actual.addr, actual.offset, actual.suffix) == (0x1008, 0, "4-6"), (
            "the dash inside the suffix is never seen by the offset parser"
        )

    def test_field_suffix(self, table):
        # Act
        actual = parse_address("U1MODE.ON", table)

        # Assert
        assert (actual.addr, actual.suffix) == (0xBF806000, "ON"), "named field suffix"

    def test_literal_suffix_with_table(self, table):
        # Act
        actual = parse_address("0xBF806000.15", table)

        # Assert
        assert (actual.addr, actual.symbol, actual.suffix) == (0xBF806000, None, "15")

    def test_offset_then_suffix(self, table):
        # Act
        actual = parse_address("main+0x10.3", table)

        # Assert
        assert (actual.addr, actual.offset, actual.suffix) == (0x2010, 16, "3")

    def test_dotted_exact_plus_suffix(self, table):
        # Act
        actual = parse_address("__func__.3.7", table)

        # Assert
        assert (actual.addr, actual.suffix) == (0x1040, "7"), "head __func__.3 is exact"

    def test_only_one_dot_is_split(self, table):
        # Act / Assert -- neither a.b.c nor a.b is a symbol: the first error stands
        with pytest.raises(ValueError, match=r"^Unknown symbol: a\.b\.c$"):
            parse_address("a.b.c", table)

    def test_suffix_error_reports_what_was_typed(self, table):
        # Act / Assert -- head nosuch is unknown too, so the whole token is reported
        with pytest.raises(ValueError, match=r"^Unknown symbol: nosuch\.4$"):
            parse_address("nosuch.4", table)


# ── parse_address: invalid ──────────────────────────────────────────────────


class TestParseAddressInvalid:

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", "Invalid address: ''"),
            ("   ", "Invalid address: ''"),
            (".4", "Invalid address: .4"),
            ("main.", "Invalid address: main."),
            ("+4", "Invalid address: +4"),
            ("main+", "Invalid address: main+"),
            ("12g4", "Invalid address: 12g4"),
            ("main-0x3000", "Invalid address: main-0x3000"),
            ("0x0-4", "Invalid address: 0x0-4"),
            ("main@", "Invalid address: main@"),
            ("tick@", "Invalid address: tick@"),
        ],
        ids=[
            "empty", "blank", "leading-dot", "trailing-dot", "bare-plus", "dangling-plus", "digits",
            "symbol-underflow", "literal-underflow", "dangling-at", "dangling-at-ambiguous-name",
        ],
    )
    def test_invalid(self, table, text, expected):
        # Act / Assert
        with pytest.raises(ValueError) as excinfo:
            parse_address(text, table)
        assert str(excinfo.value) == expected
