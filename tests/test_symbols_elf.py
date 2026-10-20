"""The ELF converter against three builds of one C file, and its place in the registry.

``tests/fixtures/elf/elf_sample.c`` holds one global per type rule, built by
``build_fixtures.ps1`` with GCC (XC32: DWARF 2, little-endian, 32-bit,
Thumb), with Clang (zig: DWARF 5, big-endian, 64-bit), and with GCC without
debug info.  Two compilers and two DWARF generations are what make "a type
only where DWARF states it" a claim about DWARF rather than about one
toolchain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from termapy.protocol.core import apply_format, parse_format_spec
from termapy.symbols import Layout, Symbol
from termapy.symbols.converters import elf, find_converter

FIXTURES = Path(__file__).parent / "fixtures" / "elf"
XC32 = FIXTURES / "elf_xc32_cm23.elf"
CLANG = FIXTURES / "elf_clang_ppc64.elf"
NODEBUG = FIXTURES / "elf_nodebug.elf"
MAP = Path(__file__).parent / "fixtures" / "maps" / "xc32_sample.map"


def _symbols(path: Path) -> dict[str, Symbol]:
    return {symbol.name: symbol for symbol in elf.convert(path.read_bytes())}


@pytest.fixture(scope="module")
def xc32() -> dict[str, Symbol]:
    return _symbols(XC32)


@pytest.fixture(scope="module")
def clang() -> dict[str, Symbol]:
    return _symbols(CLANG)


def _initial_bytes(path: Path, symbol: Symbol) -> bytes:
    """The bytes the ELF loads at ``symbol`` -- what the variable starts as."""
    with path.open("rb") as stream:
        image = ELFFile(stream)
        for section in image.iter_sections():
            start = section["sh_addr"]
            if (
                section["sh_type"] != "SHT_NOBITS"
                and start <= symbol.addr < start + section["sh_size"]
            ):
                offset = symbol.addr - start
                return section.data()[offset : offset + symbol.size]
    raise AssertionError(f"{symbol.name} is in no loaded section")


# ── types from DWARF ────────────────────────────────────────────────────────

# (name, XC32 type, Clang type): where the two differ, each is what that
# CPU's ABI makes the C declaration -- a short enum and a 4-byte pointer on
# the Cortex-M23, an int enum and an 8-byte pointer on ppc64.
_TYPES = [
    ("gU16", "u16", "u16"),
    ("gI16", "i16", "i16"),
    ("gFlag", "u8", "u8"),
    ("gChar", "u8", "u8"),
    ("gSChar", "i8", "i8"),
    ("gF32", "f32", "f32"),
    ("gF64", "f64", "f64"),
    ("gPtr", "u32", "u64"),
    ("gMode", "i8", "i32"),
    ("gLevel", "u8", "u32"),
    ("kTable", "u32", "u32"),
    ("gName", "u8", "u8"),
    ("gDeclared", "u32", "u32"),
    ("gSink", "u32", "u64"),
    ("kSpiSckPin", "u8", "u8"),
    ("kTrim", "i16", "i16"),
    ("kLevel", "u8", "u32"),
    ("SPI_PORT", "u32", "u64"),
]


class TestTypes:
    @pytest.mark.parametrize(("name", "expected", "_"), _TYPES, ids=[row[0] for row in _TYPES])
    def test_gcc_dwarf_2(self, xc32, name, expected, _):
        """elf-types-or-none: XC32's DWARF 2 types every scalar, enum, pointer and array."""
        # Act / Assert
        assert xc32[name].type == expected

    @pytest.mark.parametrize(("name", "_", "expected"), _TYPES, ids=[row[0] for row in _TYPES])
    def test_clang_dwarf_5(self, clang, name, _, expected):
        """elf-types-or-none: Clang's DWARF 5 (addresses through .debug_addr) types the same."""
        # Act / Assert
        assert clang[name].type == expected

    def test_char_signedness_comes_from_dwarf(self, xc32):
        """Plain char is unsigned on ARM and DWARF says so; signed char stays signed."""
        # Act
        actual = (xc32["gChar"].type, xc32["gSChar"].type)

        # Assert
        assert actual == ("u8", "i8"), "never assumed per CPU"

    def test_a_negative_enumerator_makes_the_enum_signed(self, xc32):
        """enum Mode has MODE_OFF = -1, so it is signed; enum Level is not."""
        # Act
        actual = (xc32["gMode"].type, xc32["gLevel"].type)

        # Assert
        assert actual == ("i8", "u8")

    def test_a_header_declared_global_is_typed_through_its_declaration(self, xc32):
        """GCC describes gDeclared twice; the definition's DW_AT_specification carries the type."""
        # Act / Assert
        assert xc32["gDeclared"].type == "u32", (
            "the type is on the declaration the definition names"
        )

    @pytest.mark.parametrize(("source", "name"), [(XC32, "count.0"), (CLANG, "tick.count")])
    def test_a_function_static_is_typed_by_its_address(self, source, name):
        """GCC and Clang rename a static differently; the address matches it to its DWARF entry."""
        # Act
        actual = _symbols(source)[name].type

        # Assert
        assert actual == "u32"

    def test_an_array_takes_its_element_type_and_keeps_its_size(self, xc32):
        """kTable is const uint32_t[4]: u32, 16 bytes."""
        # Act
        actual = (xc32["kTable"].type, xc32["kTable"].size)

        # Assert
        assert actual == ("u32", 16)

    def test_const_is_read_only(self, xc32, clang):
        """A const object is access: ro; a plain variable is not."""
        # Act
        actual = [table[name].access for table in (xc32, clang) for name in ("kTable", "gU16")]

        # Assert
        assert actual == ["ro", "rw", "ro", "rw"]

    def test_functions_are_untyped(self, xc32):
        """Code has an address and a size, never a value type."""
        # Act / Assert
        assert (xc32["main"].type, xc32["tick"].type) == ("", "")


class TestStructs:
    def test_a_struct_is_a_layout_of_its_members_in_little_endian(self, xc32):
        """Each member a column at its offset, multi-byte ranges written low byte last."""
        # Act / Assert
        assert xc32["gConfig"].type == "baud_div:U2-1 trim:I3 mode:U4 gain:F8-5 inner:H9-10"

    def test_the_same_struct_in_big_endian(self, clang):
        """On a big-endian CPU the same members read in ascending byte order."""
        # Act / Assert
        assert clang["gConfig"].type == "baud_div:U1-2 trim:I3 mode:U4 gain:F5-8 inner:H9-10"

    @pytest.mark.parametrize("source", [XC32, CLANG], ids=["gcc-le", "clang-be"])
    def test_the_layout_decodes_the_initial_value(self, source):
        """Applied to the bytes the ELF loads, the layout reads back the C initializer."""
        # Arrange -- gConfig = {0x0102, -4, 3, 0.5f, {5, 6}, 1, 5}
        config = _symbols(source)["gConfig"]
        data = _initial_bytes(source, config)

        # Act
        headers, values = apply_format(data, parse_format_spec(config.type, word=True))

        # Assert
        assert dict(zip(headers, values)) == {
            "baud_div": "258",
            "trim": "-4",
            "mode": "3",
            "gain": "0.5",
            "inner": "0506",
        }, "every member at its offset in the ELF's byte order"

    def test_bit_fields_wait_for_member_access(self, xc32):
        """ready and level are bit fields: left out until the member-access step."""
        # Act
        names = [column.name for column in parse_format_spec(xc32["gConfig"].type, word=True)]

        # Assert
        assert "ready" not in names and "level" not in names


# ── constants ───────────────────────────────────────────────────────────────

# (name, value): the C initializer each const scalar carries, in both builds.
_VALUES = [
    ("kSpiSckPin", 4),
    ("kTrim", -7),
    ("kGain", 0.1),
    ("kLevel", 1),
    ("SPI_PORT", 0x41008080),
    ("SPI_DIR", 0x41008080),
]


class TestConstants:
    @pytest.mark.parametrize(("name", "expected"), _VALUES, ids=[row[0] for row in _VALUES])
    def test_a_const_scalar_carries_its_initializer(self, xc32, clang, name, expected):
        """elf-values-const-only: the bytes the file loads, decoded by the DWARF type, in both builds."""
        # Act / Assert
        assert (xc32[name].value, clang[name].value) == (expected, expected)

    def test_a_variable_never_carries_a_value(self, xc32):
        """elf-values-const-only: gU16 is initialized to 0x1234, but a variable's bytes are not its value."""
        # Act
        actual = [xc32[name].value for name in ("gU16", "gPtr", "gConfig", "gName")]

        # Assert
        assert actual == [None] * 4, "only a const object has a value"

    def test_a_const_array_has_no_value_yet(self, xc32):
        """elf-values-const-only: kTable is const but an array; tables are a later step."""
        # Act / Assert
        assert xc32["kTable"].value is None

    def test_a_float_reads_at_its_own_precision(self, xc32):
        """0.1f is 0.100000001490116 as a double; the value reads back as the 0.1 that was written."""
        # Act / Assert
        assert xc32["kGain"].value == 0.1

    def test_a_pointer_constant_says_what_it_points_at(self, xc32, clang):
        """A pointer to a struct is a block (the alias trigger), to a word a scalar; a number is no pointer."""
        # Act
        actual = [
            (table["SPI_PORT"].pointee, table["SPI_DIR"].pointee, table["kSpiSckPin"].pointee)
            for table in (xc32, clang)
        ]

        # Assert
        assert actual == [("struct", "scalar", "")] * 2

    def test_no_debug_info_no_values(self):
        """elf-values-const-only: without DWARF a const is an address and a size, never a value."""
        # Act
        bare = _symbols(NODEBUG)

        # Assert
        actual = [(bare[name].value, bare[name].pointee) for name, _ in _VALUES]
        assert actual == [(None, "")] * len(_VALUES)


# ── the symbol table ────────────────────────────────────────────────────────


class TestSymbolTable:
    def test_sections_come_from_the_header_flags(self, xc32):
        """Initialized data, zeroed data, constants and code, whatever the sections are named."""
        # Act
        actual = [xc32[name].section for name in ("gU16", "gPtr", "kTable", "main")]

        # Assert
        assert actual == ["data", "bss", "rodata", "text"]

    def test_function_addresses_drop_the_thumb_bit(self, xc32):
        """Every Cortex-M function is Thumb; its symbol value is odd, its address is not."""
        # Act
        odd = [
            symbol.name for symbol in xc32.values() if symbol.section == "text" and symbol.addr % 2
        ]

        # Assert
        assert odd == [], "bit 0 marks the instruction set, not the address"

    def test_linker_script_symbols_are_size_zero_globals(self, xc32):
        """A NOTYPE global (a linker-script symbol) is a global, as a map shows it."""
        # Act
        globals_ = [symbol for symbol in xc32.values() if symbol.section == "global"]

        # Assert
        assert globals_, "the XC32 link defines some (stack and heap bounds)"

    def test_files_come_from_the_symbol_table_and_the_debug_info(self, xc32):
        """A static's file from its STT_FILE entry; a global's from its compile unit."""
        # Act
        actual = (xc32["count.0"].file, xc32["gU16"].file)

        # Assert
        assert actual == ("elf_sample.c", "elf_sample.c")

    def test_a_build_without_debug_info_has_the_same_symbols_untyped(self, xc32):
        """No -g: every name, address and size is still there, and the sample is untyped.

        Only the sample's own globals: XC32's startup and library objects
        carry debug info of their own, so a few library symbols stay typed.
        """
        # Act
        bare = _symbols(NODEBUG)

        # Assert
        assert {(s.name, s.addr, s.size) for s in bare.values()} == {
            (s.name, s.addr, s.size) for s in xc32.values()
        }, "the symbol table does not depend on -g"
        assert [bare[name].type for name, _, _ in _TYPES] == [""] * len(_TYPES), (
            "no DWARF for the sample, no types for its globals"
        )

    @pytest.mark.parametrize(
        "data",
        [b"", b"\x7fELF", b"\x7fELF" + bytes(60), b"not an elf at all"],
        ids=["empty", "magic-only", "zero-header", "text"],
    )
    def test_odd_input_never_raises(self, data):
        """Garbage in: an empty symbol list and the default layout, never an exception."""
        # Act
        actual = (elf.convert(data), elf.layout(data))

        # Assert
        assert actual == ([], Layout())

    @pytest.mark.parametrize("cut", [200, 2000, 20000])
    def test_a_truncated_file_never_raises(self, cut):
        """A file cut short gives whatever parsed, and never an exception."""
        # Arrange
        data = XC32.read_bytes()[:cut]

        # Act / Assert
        assert isinstance(elf.convert(data), list), "a list, possibly empty"
        assert isinstance(elf.layout(data), Layout), "a layout, possibly the default"


# ── layout ──────────────────────────────────────────────────────────────────


class TestLayout:
    def test_width_and_byte_order_come_from_the_header(self):
        """The Cortex-M23 build is 32-bit little-endian; the ppc64 build 64-bit big-endian."""
        # Act
        actual = [
            (layout.address_bits, layout.endian)
            for layout in (elf.layout(XC32.read_bytes()), elf.layout(CLANG.read_bytes()))
        ]

        # Assert
        assert actual == [(32, "le"), (64, "be")]

    def test_a_region_per_loadable_segment(self):
        """Flash segments are read-only, the RAM segment read-write, named after a section."""
        # Act
        regions = elf.layout(XC32.read_bytes()).regions

        # Assert
        assert [(region.start, region.access) for region in regions] == [
            (0x00000000, "ro"),
            (0x0000017C, "ro"),
            (0x20000000, "rw"),
        ], "every PT_LOAD segment, access from its flags"
        assert regions[0].name == "vectors", "the first section's name, no dot"


# ── the registry ────────────────────────────────────────────────────────────


class TestRegistry:
    def test_the_signature_beats_text_markers(self):
        """converter-magic-first: an XC32 ELF carries "/xc32/" and is still read as an ELF."""
        # Arrange
        data = XC32.read_bytes()
        text = data.decode("utf-8", errors="replace")
        assert "/xc32/" in text, "premise: the map converter's marker is inside the ELF"

        # Act
        spec = find_converter(text=text, data=data)

        # Assert
        assert spec is not None and spec.format == "elf"

    def test_a_map_is_not_claimed_by_the_elf_converter(self):
        """A text map has no ELF signature, so its own converter claims it."""
        # Arrange
        data = MAP.read_bytes()

        # Act
        spec = find_converter(text=data.decode("utf-8"), data=data)

        # Assert
        assert spec is not None and spec.format == "xc32"

    @pytest.mark.parametrize("source", [XC32, CLANG, NODEBUG], ids=lambda path: path.stem)
    def test_every_fixture_is_an_elf(self, source):
        """Each fixture is recognized by its signature alone, with no format named."""
        # Arrange
        data = source.read_bytes()

        # Act
        spec = find_converter(text=data.decode("utf-8", errors="replace"), data=data)

        # Assert
        assert spec is not None and spec.format == "elf"
