"""Tests for the /expect and /expect.regex commands (Phase 4).

The script-runner path (_BLOCKING_COMMANDS dispatch) is unchanged
from before Phase 4; existing tests under tests/test_engine.py and
test_proto_runner.py cover that.

These tests cover the dispatch-callable form (used by MCP):
- block_until capability gate
- happy-path match returns CmdResult.ok(value=<line>)
- timeout returns CmdResult.fail with a useful message
- regex variant
- malformed regex error path
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
