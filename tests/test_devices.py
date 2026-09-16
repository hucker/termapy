"""Unit tests for device files: the format, instances, layering and merge.

Pure functions over decoded JSON and a temp folder tree; the command-level
behavior (auto-load, survival across ``/sym.import``) lives in
``test_sym_commands.py::TestDevices``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.devices import (
    Field,
    derive_type,
    distinct_spans,
    library_path,
    load_device,
    load_devices_from_dir,
    merge_into,
    parse_device,
    registers_in_range,
    resolve_devices,
    scan_library,
)
from termapy.protocol.core import parse_format_spec
from termapy.symbols import Symbol


def _doc(**overrides) -> dict:
    """A minimal valid device document, fields overridable."""
    doc = {
        "device_version": 1,
        "device": "part",
        "registers": [{"name": "CTRL", "addr": "0x40000000", "size": 4}],
    }
    doc.update(overrides)
    return doc


def _write(folder: Path, name: str, doc: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{name}.device.json"
    file.write_text(json.dumps(doc), encoding="utf-8")
    return file


# ── The format ──────────────────────────────────────────────────────────────


class TestParse:

    def test_minimal_document(self):
        # Act
        device = parse_device(_doc())

        # Assert
        register = device.registers()[0]
        assert device.name == "part", "the device field is the identity"
        assert register.symbol.section == "sfr", "every register is an sfr"
        assert register.symbol.file == "part", "the device rides in Symbol.file"
        assert register.symbol.type == "u32", "no type, no fields: a scalar by size"

    @pytest.mark.parametrize("size, expected", [(1, "u8"), (2, "u16"), (8, "u64"), (3, "")])
    def test_default_type_follows_size(self, size, expected):
        # Act
        device = parse_device(_doc(registers=[{"name": "R", "addr": 0, "size": size}]))

        # Assert
        assert device.registers()[0].symbol.type == expected

    def test_version_is_the_hard_gate(self):
        with pytest.raises(ValueError, match="device_version"):
            parse_device(_doc(device_version=2))

    @pytest.mark.parametrize("bad", ["", "a b", "a.b", "-lead"])
    def test_device_name_rules(self, bad):
        with pytest.raises(ValueError, match="device"):
            parse_device(_doc(device=bad))

    @pytest.mark.parametrize("bad", ["PORT%s_DIR", "a.b", "1ST", "a-b"])
    def test_register_names_must_be_identifiers(self, bad):
        """An unexpanded SVD placeholder or a dotted name would load unreachable."""
        with pytest.raises(ValueError, match=r"registers\[0\]\.name"):
            parse_device(_doc(registers=[{"name": bad, "addr": 0, "size": 4}]))

    def test_size_is_required_and_positive(self):
        with pytest.raises(ValueError, match=r"registers\[0\]\.size"):
            parse_device(_doc(registers=[{"name": "R", "addr": 0}]))

    def test_duplicate_register_names_rejected(self):
        with pytest.raises(ValueError, match="duplicate name 'R'"):
            parse_device(_doc(registers=[
                {"name": "R", "addr": 0, "size": 4}, {"name": "R", "addr": 4, "size": 4},
            ]))

    def test_safety_fields_reach_the_symbol(self):
        # Act
        device = parse_device(_doc(registers=[{
            "name": "DATA", "addr": 0, "size": 1,
            "access": "ro", "read_effect": True, "rmw": False,
        }]))

        # Assert
        symbol = device.registers()[0].symbol
        assert symbol.access == "ro", "access is on the Symbol, where /mem.* can see it"
        assert symbol.read_effect is True, "read_effect too"
        assert symbol.rmw is False, "rmw still round-trips"

    def test_bad_access_rejected(self):
        with pytest.raises(ValueError, match=r"registers\[0\]\.access"):
            parse_device(_doc(registers=[{"name": "R", "addr": 0, "size": 4, "access": "r"}]))

    def test_viewer_context_reaches_the_register(self):
        # Act
        device = parse_device(_doc(
            description="A part", vendor="acme", parts=["ACME1"],
            registers=[{
                "name": "R", "addr": 0, "size": 4, "peripheral": "PORTA",
                "group": "PORT", "description": "Data Direction", "reset": "0x0000FFFF",
            }],
        ))

        # Assert
        register = device.registers()[0]
        assert device.vendor == "acme" and device.parts == ("ACME1",), "metadata kept"
        assert (register.peripheral, register.group) == ("PORTA", "PORT"), "tree keys kept"
        assert register.description == "Data Direction", "datasheet text kept"
        assert register.reset == 0xFFFF, "reset parsed as an address-shaped int"

    def test_unknown_keys_are_ignored(self):
        """Forward compatibility: a newer file loads on an older termapy."""
        # Act
        device = parse_device(_doc(future_key=1, registers=[
            {"name": "R", "addr": 0, "size": 4, "future_reg_key": True},
        ]))

        # Assert
        assert len(device) == 1, "unknown keys at both levels are ignored"


class TestFields:

    def test_fields_derive_the_type_spec(self):
        # Arrange
        fields = [{"name": "EVEN", "bit": 0, "width": 4}, {"name": "ODD", "bit": 4, "width": 4}]

        # Act
        device = parse_device(_doc(registers=[{"name": "PMUX", "addr": 0, "size": 1, "fields": fields}]))

        # Assert
        register = device.registers()[0]
        assert register.symbol.type == "EVEN:B1.0-3 ODD:B1.4-7", "single-byte form"
        assert [field.name for field in register.fields] == ["EVEN", "ODD"], "fields kept"

    def test_multibyte_form_uses_little_endian_byte_refs(self):
        # Act
        spec = derive_type(4, (Field("EN", 15), Field("MODE", 8, 2)))

        # Assert
        assert spec == "EN:B4-1.15 MODE:B4-1.8-9", "the B4-1 form the spec language reads"

    def test_derived_spec_parses(self):
        """The derivation must produce what the format-spec parser accepts."""
        # Arrange
        spec = derive_type(4, (Field("EN", 15), Field("MODE", 8, 2)))

        # Act
        columns = parse_format_spec(spec)

        # Assert
        assert [column.name for column in columns] == ["EN", "MODE"], "both columns parsed"

    def test_fields_and_type_together_is_an_error(self):
        with pytest.raises(ValueError, match="derived"):
            parse_device(_doc(registers=[{
                "name": "R", "addr": 0, "size": 1, "type": "u8",
                "fields": [{"name": "F", "bit": 0}],
            }]))

    def test_field_outside_the_register_is_an_error(self):
        with pytest.raises(ValueError, match="do not fit"):
            parse_device(_doc(registers=[{
                "name": "R", "addr": 0, "size": 1, "fields": [{"name": "F", "bit": 7, "width": 2}],
            }]))

    def test_enumerated_values_parse_from_json_keys(self):
        # Act
        device = parse_device(_doc(registers=[{
            "name": "R", "addr": 0, "size": 1,
            "fields": [{"name": "MODE", "bit": 0, "width": 2,
                        "values": {"0": "INPUT", "0x1": "OUTPUT"}}],
        }]))

        # Assert
        actual = device.registers()[0].fields[0].values
        assert actual == ((0, "INPUT"), (1, "OUTPUT")), "keys are integers, hex allowed"


# ── Instances ───────────────────────────────────────────────────────────────


_RELOCATABLE = {
    "relocatable": True,
    "registers": [{"name": "STATUS", "addr": "0x00", "size": 1},
                  {"name": "DATA", "addr": "0x04", "size": 4}],
}


class TestInstances:

    def test_non_relocatable_has_one_unnamed_instance_at_zero(self):
        # Act
        device = parse_device(_doc())

        # Assert
        (instance,) = device.instances
        assert (instance.name, instance.base) == ("", 0), "the MCU case"
        assert device.registers()[0].symbol.name == "CTRL", "no prefix"

    def test_relocatable_expands_each_instance(self):
        # Act
        device = parse_device(_doc(**_RELOCATABLE, instances=[
            {"name": "ADC1", "base": "0x60000000"}, {"name": "ADC2", "base": "0x60000100"},
        ]))

        # Assert
        names = {register.symbol.name: register.symbol.addr for register in device.registers()}
        assert names["ADC1_STATUS"] == 0x60000000, "prefixed and based"
        assert names["ADC2_DATA"] == 0x60000104, "offset added to the second base"
        assert len(device) == 4, "two registers per instance"

    def test_relocatable_without_instances_is_an_error(self):
        """Never a silent load at offset 0 -- that is real memory on many parts."""
        with pytest.raises(ValueError, match="needs at least one"):
            parse_device(_doc(**_RELOCATABLE))

    def test_instances_on_a_fixed_device_is_an_error(self):
        with pytest.raises(ValueError, match="only a relocatable"):
            parse_device(_doc(instances=[{"name": "X", "base": 0}]))

    def test_instance_needs_a_base(self):
        with pytest.raises(ValueError, match=r"instances\[0\]\.base"):
            parse_device(_doc(**_RELOCATABLE, instances=[{"name": "ADC1"}]))

    def test_duplicate_instance_names_rejected(self):
        with pytest.raises(ValueError, match="duplicate name 'ADC1'"):
            parse_device(_doc(**_RELOCATABLE, instances=[
                {"name": "ADC1", "base": 0}, {"name": "ADC1", "base": 0x100},
            ]))


# ── Folders and layers ──────────────────────────────────────────────────────


class TestFolder:

    def test_loads_every_device_file_and_nothing_else(self, tmp_path):
        # Arrange
        _write(tmp_path, "a", _doc(device="alpha"))
        _write(tmp_path, "b", _doc(device="beta"))
        (tmp_path / "notes.json").write_text("{}", encoding="utf-8")

        # Act
        load = load_devices_from_dir(tmp_path, "rig")

        # Assert
        assert [device.name for device in load.devices] == ["alpha", "beta"], "filename order"
        assert load.errors == [], "a plain .json is not a device file and not an error"
        assert load.devices[0].layer == "rig", "the layer label is recorded"

    def test_broken_file_is_reported_not_fatal(self, tmp_path):
        # Arrange
        _write(tmp_path, "bad", _doc(device_version=9))
        _write(tmp_path, "good", _doc(device="good"))

        # Act
        load = load_devices_from_dir(tmp_path)

        # Assert
        assert [device.name for device in load.devices] == ["good"], "the good one still loads"
        assert load.errors == ["bad.device.json: device_version: expected 1, got 9"], (
            "the error names the file and the field"
        )

    def test_two_files_one_device_is_an_error_on_the_second(self, tmp_path):
        # Arrange
        _write(tmp_path, "adc", _doc(device="ads1256"))
        _write(tmp_path, "adc_copy", _doc(device="ads1256"))

        # Act
        load = load_devices_from_dir(tmp_path)

        # Assert
        assert len(load.devices) == 1, "the first wins"
        assert "instances, not files" in load.errors[0], "the error says what to do instead"

    def test_missing_folder_is_empty(self, tmp_path):
        assert load_devices_from_dir(tmp_path / "nope").devices == []


class TestResolve:

    def test_per_config_overrides_global_by_device(self, tmp_path):
        # Arrange
        cfg = tmp_path / "rig" / "rig.cfg"
        cfg.parent.mkdir()
        cfg.write_text("{}", encoding="utf-8")
        _write(tmp_path / "dev", "mcu", _doc(device="mcu", description="global"))
        _write(cfg.parent / "dev", "mcu", _doc(device="mcu", description="local"))

        # Act
        devices, errors = resolve_devices(str(cfg), tmp_path)

        # Assert
        assert errors == []
        assert [device.description for device in devices] == ["local"], "per-config wins"

    def test_register_collision_skips_the_later_device(self, tmp_path):
        """Two parts claiming STATUS: the 'forgot the instance name' mistake."""
        # Arrange
        cfg = tmp_path / "rig" / "rig.cfg"
        cfg.parent.mkdir()
        cfg.write_text("{}", encoding="utf-8")
        _write(cfg.parent / "dev", "a", _doc(device="a", registers=[{"name": "STATUS", "addr": 0, "size": 1}]))
        _write(cfg.parent / "dev", "b", _doc(device="b", registers=[{"name": "STATUS", "addr": 8, "size": 1}]))

        # Act
        devices, errors = resolve_devices(str(cfg), tmp_path)

        # Assert
        assert [device.name for device in devices] == ["a"], "the later device is skipped whole"
        assert errors == [
            "b.device.json: register STATUS is also defined by a.device.json; give one an instance name"
        ], "the error names both files and the fix"

    def test_no_config_no_devices(self):
        assert resolve_devices("") == ([], [])


# ── Merge ───────────────────────────────────────────────────────────────────


class TestMerge:

    def test_registers_join_the_build_symbols(self):
        # Arrange
        build = [Symbol("main", 0x2000, 442, "text")]
        device = parse_device(_doc())

        # Act
        merged, shadowed = merge_into(build, [device])

        # Assert
        assert [symbol.name for symbol in merged] == ["main", "CTRL"], "appended after the build"
        assert shadowed == [], "nothing collided"
        assert build == [Symbol("main", 0x2000, 442, "text")], "the input is untouched"

    def test_build_wins_a_name_clash_and_it_is_reported(self):
        # Arrange
        build = [Symbol("CTRL", 0x2000, 4, "bss")]
        device = parse_device(_doc())

        # Act
        merged, shadowed = merge_into(build, [device])

        # Assert
        assert [symbol.addr for symbol in merged] == [0x2000], "the user's own symbol stays"
        assert shadowed == ["CTRL"], "and the shadowed register is named"


# ── Peripheral space ────────────────────────────────────────────────────────


class TestRegistersInRange:
    """The one address-containment question the read gates ask."""

    @staticmethod
    def _three() -> list:
        """Three 4-byte registers at 0x40000000, 0x40000004, 0x40000010."""
        return [parse_device(_doc(registers=[
            {"name": "A", "addr": "0x40000000", "size": 4},
            {"name": "B", "addr": "0x40000004", "size": 4},
            {"name": "C", "addr": "0x40000010", "size": 4},
        ]))]

    def test_ram_address_touches_nothing(self):
        # Act
        hit = registers_in_range(self._three(), 0x20000000, 1024)

        # Assert
        assert hit == [], "RAM is not peripheral space, however big the read"

    def test_a_span_returns_every_register_it_covers(self):
        # Act
        hit = registers_in_range(self._three(), 0x40000000, 0x14)

        # Assert
        actual = [register.symbol.name for register in hit]
        assert actual == ["A", "B", "C"], "all three lie inside the span"

    def test_partial_overlap_counts(self):
        # Arrange: one byte of B, from inside A.
        # Act
        hit = registers_in_range(self._three(), 0x40000003, 2)

        # Assert
        actual = [register.symbol.name for register in hit]
        assert actual == ["A", "B"], "touching one byte of a register is touching it"

    def test_a_gap_between_registers_is_not_a_register(self):
        # Act
        hit = registers_in_range(self._three(), 0x40000008, 8)

        # Assert
        assert hit == [], "the hole between B and C belongs to no register"

    def test_end_is_exclusive(self):
        # Act
        hit = registers_in_range(self._three(), 0x40000000, 4)

        # Assert
        actual = [register.symbol.name for register in hit]
        assert actual == ["A"], "a read ending where B starts has not touched B"

    @pytest.mark.parametrize("length", [0, -1])
    def test_an_empty_read_touches_nothing(self, length):
        # Act
        hit = registers_in_range(self._three(), 0x40000000, length)

        # Assert
        assert hit == [], "a zero-length read reads no byte, so it hits no register"

    def test_no_devices_loaded_is_the_common_case(self):
        # Act
        hit = registers_in_range([], 0x40000000, 0x1000)

        # Assert
        assert hit == [], "with no device file, nothing is known to be peripheral"

    def test_results_are_sorted_across_devices(self):
        # Arrange: a second part placed BELOW the first, loaded after it.
        second = parse_device(_doc(
            device="other",
            registers=[{"name": "Z", "addr": "0x3FFFFFFC", "size": 4}],
        ))
        devices = [*self._three(), second]

        # Act
        hit = registers_in_range(devices, 0x3FFFFFFC, 0x10)

        # Assert
        actual = [register.symbol.name for register in hit]
        assert actual == ["Z", "A", "B"], "address order, not load order"


class TestDistinctSpans:
    """SVD describes one register once per operating MODE; that is still one."""

    @staticmethod
    def _sercom() -> list:
        """CTRLA six ways at one address, then a genuinely separate register.

        The shape a real PIC32CM / SAM SVD emits: a SERCOM's modes each get
        a full register definition at the same offset.
        """
        return parse_device(_doc(registers=[
            {"name": f"SERCOM0_{mode}_CTRLA", "addr": "0x42000400", "size": 4}
            for mode in ("I2CM", "I2CS", "SPIM", "SPIS", "USART_INT", "USART_EXT")
        ] + [{"name": "SERCOM0_I2CM_CTRLB", "addr": "0x42000404", "size": 4}])).registers()

    def test_mode_aliases_are_one_span(self):
        # Arrange -- only the six CTRLA definitions
        registers = [r for r in self._sercom() if r.symbol.addr == 0x42000400]

        # Act
        actual = distinct_spans(registers)

        # Assert
        assert len(registers) == 6, "six definitions went in"
        assert actual == 1, "one address, one size: six names for the same four bytes"

    def test_separate_registers_still_count(self):
        # Act
        actual = distinct_spans(self._sercom())

        # Assert
        assert actual == 2, "CTRLA and CTRLB are genuinely two registers"

    def test_no_registers_is_zero(self):
        assert distinct_spans([]) == 0, "nothing covers nothing"

    def test_same_address_different_size_is_two_spans(self):
        """A 4-byte register and a 1-byte one at one address are not aliases."""
        # Arrange
        registers = parse_device(_doc(registers=[
            {"name": "WHOLE", "addr": "0x40000000", "size": 4},
            {"name": "LOW_BYTE", "addr": "0x40000000", "size": 1},
        ])).registers()

        # Act
        actual = distinct_spans(registers)

        # Assert
        assert actual == 2, "different extents are different reads"


class TestScanLibrary:
    """The library is a POOL: listed, never loaded, and checked for honesty."""

    @staticmethod
    def _tree(root):
        """A two-vendor library, nested the way the hierarchy intends."""
        _write(root / "microchip" / "mcu" / "pic32cm", "pic32part", _doc(
            device="pic32part", vendor="microchip", description="A part"))
        _write(root / "lattice" / "fpga", "icepart", _doc(
            device="icepart", vendor="lattice"))
        return root

    def test_missing_root_is_an_empty_library(self, tmp_path):
        # Act
        parts, errors = scan_library(tmp_path / "nothing-here")

        # Assert
        assert parts == [], "no folder, no parts"
        assert errors == [], "and a missing library is not an error"

    def test_walks_the_tree_and_records_the_category(self, tmp_path):
        # Arrange
        root = self._tree(tmp_path / "lib")

        # Act
        parts, errors = scan_library(root)

        # Assert
        assert errors == [], "both files are well-formed"
        actual = [(part.device, part.category) for part in parts]
        assert actual == [
            ("icepart", "lattice/fpga"),
            ("pic32part", "microchip/mcu/pic32cm"),
        ], "nested folders become the category, sorted by it"

    def test_a_root_level_file_has_no_category(self, tmp_path):
        # Arrange
        root = tmp_path / "lib"
        _write(root, "loose", _doc(device="loose"))

        # Act
        parts, _ = scan_library(root)

        # Assert
        assert parts[0].category == "", "a file at the root sits in no category"

    def test_the_filename_must_match_the_identity(self, tmp_path):
        """A file that says two different things is the wrong-part hazard."""
        # Arrange
        root = tmp_path / "lib"
        _write(root, "filename", _doc(device="different"))

        # Act
        parts, errors = scan_library(root)

        # Assert
        assert parts == [], "a self-contradicting file is not offered"
        assert "must agree" in errors[0], "and the disagreement is named"

    def test_registers_are_counted_not_parsed(self, tmp_path):
        """A thousand parts must list without a thousand validations."""
        # Arrange -- an address no parse would accept, in a listable file
        root = tmp_path / "lib"
        _write(root, "sloppy", {
            "device_version": 1, "device": "sloppy",
            "registers": [{"name": "R", "addr": "not-an-address", "size": 4}],
        })

        # Act
        parts, errors = scan_library(root)

        # Assert
        assert errors == [], "listing does not validate registers"
        assert parts[0].registers == 1, "the count is the list length"

    def test_a_duplicate_device_is_reported_once(self, tmp_path):
        # Arrange -- same identity, two categories
        root = tmp_path / "lib"
        _write(root / "a", "twin", _doc(device="twin"))
        _write(root / "b", "twin", _doc(device="twin"))

        # Act
        parts, errors = scan_library(root)

        # Assert
        assert len(parts) == 1, "one identity, one entry"
        assert "already defined by" in errors[0], "the loser is named"

    def test_a_broken_file_does_not_hide_the_good_ones(self, tmp_path):
        # Arrange
        root = self._tree(tmp_path / "lib")
        (root / "junk.device.json").write_text("{not json", encoding="utf-8")

        # Act
        parts, errors = scan_library(root)

        # Assert
        assert len(parts) == 2, "the well-formed parts still list"
        assert len(errors) == 1, "and the broken one is reported, not swallowed"

    def test_metadata_rides_along(self, tmp_path):
        # Arrange
        root = self._tree(tmp_path / "lib")

        # Act
        parts, _ = scan_library(root)
        part = next(p for p in parts if p.device == "pic32part")

        # Assert
        assert part.vendor == "microchip", "vendor for the listing"
        assert part.description == "A part", "and the one-line description"


class TestReferenceFiles:
    """A dev/ file may POINT at a library part instead of copying it."""

    @staticmethod
    def _library(root):
        """A library holding one relocatable part."""
        _write(root / "lattice", "icepart", _doc(
            device="icepart", relocatable=True,
            instances=[{"name": "EXAMPLE", "base": "0x10000000"}],
            registers=[{"name": "STATUS", "addr": "0x00", "size": 4}]))
        return root

    @staticmethod
    def _ref(folder, name, **extra):
        folder.mkdir(parents=True, exist_ok=True)
        doc = {"device_version": 1, "ref": "icepart"} | extra
        file = folder / f"{name}.device.json"
        file.write_text(json.dumps(doc), encoding="utf-8")
        return file

    def test_a_reference_loads_the_library_part(self, tmp_path):
        # Arrange -- a bare reference needs a FIXED-address part; a
        # relocatable one must state its placement (TestRelocatable...).
        library = tmp_path / "lib"
        _write(library / "lattice", "icepart", _doc(
            device="icepart",
            registers=[{"name": "STATUS", "addr": "0x40000000", "size": 4}]))
        ref = self._ref(tmp_path / "dev", "board")

        # Act
        device = load_device(ref, "cfg", library)

        # Assert
        assert device.name == "icepart", "the identity comes from the library part"
        assert len(device) == 1, "and so do its registers"

    def test_the_board_placement_wins(self, tmp_path):
        """The library's instances are at best an example; the board is real."""
        # Arrange
        library = self._library(tmp_path / "lib")
        ref = self._ref(tmp_path / "dev", "board",
                        instances=[{"name": "FPGA0", "base": "0x70000000"},
                                   {"name": "FPGA1", "base": "0x70001000"}])

        # Act
        device = load_device(ref, "cfg", library)

        # Assert
        actual = [(r.symbol.name, r.symbol.addr) for r in device.registers()]
        assert actual == [
            ("FPGA0_STATUS", 0x70000000),
            ("FPGA1_STATUS", 0x70001000),
        ], "both copies placed where THIS board puts them"

    def test_the_library_file_is_not_modified(self, tmp_path):
        # Arrange
        library = self._library(tmp_path / "lib")
        part = library / "lattice" / "icepart.device.json"
        before = part.read_text(encoding="utf-8")
        ref = self._ref(tmp_path / "dev", "board",
                        instances=[{"name": "X", "base": "0x70000000"}])

        # Act
        load_device(ref, "cfg", library)

        # Assert
        assert part.read_text(encoding="utf-8") == before, "the library stays pristine"

    def test_a_reference_may_not_redefine_registers(self, tmp_path):
        """That would be a fork, and not-being-a-fork is the whole value."""
        # Arrange
        library = self._library(tmp_path / "lib")
        ref = self._ref(tmp_path / "dev", "board",
                        registers=[{"name": "SNEAKY", "addr": "0x0", "size": 4}])

        # Act / Assert
        with pytest.raises(ValueError, match="only instances"):
            load_device(ref, "cfg", library)

    def test_an_unknown_part_is_named(self, tmp_path):
        # Arrange
        library = self._library(tmp_path / "lib")
        folder = tmp_path / "dev"
        folder.mkdir()
        file = folder / "board.device.json"
        file.write_text(json.dumps({"device_version": 1, "ref": "absent"}), encoding="utf-8")

        # Act / Assert
        with pytest.raises(ValueError, match="no library part named 'absent'"):
            load_device(file, "cfg", library)

    def test_no_library_configured_is_an_error_not_a_silent_skip(self, tmp_path):
        # Arrange
        ref = self._ref(tmp_path / "dev", "board")

        # Act / Assert
        with pytest.raises(ValueError, match="no library configured"):
            load_device(ref, "cfg", None)

    def test_an_empty_ref_is_refused(self, tmp_path):
        # Arrange
        library = self._library(tmp_path / "lib")
        folder = tmp_path / "dev"
        folder.mkdir()
        file = folder / "board.device.json"
        file.write_text(json.dumps({"device_version": 1, "ref": ""}), encoding="utf-8")

        # Act / Assert
        with pytest.raises(ValueError, match="non-empty device name"):
            load_device(file, "cfg", library)

    def test_a_plain_device_file_still_loads(self, tmp_path):
        """References are an addition; a self-contained file is untouched."""
        # Arrange
        folder = tmp_path / "dev"
        _write(folder, "plain", _doc(device="plain"))

        # Act
        device = load_device(folder / "plain.device.json", "cfg", None)

        # Assert
        assert device.name == "plain", "no library needed, none consulted"

    def test_a_broken_reference_does_not_stop_the_folder(self, tmp_path):
        # Arrange
        library = self._library(tmp_path / "lib")
        folder = tmp_path / "dev"
        _write(folder, "plain", _doc(device="plain"))
        (folder / "bad.device.json").write_text(
            json.dumps({"device_version": 1, "ref": "absent"}), encoding="utf-8")

        # Act
        load = load_devices_from_dir(folder, "cfg", library)

        # Assert
        assert [d.name for d in load.devices] == ["plain"], "the good file loaded"
        assert len(load.errors) == 1, "and the bad reference was reported"


class TestRelocatableReferenceNeedsPlacement:
    """A library part's instances are an EXAMPLE, never this board's addresses."""

    @staticmethod
    def _library(root):
        _write(root / "lattice", "icepart", _doc(
            device="icepart", relocatable=True,
            instances=[{"name": "EXAMPLE", "base": "0x10000000"}],
            registers=[{"name": "STATUS", "addr": "0x00", "size": 4}]))
        return root

    def test_a_reference_does_not_inherit_the_library_placement(self, tmp_path):
        """Inheriting would load every register at a plausible WRONG address."""
        # Arrange
        library = self._library(tmp_path / "lib")
        folder = tmp_path / "dev"
        folder.mkdir()
        file = folder / "board.device.json"
        file.write_text(json.dumps({"device_version": 1, "ref": "icepart"}),
                        encoding="utf-8")

        # Act / Assert
        with pytest.raises(ValueError, match="needs at least one"):
            load_device(file, "cfg", library)

    def test_its_own_placement_is_used(self, tmp_path):
        # Arrange
        library = self._library(tmp_path / "lib")
        folder = tmp_path / "dev"
        folder.mkdir()
        file = folder / "board.device.json"
        file.write_text(json.dumps({
            "device_version": 1, "ref": "icepart",
            "instances": [{"name": "REAL", "base": "0x70000000"}],
        }), encoding="utf-8")

        # Act
        device = load_device(file, "cfg", library)

        # Assert
        register = device.registers()[0]
        assert register.symbol.name == "REAL_STATUS", "this board's instance name"
        assert register.symbol.addr == 0x70000000, "and this board's base"

    def test_a_fixed_address_part_needs_no_placement(self, tmp_path):
        """Only a relocatable part has addresses the board must supply."""
        # Arrange
        library = tmp_path / "lib"
        _write(library / "v", "fixedpart", _doc(device="fixedpart"))
        folder = tmp_path / "dev"
        folder.mkdir()
        file = folder / "board.device.json"
        file.write_text(json.dumps({"device_version": 1, "ref": "fixedpart"}),
                        encoding="utf-8")

        # Act
        device = load_device(file, "cfg", library)

        # Assert
        assert len(device) == 1, "absolute addresses need nothing from the board"


class TestLibraryPath:
    """Where a converted part lands in the library."""

    def test_the_vendor_becomes_a_folder(self, tmp_path):
        # Act
        path = library_path(tmp_path, {"device": "part", "vendor": "Microchip Technology"})

        # Assert
        actual = path.relative_to(tmp_path).as_posix()
        assert actual == "microchip-technology/part.device.json", "prose vendor slugged"

    def test_an_explicit_category_nests_under_the_vendor(self, tmp_path):
        # Act
        path = library_path(tmp_path, {"device": "part", "vendor": "lattice"}, "fpga/ice40")

        # Assert
        actual = path.relative_to(tmp_path).as_posix()
        assert actual == "lattice/fpga/ice40/part.device.json", "vendor, then category"

    def test_no_vendor_means_no_vendor_level(self, tmp_path):
        """Only the levels that are KNOWN are created; nothing is guessed."""
        # Act
        path = library_path(tmp_path, {"device": "part", "vendor": ""})

        # Assert
        actual = path.relative_to(tmp_path).as_posix()
        assert actual == "part.device.json", "an unknown vendor is not invented"

    @pytest.mark.parametrize("vendor", ["...", "   ", "!!!"])
    def test_a_vendor_that_slugs_to_nothing_is_dropped(self, tmp_path, vendor):
        # Act
        path = library_path(tmp_path, {"device": "part", "vendor": vendor})

        # Assert
        assert path.parent == tmp_path, "never a folder named '-'"
