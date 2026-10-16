"""Tests for the /expect and /expect.regex commands.

There is one /expect (``builtins/commands/expect.py``): until 2026-10-16 the
script runner had its own copy with another default and another timeout
rule (review 2026-10-15, D1).  The engine-level tests below drive the
command and the wait; ``TestInAScript`` runs it from .run files on the CLI
against the DEMO device:

- block_until capability gate
- happy-path match returns CmdResult.ok(value=<line>)
- timeout returns CmdResult.fail with a useful message
- regex variant
- malformed regex error path
- inside a script: a timeout stops the script on every path, a stop ends a
  wait at once, and the default is 5 s (decision 6)
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from termapy.plugins import CapabilitySet, PluginContext
from termapy.repl import ReplEngine

# ── Fixture: engine with block_until enabled ────────────────────────────────


@pytest.fixture
def env(tmp_path):
    """Create an engine + ctx that allows /expect (block_until=True)."""
    cfg = {"port": "DEMO", "eol": "\r", "encoding": "utf-8"}
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output: list = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
    from termapy.plugins import IOHandle, SerialHandle
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        io=IOHandle(_write=lambda t, c=None: output.append((t, c))),
        serial=SerialHandle(is_connected=lambda: True),
        wait_for_match=eng.wait_for_match,
    )
    ctx.capabilities = CapabilitySet(block_until=True)
    eng.set_context(ctx)
    ctx.ns("flags")["echo"] = False
    ctx.ns("flags")["output_level"] = "verbose"
    return eng, ctx, output


# ── Capability gate ─────────────────────────────────────────────────────────


class TestCapabilityGate:
    def test_baseline_ctx_blocks_expect(self, tmp_path):
        """Without block_until, /expect refuses and names the missing capability."""
        # Arrange — baseline ctx WITHOUT block_until
        cfg = {"port": "DEMO"}
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None)
        from termapy.plugins import IOHandle, SerialHandle
        ctx = PluginContext(
            cfg=cfg,
            config_path=str(config_path),
            io=IOHandle(_write=lambda t, c=None: None),
            serial=SerialHandle(is_connected=lambda: True),
        )
        ctx.capabilities = CapabilitySet()  # NO block_until
        eng.set_context(ctx)
        # Act
        result = eng.dispatch("expect match=anything")
        # Assert
        assert result.success is False, "no block_until = /expect blocked"
        assert "block_until" in result.error, "error names the missing capability"


# ── Happy path: predicate matches ───────────────────────────────────────────


class TestExpectMatch:
    def test_substring_match_returns_line(self, env):
        """A line fed before /expect starts still matches; the matched line is the value."""
        # Arrange — feed the engine a line BEFORE calling /expect (retroactive scan)
        eng, ctx, _output = env
        eng.feed_lines(["READY"])
        # Act — match= must be LAST (rest_keyword); see parse_keywords docstring
        result = eng.dispatch("expect timeout=1s match=READY")
        # Assert
        assert result.success is True, "match found"
        assert "READY" in result.value, "matched line is the value"

    def test_regex_match_returns_line(self, env):
        """/expect.regex matches an anchored pattern and returns the whole matched line."""
        # Arrange
        eng, ctx, _output = env
        eng.feed_lines(["+STATUS: 42"])
        # Act
        result = eng.dispatch(
            "expect.regex timeout=1s match=^\\+STATUS: \\d+$"
        )
        # Assert
        assert result.success is True, "regex match"
        assert "+STATUS: 42" in result.value, "matched line returned"

    def test_match_arrives_after_call_starts(self, env):
        """A line arriving from another thread mid-wait wakes /expect and succeeds."""
        # Arrange
        eng, ctx, _output = env

        # Feed a line on a delay from another thread.
        def delayed_feed():
            time.sleep(0.1)
            eng.feed_lines(["LATE"])

        threading.Thread(target=delayed_feed, daemon=True).start()
        # Act
        result = eng.dispatch("expect timeout=2s match=LATE")
        # Assert
        assert result.success is True, "match arrived after call"
        assert "LATE" in result.value, "matched line returned"


# ── A match consumes: the sequence and lifetime cases ───────────────────────


def _wait_for_predicate(eng, timeout: float = 2.0) -> None:
    """Block until /expect running on another thread has installed its predicate."""
    deadline = time.monotonic() + timeout
    while eng._expect_predicate is None:
        assert time.monotonic() < deadline, "expect never started waiting"
        time.sleep(0.005)


class TestExpectConsumes:
    """expect-consumes-to-match: after a match the buffer holds only later lines."""

    def test_second_expect_waits_for_a_new_reply(self, env):
        """Two AT / expect OK pairs each need their own OK; the first reply is not matched twice."""
        # Arrange -- the device answered the first AT
        eng, _ctx, _output = env
        eng.feed_lines(["OK"])
        first = eng.dispatch("expect timeout=1s match=OK")

        # Act -- the second AT has not been answered yet
        second = eng.dispatch("expect timeout=150ms match=OK")
        eng.feed_lines(["OK"])
        third = eng.dispatch("expect timeout=1s match=OK")

        # Assert
        assert first.success is True, "the first reply matches"
        assert second.success is False, (
            "a consumed reply must not satisfy the next expect -- the script "
            "would run ahead of the device"
        )
        assert third.success is True, "a new reply matches"

    def test_a_line_after_a_live_match_in_the_same_batch_stays(self, env):
        """A line read in the same chunk as a live match can still be matched by the next expect."""
        # Arrange -- expect A is waiting when "A" and "B" arrive in one read
        eng, _ctx, _output = env
        results: dict = {}
        waiter = threading.Thread(
            target=lambda: results.setdefault("a", eng.dispatch("expect timeout=2s match=A"))
        )
        waiter.start()
        _wait_for_predicate(eng)

        # Act
        eng.feed_lines(["A", "B"])
        waiter.join(timeout=2.0)
        second = eng.dispatch("expect timeout=150ms match=B")

        # Assert
        assert results["a"].success is True, "the live match wakes the waiter"
        assert second.success is True, (
            "B arrived after the match and must still be buffered; it was "
            "dropped when feed_lines returned on the match"
        )

    def test_a_scanned_match_consumes_only_through_itself(self, env):
        """A match found among buffered lines consumes it and what came before, not what came after."""
        # Arrange
        eng, _ctx, _output = env
        eng.feed_lines(["boot", "READY", "after"])

        # Act
        result = eng.dispatch("expect timeout=1s match=READY")

        # Assert
        actual = list(eng._recent_lines)
        expected = ["after"]
        assert result.success is True, "the buffered line matches"
        assert actual == expected, "lines through the match are consumed, later ones kept"


class TestRecentLinesBuffer:
    """The buffer the waiters read is taken atomically and stays bounded."""

    def test_lines_after_a_terminator_stay_buffered(self, env):
        """wait_for_lines stops at its terminator and leaves the later lines for the next drain."""
        # Arrange
        eng, _ctx, _output = env
        eng.feed_lines(["a", "b", "END", "c"])

        # Act
        actual_collected = eng.wait_for_lines(0.2, terminator="^END$")
        actual_left = eng.drain_recent_lines()

        # Assert
        assert actual_collected == ["a", "b"], "lines before the terminator are the reply"
        assert actual_left == ["c"], "the line after the terminator is not thrown away"

    def test_every_line_fed_while_wait_for_lines_polls_is_collected(self, env):
        """Lines the reader appends between polls are all collected, in order, none lost."""
        # Arrange -- a feeder thread racing the poll loop, in small batches.
        # Fewer lines than the 100-line ring holds, so any loss is the race,
        # not the ring evicting its oldest line by design.
        eng, _ctx, _output = env
        expected = [f"line {i}" for i in range(90)]

        def feed():
            for i in range(0, len(expected), 3):
                eng.feed_lines(expected[i:i + 3])
                time.sleep(0)  # yield, so the poller interleaves with the feed
            eng.feed_lines(["DONE"])

        feeder = threading.Thread(target=feed)

        # Act
        feeder.start()
        actual = eng.wait_for_lines(5.0, terminator="^DONE$", idle_gap=2.0)
        feeder.join(timeout=2.0)

        # Assert
        assert actual == expected, "a snapshot-then-clear would drop lines appended in between"

    def test_buffer_stays_bounded_with_no_waiter(self, env):
        """With nobody expecting, a long run keeps only the newest 100 lines."""
        # Arrange / Act
        eng, _ctx, _output = env
        eng.feed_lines([f"line {i}" for i in range(1000)])

        # Assert
        actual = list(eng._recent_lines)
        expected = [f"line {i}" for i in range(900, 1000)]
        assert actual == expected, "the ring keeps the newest lines and nothing more"


# ── Timeout / error paths ───────────────────────────────────────────────────


class TestExpectTimeout:
    def test_no_match_within_timeout_fails(self, env):
        """No match before the deadline fails with an error that says it timed out."""
        # Arrange / Act — match= must be last (rest_keyword)
        eng, ctx, _output = env
        result = eng.dispatch("expect timeout=100ms match=NEVERAPPEARS")
        # Assert
        assert result.success is False, "timeout = failure"
        assert "timeout" in result.error.lower(), "error names timeout"

    def test_missing_match_keyword_fails(self, env):
        """Omitting match= is an error naming the keyword, not an expect-anything wait."""
        # Arrange / Act
        eng, ctx, _output = env
        result = eng.dispatch("expect timeout=1s")
        # Assert
        assert result.success is False, "missing match= = usage error"
        assert "match" in result.error.lower(), "error names the missing keyword"

    def test_invalid_regex_fails_fast(self, env):
        """A malformed pattern fails with a regex error instead of waiting out the timeout."""
        # Arrange / Act — match= last
        eng, ctx, _output = env
        result = eng.dispatch("expect.regex timeout=1s match=(unclosed")
        # Assert
        assert result.success is False, "invalid regex = failure"
        assert "regex" in result.error.lower(), "error names regex"

    def test_invalid_timeout_fails(self, env):
        """An unparseable timeout= value fails the command rather than using a default."""
        # Arrange / Act — note: timeout= comes BEFORE match= so it's parsed
        eng, ctx, _output = env
        result = eng.dispatch("expect timeout=notaduration match=X")
        # Assert
        assert result.success is False, "bad duration = failure"


# ── Inside a .run script, on the CLI against the DEMO device ───────────────


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port, its cfg folder under tmp_path."""
    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

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


def _script(tmp_path, *lines: str):
    """A .run file holding ``lines``."""
    path = tmp_path / "check.run"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestInAScript:
    """Decision 6: 5 s by default everywhere; a timeout inside a script stops it, however the line is spelled."""

    def test_a_match_lets_the_script_continue(self, cli, tmp_path, capsys):
        """A reply the device sends is matched, said so, and the next line runs."""
        # Arrange
        script = _script(tmp_path, "AT", "/expect timeout=2s match=OK", "/print reached-the-end")

        # Act
        cli.repl.dispatch_full(f"/run {script}")
        out = capsys.readouterr().out

        # Assert
        assert 'Expect "OK" matched' in out
        assert "reached-the-end" in out, "a matched expect does not stop the script"

    def test_a_timeout_stops_the_script(self, cli, tmp_path, capsys):
        """A missed expect fails with the timeout and the script stops there."""
        # Arrange
        script = _script(tmp_path, "/expect timeout=300ms match=NEVER", "/print reached-the-end")

        # Act
        cli.repl.dispatch_full(f"/run {script}")
        out = capsys.readouterr().out

        # Assert
        assert 'Expect "NEVER" timeout after 300ms' in out
        assert "reached-the-end" not in out, "the line after a missed expect never runs"

    def test_a_captured_timeout_stops_the_script_too(self, cli, tmp_path, capsys):
        """$(L) <- /expect is the same command: a timeout stops the script, as the plain line does.

        Before 2026-10-16 a capture line was not intercepted by the script
        runner, so it took the other /expect, which never stopped a script.
        """
        # Arrange
        script = _script(tmp_path, "$(L) <- /expect timeout=300ms match=NEVER", "/print reached-the-end")

        # Act
        cli.repl.dispatch_full(f"/run {script}")
        out = capsys.readouterr().out

        # Assert
        assert 'Expect "NEVER" timeout after 300ms' in out
        assert "reached-the-end" not in out, "the capture spelling stops the script as well"

    def test_a_stop_ends_a_long_wait_at_once(self, cli, tmp_path, capsys):
        """A stop (Escape) during a 5 s expect ends the wait within a poll, not at the timeout."""
        # Arrange
        script = _script(tmp_path, "/expect timeout=5s match=NEVER", "/print reached-the-end")
        worker = threading.Thread(target=lambda: cli.repl.dispatch_full(f"/run {script}"))

        # Act
        worker.start()
        deadline = time.monotonic() + 2.0
        while cli.repl._expect_predicate is None and time.monotonic() < deadline:
            time.sleep(0.01)
        t0 = time.monotonic()
        cli.repl._script_stop.set()
        worker.join(timeout=3.0)
        elapsed = time.monotonic() - t0

        # Assert
        assert not worker.is_alive(), "the run ended"
        assert elapsed < 1.0, f"the stop ended the wait at once; it took {elapsed:.2f}s"
        assert "reached-the-end" not in capsys.readouterr().out

    def test_the_default_timeout_is_five_seconds(self, cli):
        """Without timeout= /expect waits 5 s, typed, over MCP and in a script alike."""
        # Arrange
        params = {spec.name: spec for spec in cli.repl._plugins["expect"].params}

        # Assert
        assert params["timeout"].default == 5.0
