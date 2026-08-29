"""Tests for the xc32 converter and the converter registry.

The parser half of the retired ``tests/test_pic_map.py``, re-pointed at
``xc32.convert(text)`` (wrapped in ``SymbolTable`` where sortedness, length
and stats matter).  ``SAMPLE_MAP`` became ``tests/fixtures/maps/xc32_sample.map``.

The named addition beyond the move -- ``Symbol.file`` from the input-section
object line, and the ``strN.N`` literal-pool skip -- is pinned here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from termapy.symbols import SymbolTable
from termapy.symbols.converters import CONVERTERS, FORMATS, find_converter, xc32

MAPS_DIR = Path(__file__).parent / "fixtures" / "maps"
FIXTURE = MAPS_DIR / "xc32_sample.map"


@pytest.fixture(scope="module")
def map_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def table(map_text) -> SymbolTable:
    return SymbolTable(xc32.convert(map_text))


def _names(table: SymbolTable, section: str | None = None) -> list[str]:
    return [symbol.name for symbol in table.symbols if section is None or symbol.section == section]


# ── The move: the three passes ──────────────────────────────────────────────


class TestXc32Parse:

    def test_parses_text_symbols(self, table):
        # Act
        names = _names(table, "text")

        # Assert
        assert "main" in names, "should parse .text.main"
        assert "MonGpio" in names, "should parse .text.MonGpio"

    def test_parses_bss_symbols(self, table):
        # Act
        names = _names(table, "bss")

        # Assert
        assert "SERCOM4_USART_WriteBuffer" in names, "should parse .bss symbol with full name"
        assert "sDispatch" in names, "should parse .bss.sDispatch"

    def test_parses_data_symbols(self, table):
        # Act / Assert
        assert "sCurrentBaud" in _names(table, "data"), "should parse .data.sCurrentBaud"

    def test_parses_rodata_symbols(self, table):
        # Act / Assert
        assert "sCmdTableA" in _names(table, "rodata"), "should parse .rodata.sCmdTableA"

    def test_skips_non_symbol_lines(self, table):
        # Act / Assert -- .vectors and bare .text carry no dotted symbol name
        assert "vectors" not in _names(table), "should skip .vectors"
        assert "" not in _names(table), "no nameless symbol"

    def test_sorted_by_address(self, table):
        # Act
        addrs = [symbol.addr for symbol in table.symbols]

        # Assert
        assert addrs == sorted(addrs), "SymbolTable keeps symbols in address order"

    def test_stats(self, table):
        # Act
        stats = table.stats()

        # Assert
        assert stats["text"] >= 3, "at least 3 text symbols"
        assert stats["bss"] >= 2, "at least 2 bss symbols"

    def test_detail_section_provides_full_names(self, table):
        # Act
        names = _names(table)

        # Assert -- the detailed section overrides truncated summary names
        assert "u8PicAdcInternal_ReadBlockingAvg" in names, "full name from the detail section"
        assert "u8PicAdcInternal_EnableIfNeeded" in names, "full name from the detail section"
        assert "u8PicAdcInternal_" not in names, "truncated summary names are replaced"

    def test_parses_global_symbols(self, table):
        # Act
        names = _names(table, "global")

        # Assert
        assert "sCal" in names, "should parse global sCal"
        assert "f32Batt7Voltage" in names, "should parse global f32Batt7Voltage"

    def test_global_symbols_have_zero_size(self, table):
        # Act
        sizes = {symbol.size for symbol in table.symbols if symbol.section == "global"}

        # Assert
        assert sizes == {0}, "linker globals carry no size"

    def test_len(self, table):
        # Act / Assert -- exact: test_sym_commands.XC32_COUNT points here
        assert len(table) == 17, "every symbol in the committed fixture, no more"

    def test_first_address_wins(self, table):
        # Act -- 0x187ea appears in the detail section AND the summary
        hits = [symbol for symbol in table.symbols if symbol.addr == 0x187EA]

        # Assert
        assert [symbol.name for symbol in hits] == ["u8PicAdcInternal_EnableIfNeeded"], (
            "one symbol per address; the detail-section sighting wins"
        )

    def test_crlf_input_parses_identically(self, map_text):
        # Arrange
        crlf = map_text.replace("\n", "\r\n")

        # Act / Assert
        assert xc32.convert(crlf) == xc32.convert(map_text), "line endings are irrelevant"

    def test_empty_input(self):
        # Act / Assert
        assert xc32.convert("") == [], "nothing in, nothing out, no exception"

    def test_garbage_input(self):
        # Act / Assert
        assert xc32.convert("hello\nworld\n") == [], "unrelated text yields no symbols"


# ── The named addition: object files and literal pools ──────────────────────


class TestXc32ObjectFile:

    def test_file_from_next_line_form(self, table):
        # Act
        hits = [symbol for symbol in table.symbols if symbol.name == "sState" and symbol.addr == 0x200062F2]

        # Assert
        assert [symbol.file for symbol in hits] == ["mon.c"], "object path on the continuation line"

    def test_file_from_same_line_form(self, table):
        # Act
        hits = [symbol for symbol in table.symbols if symbol.name == "sState" and symbol.addr == 0x200062F3]

        # Assert
        assert [symbol.file for symbol in hits] == ["adc.c"], "object path on the input-section line"

    def test_duplicate_statics_are_both_kept(self, table):
        # Act
        files = sorted(symbol.file for symbol in table.symbols if symbol.name == "sState")

        # Assert
        assert files == ["adc.c", "mon.c"], "two statics, two files"

    def test_block_without_object_line_keeps_empty_file(self, table):
        # Act
        hits = [symbol for symbol in table.symbols if symbol.name == "u8PicAdcInternal_ReadBlockingAvg"]

        # Assert
        assert [symbol.file for symbol in hits] == [""], "no object line -> file stays empty"

    def test_dotted_name_kept_verbatim(self, table):
        # Act
        hits = [symbol for symbol in table.symbols if symbol.name == "sPacket.1"]

        # Assert
        assert len(hits) == 1 and hits[0].file == "mon.c", "sPacket.1 is one symbol, with its file"

    def test_literal_pool_is_not_a_symbol(self, table):
        # Act / Assert -- .rodata.str1.1 in both the summary and the detail section
        assert not any(symbol.name.startswith("str1.") for symbol in table.symbols), (
            "GCC merged string pools are skipped in both passes"
        )

    @pytest.mark.parametrize(
        ("obj", "expected"),
        [
            ("x.c.o", "x.c"),
            ("x.o", "x"),
            ("dir/sub/x.c.o", "x.c"),
            ("a\\b\\x.o", "x"),
            ("libc.a(memcmp.o)", "memcmp"),
            ("c:/tools/xc32/lib/libpic32c.a(data_init.o)", "data_init"),
        ],
        ids=["c.o", "o", "posix-path", "windows-path", "archive-member", "archive-path"],
    )
    def test_object_file_stem(self, obj, expected):
        # Act / Assert
        assert xc32.object_file_stem(obj) == expected


# ── The registry ────────────────────────────────────────────────────────────


class TestRegistry:

    def test_formats(self):
        # Act / Assert
        assert FORMATS == ("xc32",), "the registry has exactly the xc32 converter today"

    def test_every_spec_is_complete(self):
        # Act
        formats = [spec.format for spec in CONVERTERS]

        # Assert
        assert len(set(formats)) == len(formats), "format keys are unique"
        for spec in CONVERTERS:
            assert callable(spec.convert), f"{spec.format}: convert is callable"
            assert spec.detect, f"{spec.format}: detect has at least one marker"
            assert spec.description, f"{spec.format}: description is not empty"

    def test_sniff_by_header(self, map_text):
        # Act
        spec = find_converter(text=map_text)

        # Assert
        assert spec is not None and spec.format == "xc32", "the summary header identifies xc32"

    def test_sniff_by_toolchain_path_alone(self):
        # Act
        spec = find_converter(text="LOAD c:/x/xc32/v5.00/lib/foo.a\n")

        # Assert
        assert spec is not None and spec.format == "xc32", "the /xc32/ path marker is enough"

    def test_sniff_scans_the_whole_text(self):
        # Arrange -- the header sits ~49 KB into a real map; a window would miss it
        text = "x" * 60_000 + "\nMicrochip PIC32 Memory-Usage Report\n"

        # Act
        spec = find_converter(text=text)

        # Assert
        assert spec is not None and spec.format == "xc32", "no sniff window"

    def test_sniff_nothing(self):
        # Act / Assert
        assert find_converter(text="hello") is None, "unrecognized text picks no converter"

    def test_explicit_format(self):
        # Act
        spec = find_converter(format_name="xc32")

        # Assert
        assert spec is not None and spec.format == "xc32", "explicit name wins without sniffing"

    def test_explicit_unknown_format(self):
        # Act / Assert
        assert find_converter(format_name="nope") is None, "unknown key -> None (handler owns the error)"


@pytest.mark.parametrize("fixture", sorted(MAPS_DIR.glob("*.map")), ids=lambda p: p.name)
@pytest.mark.parametrize("spec", CONVERTERS, ids=lambda s: s.format)
def test_pairwise_exclusivity(fixture: Path, spec):
    """Each fixture is detected by exactly its own converter and no other."""
    # Arrange
    text = fixture.read_text(encoding="utf-8")
    owner = fixture.stem.split("_")[0]  # <format>_<anything>.map

    # Act
    detected = any(marker in text for marker in spec.detect)

    # Assert
    assert detected == (spec.format == owner), (
        f"{fixture.name} must be claimed by {owner} only; {spec.format} detect={detected}"
    )
