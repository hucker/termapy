"""/ping and /ping.quiet on the CLI DEMO rig: the value is the mean reply time, the data every round trip.

Until 2026-11-06 a timed-out ping still put its elapsed time in the
summary, so one ping that timed out answered ``250ms`` (the timeout itself)
and a run's average counted every timeout as a slow reply.  A timeout is
now counted, never averaged, and a run with no reply answers an empty
value.  The rig is ``conftest.py``'s ``cli``: a ``CLITerminal`` on the
DEMO port, which answers ``AT`` with ``OK`` in tens of milliseconds.
"""

from __future__ import annotations


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


def test_quiet_answers_the_same_record(cli, capsys):
    """/ping.quiet answers the same data and value shape, without printing the device's reply."""
    # Arrange
    capsys.readouterr()

    # Act
    result = cli.repl.dispatch_full("/ping.quiet cmd=AT")

    # Assert
    assert result.data["replies"] == 1, "the quiet form still measures the reply"
    assert "OK" not in capsys.readouterr().out, "the device's reply text is not echoed"
