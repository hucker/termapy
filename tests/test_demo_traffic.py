"""The demo traffic plugin's /traffic.count, .hexdump and .snoop, on the per-config plugin path --demo uses.

``/traffic.count`` reported RX 0 for every device command until 2026-11-06: a
device line returns as soon as it is written, and the count stopped watching
before the reply arrived.  It now waits through ``ctx.serial.wait_idle``, the
same wait ``/cap.wire`` uses, so the tests below pin the reply being counted.

The demo plugins are what plugin authors copy, so they read bytes with the
shared reader (``protocol.parse_data``: hex in any spelling or case, quoted
text) and print hex uppercase.  Until 2026-10-13 no test drove
``/traffic.snoop`` at all; the move to the shared reader left four uses of a
renamed variable in its match and timeout messages, caught only by ruff and
ty.  The rig copies the plugin into the config's ``plugin/`` folder, where the
engine loads it at config load exactly as ``--demo`` sets it up, and connects
the DEMO port (the ``tests/test_port_commands.py`` shape).
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.demo import FakeSerial

_TRAFFIC = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "termapy"
    / "builtins"
    / "demo"
    / "plugin"
    / "traffic.py"
)


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port whose config folder holds the demo traffic plugin."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200}}
    config_path = tmp_path / "rig" / "rig.cfg"
    (config_path.parent / "plugin").mkdir(parents=True)
    shutil.copy(_TRAFFIC, config_path.parent / "plugin" / "traffic.py")
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    # What CLITerminal.run() does first: resolve the plugin folders, so the
    # per-config traffic.py registers its commands.
    terminal.repl.fire_lifecycle("on_app_start")
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


def _run_line(cli: CLITerminal, tmp_path: Path, line: str) -> None:
    """Run one line as a script via /run: /traffic.snoop blocks, which only a script may (block_until)."""
    script = tmp_path / "snoop.run"
    script.write_text(line + "\n", encoding="utf-8")
    cli.repl.dispatch_full(f"/run {script}")


# AT+RAMP\r goes out (8 bytes) and the DEMO answers x.x\r\n (5 bytes).
_RAMP = {"tx_bytes": 8, "rx_bytes": 5}


class TestCount:
    """/traffic.count waits for the reply: the DEMO answers after the command has returned."""

    def test_the_reply_is_counted(self, cli):
        """With the default 50ms gap, AT+RAMP counts its 8 bytes out and its 5-byte reply."""
        result = cli.repl.dispatch_full("/traffic.count AT+RAMP")
        assert result.data == _RAMP, result.error

    def test_the_value_is_the_reply_size(self, cli):
        """The scriptable value is a scalar, the RX byte count; both counts are the data."""
        result = cli.repl.dispatch_full("/traffic.count wait_gap=200ms AT+RAMP")
        assert result.value == "5", "the value is a scalar, never the data dict"

    def test_twice_in_a_row_each_counts_its_own_reply(self, cli):
        """A second count sees its own reply and none of the first one's."""
        # Act
        first = cli.repl.dispatch_full("/traffic.count wait_gap=200ms AT+RAMP")
        second = cli.repl.dispatch_full("/traffic.count wait_gap=200ms AT+RAMP")

        # Assert
        assert (first.data, second.data) == (_RAMP, _RAMP), "each count is its own exchange"

    @pytest.mark.parametrize(
        "line",
        ["/traffic.count wait_gap=200ms AT+RAMP", "/traffic.count AT+RAMP wait_gap=200ms"],
    )
    def test_wait_gap_goes_anywhere_on_the_line(self, cli, line):
        """wait_gap= is read before or after the command, and never sent with it."""
        result = cli.repl.dispatch_full(line)
        assert result.data == _RAMP, result.error

    def test_an_equals_sign_stays_in_the_command(self, cli):
        """AT+NAME=probe is the device command, not an unknown keyword: all of it goes out."""
        result = cli.repl.dispatch_full("/traffic.count wait_gap=200ms AT+NAME=probe")
        assert result.data["tx_bytes"] == len(b"AT+NAME=probe\r"), result.error

    def test_a_bad_wait_gap_is_refused(self, cli):
        """A wait_gap= that is not a duration fails, and the error names it."""
        # Act
        result = cli.repl.dispatch_full("/traffic.count wait_gap=garbage AT+RAMP")

        # Assert
        assert result.success is False, "an unreadable gap never runs the command"
        assert "wait_gap" in result.error, result.error

    def test_cap_wire_counts_the_same_bytes(self, cli):
        """One wait for both: /cap.wire and /traffic.count see the same exchange for the same command."""
        # Act
        wire = cli.repl.dispatch_full("/cap.wire wait_gap=200ms cmd=AT+RAMP")
        count = cli.repl.dispatch_full("/traffic.count wait_gap=200ms AT+RAMP")

        # Assert
        actual = {key: wire.data[key] for key in _RAMP}
        assert actual == count.data == _RAMP, "the two commands disagree on one exchange"


def test_hexdump_lands_in_cap(cli, tmp_path):
    """writes-in-cfg-folder: a relative name goes under the config's cap/, never the working folder, and the reply names it."""
    # Act
    result = cli.repl.dispatch_full("/traffic.hexdump wire.txt 50ms")

    # Assert
    assert result.success is True, result.error
    expected = tmp_path / "rig" / "cap" / "wire.txt"
    assert expected.exists(), "the file is in cap/"
    assert result.value == str(expected.resolve()), "the reply names the full path"
    assert result.data["path"] == result.value, "the data carries the same path"


def test_rate_answers_a_scalar_and_its_data(cli):
    """/traffic.rate's value is the RX rate as text; the window's counts and rates are the data."""
    # Act
    result = cli.repl.dispatch_full("/traffic.rate 50ms")

    # Assert
    assert result.success is True, result.error
    assert isinstance(result.value, str), "the value is a scalar, never the data dict"
    actual = sorted(result.data)
    expected = ["duration_s", "rx_bytes", "rx_rate_bps", "tx_bytes", "tx_rate_bps"]
    assert actual == expected, "the data carries both directions' counts and rates"


def test_hexdump_twice_replaces_for_a_person(cli, tmp_path):
    """A person's second dump to one name starts it over, as it always has; only an MCP client is refused that."""
    # Arrange
    outside = tmp_path / "outside.txt"
    outside.write_text("before\n", encoding="utf-8")

    # Act
    result = cli.repl.dispatch_full(f"/traffic.hexdump {outside} 50ms")

    # Assert
    assert result.success is True, result.error
    assert outside.read_text(encoding="utf-8") == "", "an absolute path still works for a person"


def test_snoop_matches_a_quoted_text_pattern(cli, tmp_path, capsys):
    """A quoted-text pattern is matched in the RX stream and reported as uppercase hex."""
    # Arrange -- the device answers AT with OK a moment after snoop starts watching
    port = cli.engine.port_obj
    assert isinstance(port, FakeSerial), "the DEMO engine opens a FakeSerial"
    timer = threading.Timer(0.3, port.write, args=(b"AT\r",))
    capsys.readouterr()

    # Act
    timer.start()
    _run_line(cli, tmp_path, '/traffic.snoop "OK" timeout=5s')
    timer.join()

    # Assert
    out = capsys.readouterr().out
    assert "Matched" in out and "4F4B" in out, f"the pattern, as uppercase hex, matched: {out!r}"


def test_snoop_reads_hex_in_either_case(cli, tmp_path, capsys):
    """A lowercase hex pattern means the same bytes as uppercase; the timeout names it in uppercase."""
    capsys.readouterr()
    _run_line(cli, tmp_path, "/traffic.snoop 7e 7f timeout=50ms")
    assert "waiting for 7E7F" in capsys.readouterr().out, "nothing sends 7E 7F"


def test_snoop_refuses_a_bad_pattern(cli, tmp_path, capsys):
    """A pattern that is neither hex nor quoted text is the shared reader's Parse error."""
    capsys.readouterr()
    _run_line(cli, tmp_path, "/traffic.snoop ZZ")
    assert "Parse error: " in capsys.readouterr().out
