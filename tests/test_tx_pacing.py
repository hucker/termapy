"""Tests for transmit pacing -- tx_char_delay_ms and tx_line_delay_ms on every device line, never on binary.

``SerialHandle.send`` is the one text-line path (registry
``tx-text-one-path``).  ``TestSend`` drives it over a recording write;
``TestPaths`` drives a real ``CLITerminal`` on the DEMO port and watches
what reaches the port through a TX observer, so each route a line can take
-- typed, ``/term.send``, request mode, bare Enter, a script -- is shown to
go out paced, and ``/raw`` and ``/proto.send`` are shown not to.
"""

from __future__ import annotations

import json
import time

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins.handles.serial import SerialHandle

pytestmark = pytest.mark.rule("tx-text-one-path")


def _handle(written: list[bytes], **cfg) -> SerialHandle:
    """A SerialHandle writing into ``written``, with ``cfg`` over CR + utf-8."""
    settings = {"eol": "\r", "encoding": "utf-8", **cfg}
    return SerialHandle(write=written.append, _cfg=lambda: settings)


class TestSend:
    def test_off_by_default_a_line_is_one_write(self):
        """With no delay set a line goes out as one write, its ending included, as before pacing existed."""
        # Arrange
        written: list[bytes] = []

        # Act
        _handle(written).send("AT")

        # Assert
        assert written == [b"AT\r"]

    @pytest.mark.timing
    def test_a_char_delay_writes_each_character_on_its_own(self):
        """tx_char_delay_ms sends one character per write and waits after each."""
        # Arrange
        written: list[bytes] = []
        handle = _handle(written, tx_char_delay_ms=5)

        # Act -- perf_counter, not monotonic: the delays are real-time sleeps,
        # and Windows' monotonic before Python 3.13 ticks in 15.6 ms steps.
        start = time.perf_counter()
        handle.send("AT")
        elapsed = time.perf_counter() - start

        # Assert
        assert written == [b"A", b"T", b"\r"], "each character, the line ending too, in order"
        assert elapsed >= 0.015, "three waits of 5 ms"

    @pytest.mark.timing
    def test_a_line_delay_waits_after_the_line(self):
        """tx_line_delay_ms leaves the line in one write and waits after it."""
        # Arrange
        written: list[bytes] = []
        handle = _handle(written, tx_line_delay_ms=30)

        # Act -- perf_counter, as above: a real-time sleep, timed finely.
        start = time.perf_counter()
        handle.send("AT")
        elapsed = time.perf_counter() - start

        # Assert
        assert written == [b"AT\r"], "no character delay, so one write"
        assert elapsed >= 0.03, "the line delay ran"

    def test_a_multi_byte_character_goes_out_whole(self):
        """A character that encodes to several bytes is one write, never split across a delay."""
        # Arrange
        written: list[bytes] = []

        # Act
        _handle(written, tx_char_delay_ms=1).send("é")

        # Assert
        assert written == ["é".encode(), b"\r"]

    def test_a_stateful_encoding_writes_its_preamble_once(self):
        """Paced, a line in UTF-16 is the same bytes as unpaced: one byte-order mark, not one per character."""
        # Arrange
        paced: list[bytes] = []

        # Act
        _handle(paced, encoding="utf-16", tx_char_delay_ms=1).send("AT")

        # Assert
        actual = b"".join(paced)
        expected = "AT\r".encode("utf-16")
        assert actual == expected, "the encoder runs once over the line"

    def test_a_line_the_encoding_cannot_take_sends_nothing(self):
        """A character the encoding cannot take fails the send before any of the line goes out."""
        # Arrange
        written: list[bytes] = []

        # Act / Assert
        with pytest.raises(UnicodeError):
            _handle(written, encoding="ascii", tx_char_delay_ms=1).send("café")
        assert written == [], "not even the characters before it"

    def test_two_lines_in_a_row_each_go_out_whole(self):
        """A second paced line starts clean: both lines arrive complete and in order."""
        # Arrange
        written: list[bytes] = []
        handle = _handle(written, tx_char_delay_ms=1)

        # Act
        handle.send("A")
        handle.send("B")

        # Assert
        assert written == [b"A", b"\r", b"B", b"\r"]


# ── Every route a device line takes ──────────────────────────────────────────


@pytest.fixture
def cli(tmp_path):
    """A started CLITerminal connected to the DEMO port."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO"}, "eol": "\r"}
    path = tmp_path / "pace" / "pace.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg), encoding="utf-8")
    terminal = CLITerminal(cfg, str(path), no_color=True, term_width=120)
    # A temp global root keeps the checkout's termapy_cfg/plugin/ out.
    terminal.repl.global_root = tmp_path
    terminal._fire_app_start()
    assert terminal._connect(), "DEMO port should connect"
    yield terminal
    terminal._disconnect()


def _sent(cli, line: str, **session) -> list[bytes]:
    """Dispatch ``line`` with ``session`` cfg values set; return each write that reached the port."""
    for key, value in session.items():
        cli.repl._apply_cfg(key, value)
    written: list[bytes] = []
    with cli.ctx.serial.tx_observer(written.append):
        cli._dispatch(line)
    return written


class TestPaths:
    def test_a_typed_device_line_is_paced(self, cli):
        """A bare line typed at the prompt goes out one character per write."""
        # Act
        actual = _sent(cli, "AT", tx_char_delay_ms=1)

        # Assert
        assert actual == [b"A", b"T", b"\r"]

    def test_term_send_is_paced(self, cli):
        """/term.send is the device line primitive, so it is paced like a typed line."""
        # Act
        actual = _sent(cli, "/term.send AT", tx_char_delay_ms=1)

        # Assert
        assert actual == [b"A", b"T", b"\r"]

    def test_a_request_mode_line_is_paced(self, cli):
        """With /term.request on a device line is a request, still sent paced."""
        # Act
        actual = _sent(cli, "AT", tx_char_delay_ms=1, request_mode=True)

        # Assert
        assert actual == [b"A", b"T", b"\r"]

    def test_a_bare_enter_sends_its_ending_paced(self, cli):
        """An empty line under send_bare_enter sends the line ending through the same path."""
        # Act
        actual = _sent(cli, "", tx_char_delay_ms=1, eol="\r\n")

        # Assert
        assert actual == [b"\r", b"\n"]

    def test_a_script_device_line_is_paced(self, cli):
        """A device line in a .run script goes out paced, as typed."""
        # Arrange
        run_dir = cli.repl.scripts_dir
        run_dir.mkdir(exist_ok=True)
        (run_dir / "one.run").write_text("AT\n", encoding="utf-8")

        # Act
        actual = _sent(cli, "/run one", tx_char_delay_ms=1)

        # Assert
        assert actual == [b"A", b"T", b"\r"]

    def test_raw_is_not_paced(self, cli):
        """/raw sends text exactly as typed: one write, no line ending, no pacing."""
        # Act
        actual = _sent(cli, "/raw AT", tx_char_delay_ms=1)

        # Assert
        assert actual == [b"AT"]

    def test_proto_send_is_not_paced(self, cli):
        """The protocol subsystem is binary: /proto.send writes its bytes as given, paced or not."""
        # Act
        actual = _sent(cli, "/proto.send 41 54 0D", tx_char_delay_ms=1)

        # Assert
        assert actual == [b"AT\r"]
