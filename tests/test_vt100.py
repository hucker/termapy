"""Tests for vt100.py - pure cfg -> miniterm setting mappings and config resolution.

The byte pump itself is the vendored pyserial miniterm (its own concern),
and raw passthrough is non-deterministic, so there is no CLI-gold entry.
These tests cover the pure mapping helpers and the demo config resolution.
"""

from __future__ import annotations

import io
import sys
from argparse import Namespace

from termapy.vt100 import (
    _line_ending_to_eol,
    _miniterm_settings,
    _resolve_cfg,
    run_vt100_mode,
)


class TestLineEndingToEol:
    def test_cr(self):
        # Assert
        assert _line_ending_to_eol("\r") == "cr", "CR maps to miniterm 'cr'"

    def test_lf(self):
        # Assert
        assert _line_ending_to_eol("\n") == "lf", "LF maps to miniterm 'lf'"

    def test_crlf(self):
        # Assert
        assert _line_ending_to_eol("\r\n") == "crlf", "CRLF maps to miniterm 'crlf'"

    def test_empty_falls_back_to_crlf(self):
        # Assert
        assert _line_ending_to_eol("") == "crlf", "no Enter translation -> default"

    def test_nul_falls_back_to_crlf(self):
        # Assert
        assert _line_ending_to_eol(chr(0)) == "crlf", "NUL has no eol mode -> default"

    def test_etx_falls_back_to_crlf(self):
        # Assert
        assert _line_ending_to_eol(chr(3)) == "crlf", "ETX has no eol mode -> default"


class TestMinitermSettings:
    def test_maps_all_fields(self):
        # Arrange
        cfg = {"echo_input": True, "line_ending": "\n", "encoding": "latin-1"}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": True, "eol": "lf", "encoding": "latin-1"}
        assert actual == expected, "cfg maps to miniterm settings"

    def test_defaults_when_keys_absent(self):
        # Arrange
        cfg = {}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": False, "eol": "cr", "encoding": "utf-8"}
        assert actual == expected, "missing keys fall back to cfg defaults"

    def test_echo_defaults_false(self):
        # Arrange
        cfg = {"line_ending": "\r"}

        # Act
        actual_echo = _miniterm_settings(cfg)["echo"]

        # Assert
        assert actual_echo is False, "echo_input absent -> False"


class TestResolveCfgDemo:
    def test_demo_flag_uses_vt100_device(self):
        # Arrange: the --vt100 --demo launch case
        args = Namespace(demo=True, config=None)

        # Act
        cfg = _resolve_cfg(args)

        # Assert
        actual = cfg["serial"]["port"]
        assert actual == "DEMO_VT100", "--demo selects the VT100 widget device"

    def test_vt100_demo_attr_uses_vt100_device(self):
        # Arrange: the /demo.vt100 TUI-switch case (demo flag cleared, attr set)
        args = Namespace(demo=False, config=None, _vt100_demo=True)

        # Act
        cfg = _resolve_cfg(args)

        # Assert
        actual = cfg["serial"]["port"]
        assert actual == "DEMO_VT100", "/demo.vt100 selects the VT100 widget device"


class _FakeStream:
    """Stand-in for sys.stdout with a .buffer (like a real text stream)."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


class TestRunVt100ModeStreamRestore:
    """Re-entry regression: miniterm's Windows Console swaps sys.stdout/stderr;
    run_vt100_mode must restore them so a second session does not crash."""

    def test_streams_restored_after_session(self, monkeypatch):
        # Arrange: pretend stdout/stderr are real text streams, then make a
        # fake Miniterm that (a) reads sys.stdout.buffer like the real Console
        # and (b) swaps the streams for objects without .buffer.
        sentinel_out = _FakeStream()
        sentinel_err = _FakeStream()
        monkeypatch.setattr(sys, "stdout", sentinel_out)
        monkeypatch.setattr(sys, "stderr", sentinel_err)

        class _FakeConsole:
            def cleanup(self):
                pass

        class _FakeMiniterm:
            def __init__(self, port, echo=False, eol="crlf"):
                _ = sys.stdout.buffer  # mimic ConsoleBase: fails if not restored
                self.console = _FakeConsole()
                self.serial = port
                sys.stdout = object()  # leftover wrapper with no .buffer
                sys.stderr = io.StringIO()
                self.exit_character = ""
                self.menu_character = ""

            def set_rx_encoding(self, _enc):
                pass

            def set_tx_encoding(self, _enc):
                pass

            def start(self):
                pass

            def join(self, transmit_only=False):
                pass

            def close(self):
                self.serial.close()

        monkeypatch.setattr(
            "termapy.vendor.serial.tools.miniterm.Miniterm", _FakeMiniterm
        )
        args = Namespace(demo=True, config=None)

        # Act: run twice; the second call constructs Miniterm again, which only
        # works if the first restored the streams.
        run_vt100_mode(args)
        result = run_vt100_mode(args)

        # Assert
        assert sys.stdout is sentinel_out, "stdout restored after the session"
        assert sys.stderr is sentinel_err, "stderr restored after the session"
        assert result is None, "launch-flag vt100 returns None (quit)"
