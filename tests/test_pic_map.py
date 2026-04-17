"""Tests for pic_map.py -- PIC compiler map file parser."""

from termapy.pic_map import MapFile, Symbol, format_symbol, parse_address

# Sample lines extracted from a real XC32 map file.
SAMPLE_MAP = """\
Microchip PIC32 Memory-Usage Report

ROM Program-Memory Usage
section                    address  length [bytes]      (dec)  Description
-------                 ----------  -------------------------  -----------
.vectors                         0          0x15c         348
.text                        0x15c           0x20          32  App's exec code
.text.main                 0x10ffa          0x1ba         442
.text.MonGpio               0xcac0          0x308         776
.text.PinDiag_PinWrCmd      0x6b30          0x5ee        1518
.text.SERCOM4_USART_Ser     0xec5c          0x260         608
.text.u8PicAdcInternal_     0x16c1e           0xd4         212
.text.u8PicAdcInternal_     0x187ea           0x98         152
.rodata.sCmdTableA          0xc15c          0x334         820
.bss.SERCOM4_USART_Writ 0x20000000         0x1400        5120
.bss.sDispatch          0x200038a8          0x658        1624
.data.sCurrentBaud      0x20006514            0x4           4
.data.sFaultFn          0x20006530            0x4           4

Linker script and memory map

.text.u8PicAdcInternal_ReadBlockingAvg%358
                0x00016c1e       0xd4

.text.u8PicAdcInternal_EnableIfNeeded%398
                0x000187ea       0x98

.bss.SERCOM4_USART_WriteBuffer%6
                0x20000000     0x1400

                0x20005a58                sCal
                0x200061d8                __stderr_used
                0x20006214                f32Batt7Voltage
"""


# ── Symbol ──────────────────────────────────────────────────────────────────


class TestSymbol:

    def test_contains_start(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert sym.contains(0x10FFA) is True, "start address should be contained"

    def test_contains_middle(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert sym.contains(0x11000) is True, "middle address should be contained"

    def test_contains_last_byte(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert sym.contains(0x10FFA + 0x1BA - 1) is True, "last byte should be contained"

    def test_excludes_end(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert sym.contains(0x10FFA + 0x1BA) is False, "end address should not be contained"

    def test_excludes_before(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act / Assert
        assert sym.contains(0x10FF9) is False, "address before start should not be contained"

    def test_section_label(self):
        assert Symbol("f", 0, 1, "text").section_label == "code", "text -> code"
        assert Symbol("f", 0, 1, "bss").section_label == "bss", "bss -> bss"
        assert Symbol("f", 0, 1, "rodata").section_label == "const", "rodata -> const"
        assert Symbol("f", 0, 1, "data").section_label == "data", "data -> data"

    def test_end(self):
        # Arrange
        sym = Symbol("f", 0x100, 0x20, "text")

        # Assert
        assert sym.end == 0x120, "end should be addr + size"


# ── parse_address ───────────────────────────────────────────────────────────


class TestParseAddress:

    def test_hex_0x_prefix(self):
        actual = parse_address("0xFFFF")
        assert actual == 0xFFFF, "0x prefix should parse as hex"

    def test_hex_0X_prefix(self):
        actual = parse_address("0X10FFA")
        assert actual == 0x10FFA, "0X prefix should parse as hex"

    def test_hex_h_suffix(self):
        actual = parse_address("FFFFh")
        assert actual == 0xFFFF, "h suffix should parse as hex"

    def test_hex_all_digits_4plus(self):
        actual = parse_address("CAFE")
        assert actual == 0xCAFE, "4+ hex digit string should parse as hex"

    def test_hex_8_digit(self):
        actual = parse_address("20000000")
        assert actual == 0x20000000, "8 hex digit string should parse as hex"

    def test_decimal(self):
        actual = parse_address("12345")
        # 12345 is all hex digits and 5 chars, so it parses as hex
        # This is intentional -- for PIC addresses, hex-like strings are hex
        assert actual == 0x12345, "5-digit all-hex string should parse as hex"

    def test_small_decimal(self):
        actual = parse_address("100")
        assert actual == 100, "3-digit number should parse as decimal"

    def test_empty(self):
        assert parse_address("") is None, "empty should return None"

    def test_garbage(self):
        assert parse_address("hello") is None, "non-numeric should return None"

    def test_whitespace(self):
        actual = parse_address("  0xFF  ")
        assert actual == 0xFF, "should handle whitespace"


# ── MapFile.from_text ───────────────────────────────────────────────────────


class TestMapFileParse:

    def test_parses_text_symbols(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        names = [s.name for s in mf.symbols if s.section == "text"]
        assert "main" in names, "should parse .text.main"
        assert "MonGpio" in names, "should parse .text.MonGpio"

    def test_parses_bss_symbols(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        names = [s.name for s in mf.symbols if s.section == "bss"]
        assert "SERCOM4_USART_WriteBuffer" in names, "should parse .bss symbol with full name"
        assert "sDispatch" in names, "should parse .bss.sDispatch"

    def test_parses_data_symbols(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        names = [s.name for s in mf.symbols if s.section == "data"]
        assert "sCurrentBaud" in names, "should parse .data.sCurrentBaud"

    def test_parses_rodata_symbols(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        names = [s.name for s in mf.symbols if s.section == "rodata"]
        assert "sCmdTableA" in names, "should parse .rodata.sCmdTableA"

    def test_skips_non_symbol_lines(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert - .vectors and bare .text (no symbol name) should be skipped
        names = [s.name for s in mf.symbols]
        assert "vectors" not in names, "should skip .vectors (no dotted symbol)"

    def test_sorted_by_address(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        addrs = [s.addr for s in mf.symbols]
        assert addrs == sorted(addrs), "symbols should be sorted by address"

    def test_stats(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)
        stats = mf.stats()

        # Assert
        assert stats["text"] >= 3, "should have at least 3 text symbols"
        assert stats["bss"] >= 2, "should have at least 2 bss symbols"

    def test_detail_section_provides_full_names(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert - detailed section should override truncated summary names
        names = [s.name for s in mf.symbols]
        assert "u8PicAdcInternal_ReadBlockingAvg" in names, (
            "should use full name from detailed section"
        )
        assert "u8PicAdcInternal_EnableIfNeeded" in names, (
            "should use full name from detailed section"
        )
        assert "SERCOM4_USART_WriteBuffer" in names, (
            "should use full bss name from detailed section"
        )
        # Truncated names should NOT appear when full names are available
        truncated = [s for s in mf.symbols if s.name == "u8PicAdcInternal_"]
        assert len(truncated) == 0, (
            "truncated names should be replaced by full names"
        )

    def test_parses_global_symbols(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        names = [s.name for s in mf.symbols if s.section == "global"]
        assert "sCal" in names, "should parse global sCal"
        assert "f32Batt7Voltage" in names, "should parse global f32Batt7Voltage"

    def test_global_symbols_have_zero_size(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        globals_ = [s for s in mf.symbols if s.section == "global"]
        for sym in globals_:
            assert sym.size == 0, f"global {sym.name} should have size 0"

    def test_len(self):
        # Act
        mf = MapFile.from_text(SAMPLE_MAP)

        # Assert
        assert len(mf) >= 12, "should parse at least 12 symbols from sample"


# ── MapFile.lookup ──────────────────────────────────────────────────────────


class TestMapFileLookup:

    def test_exact_match(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        sym = mf.lookup(0x10FFA)

        # Assert
        assert sym is not None, "should find main at exact start address"
        assert sym.name == "main", "should be main"

    def test_offset_within(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act - address inside main (0x10FFA + 0x10)
        sym = mf.lookup(0x1100A)

        # Assert
        assert sym is not None, "should find main at offset"
        assert sym.name == "main", "should be main"

    def test_miss_before_first(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        sym = mf.lookup(0x0001)

        # Assert
        assert sym is None, "address before first symbol should return None"

    def test_miss_between_symbols(self):
        # Arrange - build a map with a gap
        mf = MapFile(
            [Symbol("a", 0x100, 0x10, "text"), Symbol("b", 0x200, 0x10, "text")]
        )

        # Act
        sym = mf.lookup(0x150)

        # Assert
        assert sym is None, "address in gap between symbols should return None"

    def test_bss_lookup(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        sym = mf.lookup(0x20000000)

        # Assert
        assert sym is not None, "should find bss symbol at RAM address"
        assert sym.section == "bss", "should be bss section"

    def test_data_lookup(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        sym = mf.lookup(0x20006514)

        # Assert
        assert sym is not None, "should find data symbol"
        assert sym.name == "sCurrentBaud", "should be sCurrentBaud"

    def test_global_lookup_exact(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        sym = mf.lookup(0x20006214)

        # Assert
        assert sym is not None, "should find global at exact address"
        assert sym.name == "f32Batt7Voltage", "should be f32Batt7Voltage"
        assert sym.section == "global", "should be global section"


# ── MapFile.search ──────────────────────────────────────────────────────────


class TestMapFileSearch:

    def test_substring_match(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        matches = mf.search("Mon")

        # Assert
        assert len(matches) >= 1, "should find at least MonGpio"
        assert any(s.name == "MonGpio" for s in matches), "should include MonGpio"

    def test_case_insensitive(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        matches = mf.search("mongpio")

        # Assert
        assert len(matches) >= 1, "case-insensitive search should find MonGpio"

    def test_glob_wildcard(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        matches = mf.search("*main*")

        # Assert
        assert any(s.name == "main" for s in matches), "glob *main* should find main"

    def test_glob_prefix(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        matches = mf.search("Mon*")

        # Assert
        assert any(s.name == "MonGpio" for s in matches), "glob Mon* should find MonGpio"
        assert not any(s.name == "main" for s in matches), "glob Mon* should not find main"

    def test_regex_anchor(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        matches = mf.search("^Mon")

        # Assert
        assert any(s.name == "MonGpio" for s in matches), "regex ^Mon should find MonGpio"

    def test_no_match(self):
        # Arrange
        mf = MapFile.from_text(SAMPLE_MAP)

        # Act
        matches = mf.search("__does_not_exist__")

        # Assert
        assert matches == [], "should return empty list for no match"


# ── format_symbol ───────────────────────────────────────────────────────────


class TestFormatSymbol:

    def test_basic_format(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act
        actual = format_symbol(sym)

        # Assert
        assert "0x00010FFA" in actual, "should contain hex address"
        assert "main" in actual, "should contain symbol name"
        assert "code" in actual, "should show section label"
        assert "442" in actual, "should show size in decimal"

    def test_with_offset(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act
        actual = format_symbol(sym, 0x1100A)

        # Assert
        assert "+0x10" in actual, "should show offset from symbol start"

    def test_no_offset_at_start(self):
        # Arrange
        sym = Symbol("main", 0x10FFA, 0x1BA, "text")

        # Act
        actual = format_symbol(sym, 0x10FFA)

        # Assert
        assert "+0x" not in actual, "should not show offset when at exact start"

    def test_global_format_no_size(self):
        # Arrange
        sym = Symbol("sCal", 0x20005A58, 0, "global")

        # Act
        actual = format_symbol(sym)

        # Assert
        assert "sCal" in actual, "should contain symbol name"
        assert "global" in actual, "should show global label"
        assert "0 bytes" not in actual, "should not show 0 bytes for globals"
