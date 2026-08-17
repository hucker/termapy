"""Tests for /find -- interactive scrollback match navigation.

Covers:

- /find <pattern> populates module state, indexes to first match.
- /find with no args closes any active search.
- /find xyz (no matches) sets total=0 / index=-1.
- /find.next / /find.prev wrap around at boundaries.
- /find.clear closes regardless of state.
- update_find_bar callback fires with the right payload when set.
- update_find_bar=None (CLI/MCP host) is a no-op.
- Shared find_matches helper returns (line_no, ANSI-stripped) pairs.
"""

from __future__ import annotations

import json

import pytest

from termapy.builtins.commands import find as _find
from termapy.builtins.commands.grep import find_matches
from termapy.plugins import CapabilitySet
from termapy.repl import ReplEngine


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Minimal engine + a stubbed scrollback for /find to search.

    Resets the /find module-level state before and after each test
    so leaked state from one test can't contaminate the next (the
    plugin is a singleton at module scope).
    """
    cfg = {"port": "COM4", "baud_rate": 115200}
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)

    output: list = []
    eng = ReplEngine(
        cfg, str(config_path), lambda t, c=None: output.append((t, c)),
    )
    flags = eng.ctx.ns("flags")
    flags["output_level"] = "verbose"
    # /find is gated by `needs=CapabilitySet(interactive=True)`; the
    # bare ReplEngine fixture starts with no capabilities, so opt in
    # here the same way test_builtins / test_cli do.
    eng.ctx.capabilities = CapabilitySet(interactive=True)

    # Stub get_screen_text to return a fixed scrollback so tests
    # don't depend on actual terminal rendering.
    scrollback_lines = [
        "boot OK",            # 1
        "AT response",        # 2
        "ERROR: rx timeout",  # 3
        "INFO: retrying",     # 4
        "AT response",        # 5
        "INFO: ok",           # 6
        "ERROR: parse fail",  # 7
        "AT response",        # 8
    ]
    fake_scrollback = "\n".join(scrollback_lines)
    eng.ctx.ui._get_screen_text_impl = lambda: fake_scrollback

    # Capture update_find_bar payloads so tests can assert on what
    # the UI would have been told.
    payloads: list = []
    eng.ctx.internal.update_find_bar = payloads.append

    # Reset module-level state before the test.
    _find._active = None
    _find._max_count = _find._DEFAULT_MAX_COUNT

    yield eng, payloads, scrollback_lines

    # And after, so the next test starts clean.
    _find._active = None
    _find._max_count = _find._DEFAULT_MAX_COUNT


class TestFindHandler:
    def test_find_populates_state_and_seeks_first_match(self, env):
        # Arrange
        eng, payloads, _ = env

        # Act -- three ERROR/AT lines should match "AT".
        result = eng.dispatch("find AT")

        # Assert
        assert result.success, f"find succeeded: {result.error!r}"
        assert _find._active is not None, "module state populated"
        assert _find._active.pattern == "AT", "pattern stored"
        assert len(_find._active.matches) == 3, "three AT-matching lines"
        assert _find._active.current == 0, "seeks to first match"
        # The UI was told the new state.
        assert len(payloads) == 1, "update_find_bar called once"
        assert payloads[0]["total"] == 3, "payload reports 3 matches"
        assert payloads[0]["index"] == 0, "payload index 0"

    def test_find_with_no_args_closes_active_search(self, env):
        # Arrange -- an open search.
        eng, payloads, _ = env
        eng.dispatch("find AT")
        payloads.clear()

        # Act -- bare /find closes it.
        result = eng.dispatch("find")

        # Assert
        assert result.success, "bare /find succeeds"
        assert _find._active is None, "state cleared"
        assert payloads == [None], "UI told to hide (state=None)"

    def test_find_no_matches_reports_zero_total(self, env):
        # Arrange / Act
        eng, payloads, _ = env
        result = eng.dispatch("find nonexistent_xyzzy")

        # Assert -- handler still succeeds (a zero-result search is
        # a valid outcome, not an error), but the FindBar shows 0/0.
        assert result.success, "zero-match search is still a success"
        assert _find._active is not None, "state still populated"
        assert _find._active.matches == [], "empty match list"
        assert payloads[-1]["total"] == 0, "payload total=0"
        assert payloads[-1]["index"] == -1, "payload index=-1 sentinel"

    def test_find_case_insensitive(self, env):
        # Arrange / Act -- uppercase pattern matches lower-case ERROR lines.
        eng, _, _ = env
        eng.dispatch("find error")

        # Assert
        assert _find._active is not None, "state populated"
        assert len(_find._active.matches) == 2, (
            "two ERROR lines matched case-insensitively"
        )


class TestNextPrev:
    def test_next_increments_and_wraps_at_end(self, env):
        # Arrange -- three matches: indices 0, 1, 2.
        eng, payloads, _ = env
        eng.dispatch("find AT")
        payloads.clear()

        # Act -- next, next, next (should wrap back to 0).
        eng.dispatch("find.next")
        assert _find._active.current == 1, "after one next: index 1"
        eng.dispatch("find.next")
        assert _find._active.current == 2, "after two next: index 2"
        eng.dispatch("find.next")

        # Assert -- third next wraps to 0.
        assert _find._active.current == 0, "wraps to 0 at end"
        # All three steps pushed payloads.
        assert len(payloads) == 3, "three update_find_bar calls"

    def test_prev_decrements_and_wraps_at_start(self, env):
        # Arrange
        eng, _, _ = env
        eng.dispatch("find AT")

        # Act -- from index 0, prev wraps to last (index 2).
        eng.dispatch("find.prev")

        # Assert
        assert _find._active.current == 2, "wraps from 0 to last index"

    def test_next_without_active_search_fails(self, env):
        # Arrange / Act -- no /find yet.
        eng, _, _ = env
        result = eng.dispatch("find.next")

        # Assert
        assert not result.success, "no-op when nothing to navigate"
        assert "No active find" in result.error, "clear error message"

    def test_next_with_zero_matches_fails(self, env):
        # Arrange
        eng, _, _ = env
        eng.dispatch("find nonexistent_xyzzy")

        # Act
        result = eng.dispatch("find.next")

        # Assert
        assert not result.success, "can't navigate zero-result list"


class TestClear:
    def test_clear_closes_active_search(self, env):
        # Arrange
        eng, payloads, _ = env
        eng.dispatch("find AT")
        payloads.clear()

        # Act
        result = eng.dispatch("find.clear")

        # Assert
        assert result.success, "clear succeeds"
        assert _find._active is None, "state gone"
        assert payloads == [None], "UI told to hide"

    def test_clear_on_idle_is_silent_success(self, env):
        # Arrange / Act -- clear with no active search.
        eng, payloads, _ = env
        result = eng.dispatch("find.clear")

        # Assert -- silent success; UI is still told to hide (idempotent).
        assert result.success, "clear is idempotent"
        assert _find._active is None, "still cleared"
        assert payloads == [None], "UI told to hide (idempotent)"


class TestUiCallback:
    def test_update_find_bar_none_means_no_ui_no_error(self, env):
        # Arrange -- a CLI/MCP-style host with no UI callback.
        eng, _, _ = env
        eng.ctx.internal.update_find_bar = None

        # Act -- /find should NOT crash; it still runs the search.
        result = eng.dispatch("find AT")

        # Assert
        assert result.success, "no-UI host still runs /find logic"
        assert _find._active is not None, "state still populated"

    def test_payload_carries_line_no_and_snippet(self, env):
        # Arrange / Act
        eng, payloads, lines = env
        eng.dispatch("find ERROR")

        # Assert -- the payload's line_no points at the FIRST matching
        # line (1-based), and snippet is its text.
        payload = payloads[-1]
        assert payload["line_no"] == 3, (
            "first ERROR is line 3 (1-based)"
        )
        assert "ERROR: rx timeout" in payload["snippet"], (
            "snippet is the matching line text"
        )


class TestFindMatchesHelper:
    """The shared helper that powers both /grep and /find."""

    def test_returns_matches_in_order(self):
        # Arrange
        text = "alpha\nbeta\nALPHA\ngamma\nalpha"

        # Act
        matches, err = find_matches(text, "alpha")

        # Assert
        assert err is None, "no error on valid pattern"
        assert [match[0] for match in matches] == [1, 3, 5], (
            "1-based line indices of all three 'alpha' lines"
        )

    def test_strips_ansi_from_snippet(self):
        # Arrange
        text = "\x1b[31mERROR\x1b[0m: failure"

        # Act
        matches, err = find_matches(text, "ERROR")

        # Assert
        assert err is None, "valid pattern"
        assert len(matches) == 1, "one match"
        assert "\x1b" not in matches[0][1], (
            "ANSI escapes stripped from snippet"
        )

    def test_invalid_regex_returns_error(self):
        # Arrange / Act
        matches, err = find_matches("any", "[unterminated")

        # Assert
        assert matches == [], "no matches on bad pattern"
        assert err is not None and "Invalid pattern" in err, (
            "clear error name"
        )

    def test_empty_text_returns_error(self):
        # Arrange / Act -- CLI/MCP returns "" from get_screen_text.
        matches, err = find_matches("", "anything")

        # Assert
        assert matches == [], "no matches on empty text"
        assert err is not None and "No scrollback" in err, (
            "clear error name for the no-buffer case"
        )

    def test_is_noise_filter_skips_lines(self):
        # Arrange
        text = "real match\n/find foo\nanother real match"

        def is_noise(line: str) -> bool:
            return "/find" in line

        # Act
        matches, _ = find_matches(text, "match", is_noise=is_noise)

        # Assert -- the /find line is dropped despite containing "atch".
        assert len(matches) == 2, "only the two real-match lines kept"
        assert all("/find" not in line for _, line in matches), (
            "noise filter excludes /find echo"
        )


class TestMaxCount:
    """Per-session match-count cap, default 100, override via /find.max_count."""

    def test_find_with_too_many_matches_fails(self, env, monkeypatch):
        # Arrange -- stub get_screen_text to return 150 lines that
        # all match the same pattern.
        eng, payloads, _ = env
        many = "\n".join(f"X line {i}" for i in range(150))
        eng.ctx.ui._get_screen_text_impl = lambda: many

        # Act
        result = eng.dispatch("find X")

        # Assert -- cap exceeded; clear message + the override-command
        # suggestion.
        assert not result.success, "cap exceeded -> fail"
        assert "Too many matches" in result.error, (
            f"clear cap-exceeded message; got {result.error!r}"
        )
        assert "150" in result.error and "100" in result.error, (
            "message names the actual count and the current cap"
        )
        assert "find.max_count" in result.error, (
            "message suggests the override command"
        )
        assert _find._active is None, "no state populated on refuse"

    def test_max_count_override_raises_cap(self, env, monkeypatch):
        # Arrange -- 150 matches, default cap is 100.
        eng, payloads, _ = env
        many = "\n".join(f"Y line {i}" for i in range(150))
        eng.ctx.ui._get_screen_text_impl = lambda: many

        # Act -- raise the cap to 200, then retry the find.
        raise_result = eng.dispatch("find.max_count 200")
        result = eng.dispatch("find Y")

        # Assert
        assert raise_result.success, "raising the cap succeeds"
        assert _find._max_count == 200, "module state updated"
        assert result.success, "find now under the new cap"
        assert _find._active is not None and len(_find._active.matches) == 150

    def test_max_count_bare_shows_current(self, env):
        # Arrange / Act
        eng, _, _ = env
        result = eng.dispatch("find.max_count")

        # Assert -- bare invocation reports the current value.
        assert result.success, "bare /find.max_count is informational"
        assert result.value == str(_find._DEFAULT_MAX_COUNT), (
            f"value reports current cap; got {result.value!r}"
        )

    def test_max_count_bad_arg_returns_usage(self, env):
        # Arrange / Act
        eng, _, _ = env
        result = eng.dispatch("find.max_count abc")

        # Assert
        assert not result.success, "non-integer is a usage error"
        assert "Usage" in result.error, "clear usage hint"
        assert "current" in result.error.lower(), (
            "message includes the current cap value for context"
        )

    def test_max_count_zero_rejected(self, env):
        # Arrange / Act
        eng, _, _ = env
        result = eng.dispatch("find.max_count 0")

        # Assert -- a cap of zero would render /find unusable.
        assert not result.success, "rejected"
        assert ">= 1" in result.error, "explains the constraint"


class TestStateShape:
    """The UI build the frozen view from current_state(); verify shape."""

    def test_active_state_includes_matches_and_scrollback(self, env):
        # Arrange / Act
        eng, _, lines = env
        eng.dispatch("find AT")

        # Assert -- payload carries the full match list AND the
        # captured scrollback so the UI can construct the
        # highlighted snapshot in one pass.
        state = _find.current_state()
        assert state is not None, "active state returned"
        assert "matches" in state, "includes the match list"
        assert len(state["matches"]) == 3, "all three AT matches present"
        assert "scrollback_text" in state, "includes the snapshot text"
        assert "AT response" in state["scrollback_text"], (
            "scrollback text is the actual buffer content"
        )

    def test_idle_state_returns_none(self, env):
        # Arrange -- no /find ran.
        # Act
        state = _find.current_state()

        # Assert
        assert state is None, "idle -> None (signals UI to hide)"
