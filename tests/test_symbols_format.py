"""Tests for ``termapy.symbols.format`` -- prose renderers and their ``data=`` twins.

Re-homes ``TestFormatSymbol`` from the retired ``tests/test_pic_map.py`` with
the new spelling (``main+0x10``, no space; file inside the brackets).
"""

from __future__ import annotations

from termapy.symbols import (
    Address,
    Symbol,
    SymbolTable,
    format_symbol,
    info_rows,
    lookup_record,
    symbol_record,
    symbolic_name,
    table_record,
)
from termapy.symbols.format import hex_addr

_MAIN = Symbol("main", 0x10FFA, 0x1BA, "text", file="main.c")


class TestHexAddr:

    def test_default_width(self):
        """With no width given, addresses render as 32-bit, eight hex digits."""
        assert hex_addr(0x2010) == "0x00002010", "32-bit -> 8 digits"

    def test_sixteen_bit(self):
        """A 16-bit address width renders four hex digits."""
        assert hex_addr(0x2010, 16) == "0x2010", "16-bit -> 4 digits"

    def test_odd_bits_round_up(self):
        """A width that is not a multiple of 4 rounds up to whole hex digits."""
        assert hex_addr(0x1, 20) == "0x00001", "20 bits -> 5 digits"


class TestSymbolicName:

    def test_at_start(self):
        """An address at the symbol's start is the bare name, with no +0x0."""
        assert symbolic_name(_MAIN, 0x10FFA) == "main", "no offset at the start"

    def test_above(self):
        """An address inside the symbol reads name+0xNN, with no space."""
        assert symbolic_name(_MAIN, 0x1100A) == "main+0x10", "hex offset, no space"

    def test_below(self):
        """An address below the start reads name-0xN, a negative offset."""
        assert symbolic_name(_MAIN, 0x10FF6) == "main-0x4", "explicit name-off reaches below"


class TestFormatSymbol:

    def test_basic_format(self):
        """The line is address, name, then [label size file] in brackets."""
        # Act
        actual = format_symbol(_MAIN)

        # Assert
        assert actual == "0x00010FFA  main  [code 442 bytes main.c]"

    def test_with_offset(self):
        """Given a query address, the line shows that address and the name+offset."""
        # Act
        actual = format_symbol(_MAIN, 0x1100A)

        # Assert
        assert actual == "0x0001100A  main+0x10  [code 442 bytes main.c]", (
            "the queried address is shown with the symbolic offset"
        )

    def test_no_offset_at_start(self):
        """A query at the exact start adds no +0x offset to the name."""
        # Act
        actual = format_symbol(_MAIN, 0x10FFA)

        # Assert
        assert "+0x" not in actual, "no offset when at the exact start"

    def test_global_format_no_size_no_file(self):
        """A size-0, fileless global shows only its section label in brackets."""
        # Arrange
        symbol = Symbol("sCal", 0x20005A58, 0, "global")

        # Act
        actual = format_symbol(symbol)

        # Assert
        assert actual == "0x20005A58  sCal  [global]", "size 0 and empty file are omitted"

    def test_sixteen_bit_width(self):
        """address_bits sets the printed address width."""
        # Arrange
        symbol = Symbol("x", 0x1F, 2, "bss")

        # Act
        actual = format_symbol(symbol, address_bits=16)

        # Assert
        assert actual.startswith("0x001F  "), "address width follows address_bits"


class TestSymbolRecord:

    def test_fixed_keys(self):
        """The data record always carries every key, with int and hex address both."""
        # Act
        actual = symbol_record(_MAIN)

        # Assert
        expected = {
            "name": "main", "addr": 0x10FFA, "addr_hex": "0x00010FFA", "end": 0x10FFA + 0x1BA,
            "size": 0x1BA, "section": "text", "file": "main.c", "type": "", "space": "",
            "rmw": True, "access": "rw", "read_effect": False,
        }
        assert actual == expected, "int and hex addr side by side; every key present"


class TestLookupRecord:

    def test_hit(self):
        """A hit record carries the query, address, symbolic form and nested symbol."""
        # Arrange
        parsed = Address("0x1100A", 0x1100A)

        # Act
        actual = lookup_record(parsed, _MAIN)

        # Assert
        assert actual["query"] == "0x1100A", "what was typed"
        assert (actual["addr"], actual["addr_hex"]) == (0x1100A, "0x0001100A")
        assert actual["symbolic"] == "main+0x10", "symbolic form of the hit"
        assert actual["symbol"] == symbol_record(_MAIN), "the symbol record rides inside"
        assert (actual["offset"], actual["suffix"]) == (0, "")

    def test_miss_has_same_keys(self):
        """A miss has the same keys as a hit, with a null symbol, so agents need no branch."""
        # Arrange
        parsed = Address("1000", 1000)

        # Act
        hit = lookup_record(Address("main", 0x10FFA, _MAIN), _MAIN)
        miss = lookup_record(parsed, None)

        # Assert
        assert set(miss) == set(hit), "hit and miss share one key set"
        assert miss["symbol"] is None and miss["symbolic"] == "", "null symbol, empty symbolic"


class TestTableRecord:

    def test_agrees_with_info_rows(self):
        """The data record and the prose rows report the same count, span and sources."""
        # Arrange
        table = SymbolTable(
            [_MAIN, Symbol("sCal", 0x20005A58, 0, "global"), Symbol("buf", 0x20000000, 16, "bss")],
            source="build/mem.map", imported="2026-08-29T10:12:00",
            regions=[{"name": "ram"}],
        )

        # Act
        record = table_record(table, file="rig.symbols.json")
        rows = dict(info_rows(table, file="rig.symbols.json"))

        # Assert
        assert record["count"] == 3 and rows["symbols"].startswith("3  ("), "same count"
        assert record["sections"] == table.stats(), "record carries the raw section counts"
        assert rows["symbols"] == "3  (bss 1, code 1, global 1)", "rows use display labels, sorted"
        assert record["range"] == {
            "start": 0x10FFA, "end": 0x20005A58, "start_hex": "0x00010FFA", "end_hex": "0x20005A58",
        }, "range is the table span"
        assert rows["range"] == "0x00010FFA - 0x20005A58", "same span as prose"
        assert (record["path"], rows["file"]) == ("rig.symbols.json", "rig.symbols.json")
        assert (record["source"], rows["source"]) == ("build/mem.map", "build/mem.map")
        assert (record["regions"], rows["regions"]) == (1, "1"), "regions reported as a count"
        assert (record["address_bits"], record["endian"]) == (32, "le")

    def test_empty_table(self):
        """An empty table gives a null range in data and (empty)/(none) in prose."""
        # Arrange
        table = SymbolTable([])

        # Act
        record = table_record(table)
        rows = dict(info_rows(table))

        # Assert
        assert record["range"] is None, "no span, null range"
        assert rows["range"] == "(empty)", "prose says empty"
        assert rows["symbols"] == "0", "no section breakdown for an empty table"
        assert (rows["file"], rows["source"], rows["imported"]) == ("(in-memory)", "(none)", "(none)")
