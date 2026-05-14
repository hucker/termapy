"""Tests for usb_vendor lookup + scripts/refresh_usb_ids.py transformer.

Two-tier lookup model:

  1. Curated short-form names in ``USB_VENDORS`` (e.g. "FTDI", "SiLabs").
  2. Generated canonical names in ``USB_VENDORS_FULL`` (e.g. "Future
     Technology Devices International, Ltd") as a fallback.

Tests below verify both tiers, the fall-through ordering (curated
beats generated), and the transformer's usb.ids parser against a
hand-written fixture.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Make scripts/ importable so we can unit-test the transformer.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ── parse_vendors (transformer) ───────────────────────────────────────────────


@pytest.fixture
def usb_ids_fixture():
    """Hand-written usb.ids fragment exercising every line type the parser
    is expected to handle.
    """
    return (
        "# Comment line, must be skipped\n"
        "#\n"
        "0403  Future Technology Devices International, Ltd\n"
        "\t6001  FT232 Serial (UART) IC\n"
        "\t\t00  no subclass\n"
        "\t6014  FT232H Single HS USB-UART/FIFO IC\n"
        "\n"
        "04D8  Microchip Technology, Inc.\n"
        "\t9036  Some product\n"
        "10C4  Silicon Labs\n"
        "\n"
        "# C 00  (Unused)\n"
        "C 00  (Defined at Interface level)\n"
        "\t00  No Subclass\n"
        "C 01  Audio\n"
        "AT 00  ATM Network\n"
        "HID 00  None\n"
        "\n"
        # Edge: lowercase hex
        "abcd  Lowercase Hex Vendor\n"
        # Edge: name with non-ASCII characters
        "1234  Acme Ä GmbH\n"
    )


class TestParseVendors:
    def test_parses_vendor_lines(self, usb_ids_fixture):
        # Arrange
        from refresh_usb_ids import parse_vendors

        # Act
        actual = parse_vendors(usb_ids_fixture)

        # Assert
        assert actual[0x0403] == "Future Technology Devices International, Ltd", (
            "FTDI line parsed"
        )
        assert actual[0x04D8] == "Microchip Technology, Inc.", "Microchip parsed"
        assert actual[0x10C4] == "Silicon Labs", "SiLabs parsed"

    def test_skips_product_lines(self, usb_ids_fixture):
        # Arrange
        from refresh_usb_ids import parse_vendors

        # Act
        actual = parse_vendors(usb_ids_fixture)

        # Assert -- product PIDs (tab-indented) must NOT appear as vendors.
        assert 0x6001 not in actual, "FT232 product not treated as vendor"
        assert 0x6014 not in actual, "FT232H product not treated as vendor"
        assert 0x9036 not in actual, "Microchip product not treated as vendor"

    def test_skips_class_section_lines(self, usb_ids_fixture):
        # Arrange -- "C 00  (Defined at Interface level)" etc. don't have
        # a 4-hex prefix; the regex rejects them.  Also "AT 00" / "HID 00".
        from refresh_usb_ids import parse_vendors

        # Act
        actual = parse_vendors(usb_ids_fixture)

        # Assert -- nothing keyed on 0x0000 or 0x0001 from the C/AT/HID
        # lines.  All real vendors above start at 0x0403 or higher.
        assert 0x0000 not in actual, "C 00 not parsed as vendor 0x0000"
        assert 0x0001 not in actual, "C 01 not parsed as vendor 0x0001"

    def test_skips_comments_and_blanks(self, usb_ids_fixture):
        # Arrange + Act
        from refresh_usb_ids import parse_vendors

        actual = parse_vendors(usb_ids_fixture)

        # Assert -- the comment lines never produced an entry.  Counted
        # vendors should match exactly the real vendor lines.
        expected_count = 5  # 0403, 04D8, 10C4, abcd, 1234
        assert len(actual) == expected_count, "comments and blanks ignored"

    def test_lowercase_hex(self, usb_ids_fixture):
        # Arrange
        from refresh_usb_ids import parse_vendors

        # Act
        actual = parse_vendors(usb_ids_fixture)

        # Assert
        assert actual[0xABCD] == "Lowercase Hex Vendor", (
            "lowercase hex VID parsed"
        )

    def test_non_ascii_name(self, usb_ids_fixture):
        # Arrange
        from refresh_usb_ids import parse_vendors

        # Act
        actual = parse_vendors(usb_ids_fixture)

        # Assert -- non-ASCII names round-trip cleanly.
        assert actual[0x1234] == "Acme Ä GmbH", (
            "non-ASCII vendor name preserved"
        )


# ── emit_python_module ────────────────────────────────────────────────────────


class TestEmitPythonModule:
    def test_emits_valid_python(self):
        # Arrange
        from refresh_usb_ids import emit_python_module

        vendors = {
            0x0403: "Future Technology Devices International, Ltd",
            0x10C4: "Silicon Labs",
        }

        # Act
        text = emit_python_module(vendors, "test://source")

        # Assert
        # Compile to confirm it's valid Python.
        compile(text, "<generated>", "exec")
        # Spot-check key content.
        assert "USB_VENDORS_FULL" in text, "module exports the dict name"
        assert "0x0403" in text, "FTDI VID present"
        assert "Silicon Labs" in text, "SiLabs name present"
        assert "Generated:" in text, "header includes generation date"

    def test_entries_are_sorted(self):
        # Arrange -- pass entries out of order; output should be sorted.
        from refresh_usb_ids import emit_python_module

        vendors = {0xFFFF: "Last", 0x0001: "First", 0x10C4: "Middle"}

        # Act
        text = emit_python_module(vendors, "test://")

        # Assert -- find the line offsets for each VID; First < Middle < Last.
        first = text.index("0x0001")
        middle = text.index("0x10C4")
        last = text.index("0xFFFF")
        assert first < middle < last, "entries sorted by VID for stable diffs"

    def test_handles_quotes_in_name(self):
        # Arrange -- vendor names with quotes / backslashes round-trip via
        # repr().
        from refresh_usb_ids import emit_python_module

        vendors = {0xCAFE: 'Some "Quoted" Name'}

        # Act
        text = emit_python_module(vendors, "test://")

        # Assert -- compile succeeds; loading the module gives the right value.
        ns: dict = {}
        exec(text, ns)
        actual = ns["USB_VENDORS_FULL"][0xCAFE]
        expected = 'Some "Quoted" Name'
        assert actual == expected, "quoted strings emitted via repr round-trip"


# ── usb_vendor.vendor_for() lookup ────────────────────────────────────────────


class TestVendorLookupCurated:
    """Curated USB_VENDORS table wins for known short forms."""

    def test_curated_short_form_used(self):
        # Arrange + Act
        from termapy.usb import vendor_for

        # Assert -- curated table maps 0x0403 to the short "FTDI",
        # not the canonical "Future Technology Devices International, Ltd".
        assert vendor_for(0x0403) == "FTDI", "curated short form wins"

    def test_curated_silabs(self):
        from termapy.usb import vendor_for

        assert vendor_for(0x10C4) == "Silicon Labs", "SiLabs curated"

    def test_curated_microchip(self):
        from termapy.usb import vendor_for

        assert vendor_for(0x04D8) == "Microchip", "Microchip curated"

    def test_none_input(self):
        from termapy.usb import vendor_for

        assert vendor_for(None) is None, "None VID returns None"


class TestVendorLookupFallback:
    """USB_VENDORS_FULL fills in vendors the curated table doesn't cover."""

    def test_fallback_to_full_table(self):
        # Arrange -- pick a VID that exists in usb.ids but NOT in our
        # curated USB_VENDORS table.  Cypress's older USB controllers
        # use 0x04B4 (curated; "Cypress"); Logitech 0x046D is curated.
        # Try a vendor we don't curate but is well-known: Yubico (0x1050).
        from termapy.usb import vendor_for, USB_VENDORS

        assert 0x1050 not in USB_VENDORS, "fixture pre-condition: 0x1050 uncurated"

        # Act
        actual = vendor_for(0x1050)

        # Assert -- canonical name from usb.ids.
        assert actual is not None, "fallback found a vendor"
        assert "Yubico" in actual or "Logitech" in actual, (
            f"Yubico expected; got {actual!r}"
        )

    def test_truly_unknown_vid_returns_none(self):
        from termapy.usb import vendor_for

        # Pick a VID very unlikely to be in either table.
        actual = vendor_for(0xFFFE)
        assert actual is None, "unknown VID returns None"

    def test_full_table_has_thousands_of_entries(self):
        """Sanity: the bundled full table should be substantially larger
        than the curated one.
        """
        from termapy.usb._vendors_full import USB_VENDORS_FULL
        from termapy.usb import USB_VENDORS

        actual = len(USB_VENDORS_FULL)
        assert actual > 1000, (
            f"full table looks too small ({actual} entries); refresh failed?"
        )
        assert actual > len(USB_VENDORS) * 30, (
            "full table should dwarf curated table"
        )


# ── Generated-module metadata ────────────────────────────────────────────────


class TestGeneratedMetadata:
    """The generator emits constants for /term.usb_db introspection."""

    def test_generated_date_present(self):
        from termapy.usb import _vendors_full as _usb_vendor_full

        actual = _usb_vendor_full.GENERATED_DATE
        # ISO date format: YYYY-MM-DD.
        import re

        assert re.match(r"^\d{4}-\d{2}-\d{2}$", actual), (
            f"GENERATED_DATE looks wrong: {actual!r}"
        )

    def test_source_url_present(self):
        from termapy.usb import _vendors_full as _usb_vendor_full

        actual = _usb_vendor_full.SOURCE_URL
        assert "usb.ids" in actual, (
            f"SOURCE_URL should reference usb.ids; got {actual!r}"
        )



# ── /term.usb_db handler ──────────────────────────────────────────────────────


class TestTermUsbDbHandler:
    """The /term.usb_db command reports local metadata only -- no network."""

    def test_handler_writes_expected_fields(self):
        # Arrange
        from termapy.builtins.commands.term import _handler_usb_db
        from termapy.plugins import IOHandle, PluginContext

        out: list[str] = []
        ctx = PluginContext(
            io=IOHandle(
                _write=lambda t, c=None: out.append(t),
                _write_markup=lambda t: out.append(t),
            ),
        )

        # Act
        result = _handler_usb_db(ctx, "")

        # Assert
        assert result.success, "/term.usb_db should succeed"
        joined = "\n".join(out)
        for label in ("curated", "full_table", "generated", "source", "path"):
            assert label in joined, f"output missing {label}: {joined!r}"
        # The trailing update hint must be present and point at the
        # package-upgrade path (not the dev script, which PyPI users
        # don't have access to).
        assert "upgrade" in joined.lower() or "update" in joined.lower(), (
            "output should include an update hint"
        )
        assert "scripts/refresh_usb_ids" not in joined, (
            "output must not point at dev-only scripts; PyPI users "
            "get the package, not the scripts/ directory"
        )

    def test_handler_returns_full_count_as_value(self):
        """CmdResult.value carries the full-table count so scripts can
        read it via .quiet/.silent and use the integer programmatically.
        """
        # Arrange
        from termapy.builtins.commands.term import _handler_usb_db
        from termapy.usb._vendors_full import USB_VENDORS_FULL
        from termapy.plugins import PluginContext

        from termapy.plugins import IOHandle
        ctx = PluginContext(
            io=IOHandle(
                _write=lambda t, c=None: None,
                _write_markup=lambda t: None,
            ),
        )

        # Act
        result = _handler_usb_db(ctx, "")

        # Assert
        actual = result.value
        expected = str(len(USB_VENDORS_FULL))
        assert actual == expected, (
            f"value should be the full-table count ({expected}); "
            f"got {actual!r}"
        )

    def test_handler_does_not_make_network_calls(self, monkeypatch):
        """Sanity guard: the handler must never reach for the network."""
        # Arrange -- monkeypatch urllib so any network call would fail loudly.
        import urllib.request

        def _trap(*args, **kwargs):
            raise AssertionError(
                "/term.usb_db must not make HTTP requests; "
                f"caught urlopen({args!r}, {kwargs!r})"
            )

        monkeypatch.setattr(urllib.request, "urlopen", _trap)

        from termapy.builtins.commands.term import _handler_usb_db
        from termapy.plugins import PluginContext

        from termapy.plugins import IOHandle
        ctx = PluginContext(
            io=IOHandle(
                _write=lambda t, c=None: None,
                _write_markup=lambda t: None,
            ),
        )

        # Act -- if the handler ever calls urlopen, this raises.
        result = _handler_usb_db(ctx, "")

        # Assert
        assert result.success, "handler ran offline cleanly"
