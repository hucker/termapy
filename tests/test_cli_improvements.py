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
            "device", "manufacturer", "description", "chip", "speed",
            "vid", "pid", "vid_pid", "serial_number", "in_use", "driver",
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
