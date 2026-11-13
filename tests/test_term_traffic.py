"""/term.traffic: the engine's TX/RX byte counts, the TUI-only toggle that shows them, and /term.info's copy.

The counting lives in ``SerialEngine`` (each chunk the reader receives,
each write that went out), so these tests drive a real engine on
pyserial's ``loop://``, which echoes every write back: what went out
comes in, byte for byte.  The display and the on/off setting are the
TUI's, Pilot-tested in ``tests/test_app_pilot.py``.
"""

from __future__ import annotations

import json
import time

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG

pytestmark = pytest.mark.sw_loopback


def _cli(tmp_path, port: str) -> CLITerminal:
    """A CLI host on ``port``, its cfg folder under tmp_path, not yet connected."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": port, "baud_rate": 115200}}
    path = tmp_path / "rig" / "rig.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(path), no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path  # keep the checkout's plugin/ out of the boot
    return terminal


@pytest.fixture
def loop_cli(tmp_path):
    """A CLI connected to loop://, where every byte written comes straight back."""
    terminal = _cli(tmp_path, "loop://")
    assert terminal._connect() is True, "loop:// opens"
    yield terminal
    terminal._disconnect()


def _await_counts(cli: CLITerminal, expected: tuple[int, int]) -> tuple[int, int]:
    """Poll the engine until its ``(tx, rx)`` counts are ``expected`` or 5 s pass; return the last seen."""
    deadline = time.monotonic() + 5.0
    actual = cli.engine.byte_counts()
    while actual != expected and time.monotonic() < deadline:
        time.sleep(0.02)
        actual = cli.engine.byte_counts()
    return actual


class TestEngineCounts:
    """The counts ``/term.traffic`` shows: every byte each way since the port opened."""

    def test_a_write_is_counted_going_out_and_coming_back(self, loop_cli):
        """Three bytes written to loop:// count three TX, and their echo three RX."""
        # Act
        loop_cli.ctx.serial.write(b"abc")

        # Assert
        assert _await_counts(loop_cli, (3, 3)) == (3, 3)

    def test_two_writes_add_up(self, loop_cli):
        """A second write adds to the first, each way: the counts are a running total."""
        # Act
        loop_cli.ctx.serial.write(b"abc")
        loop_cli.ctx.serial.write(b"defg")

        # Assert
        assert _await_counts(loop_cli, (7, 7)) == (7, 7)

    def test_a_reopen_starts_from_zero(self, loop_cli):
        """Each open of the port zeroes the counts, and they count again from there."""
        # Arrange
        loop_cli.ctx.serial.write(b"abc")
        assert _await_counts(loop_cli, (3, 3)) == (3, 3), "counted before the reopen"

        # Act
        loop_cli._disconnect()
        assert loop_cli._connect() is True, "loop:// reopens"
        reopened = loop_cli.engine.byte_counts()
        loop_cli.ctx.serial.write(b"de")

        # Assert
        assert reopened == (0, 0), "the reopen zeroed the counts"
        assert _await_counts(loop_cli, (2, 2)) == (2, 2), "counting resumed from zero"

    def test_a_sustained_run_counts_every_byte(self, loop_cli):
        """Two hundred writes with nothing else reading are counted exactly: no chunk is lost or counted twice."""
        # Arrange
        chunk = bytes(range(64))
        expected = (200 * len(chunk), 200 * len(chunk))

        # Act
        for _ in range(200):
            loop_cli.ctx.serial.write(chunk)

        # Assert
        assert _await_counts(loop_cli, expected) == expected

    @pytest.mark.rule("data-structured-twin")
    def test_term_info_reports_the_counts(self, loop_cli):
        """/term.info answers the counts as integers in its data and as exact numbers on its page."""
        # Arrange
        loop_cli.ctx.serial.write(b"abcde")
        assert _await_counts(loop_cli, (5, 5)) == (5, 5), "the echo came back"

        # Act
        result = loop_cli.repl.dispatch_full("/term.info")

        # Assert
        actual = (result.data["tx_bytes"], result.data["rx_bytes"])
        assert actual == (5, 5), "the data carries the engine's counts"
        assert "tx_bytes=5" in result.value and "rx_bytes=5" in result.value, (
            "the scriptable snapshot carries them too"
        )


def test_the_cli_refuses_the_toggle(tmp_path):
    """/term.traffic drives a TUI display, so the CLI refuses it by capability before the handler runs."""
    # Arrange
    cli = _cli(tmp_path, "DEMO")

    # Act
    result = cli.repl.dispatch_full("/term.traffic on")

    # Assert
    assert result.error == (
        "/term.traffic requires the tui_mode capability, the TUI (switch with /tui); "
        "not available in this environment"
    )


def test_term_info_reports_traffic_off_outside_the_tui(tmp_path):
    """/term.info carries the traffic flag, off in a CLI, which cannot turn it on."""
    # Arrange
    cli = _cli(tmp_path, "DEMO")

    # Act
    result = cli.repl.dispatch_full("/term.info")

    # Assert
    assert result.data["traffic"] is False, "/term.info reads the flag every load starts off"
