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
from pathlib import Path

import pytest

from termapy.plugins import CapabilitySet, PluginContext
from termapy.repl import ReplEngine


# ── Fixture: engine with block_until enabled ────────────────────────────────


@pytest.fixture
def env(tmp_path):
    """Create an engine + ctx that allows /expect (block_until=True)."""
    cfg = {"port": "DEMO", "line_ending": "\r", "encoding": "utf-8"}
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output: list = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
    ctx = PluginContext(
        write=lambda t, c=None: output.append((t, c)),
        cfg=cfg,
        config_path=str(config_path),
        is_connected=lambda: True,
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
        # Arrange — baseline ctx WITHOUT block_until
        cfg = {"port": "DEMO"}
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None)
        ctx = PluginContext(
            write=lambda t, c=None: None,
            cfg=cfg,
            config_path=str(config_path),
            is_connected=lambda: True,
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
        # Arrange — feed the engine a line BEFORE calling /expect (retroactive scan)
        eng, ctx, _output = env
        eng.feed_lines(["READY"])
        # Act — match= must be LAST (rest_keyword); see parse_keywords docstring
        result = eng.dispatch("expect timeout=1s match=READY")
        # Assert
        assert result.success is True, "match found"
        assert "READY" in result.value, "matched line is the value"

    def test_regex_match_returns_line(self, env):
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


# ── Timeout / error paths ───────────────────────────────────────────────────


class TestExpectTimeout:
    def test_no_match_within_timeout_fails(self, env):
        # Arrange / Act — match= must be last (rest_keyword)
        eng, ctx, _output = env
        result = eng.dispatch("expect timeout=100ms match=NEVERAPPEARS")
        # Assert
        assert result.success is False, "timeout = failure"
        assert "timeout" in result.error.lower(), "error names timeout"

    def test_missing_match_keyword_fails(self, env):
        # Arrange / Act
        eng, ctx, _output = env
        result = eng.dispatch("expect timeout=1s")
        # Assert
        assert result.success is False, "missing match= = usage error"
        assert "match" in result.error.lower(), "error names the missing keyword"

    def test_invalid_regex_fails_fast(self, env):
        # Arrange / Act — match= last
        eng, ctx, _output = env
        result = eng.dispatch("expect.regex timeout=1s match=(unclosed")
        # Assert
        assert result.success is False, "invalid regex = failure"
        assert "regex" in result.error.lower(), "error names regex"

    def test_invalid_timeout_fails(self, env):
        # Arrange / Act — note: timeout= comes BEFORE match= so it's parsed
        eng, ctx, _output = env
        result = eng.dispatch("expect timeout=notaduration match=X")
        # Assert
        assert result.success is False, "bad duration = failure"
