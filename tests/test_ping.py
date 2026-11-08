"""/ping on the CLI DEMO rig: the value is the mean reply time, the data every round trip, the level what prints.

Until 2026-11-06 a timed-out ping still put its elapsed time in the
summary, so one ping that timed out answered ``250ms`` (the timeout itself)
and a run's average counted every timeout as a slow reply.  A timeout is
now counted, never averaged, and a run with no reply answers an empty
value.  The rig is ``conftest.py``'s ``cli``: a ``CLITerminal`` on the
DEMO port, which answers ``AT`` with ``OK`` in tens of milliseconds.
"""

from __future__ import annotations

import json


def test_one_ping_answers_its_reply_time(cli):
    """One answered ping: the value is its time in ms, the data one reply and no timeout."""
    # Act
    result = cli.repl.dispatch_full("/ping cmd=AT")

    # Assert
    assert result.success is True, result.error
    assert result.value.endswith("ms"), f"the value is a time: {result.value!r}"
    actual = (result.data["count"], result.data["replies"], result.data["timeouts"])
    assert actual == (1, 1, 0), "one ping, one reply"


def test_several_pings_carry_every_round_trip(cli):
    """count=3: the data holds three reply times, and the stats are over them."""
    # Act
    result = cli.repl.dispatch_full("/ping count=3 cmd=AT")

    # Assert
    data = result.data
    assert len(data["times_ms"]) == 3, "one entry per round trip"
    assert data["min_ms"] <= data["avg_ms"] <= data["max_ms"], "the stats bracket the mean"
    assert result.value == f"{data['avg_ms']:.0f}ms", "the value is the mean, as one token"


def test_a_timeout_is_counted_never_averaged(cli):
    """Every ping timing out: an empty value, None for each time, and no stats to report."""
    # Act -- 1ms is far under the DEMO's reply time, so each read gives up first
    result = cli.repl.dispatch_full("/ping count=2 timeout=1ms cmd=AT")

    # Assert
    assert result.value == "", "no reply, so no reply time, never the timeout as a time"
    actual = (result.data["timeouts"], result.data["times_ms"], result.data["avg_ms"])
    assert actual == (2, [None, None], None), "a timeout is counted, never averaged"


def test_a_plain_ping_prints_each_time_not_the_reply(cli, capsys):
    """At normal level each round trip prints its time; the reply's text is detail, left to verbose."""
    # Arrange
    capsys.readouterr()

    # Act
    cli.repl.dispatch_full("/ping cmd=AT")
    out = capsys.readouterr().out

    # Assert
    assert "AT -- " in out, "the round trip's time"
    assert "OK" not in out, "the device's reply text waits for .verbose"


def test_verbose_adds_the_reply_text(cli, capsys):
    """/ping.verbose prints each reply's text under its time."""
    # Arrange
    capsys.readouterr()

    # Act
    cli.repl.dispatch_full("/ping.verbose cmd=AT")
    out = capsys.readouterr().out

    # Assert
    assert "AT -- " in out, "the round trip's time"
    assert "OK" in out, "the device's reply under it"


def test_a_json_reply_is_laid_out_on_a_screen(cli, capsys):
    """/ping.verbose lays a JSON reply out as the screen lays device JSON out (json_fmt), each line under the time."""
    # Arrange -- a terminal, as the TUI and an interactive CLI are; the rig has color off
    cli.repl.screen_json = True
    capsys.readouterr()

    # Act -- the DEMO answers MEM.INFO with one JSON line, then OK
    cli.repl.dispatch_full("/ping.verbose cmd=MEM.INFO")
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]

    # Assert -- stripped: the CLI's own margin is not what is pinned
    assert '"max_block": 64,' in lines, f"one key per line, as json_fmt lays it out: {lines}"
    assert "OK" in lines, "a line that is not JSON shows as it came"


def test_a_json_reply_stays_one_line_in_a_pipe(cli, capsys):
    """Off a screen, a pipe or MCP, the JSON reply is one line, as it arrived."""
    # Arrange
    cli.repl.screen_json = False
    capsys.readouterr()

    # Act
    cli.repl.dispatch_full("/ping.verbose cmd=MEM.INFO")
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]

    # Assert
    assert any(line.startswith('{"max_block": 64, "address_bits"') for line in lines), lines


def test_a_json_answer_keeps_the_reply_as_it_came(cli, capsys):
    """/ping.verbose.json on a screen: output_lines hold the device's JSON reply as one line, not laid out."""
    # Arrange -- a terminal, where the reply alone would be laid out
    cli.repl.screen_json = True
    capsys.readouterr()

    # Act -- only the envelope prints, itself laid out for the screen
    cli.repl.dispatch_full("/ping.verbose.json cmd=MEM.INFO")
    envelope = json.loads(capsys.readouterr().out)

    # Assert
    replies = [line.strip() for line in envelope["output_lines"]]
    assert any(line.startswith('{"max_block": 64, "address_bits"') for line in replies), replies


def test_quiet_prints_only_the_summary(cli, capsys):
    """/ping.quiet is the level: the summary alone, and the same record."""
    # Arrange
    capsys.readouterr()

    # Act
    result = cli.repl.dispatch_full("/ping.quiet count=2 cmd=AT")
    out = capsys.readouterr().out

    # Assert
    assert "AT -- " not in out, "no round trip lines at quiet"
    assert "2 pings: avg=" in out, "the summary is the result"
    assert result.data["replies"] == 2, "every round trip is still measured"
