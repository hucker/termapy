"""The CMSIS-SVD converter, against a trimmed fixture that has every shape.

``svd.convert`` is pure (text in, document out), so these are direct
calls; the ``/dev.import`` command that drives it lives in
``test_sym_commands.py::TestDevImport``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from termapy.devices import parse_device
from termapy.devices.converters import CONVERTERS, FORMATS, find_converter, svd

SVD_SAMPLE = Path(__file__).parent / "fixtures" / "devices" / "svd_sample.svd"

# PORT: 2 groups x (DIR, IN, PMUX0, PMUX1) = 8; SERCOM0 + SERCOM1: 3 each;
# TC0 + TC1: 1 each.
EXPECTED_COUNT = 16


@pytest.fixture(scope="module")
def doc():
    return svd.convert(SVD_SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(doc):
    return {row["name"]: row for row in doc["registers"]}


class TestFlattening:

    def test_count(self, doc):
        assert len(doc["registers"]) == EXPECTED_COUNT, "every dim, cluster and derivation expanded"

    def test_cluster_dim_expands_with_offset(self, rows):
        assert rows["PORT_GROUP0_DIR"]["addr"] == "0x40003000", "group 0 at the base"
        assert rows["PORT_GROUP1_DIR"]["addr"] == "0x40003080", "group 1 one dimIncrement up"

    def test_register_dim_uses_dimindex_labels(self, rows):
        assert rows["PORT_GROUP0_PMUX0"]["addr"] == "0x40003030", "%s -> first label"
        assert rows["PORT_GROUP0_PMUX1"]["addr"] == "0x40003031", "%s -> second label, +dimIncrement"

    def test_derived_peripheral_copies_registers_at_its_own_base(self, rows):
        assert rows["SERCOM1_DATA"]["addr"] == "0x40003C28", "SERCOM0's layout at SERCOM1's base"
        assert rows["SERCOM1_INTFLAG"]["rmw"] is False, "and its write semantics"

    def test_peripheral_dim_with_range_index(self, rows):
        assert rows["TC0_CTRLA"]["addr"] == "0x40004000", "0-1 range: first"
        assert rows["TC1_CTRLA"]["addr"] == "0x40004400", "second, one dimIncrement up"

    def test_size_inherits_from_the_device(self, rows):
        assert rows["PORT_GROUP0_DIR"]["size"] == 4, "device default 32 bits"
        assert rows["PORT_GROUP0_PMUX0"]["size"] == 1, "register override 8 bits"


class TestSemantics:

    def test_access_maps(self, rows):
        assert rows["PORT_GROUP0_IN"]["access"] == "ro", "read-only -> ro"
        assert rows["SERCOM0_DBGCTRL"]["access"] == "wo", "write-only -> wo"
        assert "access" not in rows["PORT_GROUP0_DIR"], "rw is the default and is omitted"

    def test_read_action_becomes_read_effect(self, rows):
        assert rows["SERCOM0_DATA"]["read_effect"] is True, "readAction present"
        assert "read_effect" not in rows["PORT_GROUP0_DIR"], "absent otherwise"

    def test_one_to_clear_forbids_rmw(self, rows):
        assert rows["SERCOM0_INTFLAG"]["rmw"] is False, "oneToClear must not be mask-written"

    def test_reset_value_inherits_and_overrides(self, rows):
        assert rows["PORT_GROUP0_DIR"]["reset"] == "0x00000000", "device default, register width"
        assert rows["SERCOM0_DBGCTRL"]["reset"] == "0x01", "register override, one byte"

    def test_peripheral_group_and_description(self, rows):
        row = rows["PORT_GROUP0_DIR"]
        assert (row["peripheral"], row["group"]) == ("PORT", "PORT"), "the SVD names"
        assert row["description"] == "Data Direction register", "whitespace collapsed"


class TestFields:

    def test_three_bit_spellings(self, rows):
        by_name = {field["name"]: field for field in rows["PORT_GROUP0_PMUX0"]["fields"]}
        assert (by_name["PMUXE"]["bit"], by_name["PMUXE"]["width"]) == (0, 4), "bitRange [3:0]"
        assert (by_name["PMUXO"]["bit"], by_name["PMUXO"]["width"]) == (4, 4), "lsb/msb"
        dir_field = rows["PORT_GROUP0_DIR"]["fields"][0]
        assert (dir_field["bit"], dir_field["width"]) == (0, 32), "bitOffset/bitWidth"

    def test_single_bit_omits_width(self, rows):
        dre = rows["SERCOM0_INTFLAG"]["fields"][0]
        assert dre == {"name": "DRE", "bit": 0}, "a flag is bit only"

    def test_enumerated_values_parse_hex_and_binary_and_skip_wildcards(self, rows):
        pmuxe = rows["PORT_GROUP0_PMUX0"]["fields"][0]
        assert pmuxe["values"] == {"0": "A", "1": "B"}, "0x0, #0001 parsed; #xxxx skipped"


class TestDocument:

    def test_metadata(self, doc):
        assert doc["device"] == "atsample1", "the SVD name, lowercased"
        assert doc["vendor"] == "Sample Semiconductor"
        assert doc["parts"] == ["ATSAMPLE1"]
        assert doc["license"] == "Copyright (c) 2026 Sample Semiconductor.", "first line only"
        assert doc["source"].startswith("CMSIS-SVD 1.3: ATSAMPLE1"), "schema version and part"

    def test_spdx_header_comment_wins_for_license(self):
        """Microchip puts the license in a comment above <device>, not in licenseText."""
        # Arrange
        text = SVD_SAMPLE.read_text(encoding="utf-8").replace(
            "<device schemaVersion",
            "<!-- SPDX-License-Identifier: Apache-2.0 -->\n<device schemaVersion",
            1,
        )

        # Act
        actual = svd.convert(text)["license"]

        # Assert
        assert actual == "Apache-2.0", "the SPDX tag, not the licenseText line"

    def test_round_trips_through_the_validator(self, doc):
        """A converter's output must be a valid device file, not merely plausible."""
        # Act
        device = parse_device(doc)

        # Assert
        assert len(device) == EXPECTED_COUNT
        by_name = {register.symbol.name: register for register in device.registers()}
        assert by_name["PORT_GROUP0_PMUX0"].symbol.type == "PMUXE:B1.0-3 PMUXO:B1.4-7", (
            "the type spec derives from the converted fields"
        )
        assert by_name["SERCOM0_DATA"].symbol.read_effect is True, "safety facts reach the Symbol"

    @pytest.mark.parametrize("text", ["", "not xml", "<root><peripherals/></root>", "<device/>"])
    def test_junk_is_empty_not_an_error(self, text):
        assert svd.convert(text) == {}, "never raises on odd input"


class TestRegistry:

    def test_svd_is_registered(self):
        assert FORMATS == ("svd",)
        assert CONVERTERS[0].kind == "device", "a device converter, never offered to /sym.import"

    def test_sniff_explicit_and_unknown(self):
        text = SVD_SAMPLE.read_text(encoding="utf-8")
        assert find_converter(text=text).format == "svd", "sniffed by its schema marker"
        assert find_converter(format_name="svd").format == "svd", "explicit"
        assert find_converter(text="hello") is None, "junk picks nothing"
        assert find_converter(format_name="nope") is None, "unknown key -> None"
