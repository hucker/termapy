"""Tests for the CLI-improvements branch.

Covers:

- The Textual/Rich import-discipline guard for ``termapy.cli_flags``.
- ``--json`` output shape for ``--ports`` and ``--chips``.
- ``--vid`` / ``--pid`` / ``--mfg`` / ``--sn`` filters.
- The driver column in ``--ports`` (Linux populates from sysfs;
  Windows from winreg; macOS leaves it null and the column drops).
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from termapy import cli_flags, port_control
from termapy.port_control import ChipFacts

pytestmark = pytest.mark.slow  # subprocess-spawning CLI flag tests


# ── Import guard: cli_flags must not pull in Textual / Rich / etc. ────────────


class TestCliFlagsImportDiscipline:
    """The CLI flag dispatch path must stay heavy-import-free.

    Documented constraint at the top of ``entry.py`` and ``cli_flags.py``:
    ``termapy --ports`` shouldn't pay the ~300 ms / 40 MB cost of
    Textual.  We enforce it mechanically here -- importing the module
    in a fresh subprocess and checking ``sys.modules`` is the only way
    to verify the discipline survives all transitive imports.
    """

    @pytest.mark.parametrize(
        "module",
        ["textual", "rich", "prompt_toolkit"],
    )
    def test_cli_flags_does_not_import(self, module):
        # Arrange + Act
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import termapy.cli_flags as _; "
                f"import sys; "
                f"print('YES' if {module!r} in sys.modules else 'NO')",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        # Assert
        actual = result.stdout.strip()
        expected = "NO"
        assert actual == expected, (
            f"importing termapy.cli_flags pulled in {module}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_entry_does_not_import(self):
        # Arrange + Act -- termapy.entry is the real cold path users hit.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import termapy.entry as _; "
                "import sys; "
                "loaded = sorted(m for m in ('textual', 'rich', "
                "'prompt_toolkit') if m in sys.modules); "
                "print(loaded)",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        # Assert
        actual = result.stdout.strip()
        expected = "[]"
        assert actual == expected, (
            "termapy.entry pulled in heavy UI deps at import time"
        )


# ── JSON output: --ports --json ───────────────────────────────────────────────


def _ports_args(**overrides) -> argparse.Namespace:
    """Build an argparse.Namespace mirroring entry.py defaults."""
    base = dict(ports="*", json=False, vid=None, pid=None, mfg=None, sn=None)
    base.update(overrides)
    return argparse.Namespace(**base)


def _chips_args(**overrides) -> argparse.Namespace:
    base = dict(chips="*", json=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def _make_facts(**kw) -> ChipFacts:
    """Build a ChipFacts with sensible defaults for tests."""
    base = dict(
        device="COM4",
        description="USB Serial Converter",
        manufacturer="FTDI",
        serial="AL01ABCD",
        vid_pid="0403:6001",
        model="FTDI FT232R",
        usb_speed="USB Full-Speed (1 ms min latency)",
        in_use="no",
        driver="ftdi_sio",
    )
    base.update(kw)
    return ChipFacts(**base)


class TestPortsJsonShape:
    def _run_ports_json(self, monkeypatch, facts_list):
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: facts_list,
        )
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))
        return buf.getvalue(), exc.value.code

    def test_full_record_shape(self, monkeypatch):
        # Arrange
        facts = _make_facts()

        # Act
        out, code = self._run_ports_json(monkeypatch, [facts])

        # Assert
        records = json.loads(out)
        assert code == 0, "exit 0 on at least one record"
        assert len(records) == 1, "one record for one fact"
        actual = records[0]
        expected_keys = {
            "device", "manufacturer", "manufacturer_raw", "vendor",
            "description", "chip", "speed",
            "vid", "pid", "vid_pid", "serial_number", "in_use", "driver",
            "location",
        }
        assert set(actual.keys()) == expected_keys, (
            f"every documented field present; got {sorted(actual)}"
        )

    def test_vid_pid_split_to_ints(self, monkeypatch):
        # Arrange
        facts = _make_facts(vid_pid="0403:6001")

        # Act
        out, _ = self._run_ports_json(monkeypatch, [facts])

        # Assert
        record = json.loads(out)[0]
        assert record["vid"] == 0x0403, "vid as int"
        assert record["pid"] == 0x6001, "pid as int"
        assert record["vid_pid"] == "0403:6001", "formatted vid_pid lowercase"

    def test_in_use_is_boolean(self, monkeypatch):
        # Arrange -- ChipFacts.in_use is a string ("yes"/"no"/"yes (this session)").
        # The JSON record exposes a clean boolean.
        facts_yes = _make_facts(in_use="yes")
        facts_no = _make_facts(device="COM5", in_use="no")

        # Act
        out, _ = self._run_ports_json(monkeypatch, [facts_yes, facts_no])

        # Assert
        records = json.loads(out)
        assert records[0]["in_use"] is True, "yes -> True"
        assert records[1]["in_use"] is False, "no -> False"

    def test_no_ports_returns_empty_array_and_exits_1(self, monkeypatch):
        # Arrange + Act
        out, code = self._run_ports_json(monkeypatch, [])

        # Assert
        actual = json.loads(out)
        assert actual == [], "empty array on no ports"
        assert code == 1, "exit 1 when nothing matched"

    def test_unknown_chip_serializes_as_null(self, monkeypatch):
        # Arrange
        facts = _make_facts(model="unknown")

        # Act
        out, _ = self._run_ports_json(monkeypatch, [facts])

        # Assert -- "unknown" becomes null so consumers can do
        # `select(.chip != null)` instead of string-matching the sentinel.
        record = json.loads(out)[0]
        assert record["chip"] is None, "unknown chip -> null"


# ── JSON output: --chips --json ───────────────────────────────────────────────


class TestChipsJsonShape:
    def test_record_shape(self):
        # Arrange + Act
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
            cli_flags.run_chips(_chips_args(json=True, chips="*"))

        # Assert
        records = json.loads(buf.getvalue())
        assert exc.value.code == 0, "exit 0"
        assert len(records) > 0, "chip table not empty"
        actual = records[0]
        expected_keys = {"vid", "pid", "vid_pid", "model", "speed", "max_baud"}
        assert set(actual.keys()) == expected_keys, "every field present"
        assert isinstance(actual["vid"], int), "vid is int"
        assert isinstance(actual["pid"], int), "pid is int"
        assert isinstance(actual["max_baud"], int), "max_baud is int"


# ── Multi-axis filters ────────────────────────────────────────────────────────


class TestMultiAxisFilters:
    @pytest.fixture
    def fleet(self, monkeypatch):
        """Three mock ports across two manufacturers + chip types."""
        ftdi = _make_facts(
            device="COM3", manufacturer="FTDI",
            vid_pid="0403:6001", serial="FT01",
        )
        cp210 = _make_facts(
            device="COM4", manufacturer="Silabs",
            vid_pid="10C4:EA60", serial="SL02", model="CP2102",
        )
        ch340 = _make_facts(
            device="COM5", manufacturer="WCH",
            vid_pid="1A86:7523", serial="CH03", model="CH340",
        )
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [ftdi, cp210, ch340],
        )
        return [ftdi, cp210, ch340]

    def _run(self, **flags):
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True, **flags))
        return json.loads(buf.getvalue())

    def test_vid_filter_hex(self, fleet):
        actual = self._run(vid="0403")
        assert len(actual) == 1, "single match"
        assert actual[0]["device"] == "COM3", "FTDI matched"

    def test_vid_filter_with_0x_prefix(self, fleet):
        actual = self._run(vid="0x10C4")
        assert len(actual) == 1, "single match with 0x prefix"
        assert actual[0]["device"] == "COM4", "Silabs matched"

    def test_pid_filter(self, fleet):
        actual = self._run(pid="7523")
        assert len(actual) == 1, "single PID match"
        assert actual[0]["device"] == "COM5", "CH340 matched"

    def test_mfg_substring_case_insensitive(self, fleet):
        actual = self._run(mfg="silabs")
        assert len(actual) == 1, "case-insensitive match"
        assert actual[0]["manufacturer"] == "Silabs", "Silabs row"

    def test_sn_exact_match_case_insensitive(self, fleet):
        actual = self._run(sn="ch03")
        assert len(actual) == 1, "single SN match"
        assert actual[0]["serial_number"] == "CH03", "WCH row"

    def test_filters_and_together(self, fleet):
        # Arrange + Act -- vid AND mfg both narrow; both must match.
        actual = self._run(vid="0403", mfg="ftdi")

        # Assert
        assert len(actual) == 1, "AND of two filters"
        assert actual[0]["device"] == "COM3", "FTDI matched"

    def test_filter_no_match_returns_empty_and_exits_1(self, fleet):
        # Arrange + Act
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True, vid="dead"))

        # Assert
        assert exc.value.code == 1, "exit 1 on no match"
        actual = json.loads(buf.getvalue())
        assert actual == [], "empty array"

    def test_invalid_vid_exits_2(self, fleet):
        # Arrange + Act
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc:
            cli_flags.run_ports(_ports_args(json=True, vid="not-hex"))

        # Assert -- exit 2 distinguishes "bad input" from "no match" (1).
        assert exc.value.code == 2, "exit 2 on invalid input"


# ── Driver column behavior in port_format ─────────────────────────────────────


class TestDriverColumn:
    def test_driver_visible_when_any_row_has_one(self):
        # Arrange -- one row with a driver, one without.  active_columns
        # keeps the column because some row has data.
        from termapy.port_format import active_columns, row_from_facts

        rows = [
            row_from_facts(_make_facts(driver="ftdi_sio")),
            row_from_facts(_make_facts(device="COM5", driver=None)),
        ]

        # Act
        actual = active_columns(rows)

        # Assert
        assert "driver" in actual, "driver column kept when any row has data"

    def test_driver_dropped_when_every_row_blank(self):
        # Arrange -- all rows missing driver (typical macOS case).
        from termapy.port_format import active_columns, row_from_facts

        rows = [
            row_from_facts(_make_facts(driver=None)),
            row_from_facts(_make_facts(device="COM5", driver=None)),
        ]

        # Act
        actual = active_columns(rows)

        # Assert
        assert "driver" not in actual, "driver column dropped when all blank"

    def test_driver_appears_in_json(self, monkeypatch):
        # Arrange
        facts = _make_facts(driver="ftdi_sio")
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [facts],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))

        # Assert
        record = json.loads(buf.getvalue())[0]
        assert record["driver"] == "ftdi_sio", "driver field present in JSON"


# ── Reserved-port synthesis (DEMO / DEMO_FAIL) ────────────────────────────────


class TestReservedPortSynthesis:
    """`--ports DEMO` returns a synthetic record so CI can exercise the
    CLI without hardware.  Bare `--ports` (no name) does NOT include
    DEMO in the listing -- it appears only when explicitly named, the
    same way pyserial's loop:// URL handler is reachable but not
    enumerated.
    """

    def test_demo_not_in_unfiltered_listing(self, monkeypatch):
        # Arrange -- no real ports; nothing should appear bare.
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))

        # Assert
        actual = json.loads(buf.getvalue())
        assert actual == [], "DEMO is not enumerated by default"
        assert exc.value.code == 1, "exit 1 on empty listing"

    def test_demo_synthesized_when_named(self, monkeypatch):
        # Arrange -- no real ports, but user names DEMO explicitly.
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True, ports="DEMO"))

        # Assert
        records = json.loads(buf.getvalue())
        assert exc.value.code == 0, "exit 0 -- record was synthesized"
        assert len(records) == 1, "one synthetic record"
        actual = records[0]
        assert actual["device"] == "DEMO", "device name preserved"
        assert actual["chip"] == "DEMO", "chip = DEMO sentinel"
        assert actual["manufacturer"] == "termapy", "manufacturer = termapy"
        assert actual["vid"] is None, "no VID for virtual port"
        assert actual["pid"] is None, "no PID for virtual port"
        assert actual["in_use"] is False, "synthesized as not-in-use"

    def test_demo_fail_synthesized_when_named(self, monkeypatch):
        # Arrange
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True, ports="DEMO_FAIL"))

        # Assert
        records = json.loads(buf.getvalue())
        assert len(records) == 1, "one synthetic record"
        assert records[0]["device"] == "DEMO_FAIL", "DEMO_FAIL preserved"

    def test_unknown_reserved_name_still_errors(self, monkeypatch):
        # Arrange -- a fake name that isn't reserved should not synthesize.
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True, ports="NOPE"))

        # Assert -- empty array, exit 1; the synthesis only triggers for
        # the reserved names DEMO and DEMO_FAIL.
        actual = json.loads(buf.getvalue())
        assert actual == [], "no synthesis for arbitrary names"
        assert exc.value.code == 1, "exit 1 on no match"

    def test_demo_real_port_takes_precedence(self, monkeypatch):
        # Arrange -- if the OS somehow enumerates a port named "DEMO"
        # (shouldn't happen, but fence it), the real one wins.
        real_demo = _make_facts(
            device="DEMO", manufacturer="real-vendor", driver="usbser"
        )
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [real_demo],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True, ports="DEMO"))

        # Assert -- real port's manufacturer wins, not the synthesized "termapy".
        record = json.loads(buf.getvalue())[0]
        assert record["manufacturer"] == "real-vendor", (
            "OS-enumerated record wins over synthesis"
        )


# ── Fast-gather path used by --watch ──────────────────────────────────────────


class TestFastGather:
    """The fast-gather variant skips per-port enrichment so --watch
    doesn't scale linearly with port count.

    Pre-existing _check_in_use opens each port via serial.Serial() to
    detect contention -- ~250 ms per port on Windows.  At 5 ports that
    was ~1.25 s of gather alone, plus the 500 ms sleep, giving the
    watch loop a 2-3 s reaction time.  fast=True drops it to ~5 ms
    regardless of port count.
    """

    def test_fast_gather_skips_in_use(self, monkeypatch):
        # Arrange -- if anything calls _check_in_use, the test fails.
        from termapy import port_control

        called = []

        def _trap(*a, **kw):
            called.append(("_check_in_use", a, kw))
            return "yes"

        monkeypatch.setattr(port_control, "_check_in_use", _trap)
        # Also fence off the platform extras so the test doesn't depend
        # on whether we're running on Linux or Windows.
        monkeypatch.setattr(port_control, "_gather_linux_extras",
                            lambda *a, **kw: None)
        monkeypatch.setattr(port_control, "_gather_windows_extras",
                            lambda *a, **kw: None)

        # Act
        port_control._gather_all_chip_facts(fast=True)

        # Assert
        assert called == [], (
            "fast=True must not call _check_in_use; "
            f"trap recorded: {called}"
        )

    def test_fast_gather_marks_in_use_none(self):
        # Arrange + Act
        from termapy import port_control
        from unittest.mock import MagicMock

        # Use a synthetic ListPortInfo since we don't want to depend on
        # the real OS port set for this assertion.
        p = MagicMock()
        p.device = "COM4"
        p.description = "USB Serial"
        p.manufacturer = "FTDI"
        p.product = None
        p.serial_number = "AL01"
        p.location = None
        p.interface = None
        p.vid = 0x0403
        p.pid = 0x6001

        facts = port_control._facts_from_port_info(p, fast=True)

        # Assert -- fast=True leaves in_use and permissions untouched
        # (None defaults from the dataclass) so callers can tell the
        # field wasn't gathered.
        assert facts.in_use is None, "fast=True leaves in_use unset"
        assert facts.permissions is None, "fast=True leaves permissions unset"
        # Identity fields populate normally.
        assert facts.device == "COM4", "device populated in fast mode"
        assert facts.vid_pid == "0403:6001", "vid_pid populated"

    def test_state_column_shows_dash_when_in_use_unknown(self):
        # Arrange
        from termapy.cli_flags import _state_of

        facts = ChipFacts(device="COM4", in_use=None)

        # Act
        actual = _state_of(facts)

        # Assert
        expected = "-"
        assert actual == expected, "fast gather marks state as unknown"


class TestLocationField:
    """`location` disambiguates devices with identical VID/PID/SN.

    Windows FTDI driver hides location from SetupAPI (pyserial returns
    None); ``_gather_windows_extras`` falls back to reading
    ``LocationInformation`` from the device's Enum registry key.
    """

    def test_location_present_in_json_record(self, monkeypatch):
        # Arrange
        facts = _make_facts(device="COM7")
        facts.location = "1-2.3"
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [facts],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))

        # Assert
        record = json.loads(buf.getvalue())[0]
        actual = record["location"]
        expected = "1-2.3"
        assert actual == expected, "location surfaced in JSON"

    def test_location_null_when_unknown(self, monkeypatch):
        # Arrange
        facts = _make_facts()
        facts.location = None
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [facts],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))

        # Assert
        record = json.loads(buf.getvalue())[0]
        assert record["location"] is None, "missing location -> null, not omitted"


class TestVendorLookup:
    """``vendor`` is the silicon-vendor name resolved from the VID via
    ``usb_vendor.USB_VENDORS``.  Independent of ``manufacturer`` (which
    is the descriptor / INF string) -- they often agree, and when they
    disagree the disagreement is itself useful diagnostic information.
    """

    def test_vendor_from_vid(self):
        # Arrange + Act
        from termapy.usb_vendor import vendor_for

        # Assert
        assert vendor_for(0x0403) == "FTDI", "FTDI VID resolves"
        assert vendor_for(0x10C4) == "Silicon Labs", "SiLabs VID resolves"
        assert vendor_for(0x04D8) == "Microchip", "Microchip VID resolves"

    def test_vendor_unknown_vid_returns_none(self):
        # Arrange + Act
        from termapy.usb_vendor import vendor_for

        # Assert
        assert vendor_for(0xDEAD) is None, "unknown VID -> None"
        assert vendor_for(None) is None, "None VID -> None"

    def test_microchip_via_microsoft_driver(self, monkeypatch):
        """The breadcrumb case: descriptor says Microsoft (driver INF),
        VID 0x04D8 says Microchip (silicon).  JSON exposes both so the
        engineer can see the layering.
        """
        # Arrange -- ChipFacts as gathered for the user's COM3-style port.
        from termapy.port_control import ChipFacts

        facts = ChipFacts(
            device="COM3",
            description="USB Serial Device (COM3)",
            manufacturer="Microsoft",   # from usbser.sys INF
            vendor="Microchip",         # from VID 0x04D8 lookup
            vid_pid="04D8:9036",
            model=None,
            in_use="no",
            driver="usbser",
        )
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [facts],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))

        # Assert -- all three fields visible, telling the full story.
        record = json.loads(buf.getvalue())[0]
        assert record["manufacturer_raw"] == "Microsoft", (
            "raw descriptor / INF string preserved"
        )
        assert record["manufacturer"] == "MSFT", (
            "manufacturer column-aliased to short form"
        )
        assert record["vendor"] == "Microchip", (
            "silicon vendor from VID lookup"
        )

    def test_ftdi_all_three_fields_agree(self, monkeypatch):
        # Arrange -- FTDI port: descriptor says FTDI, alias short-forms
        # to FTDI, VID 0x0403 also resolves to FTDI.  All three agree.
        from termapy.port_control import ChipFacts

        facts = ChipFacts(
            device="COM7",
            manufacturer="FTDI",
            vendor="FTDI",
            vid_pid="0403:6001",
            model="FTDI FT232R",
            in_use="no",
            driver="FTSER2K",
        )
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [facts],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))

        # Assert
        record = json.loads(buf.getvalue())[0]
        assert record["manufacturer_raw"] == "FTDI", "raw FTDI string"
        assert record["manufacturer"] == "FTDI", "alias passes through"
        assert record["vendor"] == "FTDI", "VID lookup matches"

    def test_normalize_windows_location_reorders_hub_first(self):
        """Windows' LocationInformation stores port before hub; we
        flip it so hub comes first, matching natural reading order
        ('the device is on hub 9, port 4').
        """
        # Arrange + Act + Assert
        from termapy.port_control import _normalize_windows_location

        actual = _normalize_windows_location("Port_#0004.Hub_#0009")
        expected = "Hub_#0009.Port_#0004"
        assert actual == expected, "hub before port for natural reading"

    def test_normalize_windows_location_passes_through_unknown_shape(self):
        """Multi-hub chains and pre-formatted strings shouldn't be
        mangled by the simple two-element reorder.
        """
        # Arrange + Act + Assert
        from termapy.port_control import _normalize_windows_location

        # Multi-element chain -- leave as-is.
        actual = _normalize_windows_location(
            "Port_#0001.Hub_#0001.Hub_#0002"
        )
        expected = "Port_#0001.Hub_#0001.Hub_#0002"
        assert actual == expected, "complex chains unchanged"

        # Linux-style passthrough.
        actual2 = _normalize_windows_location("1-8.3:x.1")
        assert actual2 == "1-8.3:x.1", "non-Windows format unchanged"

    def test_silicon_labs_short_form_via_alias(self, monkeypatch):
        # Arrange -- USB_VENDORS uses the canonical "Silicon Labs", but
        # narrow-column display goes through usb_mfg.mfg() which folds
        # it to "SiLabs".  Verify the JSON record's `manufacturer` (the
        # column-aliased one) gets the short form when the raw input
        # is "Silicon Labs".
        from termapy.port_control import ChipFacts

        facts = ChipFacts(
            device="COM4",
            manufacturer="Silicon Labs",
            vendor="Silicon Labs",
            vid_pid="10C4:EA60",
            model="CP2102",
            in_use="no",
        )
        monkeypatch.setattr(
            port_control,
            "_gather_all_chip_facts",
            lambda *a, **kw: [facts],
        )

        # Act
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf):
            cli_flags.run_ports(_ports_args(json=True))

        # Assert
        record = json.loads(buf.getvalue())[0]
        assert record["manufacturer_raw"] == "Silicon Labs", "raw preserved"
        assert record["manufacturer"] == "SiLabs", (
            "alias collapses to narrow column form"
        )
        assert record["vendor"] == "Silicon Labs", (
            "vendor stays canonical for machine consumers"
        )
