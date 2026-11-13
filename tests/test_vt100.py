"""Tests for vt100.py - the passthrough session on the engine, settings, config resolution, streams.

The console and key pump are the vendored pyserial miniterm (its own concern),
so there is no CLI-gold entry.  The session tests drive a PassthroughSession on
pyserial's loop:// port: what the device sends reaches the console verbatim and
the session log as lines, and what is typed is never logged.
"""

from __future__ import annotations

import io
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest

from termapy.defaults import default_cfg
from termapy.port_control import LOOPBACK_PORT
from termapy.session_log import SESSION_END, SESSION_START
from termapy.vt100 import (
    PassthroughSession,
    _miniterm_settings,
    _resolve_cfg,
    _tx_transforms,
    _vscode_key_hint,
    run_vt100_mode,
)


class TestMinitermSettings:
    def test_maps_all_fields(self):
        """echo, eol and encoding from the cfg carry straight into the passthrough settings."""
        # Arrange
        cfg = {"echo": True, "eol": "\n", "encoding": "latin-1"}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": True, "eol": "\n", "encoding": "latin-1"}
        assert actual == expected, "cfg maps to passthrough settings"

    def test_defaults_when_keys_absent(self):
        """An empty cfg yields no echo, a CR line ending and UTF-8 encoding."""
        # Arrange
        cfg = {}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": False, "eol": "\r", "encoding": "utf-8"}
        assert actual == expected, "missing keys fall back to cfg defaults"

    def test_echo_defaults_false(self):
        """A cfg without an echo key never turns local echo on."""
        # Arrange
        cfg = {"eol": "\r"}

        # Act
        actual_echo = _miniterm_settings(cfg)["echo"]

        # Assert
        assert actual_echo is False, "echo_input absent -> False"


def _apply_tx(transforms, text):
    for transform in transforms:
        text = transform.tx(text)
    return text


class TestTxTransforms:
    """The one transform on the typed side: Enter becomes the line ending, nothing else changes."""

    def test_tx_translates_only_enter(self):
        """Enter becomes the cfg line ending; arrow-key escapes and text go out untouched."""
        # Arrange
        tx = _tx_transforms("\r")

        # Act
        enter = _apply_tx(tx, "\n")
        typed = _apply_tx(tx, "AT+VER\n")
        arrow = _apply_tx(tx, "\x1b[A")

        # Assert
        assert enter == "\r", "Enter translates to the cfg line ending"
        assert typed == "AT+VER\r", "Enter after typed text becomes the line ending"
        assert arrow == "\x1b[A", "non-Enter keys (arrow seq) pass through verbatim"

    def test_tx_honors_crlf_line_ending(self):
        """A CRLF line ending sends both bytes on Enter, not just CR."""
        # Arrange
        tx = _tx_transforms("\r\n")

        # Act
        actual = _apply_tx(tx, "x\n")

        # Assert
        assert actual == "x\r\n", "line_ending CRLF sends CR+LF on Enter"


def _loop_cfg() -> dict:
    """A default config on the software loopback, so a write comes straight back as RX."""
    cfg = default_cfg()
    cfg["serial"]["port"] = LOOPBACK_PORT
    return cfg


def _wait_for(condition, what: str, timeout_s: float = 3.0) -> None:
    """Poll ``condition`` until it holds; fail naming ``what`` when it does not in time."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {what}")


def _entries(log_path: Path) -> list[tuple[str, str]]:
    """The session log as ``(marker, text)`` pairs, timestamps dropped."""
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        marker, _, text = line.partition("] ")[2].partition(" ")
        entries.append((marker, text))
    return entries


class _BadPort:
    """A port whose first read fails, the engine's lost-device shape."""

    is_open = True
    in_waiting = 1

    def read(self, _n):
        raise OSError("read failed")

    def close(self):
        self.is_open = False


@pytest.mark.sw_loopback
@pytest.mark.rule("vt100-on-engine")
class TestPassthroughSession:
    """--vt100 on SerialEngine: raw bytes to the console, received lines to the TUI's log, keys unlogged."""

    def test_received_lines_land_in_the_log_and_keys_do_not(self, tmp_path):
        """Each received line is a `<` entry in the config's log, in order, across writes and within one; nothing typed is logged."""
        # Arrange
        config_path = tmp_path / "loop.cfg"
        session = PassthroughSession(_loop_cfg(), str(config_path))
        assert session.connect(), session.engine.last_error
        console: list[str] = []
        lost: list[str] = []
        session.start(console_write=console.append, on_lost=lost.append)
        log_path = tmp_path / "loop.log"
        assert session.session_log_path() == log_path, "the TUI's file, <cfg>.log beside the config"

        # Act: one line, then two in one write (the loopback echoes each key back)
        session.write(b"hello\r\n")
        session.write(b"two\r\nthree\r\n")
        _wait_for(
            lambda: "three" in log_path.read_text(encoding="utf-8"), "the third line in the log"
        )
        session.close()

        # Assert
        entries = _entries(log_path)
        received = [text for marker, text in entries if marker == "<"]
        assert received == ["hello", "two", "three"], "every received line, in order, as a < entry"
        assert entries[0] == ("#", SESSION_START), "the log opens with Session Start"
        assert entries[1] == ("#", f"VT100 passthrough: {LOOPBACK_PORT}"), (
            "the session says which mode wrote it"
        )
        assert entries[-1] == ("#", SESSION_END), "close ends the log with Session End"
        assert all(marker != ">" for marker, _ in entries), (
            "nothing typed is logged: in passthrough the device's echo is the record"
        )
        assert lost == [], "a clean session reports no lost port"

    def test_received_bytes_reach_the_console_verbatim(self, tmp_path):
        """Escape sequences, bare-CR redraws and control bytes reach the console unchanged, and the log holds them ANSI-stripped."""
        # Arrange: canonical VT100/ANSI byte sequences a real device emits
        samples = (
            "\x1b[10;5H",  # cursor to row 10, col 5
            "\x1b[1;31mERROR\x1b[0m\r\n",  # bold red text
            "load 10%\rload 50%\rload 100%\r",  # bare-CR progress redraw
            "\x07\x08\t",  # BEL, BS, TAB control bytes
            "line one\r\nline two\r\n",  # CRLF line endings
        )
        expected = "".join(samples)
        config_path = tmp_path / "loop.cfg"
        session = PassthroughSession(_loop_cfg(), str(config_path))
        assert session.connect(), session.engine.last_error
        console: list[str] = []
        session.start(console_write=console.append, on_lost=lambda _d: None)

        # Act
        for sample in samples:
            session.write(sample.encode())
        _wait_for(
            lambda: "".join(console).endswith("line two\r\n"), "the last sample on the console"
        )
        session.close()

        # Assert
        actual = "".join(console)
        assert actual == expected, "the console gets every received byte as sent, nothing rewritten"
        log_text = (tmp_path / "loop.log").read_text(encoding="utf-8")
        assert "< ERROR" in log_text, "the log holds the line with its color codes stripped"
        assert "\x1b" not in log_text, "no escape sequence reaches the log"

    def test_lost_port_is_reported_once_and_the_log_still_ends(self, tmp_path):
        """A port that fails is reported to the caller once and logged once, and close still writes Session End."""
        # Arrange
        config_path = tmp_path / "bad.cfg"
        session = PassthroughSession(default_cfg(), str(config_path), open_fn=lambda _c: _BadPort())
        assert session.connect(), "the open itself succeeds; the read is what fails"
        lost: list[str] = []
        session.start(console_write=lambda _t: None, on_lost=lost.append)

        # Act
        _wait_for(lambda: bool(lost), "the lost-port report")
        session.write(b"x")  # a key after the loss must not report it again
        session.close()

        # Assert
        assert lost == ["Serial error: OSError: read failed"], (
            "one report, the reader's, although the engine also calls on_disconnect"
        )
        entries = _entries(tmp_path / "bad.log")
        assert ("#", "Serial error: OSError: read failed") in entries, (
            "the loss is in the transcript"
        )
        assert entries[-1] == ("#", SESSION_END), "the log ends cleanly after a lost port"


class TestResolveCfgDemo:
    def test_demo_flag_uses_vt100_device(self):
        """The --vt100 --demo launch connects to the VT100 demo device, not the plain one."""
        # Arrange: the --vt100 --demo launch case
        args = Namespace(demo=True, config=None)

        # Act
        cfg = _resolve_cfg(args)

        # Assert
        actual = cfg["serial"]["port"]
        assert actual == "DEMO_VT100", "--demo selects the VT100 widget device"

    def test_vt100_demo_attr_uses_vt100_device(self):
        """Switching from the TUI via /demo.vt100 also lands on the VT100 demo device."""
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
        """stdout and stderr are put back after a session so a second launch does not crash."""
        # Arrange: pretend stdout/stderr are real text streams, then make a
        # fake key pump that (a) reads sys.stdout.buffer like the real Console
        # and (b) swaps the streams for objects without .buffer.
        sentinel_out = _FakeStream()
        sentinel_err = _FakeStream()
        monkeypatch.setattr(sys, "stdout", sentinel_out)
        monkeypatch.setattr(sys, "stderr", sentinel_err)

        class _FakeConsole:
            def write(self, _text):
                pass

            def cancel(self):
                pass

            def cleanup(self):
                pass

        class _FakePump:
            def __init__(self, link, echo=False):
                _ = sys.stdout.buffer  # mimic ConsoleBase: fails if not restored
                self.console = _FakeConsole()
                self.serial = link
                sys.stdout = object()  # leftover wrapper with no .buffer
                sys.stderr = io.StringIO()
                self.exit_character = ""
                self.menu_character = ""
                self.tx_transformations = []

            def set_tx_encoding(self, _enc):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def join(self, transmit_only=False):
                pass

        monkeypatch.setattr("termapy.vt100._KeyPump", _FakePump)
        args = Namespace(demo=True, config=None)

        # Act: run twice; the second call constructs the pump again, which only
        # works if the first restored the streams.
        run_vt100_mode(args)
        result = run_vt100_mode(args)

        # Assert
        assert sys.stdout is sentinel_out, "stdout restored after the session"
        assert sys.stderr is sentinel_err, "stderr restored after the session"
        assert result is None, "launch-flag vt100 returns None (quit)"


class TestVscodeKeyHint:
    def test_hint_shown_in_vscode(self, monkeypatch):
        """Under VS Code the user is told about sendKeybindingsToShell for key capture."""
        # Arrange
        monkeypatch.setenv("TERM_PROGRAM", "vscode")

        # Act
        actual = _vscode_key_hint({})

        # Assert
        assert actual is not None, "VS Code terminal gets the key-capture tip"
        assert "sendKeybindingsToShell" in actual, "tip names the fix setting"

    def test_no_hint_outside_vscode(self, monkeypatch):
        """Terminals other than VS Code get no key-capture tip."""
        # Arrange
        monkeypatch.delenv("TERM_PROGRAM", raising=False)

        # Act
        actual = _vscode_key_hint({})

        # Assert
        assert actual is None, "no tip when not in a VS Code terminal"

    def test_opt_out_suppresses_hint(self, monkeypatch):
        """vt100_hint=false silences the tip even inside VS Code."""
        # Arrange -- under VS Code, but the user disabled the hint
        monkeypatch.setenv("TERM_PROGRAM", "vscode")

        # Act
        actual = _vscode_key_hint({"vt100_hint": False})

        # Assert
        assert actual is None, "vt100_hint=false hides the tip even under VS Code"
