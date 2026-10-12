"""``/cap.poll``: the scheduled polling loop, its files, its validation, and its stop.

Runs on the ``CLITerminal`` DEMO rig (the ``tests/test_capture_claim.py``
pattern): a real engine and the demo device, no mocks.  REPL commands
(``/print``) drive the loop without the port; one test polls the device.
The handler had no test at all until this file (2026-10-12).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port, its cfg folder under tmp_path."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200}}
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


def _cap_dir(tmp_path: Path) -> Path:
    return tmp_path / "rig" / "cap"


class TestTerminalPoll:

    def test_polls_a_repl_command_count_times(self, cli, capsys):
        """count= ticks, each row the command's value under its label, then the sample count as the value."""
        # Act
        result = cli.repl.dispatch_full("/cap.poll count=3 delay=0 --notime cmd=/print 42")

        # Assert
        out = capsys.readouterr().out
        assert result.success is True, result.error
        assert result.value == "3", "the sample count is the scriptable value"
        assert "counter   /print 42" in out, "the command is its own label"
        assert out.count("42") == 4, "three rows plus the label"
        assert "Poll complete (3 samples)" in out

    def test_several_commands_make_several_columns(self, cli, capsys):
        """Newline-separated commands each get a column, under the comma-separated labels= when given."""
        # Act
        result = cli.repl.dispatch_full(
            "/cap.poll count=1 delay=0 --notime labels=a,b cmd=/print 1\\n/print 2",
        )

        # Assert
        out = capsys.readouterr().out
        assert result.success is True, result.error
        header = next(line for line in out.splitlines() if "counter" in line)
        assert header.split() == ["counter", "a", "b"]
        assert "1           1           2" in out, "one row, both values"

    def test_timestamp_column_unless_notime(self, cli, capsys):
        """Without --notime every row starts with a timestamp column."""
        cli.repl.dispatch_full("/cap.poll count=1 delay=0 cmd=/print 5")
        out = capsys.readouterr().out
        header = next(line for line in out.splitlines() if "counter" in line)
        assert header.split()[:2] == ["timestamp", "counter"]

    def test_regex_extracts_the_value(self, cli, capsys):
        """regex= keeps the matching part of each response, so a prose reply still yields a number."""
        result = cli.repl.dispatch_full(
            "/cap.poll count=2 delay=0 --notime regex=[0-9.]+ cmd=/print temp=23.5C",
        )
        out = capsys.readouterr().out
        assert result.success is True, result.error
        assert out.count("      23.5") == 2, "two rows of the extracted number"

    def test_polls_the_device(self, cli, capsys):
        """A command without the prefix goes to the port; the demo's AT+TEMP reply yields its number."""
        result = cli.repl.dispatch_full(
            "/cap.poll count=2 delay=0 --notime regex=[0-9.]+ cmd=AT+TEMP",
        )
        out = capsys.readouterr().out
        assert result.success is True, result.error
        rows = [line.split() for line in out.splitlines() if line.strip()[:1].isdigit()]
        assert len(rows) == 2 and all(float(row[1]) for row in rows), f"two numeric samples, got {rows}"


class TestFiles:

    def test_csv_file_gets_a_header_and_the_rows(self, cli, tmp_path):
        """file= writes a CSV under cap/ with the header row and one row per tick; the path is the value."""
        # Act
        result = cli.repl.dispatch_full(
            "/cap.poll count=2 delay=0 --notime file=poll labels=val cmd=/print 7",
        )

        # Assert
        path = _cap_dir(tmp_path) / "poll.csv"
        assert result.success is True, result.error
        assert result.value == str(path), "the file path is the value"
        assert path.read_text(encoding="utf-8").splitlines() == ["counter,val", "1,7", "2,7"]

    def test_jsonl_rows_carry_the_labels(self, cli, tmp_path):
        """fmt=json writes one JSON object per tick, keyed by label, with a timestamp unless --notime."""
        # Act
        cli.repl.dispatch_full("/cap.poll count=2 delay=0 fmt=json file=poll labels=val cmd=/print 7")
        cli.repl.dispatch_full(
            "/cap.poll count=1 delay=0 --notime fmt=json file=bare labels=val cmd=/print 8",
        )

        # Assert
        rows = [json.loads(line) for line in (_cap_dir(tmp_path) / "poll.jsonl").read_text().splitlines()]
        bare = [json.loads(line) for line in (_cap_dir(tmp_path) / "bare.jsonl").read_text().splitlines()]
        assert [row["counter"] for row in rows] == [1, 2]
        assert all(row["val"] == "7" and "timestamp" in row for row in rows)
        assert bare == [{"counter": 1, "val": "8"}], "no timestamp key under --notime"

    def test_an_existing_file_is_auto_numbered_unless_overwrite(self, cli, tmp_path):
        """A second poll to the same name lands in name_001; --overwrite reuses the name."""
        # Act
        cli.repl.dispatch_full("/cap.poll count=1 delay=0 --notime file=poll cmd=/print 1")
        second = cli.repl.dispatch_full("/cap.poll count=1 delay=0 --notime file=poll cmd=/print 2")
        third = cli.repl.dispatch_full(
            "/cap.poll count=1 delay=0 --notime --overwrite file=poll cmd=/print 3",
        )

        # Assert
        cap = _cap_dir(tmp_path)
        assert second.value == str(cap / "poll_001.csv"), "numbered beside the original"
        assert third.value == str(cap / "poll.csv"), "--overwrite takes the name back"
        assert (cap / "poll.csv").read_text(encoding="utf-8").splitlines()[1] == "1,3", "overwritten"

    def test_a_poll_that_never_samples_leaves_no_file(self, cli, tmp_path):
        """A first sample that is not numeric aborts the poll and removes the empty file."""
        result = cli.repl.dispatch_full("/cap.poll count=3 delay=0 file=empty cmd=/print hello")
        assert result.success is False
        assert result.error.startswith("Non-numeric output: 'hello'"), result.error
        assert not (_cap_dir(tmp_path) / "empty.csv").exists(), "nothing to keep"


class TestValidation:

    @pytest.mark.parametrize(
        ("args", "fragment"),
        [
            ("labels=a,b cmd=/print 1", "labels count (2) must match cmd count (1)"),
            ("labels=1bad cmd=/print 1", "Invalid label: '1bad'"),
            ("regex=( cmd=/print 1", "Invalid regex:"),
            ("cmd=/print hello", "Non-numeric output: 'hello'"),
        ],
    )
    def test_bad_arguments_fail_with_the_rule(self, cli, args, fragment):
        """Each validation rule names what it refused."""
        result = cli.repl.dispatch_full(f"/cap.poll count=1 delay=0 {args}")
        assert result.success is False
        assert fragment in result.error, result.error

    def test_cmd_with_no_command_in_it_is_refused(self, cli):
        """cmd= made only of separators has no command to poll."""
        result = cli.repl.dispatch_full("/cap.poll count=1 delay=0 cmd=\\n")
        assert result.success is False
        assert "cmd=" in result.error, result.error


class TestStop:

    def test_a_stop_already_requested_ends_the_poll_before_the_first_tick(self, cli):
        """The poll honors the script-stop event and never clears it: set beforehand, it samples nothing."""
        cli.repl._script_stop.set()
        try:
            result = cli.repl.dispatch_full("/cap.poll count=3 delay=1s cmd=/print 1")
        finally:
            cli.repl._script_stop.clear()
        assert result.success is False and result.error == "Poll interrupted."

    @pytest.mark.slow
    def test_a_stop_during_the_delay_wakes_it(self, cli, tmp_path):
        """A stop raised during the between-tick delay ends the poll at once, keeping the samples taken."""
        # Arrange
        threading.Timer(0.2, cli.repl._script_stop.set).start()

        # Act
        t0 = time.monotonic()
        try:
            result = cli.repl.dispatch_full("/cap.poll count=5 delay=2s file=part cmd=/print 1")
        finally:
            cli.repl._script_stop.clear()
        elapsed_s = time.monotonic() - t0

        # Assert
        assert result.error == "Poll interrupted."
        assert elapsed_s < 1.5, "the 2 s delay was cut short"
        lines = (_cap_dir(tmp_path) / "part.csv").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, "the header and the one sample taken survive"
