"""The /port command family driven through the dispatcher on the CLI DEMO rig.

Until 2026-10-12 the port plugin's output path had never run in-process:
the CLI gold script and the subprocess tests named the commands, but
coverage showed ``_apply`` and every handler below ``/port.list`` as
unexecuted, and eight of them (the signal reads, ``/port.flow_control``,
``/port.help``, ``/port.mode``, ``/port.usb``) were named by no test at
all.  These tests drive each command the way a user or a script does --
``repl.dispatch_full`` on a ``CLITerminal`` connected to the DEMO port
(``conftest.py``'s ``cli``) -- and pin the scriptable
``value``, the printed line and the side effect on the open port and the
session cfg.  The chip commands run against the ``TERMAPY_DEMO_FLEET``
roster, the environment layer that exists precisely because a dispatched
command has no ``source=`` to inject.
"""

from __future__ import annotations

import json
import sys

import pytest

from termapy.cli import CLITerminal
from termapy.demo import FakeSerial
from termapy.plugins.capabilities import STATE_MESSAGES


def _demo_port(cli: CLITerminal) -> FakeSerial:
    """The FakeSerial the rig holds open, typed for attribute asserts."""
    port = cli.engine.port_obj
    assert isinstance(port, FakeSerial), "the DEMO engine opens a FakeSerial"
    return port


# ── help and the bare root ──────────────────────────────────────────────────


class TestHelp:
    def test_port_help_renders_the_port_page(self, cli, capsys):
        """/port.help is /help port: the man page for the family, with the resolved name as the value."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.help")
        assert result.value == "port", "the resolved name, so a script can confirm the match"
        assert "port.connect" in capsys.readouterr().out, "the page lists the subcommands"

    def test_bare_port_lists_without_a_picker(self, cli):
        """bare-declared: a bare /port runs /port.list where no picker exists (the CLI); the TUI opens its picker."""
        bare = cli.repl.dispatch_full("/port")
        listed = cli.repl.dispatch_full("/port.list")
        assert bare.success, bare.error
        assert bare.value == listed.value, "bare /port is /port.list"


# ── read-only input signals ─────────────────────────────────────────────────


class TestSignals:
    def test_each_signal_reads_the_port_level(self, cli):
        """/port.cts, /port.dsr, /port.ri and /port.cd each return the live level as 0 or 1."""
        port = _demo_port(cli)
        for signal in ("cts", "dsr", "ri", "cd"):
            result = cli.repl.dispatch_full(f"/port.{signal}")
            expected = str(int(getattr(port, signal)))
            assert result.value == expected, f"{signal} mirrors the port"

    def test_a_value_is_refused_as_read_only(self, cli, capsys):
        """An input signal takes no argument: the command fails saying so, printed once, with no level."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.dsr 1")
        assert (result.success, result.error) == (False, "DSR is read-only"), (
            "a refusal is a failure"
        )
        assert capsys.readouterr().out.count("DSR is read-only") == 1, "dispatch prints it, once"
        assert result.value == "", "a refusal is not a scriptable level"

    def test_disconnected_reports_not_connected(self, cli, capsys):
        """Without an open port a signal read fails with Not connected and returns no level."""
        cli._disconnect()
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.cd")
        assert STATE_MESSAGES["serial_connected"] in capsys.readouterr().out
        assert (result.success, result.error) == (False, STATE_MESSAGES["serial_connected"]), (
            "no port, no level"
        )
        assert result.value == ""


# ── DTR / RTS ───────────────────────────────────────────────────────────────


class TestHardwareLines:
    def test_bare_reads_the_level(self, cli):
        """/port.dtr with no argument returns the live level as 0 or 1."""
        port = _demo_port(cli)
        port.dtr = True
        result = cli.repl.dispatch_full("/port.dtr")
        assert result.value == "1"

    def test_a_value_sets_the_line(self, cli, capsys):
        """/port.dtr off drives the line low and reports the new level; a set has no scriptable value."""
        port = _demo_port(cli)
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.dtr off")
        assert port.dtr is False, "the line followed the command"
        assert "DTR -> 0" in capsys.readouterr().out
        assert result.value == ""

    def test_an_unknown_token_is_refused(self, cli, capsys):
        """A token outside the 0/1/on/off set changes nothing and names the valid forms."""
        port = _demo_port(cli)
        port.rts = True
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.rts maybe")
        assert (
            "Invalid RTS value: maybe (use on/off/true/false/yes/no/1/0/y/n/t/f)"
            in capsys.readouterr().out
        )
        assert result.success is False, "a refused value fails the command"
        assert port.rts is True, "a refused value leaves the line alone"

    def test_disconnected_reports_not_connected(self, cli, capsys):
        """A line read or set without an open port fails with Not connected."""
        cli._disconnect()
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.dtr 1")
        assert STATE_MESSAGES["serial_connected"] in capsys.readouterr().out
        assert result.success is False, "nothing was set"

    @pytest.mark.parametrize(
        ("command", "level"),
        [
            ("dtr", True),
            ("dtr", False),
            ("cts", None),  # an input: the DEMO port's own level, read-only
        ],
    )
    def test_help_shows_the_level_the_command_returns(self, cli, capsys, command, level):
        """/help's CURRENT STATE for a line or signal is the 0/1 the command returns, not pyserial's True/False."""
        # Arrange
        if level is not None:
            setattr(_demo_port(cli), command, level)
        expected = cli.repl.dispatch_full(f"/port.{command}").value
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full(f"/help port.{command}")

        # Assert
        actual = capsys.readouterr().out
        assert f"CURRENT STATE\n  {expected}\n" in actual, (
            f"help and /port.{command} agree:\n{actual}"
        )
        assert expected in ("0", "1"), "the command answers 0 or 1"


# ── frame properties ────────────────────────────────────────────────────────


class TestProperties:
    def test_bare_reads_the_live_value(self, cli):
        """/port.baud_rate with no argument returns the open port's baud rate."""
        result = cli.repl.dispatch_full("/port.baud_rate")
        assert result.value == "115200"

    def test_a_value_sets_the_port_and_the_session_cfg(self, cli, capsys):
        """/port.baud_rate 9600 changes the open port and the session cfg together."""
        port = _demo_port(cli)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/port.baud_rate 9600")

        # Assert
        assert (port.baudrate, cli.cfg["serial"]["baud_rate"]) == (9600, 9600), "port and cfg agree"
        assert "Baud rate -> 9600" in capsys.readouterr().out
        assert result.value == "", "a set returns prose, not a value"

    def test_parity_is_upper_cased_before_validation(self, cli):
        """/port.parity e is parity E: the letter is normalized, not rejected."""
        port = _demo_port(cli)
        cli.repl.dispatch_full("/port.parity e")
        assert (port.parity, cli.cfg["serial"]["parity"]) == ("E", "E")

    def test_a_value_outside_the_valid_set_is_refused(self, cli, capsys):
        """A data-bits value outside 5-8 is refused with the valid set, and nothing changes."""
        port = _demo_port(cli)
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.byte_size 9")
        assert "Invalid data bits: 9 (use 5, 6, 7, 8)" in capsys.readouterr().out
        assert result.success is False, "a refused value fails the command"
        assert (port.bytesize, cli.cfg["serial"]["byte_size"]) == (8, 8), (
            "refused value applied nowhere"
        )

    def test_a_non_number_is_refused(self, cli, capsys):
        """failure-is-failure: a baud rate that is not an integer fails by name, printed once.

        Until 2026-10-16 every /port.* setter printed its refusal and
        returned success, so a script or agent read a typo as applied.
        """
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.baud_rate fast")
        assert (result.success, result.error) == (False, "Invalid baud rate: fast"), (
            "a refusal is a failure"
        )
        assert capsys.readouterr().out.count("Invalid baud rate: fast") == 1, (
            "dispatch prints it, once"
        )

    def test_json_carries_the_refusal(self, cli, capsys):
        """A refused set answers --json with success false and the reason in error."""
        capsys.readouterr()
        cli.repl.dispatch_full("/port.baud_rate fast --json")
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert (envelope["success"], envelope["error"]) == (False, "Invalid baud rate: fast")

    def test_disconnected_read_shows_the_cfg_value_and_strips_the_annotation(self, cli, capsys):
        """Without an open port the cfg value is shown as '<v> (disconnected)' and the value is the bare number."""
        cli._disconnect()
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.baud_rate")
        assert "115200 (disconnected)" in capsys.readouterr().out
        assert result.value == "115200", "scripts get the number, not the annotation"


# ── /port.mode ──────────────────────────────────────────────────────────────


class TestMode:
    def test_bare_shows_baud_and_frame(self, cli):
        """/port.mode with no argument reports the configured baud and frame as one token pair."""
        result = cli.repl.dispatch_full("/port.mode")
        assert result.value == "115200 8N1"

    def test_bare_data_carries_each_field(self, cli):
        """Bare /port.mode's data is the frame field by field, baud and custom as /port.baud_rate spells them."""
        # Act
        result = cli.repl.dispatch_full("/port.mode")

        # Assert
        actual = result.data
        expected = {"baud": 115200, "custom": False, "byte_size": 8, "parity": "N", "stop_bits": 1}
        assert actual == expected, "four frame facts with their JSON types, no prose to parse"

    def test_bare_data_follows_a_set(self, cli):
        """After /port.mode 9600 E71, bare /port.mode's data reports the new frame."""
        # Act
        cli.repl.dispatch_full("/port.mode 9600 E71")
        result = cli.repl.dispatch_full("/port.mode")

        # Assert
        actual = result.data
        expected = {"baud": 9600, "custom": False, "byte_size": 7, "parity": "E", "stop_bits": 1}
        assert actual == expected, "the data is read at the query, not cached"

    def test_baud_and_frame_apply_to_the_port_and_the_cfg(self, cli):
        """/port.mode 9600 E71 sets all four frame fields on the port and in the session cfg."""
        port = _demo_port(cli)

        # Act
        result = cli.repl.dispatch_full("/port.mode 9600 E71")

        # Assert
        actual_port = (port.baudrate, port.parity, port.bytesize, port.stopbits)
        assert actual_port == (9600, "E", 7, 1), "the open port carries the new frame"
        serial = cli.cfg["serial"]
        actual_cfg = (
            serial["baud_rate"],
            serial["parity"],
            serial["byte_size"],
            serial["stop_bits"],
        )
        assert actual_cfg == (9600, "E", 7, 1), "the session cfg agrees with the port"
        assert result.value == "Mode -> 9600 7E1"

    def test_a_frame_alone_keeps_the_baud(self, cli):
        """/port.mode N82 changes the frame and leaves the baud rate as it was."""
        port = _demo_port(cli)
        result = cli.repl.dispatch_full("/port.mode N82")
        assert (port.baudrate, port.stopbits) == (115200, 2)
        assert result.value == "Mode -> 115200 8N2"

    def test_a_bad_token_is_refused(self, cli, capsys):
        """A token that is neither a baud rate nor a frame is refused with an example, and nothing changes."""
        port = _demo_port(cli)
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.mode fast")
        assert "Invalid mode argument: fast (use e.g. 9600 N81)" in capsys.readouterr().out
        assert result.success is False, "a refused line fails the command"
        assert port.baudrate == 115200, "a refused line applies nothing"

    def test_two_frames_are_refused(self, cli, capsys):
        """Two frame tokens on one line are a duplicate, not a last-wins."""
        capsys.readouterr()
        cli.repl.dispatch_full("/port.mode N81 E71")
        assert "Duplicate mode: E71" in capsys.readouterr().out

    def test_disconnected_set_reports_not_connected(self, cli, capsys):
        """A mode set without an open port says Not connected and leaves the cfg alone."""
        cli._disconnect()
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.mode 9600")
        assert STATE_MESSAGES["serial_connected"] in capsys.readouterr().out
        assert result.success is False, "nothing was set"
        assert cli.cfg["serial"]["baud_rate"] == 115200, "the cfg did not move without a port"


# ── /port.flow_control ──────────────────────────────────────────────────────


class TestFlowControl:
    def test_bare_shows_the_configured_mode(self, cli):
        """/port.flow_control with no argument reports the cfg mode."""
        result = cli.repl.dispatch_full("/port.flow_control")
        assert result.value == "none"

    def test_rtscts_sets_the_port_and_the_cfg(self, cli, capsys):
        """/port.flow_control rtscts turns on the hardware handshake on the port and records it in the cfg."""
        port = _demo_port(cli)
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full("/port.flow_control rtscts")

        # Assert
        assert (port.rtscts, port.xonxoff) == (True, False), "hardware on, software off"
        assert cli.cfg["serial"]["flow_control"] == "rtscts"
        assert "Flow control -> rtscts" in capsys.readouterr().out

    def test_xonxoff_replaces_rtscts(self, cli):
        """Switching to xonxoff turns the hardware handshake off: the modes are exclusive."""
        port = _demo_port(cli)
        cli.repl.dispatch_full("/port.flow_control rtscts")
        cli.repl.dispatch_full("/port.flow_control xonxoff")
        assert (port.rtscts, port.xonxoff) == (False, True)

    def test_an_unknown_mode_is_refused(self, cli, capsys):
        """A mode outside none/rtscts/xonxoff/manual is refused with the valid set, and nothing changes."""
        port = _demo_port(cli)
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.flow_control bogus")
        assert (
            "Invalid flow control: bogus (use none/rtscts/xonxoff/manual)"
            in capsys.readouterr().out
        )
        assert result.success is False, "a refused mode fails the command"
        assert (port.rtscts, cli.cfg["serial"]["flow_control"]) == (False, "none"), (
            "refused mode applied nowhere"
        )

    def test_disconnected_read_carries_the_annotation(self, cli, capsys):
        """Without an open port the cfg mode is shown with the (disconnected) annotation."""
        cli._disconnect()
        capsys.readouterr()
        cli.repl.dispatch_full("/port.flow_control")
        assert "none (disconnected)" in capsys.readouterr().out


# ── /port.break ─────────────────────────────────────────────────────────────


class TestBreak:
    def test_default_duration_is_reported(self, cli):
        """/port.break with no argument sends the 250 ms default and says so."""
        result = cli.repl.dispatch_full("/port.break")
        assert result.value.startswith("Break sent ("), result.value

    def test_a_non_positive_duration_is_refused(self, cli, capsys):
        """A zero or non-numeric duration is refused with the unit named."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.break 0")
        assert "Invalid duration (use milliseconds, e.g. 250)" in capsys.readouterr().out
        assert result.success is False, "no break was sent"

    def test_disconnected_reports_not_connected(self, cli, capsys):
        """A break without an open port fails with Not connected."""
        cli._disconnect()
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.break")
        assert STATE_MESSAGES["serial_connected"] in capsys.readouterr().out
        assert result.success is False, "no break was sent"


# ── /port.info ──────────────────────────────────────────────────────────────


class TestInfo:
    def test_connected_report_has_all_three_sections(self, cli, capsys, monkeypatch):
        """/port.info on the open DEMO port prints the frame, the synthesized chip rows and the live lines."""
        # Arrange -- no fleet in the environment, so DEMO resolves to its
        # synthesized record and the chip section renders.
        monkeypatch.delenv("TERMAPY_DEMO_FLEET", raising=False)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/port.info")

        # Assert
        out = capsys.readouterr().out
        assert "DEMO  (connected)" in out, "the port row carries the state"
        assert "Termapy simulated device" in out, (
            "the chip section came from the reserved-port record"
        )
        for line in ("DTR", "RTS", "CTS", "DSR", "RI", "CD"):
            assert line in out, f"the live {line} row"
        assert result.value == "DEMO", "the scriptable value is the port name"

    def test_the_record_holds_settings_chip_and_signals_as_values(self, cli):
        """The /port.info data is real values: the baud an int, the chip its fields, the six lines booleans."""
        # Act
        data = cli.repl.dispatch_full("/port.info").data

        # Assert
        assert (data["port"], data["connected"], data["baud_rate"]) == ("DEMO", True, 115200), (
            "settings as values"
        )
        assert data["chip"]["model"] == "DEMO", "the chip record, keyed like /port.chip.<field>"
        actual = sorted(data["signals"])
        expected = ["cd", "cts", "dsr", "dtr", "ri", "rts"]
        assert actual == expected, "the six hardware lines"
        assert all(isinstance(state, bool) for state in data["signals"].values()), (
            "each line is a boolean"
        )

    def test_an_argument_is_redirected_to_port_chip(self, cli):
        """/port.info takes no port name: it fails and points at /port.chip for another port."""
        result = cli.repl.dispatch_full("/port.info COM3")
        assert result.success is False
        assert "use /port.chip COM3" in result.error


# ── /port.connect ───────────────────────────────────────────────────────────


class TestConnect:
    def test_optional_settings_land_in_the_cfg_before_the_open(self, cli):
        """/port.connect DEMO 9600 E71 lf noecho applies every optional field to the session cfg and stays connected."""
        # Act
        result = cli.repl.dispatch_full("/port.connect DEMO 9600 E71 lf noecho")

        # Assert
        serial = cli.cfg["serial"]
        actual = (serial["baud_rate"], serial["parity"], serial["byte_size"], serial["stop_bits"])
        assert actual == (9600, "E", 7, 1), "the frame fields"
        assert (cli.cfg["eol"], cli.cfg["echo"]) == ("\n", False), (
            "lf is stored as the newline it names; echo off"
        )
        assert cli.engine.is_connected is True
        assert result.value == "DEMO"

    def test_a_parse_error_fails_before_touching_the_cfg(self, cli):
        """A duplicate baud rate is refused as a failure and the cfg keeps its values."""
        result = cli.repl.dispatch_full("/port.connect DEMO 9600 19200")
        assert result.success is False
        assert "Duplicate baud rate" in result.error
        assert cli.cfg["serial"]["baud_rate"] == 115200, "nothing applied on a refused line"

    def test_a_port_that_does_not_open_fails_with_its_reason(self, cli, capsys):
        """failure-reported-once: an open that fails answers with why, in error and printed once.

        The reason used to reach only the host's status line while the
        command failed with an empty error, so --json and MCP said nothing.
        """
        # Arrange
        cli._disconnect()
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/port.connect DEMO_FAIL")
        out = capsys.readouterr().out

        # Assert
        assert result.success is False, "the port did not open"
        assert result.error.startswith("Cannot open DEMO_FAIL"), result.error
        assert out.count("Cannot open DEMO_FAIL") == 1, (
            "dispatch prints it; the status line stays quiet"
        )


class _OpensAfter:
    """``ctx.serial.connect`` that misses the first tries as an absent port does, then opens the real one."""

    def __init__(self, cli: CLITerminal, misses: int, on_miss=None) -> None:
        self._real = cli.repl.ctx.serial.connect
        self._misses = misses
        self._on_miss = on_miss
        self.calls = 0
        cli.repl.ctx.serial.connect = self

    def __call__(self, port: str | None = None) -> None:
        self.calls += 1
        if self.calls <= self._misses:
            if self._on_miss is not None:
                self._on_miss()
            raise OSError("Cannot open DEMO: Port not found")
        self._real(port)


class TestConnectTimeout:
    """/port.connect timeout=: tries until the port opens or the time runs out."""

    def test_a_timed_connect_waits_until_the_port_opens_twice(self, cli, capsys):
        """A port that misses twice opens on the third try, and a second timed connect does the same."""
        for _ in range(2):
            # Arrange
            cli._disconnect()
            opener = _OpensAfter(cli, misses=2)
            capsys.readouterr()

            # Act
            result = cli.repl.dispatch_full("/port.connect timeout=5s")

            # Assert
            assert result.success, result.error
            assert cli.engine.is_connected is True
            assert opener.calls == 3, "two misses, then the open"
            out = capsys.readouterr().out
            assert out.count("Waiting for DEMO...") == 1, "the default message, said once"

    def test_msg_replaces_the_waiting_line_and_takes_the_rest(self, cli, capsys):
        """msg= is the waiting line, verbatim to the end; a 9600 inside it is not a baud rate."""
        # Arrange
        cli._disconnect()
        _OpensAfter(cli, misses=1)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full(
            "/port.connect timeout=5s msg=Waiting for reboot at 9600..."
        )

        # Assert
        assert result.success, result.error
        out = capsys.readouterr().out
        assert "Waiting for reboot at 9600..." in out
        assert "Waiting for DEMO" not in out, "the message replaces the default"
        assert cli.cfg["serial"]["baud_rate"] == 115200, "text in msg= sets nothing"

    def test_a_port_that_opens_at_once_shows_no_waiting_line(self, cli, capsys):
        """When the first try opens the port there is nothing to wait for and no message."""
        cli._disconnect()
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.connect DEMO 9600 timeout=5s msg=Waiting...")
        assert result.success, result.error
        assert "Waiting" not in capsys.readouterr().out
        assert cli.cfg["serial"]["baud_rate"] == 9600, "the settings before timeout= still apply"

    def test_without_a_timeout_it_tries_once(self, cli):
        """No timeout= is one try, as /port.connect always was: the miss is the failure."""
        cli._disconnect()
        opener = _OpensAfter(cli, misses=1)
        result = cli.repl.dispatch_full("/port.connect")
        assert (result.success, opener.calls) == (False, 1)
        assert result.error == "Cannot open DEMO: Port not found"

    def test_a_timeout_that_runs_out_fails_with_the_last_reason(self, cli):
        """A port that never opens fails once the time is up, with why and how long it waited."""
        cli._disconnect()
        result = cli.repl.dispatch_full("/port.connect DEMO_FAIL timeout=300ms")
        assert result.success is False
        assert result.error.startswith("Cannot open DEMO_FAIL"), result.error
        assert "(waited " in result.error, result.error

    def test_a_bad_timeout_is_refused(self, cli):
        """A timeout that is not a duration fails before any try."""
        cli._disconnect()
        result = cli.repl.dispatch_full("/port.connect timeout=soon")
        assert result.error == "Invalid timeout: soon"
        assert cli.engine.is_connected is False

    def test_a_stop_ends_the_wait(self, cli):
        """Escape or Ctrl+C during the wait cancels the connect: typed, it says so."""
        # Arrange -- the stop arrives while the second try misses
        cli._disconnect()
        stops = iter([None, cli.repl.ctx.internal.script_stop])
        opener = _OpensAfter(cli, misses=10, on_miss=lambda: (next(stops) or (lambda: None))())

        # Act
        result = cli.repl.dispatch_full("/port.connect timeout=5s")

        # Assert
        assert result.error == "Connect canceled."
        assert opener.calls < 10, "the wait ended at the stop, not after every try"


# ── the connection banners follow the command's output level ───────────────


_QUIET_SPELLINGS = ["{cmd}.quiet", "{cmd} --quiet", "{cmd}.silent", "{cmd} --silent"]


class TestBannersFollowTheLevel:
    """A connect or disconnect banner is the command's output: a quiet or silent call keeps it off the screen."""

    @pytest.mark.parametrize("spelling", _QUIET_SPELLINGS)
    def test_a_quiet_disconnect_prints_no_banner(self, cli, capsys, spelling):
        """The suffix and the flag hide the banner alike, and the port still closes."""
        # Arrange
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/" + spelling.format(cmd="port.disconnect"))
        out = capsys.readouterr().out

        # Assert
        assert result.success is True, result.error
        assert "Disconnected" not in out, f"{spelling} still printed the banner:\n{out}"
        assert cli.engine.is_connected is False, "the port closed all the same"

    @pytest.mark.parametrize("spelling", _QUIET_SPELLINGS)
    def test_a_quiet_connect_prints_no_banner(self, cli, capsys, spelling):
        """The connect banner hides alike, and the port opens."""
        # Arrange
        cli.repl.dispatch_full("/port.disconnect.silent")
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/" + spelling.format(cmd="port.connect"))
        out = capsys.readouterr().out

        # Assert
        assert result.success is True, result.error
        assert "Connected" not in out, f"{spelling} still printed the banner:\n{out}"
        assert cli.engine.is_connected is True, "the port opened all the same"

    def test_a_plain_call_still_prints_both_banners_after_a_quiet_one(self, cli, capsys):
        """The level is the call's own: after a quiet pair, plain calls print their banners again."""
        # Arrange
        cli.repl.dispatch_full("/port.disconnect.quiet")
        cli.repl.dispatch_full("/port.connect --quiet")
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full("/port.disconnect")
        cli.repl.dispatch_full("/port.connect")
        out = capsys.readouterr().out

        # Assert
        assert "Disconnected: DEMO" in out, "a plain disconnect shows its banner"
        assert "Connected: DEMO" in out, "a plain connect shows its banner"

    @pytest.mark.parametrize(
        ("cmd", "banner"),
        [("/port.disconnect", "Disconnected: DEMO"), ("/port.connect", "Connected: DEMO")],
    )
    def test_a_json_call_carries_its_banner_in_the_envelope(self, cli, capsys, cmd, banner):
        """--json answers with one envelope: the banner is in its output_lines and nothing prints beside it."""
        # Arrange
        if cmd == "/port.connect":
            cli.repl.dispatch_full("/port.disconnect.silent")
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full(f"{cmd} --json")
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]

        # Assert
        assert len(lines) == 1, f"only the envelope prints: {lines}"
        output_lines = json.loads(lines[0])["output_lines"]
        assert [line.startswith(banner) for line in output_lines] == [True], output_lines

    def test_a_quiet_json_call_has_no_banner_anywhere(self, cli, capsys):
        """--json --quiet: the envelope's output is empty and nothing prints beside it."""
        # Arrange
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full("/port.disconnect --json --quiet")
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]

        # Assert
        assert len(lines) == 1, f"only the envelope prints: {lines}"
        assert json.loads(lines[0])["output_lines"] == [], "quiet keeps it out of the envelope too"


# ── a failed command captured into a variable ───────────────────────────────


class TestCapture:
    def test_a_failed_capture_answers_with_the_inner_error_once(self, cli, capsys):
        """failure-reported-once: $(B) <- a refused command fails with its error, printed once, B unset.

        The capture used to blank the error to dodge a double print, so an
        agent saw success false with error "" (review 2026-10-15, S4).
        """
        # Arrange
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("$(B) <- /port.baud_rate fast")
        out = capsys.readouterr().out

        # Assert
        assert (result.success, result.error) == (False, "Invalid baud rate: fast"), (
            "the inner failure is the answer"
        )
        assert out.count("Invalid baud rate: fast") == 1, "printed once, at the capture's exit"
        assert cli.repl.dispatch_full("/var B").value == "", "B was not assigned"

    def test_json_names_the_inner_error(self, cli, capsys):
        """--json on a failed capture carries the inner error in the envelope and prints no line outside it."""
        # Arrange
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full("$(B) <- /port.baud_rate fast --json")
        lines = capsys.readouterr().out.strip().splitlines()

        # Assert
        envelope = json.loads(lines[-1])
        assert (envelope["success"], envelope["error"]) == (False, "Invalid baud rate: fast")
        assert len(lines) == 1, "the envelope is the whole answer"


# ── /port.chip family on the DEMO fleet ─────────────────────────────────────


class TestChip:
    def test_a_named_port_dumps_every_known_field(self, cli, capsys, monkeypatch):
        """/port.chip COM4 prints the device as a header and one labeled row per known field."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/port.chip COM4")

        # Assert
        out = capsys.readouterr().out
        assert "Silicon Labs CP2102" in out, "the model row"
        assert "10C4:EA60" in out, "the VID:PID row"
        assert result.value.startswith("COM4"), "the device heads the scriptable dump"

    def test_a_star_dumps_every_port_in_the_fleet(self, cli, capsys, monkeypatch):
        """/port.chip * dumps each port of the roster, blank-line separated."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        capsys.readouterr()
        cli.repl.dispatch_full("/port.chip *")
        out = capsys.readouterr().out
        assert all(device in out for device in ("COM3", "COM4", "COM7")), "every roster port"

    def test_a_star_returns_one_chip_record_per_port(self, cli, monkeypatch):
        """/port.chip * returns a list of chip records, and their device names as the value."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        result = cli.repl.dispatch_full("/port.chip *")
        actual = [record["device"] for record in result.data]
        assert actual == result.value.split("\n"), (
            "the value lists the devices the records describe"
        )
        assert {"COM3", "COM4", "COM7"} <= set(actual), "one record per roster port"

    def test_the_fleet_is_authoritative_for_the_reserved_name(self, cli, capsys, monkeypatch):
        """With a fleet active, bare /port.chip on the DEMO cfg finds no match: a substitute roster never synthesizes."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.chip")
        assert "No port matching 'DEMO'" in capsys.readouterr().out
        assert result.value == "", "a warning is not a dump"

    def test_a_field_for_one_port_is_the_bare_value(self, cli, monkeypatch):
        """/port.chip.serial COM4 returns just the field, the shape a script captures."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        result = cli.repl.dispatch_full("/port.chip.serial COM4")
        assert result.value == "0001"

    def test_a_field_for_every_port_is_device_prefixed(self, cli, monkeypatch):
        """/port.chip.model * values '<device>: <value>' per port, and answers with a device-to-value mapping."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        result = cli.repl.dispatch_full("/port.chip.model *")
        expected = "COM3: FTDI FT232R / FT245R\nCOM4: Silicon Labs CP2102\nCOM7: -"
        assert result.value == expected, "'-' kept as the roster has it"
        assert result.data["COM4"] == "Silicon Labs CP2102", "a port is looked up, not searched for"

    def test_a_field_table_is_titled_by_the_fields_label(self, cli, capsys, monkeypatch):
        """/port.chip.<field> * heads its table with the label the /port.chip page uses, never the raw field name."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        capsys.readouterr()
        # Act
        cli.repl.dispatch_full("/port.chip.serial *")
        serial_out = capsys.readouterr().out
        cli.repl.dispatch_full("/port.chip.vid_pid *")
        vid_pid_out = capsys.readouterr().out
        # Assert
        assert "SERIAL #" in serial_out, "the serial column is headed SERIAL #"
        assert "Chip Serial #" in serial_out, "and the table titled by the same label"
        assert "VID:PID" in vid_pid_out, "vid_pid reads VID:PID, as on the /port.chip page"
        assert "VID_PID" not in vid_pid_out, "the raw field name never reaches the header"

    def test_the_chip_page_labels_the_serial_number_serial_hash(self, cli, capsys, monkeypatch):
        """/port.chip COM4 labels the serial row "Serial #", the label its field table uses."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        capsys.readouterr()
        cli.repl.dispatch_full("/port.chip COM4")
        out = capsys.readouterr().out
        assert "Serial #" in out, "the page row and the table header share one label"

    def test_the_port_list_heads_the_serial_column_like_the_chip_views(
        self, cli, capsys, monkeypatch
    ):
        """/port.list heads the serial-number column SERIAL #, the label of /port.chip and its field tables."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        capsys.readouterr()
        cli.repl.dispatch_full("/port.list")
        header = next(line for line in capsys.readouterr().out.splitlines() if "PORT" in line)
        assert "SERIAL #" in header, "the port table uses the shared label"
        assert " SN " not in f" {header} ", "the old abbreviation is gone"

    def test_display_labels_are_title_case(self, cli, capsys):
        """Every word of a /port.info label starts upper case (Baud Rate, USB Speed, In Use); symbols and acronyms stay."""
        capsys.readouterr()
        cli.repl.dispatch_full("/port.info")
        labels = [
            line.split(":", 1)[0].strip()
            for line in capsys.readouterr().out.splitlines()
            if ":" in line and line.startswith("  ")
        ]
        assert "Baud Rate" in labels and "In Use" in labels, (
            f"the page's own and chip rows: {labels}"
        )
        lower = [label for label in labels for word in label.split() if word[:1].islower()]
        assert lower == [], f"a display label word starts lower case: {lower}"

    def test_every_chip_field_has_a_label(self):
        """Every /port.chip field has a label, so no page row or field table falls back to the raw name."""
        from termapy.port_control import CHIP_FIELD_LABELS, CHIP_FIELDS

        actual = set(CHIP_FIELD_LABELS)
        expected = set(CHIP_FIELDS)
        assert actual == expected, "one label per field, no strays"

    def test_an_unknown_field_value_reads_n_a(self, cli, monkeypatch):
        """A field the roster leaves empty renders n/a rather than None."""
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        result = cli.repl.dispatch_full("/port.chip.driver COM3")
        assert result.value == "n/a"


# ── /port.usb ───────────────────────────────────────────────────────────────


class TestUsbTree:
    def test_the_tree_renders_where_a_backend_exists(self, cli, capsys):
        """/port.usb draws the machine's tree on Windows and Linux and fails by name elsewhere."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.usb")
        out = capsys.readouterr().out
        if sys.platform == "win32" or sys.platform.startswith("linux"):
            assert result.success is True, result.error
            # Whitespace removed: the console indents and may wrap a long row.
            assert "".join(result.value.split()) == "".join(out.split()), (
                "the rendered lines are the scriptable value"
            )
        else:
            assert result.success is False
            assert "USB tree is not available" in result.error

    @pytest.mark.skipif(
        not (sys.platform == "win32" or sys.platform.startswith("linux")),
        reason="the USB tree has Windows and Linux backends only",
    )
    def test_a_data_consumer_gets_the_record_tree(self, cli, capsys):
        """/port.usb --json returns the nested records as data; the CLI prints that document, never the drawing."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/port.usb --json")
        assert isinstance(result.data, list), "the record tree, one entry per root"
        assert result.value == "", "no rendered text on the data path"
        document = json.loads(capsys.readouterr().out.strip())
        assert document["data"] == result.data, "the one line printed is the JSON document"
