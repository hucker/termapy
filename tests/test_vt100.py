"""Tests for vt100.py - settings/transform mappings, config resolution, streams.

The byte pump itself is the vendored pyserial miniterm (its own concern), so
there is no CLI-gold entry. The transform tests are the ground-truth check that
the passthrough relays device bytes verbatim (no real device required).
"""

from __future__ import annotations

import io
import sys
from argparse import Namespace

from termapy.vt100 import (
    _miniterm_settings,
    _passthrough_transforms,
    _resolve_cfg,
    _vscode_key_hint,
    run_vt100_mode,
)


class TestMinitermSettings:
    def test_maps_all_fields(self):
        # Arrange
        cfg = {"echo_input": True, "line_ending": "\n", "encoding": "latin-1"}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": True, "line_ending": "\n", "encoding": "latin-1"}
        assert actual == expected, "cfg maps to passthrough settings"

    def test_defaults_when_keys_absent(self):
        # Arrange
        cfg = {}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": False, "line_ending": "\r", "encoding": "utf-8"}
        assert actual == expected, "missing keys fall back to cfg defaults"

    def test_echo_defaults_false(self):
        # Arrange
        cfg = {"line_ending": "\r"}

        # Act
        actual_echo = _miniterm_settings(cfg)["echo"]

        # Assert
        assert actual_echo is False, "echo_input absent -> False"


def _apply_rx(transforms, text):
    for t in transforms:
        text = t.rx(text)
    return text


def _apply_tx(transforms, text):
    for t in transforms:
        text = t.tx(text)
    return text


class TestPassthroughTransforms:
    """Ground truth for 'make it work right' without real hardware: a VT100
    passthrough must relay device bytes verbatim and translate only Enter."""

    # Canonical VT100/ANSI byte sequences a real device emits.
    _SAMPLES = (
        "\x1b[2J",                   # clear screen
        "\x1b[10;5H",                # cursor to row 10, col 5
        "\x1b[1;31mERROR\x1b[0m",    # bold red text
        "line one\r\nline two\r\n",  # CRLF line endings
        "load 10%\rload 50%\rload 100%\r",  # bare-CR progress redraw
        "\x07\x08\t",                # BEL, BS, TAB control bytes
    )

    def test_rx_is_byte_verbatim(self):
        # Arrange
        rx, _tx = _passthrough_transforms("\r")

        # Act / Assert: every received sequence reaches the terminal unchanged.
        for sample in self._SAMPLES:
            actual = _apply_rx(rx, sample)
            assert actual == sample, f"RX must pass {sample!r} through verbatim"

    def test_tx_translates_only_enter(self):
        # Arrange
        _rx, tx = _passthrough_transforms("\r")

        # Act
        enter = _apply_tx(tx, "\n")
        typed = _apply_tx(tx, "AT+VER\n")
        arrow = _apply_tx(tx, "\x1b[A")

        # Assert
        assert enter == "\r", "Enter translates to the cfg line ending"
        assert typed == "AT+VER\r", "Enter after typed text becomes the line ending"
        assert arrow == "\x1b[A", "non-Enter keys (arrow seq) pass through verbatim"

    def test_tx_honors_crlf_line_ending(self):
        # Arrange
        _rx, tx = _passthrough_transforms("\r\n")

        # Act
        actual = _apply_tx(tx, "x\n")

        # Assert
        assert actual == "x\r\n", "line_ending CRLF sends CR+LF on Enter"


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


class TestVscodeKeyHint:
    def test_hint_shown_in_vscode(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("TERM_PROGRAM", "vscode")

        # Act
        actual = _vscode_key_hint({})

        # Assert
        assert actual is not None, "VS Code terminal gets the key-capture tip"
        assert "sendKeybindingsToShell" in actual, "tip names the fix setting"

    def test_no_hint_outside_vscode(self, monkeypatch):
        # Arrange
        monkeypatch.delenv("TERM_PROGRAM", raising=False)

        # Act
        actual = _vscode_key_hint({})

        # Assert
        assert actual is None, "no tip when not in a VS Code terminal"

    def test_opt_out_suppresses_hint(self, monkeypatch):
        # Arrange -- under VS Code, but the user disabled the hint
        monkeypatch.setenv("TERM_PROGRAM", "vscode")

        # Act
        actual = _vscode_key_hint({"vt100_hint": False})

        # Assert
        assert actual is None, "vt100_hint=false hides the tip even under VS Code"
