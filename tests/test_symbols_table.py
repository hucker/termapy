"""Tests for ``termapy.symbols.table`` -- the model and the file format.

Re-homes the ``Symbol``, lookup and search cases that ``tests/test_pic_map.py``
pinned against the example plugin, now built from a hand-written symbol
list so nothing here depends on a converter.
"""

from __future__ import annotations

import json

import pytest

from termapy import folders
from termapy.symbols import SYMBOLS_VERSION, Symbol, SymbolTable, sidecar_path


def _demo_symbols() -> list[Symbol]:
    """The shape of the bundled demo table, hand-written."""
    return [
        Symbol("gTemp", 0x1000, 2, "bss", file="sensor.c", type="u16"),
        Symbol("gPressure", 0x1002, 4, "bss", file="sensor.c", type="f32"),
        Symbol("gFlags", 0x1008, 4, "data", file="main.c", type="u32"),
        Symbol("count.12", 0x100C, 4, "bss", file="main.c", type="u32"),
        Symbol("tick", 0x1010, 4, "bss", file="src/mon.c", type="u32"),
        Symbol("tick", 0x1014, 4, "bss", file="src/adc.c", type="u32"),
        Symbol("sCal", 0x1020, 0, "global"),
        Symbol("main", 0x2000, 442, "text", file="main.c"),
        Symbol("MonGpio", 0x2200, 776, "text", file="mon.c"),
        Symbol("sCmdTable", 0x3000, 64, "rodata", file="main.c"),
        Symbol("U1MODE", 0xBF806000, 4, "sfr", type="ON:B1-4.15 UEN:B1-4.8-9 BRGH:B1-4.3"),
        Symbol("U1STA", 0xBF806010, 4, "sfr", type="u32", rmw=False),
    ]


@pytest.fixture
def table() -> SymbolTable:
    return SymbolTable(_demo_symbols(), source="hand", imported="2026-08-29T00:00:00")


# ── Symbol ──────────────────────────────────────────────────────────────────


class TestSymbol:

    def test_contains_start(self):
        """The first byte of a symbol's range is inside it."""
        # Arrange
        symbol = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert symbol.contains(0x10FFA) is True, "start address should be contained"

    def test_contains_middle(self):
        """An address partway through the range is inside the symbol."""
        # Arrange
        symbol = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert symbol.contains(0x11000) is True, "middle address should be contained"

    def test_contains_last_byte(self):
        """The last byte (addr + size - 1) is still inside the range."""
        # Arrange
        symbol = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert symbol.contains(0x10FFA + 0x1BA - 1) is True, "last byte should be contained"

    def test_excludes_end(self):
        """The range is half-open: addr + size is outside it."""
        # Arrange
        symbol = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert symbol.contains(0x10FFA + 0x1BA) is False, "end address should not be contained"

    def test_excludes_before(self):
        """The byte just before the start is outside the range."""
        # Arrange
        symbol = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert symbol.contains(0x10FF9) is False, "address before start should not be contained"

    def test_size_zero_contains_nothing(self):
        """A size-0 symbol has an empty range, not even its own address."""
        # Arrange
        symbol = Symbol("sCal", 0x1020, 0, "global")

        # Act / Assert
        assert symbol.contains(0x1020) is False, "a size-0 symbol has an empty range"

    def test_end(self):
        """end is the exclusive bound addr + size."""
        # Arrange
        symbol = Symbol("f", 0x100, 0x20, "text")

        # Assert
        assert symbol.end == 0x120, "end should be addr + size"

    def test_section_label(self):
        """Raw sections map to display labels; unknown sections pass through unchanged."""
        assert Symbol("f", 0, 1, "text").section_label == "code", "text -> code"
        assert Symbol("f", 0, 1, "bss").section_label == "bss", "bss -> bss"
        assert Symbol("f", 0, 1, "rodata").section_label == "const", "rodata -> const"
        assert Symbol("f", 0, 1, "data").section_label == "data", "data -> data"
        assert Symbol("f", 0, 1, "sfr").section_label == "sfr", "sfr -> sfr"
        assert Symbol("f", 0, 1, "weird").section_label == "weird", "unknown passes through"

    def test_positional_constructor_still_works(self):
        """The old four-positional constructor still works, with optionals defaulted."""
        # Arrange / Act -- the historical 4-positional shape
        symbol = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Assert
        assert (symbol.name, symbol.addr, symbol.size, symbol.section) == ("main", 0x10FFA, 0x1BA, "text")
        assert (symbol.file, symbol.type, symbol.space, symbol.rmw) == ("", "", "", True), (
            "optional fields default to empty / rmw True"
        )

    def test_to_dict_omits_empty_optionals(self):
        """Empty file/type/space and the default rmw=True are left out of the dict."""
        # Arrange
        symbol = Symbol("sCal", 0x1020, 0, "global")

        # Act
        actual = symbol.to_dict()

        # Assert
        expected = {"name": "sCal", "addr": "0x00001020", "size": 0, "section": "global"}
        assert actual == expected, "empty file/type/space and rmw=True are not written"

    def test_to_dict_writes_rmw_only_when_false(self):
        """rmw is written only when False, and a set type is written."""
        # Arrange
        symbol = Symbol("U1STA", 0xBF806010, 4, "sfr", type="u32", rmw=False)

        # Act
        actual = symbol.to_dict()

        # Assert
        assert actual["rmw"] is False, "rmw False is written"
        assert actual["type"] == "u32", "type is written when set"

    def test_to_dict_pads_addr_to_address_bits(self):
        """The written hex address is zero-padded to the table's address_bits."""
        # Arrange
        symbol = Symbol("x", 0x1F, 1, "bss")

        # Act / Assert
        assert symbol.to_dict(address_bits=16)["addr"] == "0x001F", "16-bit -> 4 hex digits"
        assert symbol.to_dict(address_bits=32)["addr"] == "0x0000001F", "32-bit -> 8 hex digits"

    def test_from_dict_accepts_int_addr(self):
        """A plain JSON integer address is accepted as is."""
        # Act
        symbol = Symbol.from_dict({"name": "a", "addr": 4096}, "symbols[0]")

        # Assert
        assert symbol.addr == 0x1000, "int addr accepted verbatim"

    def test_from_dict_accepts_hex_string_addr(self):
        """A 0x-prefixed hex string address is accepted."""
        # Act
        symbol = Symbol.from_dict({"name": "a", "addr": "0x1000"}, "symbols[0]")

        # Assert
        assert symbol.addr == 0x1000, "0x-prefixed hex string accepted"

    def test_from_dict_accepts_scalar_type(self):
        """A scalar type name such as u16 is kept as given."""
        # Act
        symbol = Symbol.from_dict({"name": "a", "addr": 0, "type": "u16"}, "symbols[0]")

        # Assert
        assert symbol.type == "u16", "scalar type kept"

    def test_from_dict_accepts_format_spec_type(self):
        """A bit-field format spec is accepted as the type and kept verbatim."""
        # Act
        symbol = Symbol.from_dict(
            {"name": "U1MODE", "addr": 0, "type": "ON:B1-4.15 UEN:B1-4.8-9"}, "symbols[0]",
        )

        # Assert
        assert symbol.type == "ON:B1-4.15 UEN:B1-4.8-9", "format-spec type kept"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"name": "", "addr": 0}, "symbols[3].name: expected a non-empty string"),
            ({"addr": 0}, "symbols[3].name: expected a non-empty string"),
            ({"name": "a", "addr": "zz"}, "symbols[3].addr: expected 0x-hex string or int, got 'zz'"),
            ({"name": "a", "addr": "4096"}, "symbols[3].addr: expected 0x-hex string or int, got '4096'"),
            ({"name": "a", "addr": -1}, "symbols[3].addr: expected 0x-hex string or int, got -1"),
            ({"name": "a"}, "symbols[3].addr: expected 0x-hex string or int, got None"),
            ({"name": "a", "addr": True}, "symbols[3].addr: expected 0x-hex string or int, got True"),
            ({"name": "a", "addr": 0, "size": -1}, "symbols[3].size: expected a non-negative integer"),
            ({"name": "a", "addr": 0, "size": True}, "symbols[3].size: expected a non-negative integer"),
            ({"name": "a", "addr": 0, "type": "Q9"}, "symbols[3].type: invalid type spec 'Q9'"),
            ({"name": "a", "addr": 0, "rmw": "yes"}, "symbols[3].rmw: expected a boolean"),
            ({"name": "a", "addr": 0, "file": 3}, "symbols[3].file: expected a string"),
            ("not an object", "symbols[3]: expected an object"),
        ],
        ids=[
            "name-empty", "name-missing", "addr-zz", "addr-decimal-string", "addr-negative",
            "addr-missing", "addr-bool", "size-negative", "size-bool", "type-bad", "rmw-string",
            "file-int", "not-object",
        ],
    )
    def test_from_dict_errors_are_field_qualified(self, raw, expected):
        """Every bad field reports its full path (symbols[3].addr) and the bad value."""
        # Act / Assert
        with pytest.raises(ValueError) as excinfo:
            Symbol.from_dict(raw, "symbols[3]")
        assert str(excinfo.value) == expected

    def test_from_dict_ignores_unknown_keys(self):
        """Unknown per-symbol keys are ignored so newer files still load."""
        # Act
        symbol = Symbol.from_dict({"name": "a", "addr": 0, "comment": "hi"}, "symbols[0]")

        # Assert
        assert symbol.name == "a", "unknown per-symbol keys are ignored (forward compat)"


# ── SymbolTable.lookup ──────────────────────────────────────────────────────


class TestSymbolTableLookup:

    def test_exact_match(self, table):
        """A lookup at a symbol's start address returns that symbol."""
        # Act
        symbol = table.lookup(0x2000)

        # Assert
        assert symbol is not None and symbol.name == "main", "exact start address finds main"

    def test_offset_within(self, table):
        """A lookup inside a symbol's range returns the containing symbol."""
        # Act
        symbol = table.lookup(0x2010)

        # Assert
        assert symbol is not None and symbol.name == "main", "address inside main finds main"

    def test_miss_before_first(self, table):
        """An address below the lowest symbol is a miss, not the first symbol."""
        # Act / Assert
        assert table.lookup(0x0001) is None, "address before the first symbol is a miss"

    def test_miss_between_symbols(self):
        """An address in a gap is a miss, not the preceding symbol."""
        # Arrange -- a gap between two sized symbols
        table = SymbolTable([Symbol("a", 0x100, 0x10, "text"), Symbol("b", 0x200, 0x10, "text")])

        # Act / Assert
        assert table.lookup(0x150) is None, "address in the gap is a miss"

    def test_bss_lookup(self, table):
        """A byte inside a bss variable resolves to it, with its section."""
        # Act
        symbol = table.lookup(0x1001)

        # Assert
        assert symbol is not None and symbol.name == "gTemp", "second byte of gTemp"
        assert symbol.section == "bss", "bss section"

    def test_data_lookup(self, table):
        """A data-section symbol resolves at its start address."""
        # Act
        symbol = table.lookup(0x1008)

        # Assert
        assert symbol is not None and symbol.name == "gFlags", "data symbol at its start"

    def test_global_lookup_exact_only(self, table):
        """A size-0 global matches its own address only, never the byte after."""
        # Act
        at_start = table.lookup(0x1020)
        one_past = table.lookup(0x1021)

        # Assert
        assert at_start is not None and at_start.name == "sCal", "size-0 global hit at its address"
        assert one_past is None, "size-0 global claims nothing past its address"

    def test_size0_global_inside_sized_symbol_does_not_hide_it(self):
        """A size-0 marker inside a function does not hide the function's other bytes."""
        # Arrange -- a linker global sits inside main's range
        table = SymbolTable([
            Symbol("main", 0x2000, 442, "text"),
            Symbol("_marker", 0x2010, 0, "global"),
        ])

        # Act
        above = table.lookup(0x2020)
        at_global = table.lookup(0x2010)

        # Assert
        assert above is not None and above.name == "main", (
            "the backward walk steps over the size-0 global to the containing symbol"
        )
        assert at_global is not None and at_global.name == "_marker", (
            "the global still wins at its own address"
        )

    def test_sized_symbol_sorts_after_size0_at_same_address(self):
        """At a shared address size-0 sorts first, so lookup lands on the sized one."""
        # Arrange
        table = SymbolTable([
            Symbol("main", 0x2000, 442, "text"),
            Symbol("_start", 0x2000, 0, "global"),
        ])

        # Act
        names = [symbol.name for symbol in table.symbols]
        at = table.lookup(0x2000)

        # Assert
        assert names == ["_start", "main"], "size-0 sorts first at a shared address"
        assert at is not None and at.name == "main", "bisect lands on the sized symbol"


# ── SymbolTable.by_name ─────────────────────────────────────────────────────


class TestSymbolTableByName:

    def test_single(self, table):
        """An exact name with one definition returns that one symbol."""
        # Act
        hits = table.by_name("main")

        # Assert
        assert [symbol.addr for symbol in hits] == [0x2000], "one exact hit"

    def test_case_sensitive(self, table):
        """Name lookup is case-sensitive, unlike search."""
        # Act / Assert
        assert table.by_name("MAIN") == [], "names are case-sensitive"

    def test_duplicates(self, table):
        """A name defined twice returns both symbols, in address order."""
        # Act
        hits = table.by_name("tick")

        # Assert
        assert [symbol.addr for symbol in hits] == [0x1010, 0x1014], "both statics, address order"

    @pytest.mark.parametrize("file", ["src/adc.c", "adc.c", "adc"], ids=["exact", "basename", "stem"])
    def test_file_qualifier_forms(self, table, file):
        """A file qualifier may be the full path, basename or stem."""
        # Act
        hits = table.by_name("tick", file)

        # Assert
        assert [symbol.addr for symbol in hits] == [0x1014], f"{file!r} reaches src/adc.c"

    def test_unknown_file(self, table):
        """A file qualifier matching no definition returns nothing."""
        # Act / Assert
        assert table.by_name("tick", "usb.c") == [], "no static in that file"

    def test_file_qualifier_needs_a_file(self, table):
        """A symbol with no file never matches when a file qualifier is given."""
        # Act / Assert -- sCal has no file, so no qualifier can reach it
        assert table.by_name("sCal", "main.c") == [], "fileless symbols never match a qualifier"


# ── SymbolTable.search ──────────────────────────────────────────────────────


class TestSymbolTableSearch:

    def test_substring_match(self, table):
        """A plain pattern matches as a substring of the name."""
        # Act
        matches = table.search("Mon")

        # Assert
        assert any(symbol.name == "MonGpio" for symbol in matches), "substring finds MonGpio"

    def test_case_insensitive(self, table):
        """Search ignores case, unlike by_name."""
        # Act
        matches = table.search("mongpio")

        # Assert
        assert [symbol.name for symbol in matches] == ["MonGpio"], "case-insensitive substring"

    def test_glob_wildcard(self, table):
        """A pattern with * is treated as a glob."""
        # Act
        matches = table.search("*main*")

        # Assert
        assert any(symbol.name == "main" for symbol in matches), "glob *main* finds main"

    def test_glob_prefix(self, table):
        """A glob is anchored: Mon* does not match names that merely contain mon."""
        # Act
        matches = table.search("Mon*")

        # Assert
        assert any(symbol.name == "MonGpio" for symbol in matches), "glob Mon* finds MonGpio"
        assert not any(symbol.name == "main" for symbol in matches), "glob Mon* is anchored"

    def test_regex_anchor(self, table):
        """A ^-led pattern is treated as a regular expression."""
        # Act
        matches = table.search("^Mon")

        # Assert
        assert [symbol.name for symbol in matches] == ["MonGpio"], "regex ^Mon finds MonGpio only"

    def test_exact_first(self):
        """An exact name hit returns only that symbol, not its substring matches."""
        # Arrange -- "a" is both an exact name and a substring of "ab"
        table = SymbolTable([Symbol("ab", 0x10, 1, "bss"), Symbol("a", 0x20, 1, "bss")])

        # Act
        matches = table.search("a")

        # Assert
        assert [symbol.name for symbol in matches] == ["a"], "an exact hit returns only itself"

    def test_no_match(self, table):
        """No match returns an empty list rather than None."""
        # Act / Assert
        assert table.search("__does_not_exist__") == [], "empty list for no match"

    def test_results_in_address_order(self, table):
        """Search results come back in address order, not match order."""
        # Act
        matches = table.search("g*")

        # Assert
        addrs = [symbol.addr for symbol in matches]
        assert addrs == sorted(addrs), "search results follow table (address) order"


# ── stats / span ────────────────────────────────────────────────────────────


class TestSymbolTableStats:

    def test_stats(self, table):
        """stats counts symbols per raw section name."""
        # Act
        actual = table.stats()

        # Assert
        expected = {"bss": 5, "data": 1, "global": 1, "text": 2, "rodata": 1, "sfr": 2}
        assert actual == expected

    def test_span(self, table):
        """span runs from the lowest start to the highest end."""
        # Act / Assert
        assert table.span() == (0x1000, 0xBF806014), "lowest start to highest end"

    def test_span_empty(self):
        """An empty table has no span (None) and length 0."""
        # Act / Assert
        assert SymbolTable([]).span() is None, "empty table has no range"
        assert len(SymbolTable([])) == 0, "empty table has length 0"


# ── JSON round trip and validation ──────────────────────────────────────────


def _doc(**overrides) -> dict:
    """A minimal valid document, with overrides applied."""
    doc = {
        "symbols_version": 1,
        "symbols": [{"name": "a", "addr": "0x00000010", "size": 4, "section": "bss"}],
    }
    doc.update(overrides)
    return doc


class TestSymbolTableJson:

    def test_round_trip_preserves_every_field(self):
        """to_dict then from_dict preserves symbols, metadata, endian and regions."""
        # Arrange
        regions = [{"name": "ram", "start": "0x20000000", "size": "0x10000", "kind": "ram"}]
        original = SymbolTable(
            _demo_symbols(), source="build/mem.map", imported="2026-08-29T10:12:00",
            address_bits=32, endian="be", regions=regions,
        )

        # Act
        rebuilt = SymbolTable.from_dict(json.loads(json.dumps(original.to_dict())))

        # Assert
        assert rebuilt.symbols == original.symbols, "every symbol field survives"
        assert (rebuilt.source, rebuilt.imported) == ("build/mem.map", "2026-08-29T10:12:00")
        assert (rebuilt.address_bits, rebuilt.endian) == (32, "be")
        assert rebuilt.regions == regions, "regions round-trip opaquely"

    def test_to_dict_key_order(self, table):
        """Top-level keys are written in a fixed order, with the current version."""
        # Act
        keys = list(table.to_dict())

        # Assert
        expected = [
            "symbols_version", "source", "imported", "address_bits", "endian", "regions", "symbols",
        ]
        assert keys == expected, "stable key order keeps the file diff-friendly"
        assert table.to_dict()["symbols_version"] == SYMBOLS_VERSION, "current version written"

    def test_save_load_round_trip(self, table, tmp_path):
        """save then load preserves symbols, records the path and ends with a newline."""
        # Arrange
        path = tmp_path / "rig.symbols.json"

        # Act
        table.save(path)
        loaded = SymbolTable.load(path)

        # Assert
        assert loaded.symbols == table.symbols, "symbols survive the file"
        assert loaded.path == path, "load records the path"
        assert path.read_text(encoding="utf-8").endswith("}\n"), "trailing newline written"

    @pytest.mark.parametrize(
        ("doc", "expected"),
        [
            (_doc(symbols_version=2), "symbols_version: expected 1, got 2"),
            (_doc(symbols_version=True), "symbols_version: expected 1, got True"),
            ({"symbols": []}, "symbols_version: expected 1, got missing"),
            ([], "expected a JSON object"),
            (_doc(symbols={}), "symbols: expected a list"),
            (_doc(symbols=[5]), "symbols[0]: expected an object"),
            (_doc(endian="middle"), "endian: expected le or be, got 'middle'"),
            (_doc(address_bits=0), "address_bits: expected a positive integer"),
            (_doc(address_bits="32"), "address_bits: expected a positive integer"),
            (_doc(address_bits=True), "address_bits: expected a positive integer"),
            (_doc(regions={}), "regions: expected a list of objects"),
            (_doc(regions=[1]), "regions: expected a list of objects"),
            (_doc(source=7), "source: expected a string"),
        ],
        ids=[
            "version-2", "version-bool", "version-missing", "not-object", "symbols-not-list",
            "symbol-not-object", "endian", "address_bits-0", "address_bits-str",
            "address_bits-bool", "regions-not-list",
            "region-not-object", "source-int",
        ],
    )
    def test_from_dict_errors(self, doc, expected):
        """Each malformed top-level field raises a ValueError naming the field."""
        # Act / Assert
        with pytest.raises(ValueError) as excinfo:
            SymbolTable.from_dict(doc)
        assert str(excinfo.value) == expected

    def test_unknown_top_level_key_ignored(self):
        """Unknown top-level keys load without error and are dropped on save."""
        # Act
        table = SymbolTable.from_dict(_doc(toolchain="xc32 v5.00"))

        # Assert
        assert len(table) == 1, "unknown top-level keys are ignored (forward compat)"
        assert "toolchain" not in table.to_dict(), "and dropped on save"

    def test_empty_symbols_list_is_valid(self):
        """A file with an empty symbols list loads as an empty table."""
        # Act
        table = SymbolTable.from_dict(_doc(symbols=[]))

        # Assert
        assert len(table) == 0, "an empty table is a valid file"

    def test_defaults_when_optionals_absent(self):
        """Missing optional fields default to empty text, 32 bits, le and no regions."""
        # Act
        table = SymbolTable.from_dict(_doc())

        # Assert
        assert (table.source, table.imported) == ("", ""), "free-text fields default empty"
        assert (table.address_bits, table.endian, table.regions) == (32, "le", [])

    def test_invalid_json_is_a_value_error(self, tmp_path):
        """A file that is not JSON raises ValueError, the same as a bad document."""
        # Arrange
        path = tmp_path / "bad.symbols.json"
        path.write_text("{not json", encoding="utf-8")

        # Act / Assert
        with pytest.raises(ValueError):
            SymbolTable.load(path)

    def test_missing_file_is_an_os_error(self, tmp_path):
        """A missing file raises OSError, kept apart from a malformed one."""
        # Act / Assert
        with pytest.raises(OSError):
            SymbolTable.load(tmp_path / "nope.symbols.json")


# ── sidecar_path ────────────────────────────────────────────────────────────


class TestSidecarPath:

    def test_beside_the_cfg(self, tmp_path):
        """The symbol sidecar lives at <cfg dir>/sym/<stem>.symbols.json."""
        # Arrange
        cfg = tmp_path / "rig" / "rig.cfg"

        # Act
        actual = sidecar_path(str(cfg))

        # Assert
        expected = tmp_path / "rig" / "sym" / f"rig{folders.SYMBOLS_SUFFIX}"
        assert actual == expected, "sidecar is <dir>/sym/<stem>.symbols.json"
        assert actual is not None and actual.name == "rig.symbols.json", "suffix spelled by folders"

    def test_no_config(self):
        """With no config path (zero-config CLI) there is no sidecar."""
        # Act / Assert
        assert sidecar_path("") is None, "zero-config CLI has no sidecar"
