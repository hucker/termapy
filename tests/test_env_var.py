"""Tests for the env_var built-in transform and commands."""

import os

import pytest

from termapy.builtins.commands.env import (
    _ENV,
    _cli_transform,
    _handler_list,
    _handler_reload,
    _handler_set,
)
from termapy.plugins import PluginContext, UsageError


class TestEnvVarTransform:
    def test_expands_known_var(self):
        # Arrange
        _ENV["TEST_PORT"] = "COM7"

        # Act
        actual = _cli_transform("connect $(env.TEST_PORT)")

        # Assert
        expected = "connect COM7"
        assert actual == expected, "known var expanded"

    def test_unknown_var_raises(self):
        # Arrange - ensure var does not exist
        _ENV.pop("NONEXISTENT_XYZ", None)

        # Act / Assert
        with pytest.raises(ValueError, match="NONEXISTENT_XYZ"):
            _cli_transform("open $(env.NONEXISTENT_XYZ)")

    def test_fallback_when_not_set(self):
        # Arrange
        _ENV.pop("MISSING_VAR", None)

        # Act
        actual = _cli_transform("connect $(env.MISSING_VAR|COM1)")

        # Assert
        expected = "connect COM1"
        assert actual == expected, "fallback value used"

    def test_fallback_ignored_when_set(self):
        # Arrange
        _ENV["MY_PORT"] = "COM7"

        # Act
        actual = _cli_transform("connect $(env.MY_PORT|COM1)")

        # Assert
        expected = "connect COM7"
        assert actual == expected, "real value used, fallback ignored"

    def test_empty_fallback(self):
        # Arrange
        _ENV.pop("EMPTY_FB", None)

        # Act -- underscores are literal in the input so the expected
        # string doesn't concatenate two words that spellcheckers flag.
        actual = _cli_transform("prefix_$(env.EMPTY_FB|)_suffix")

        # Assert
        expected = "prefix__suffix"
        assert actual == expected, "empty fallback produces empty string"

    def test_multiple_vars(self):
        # Arrange
        _ENV["DEV_PORT"] = "COM3"
        _ENV["DEV_BAUD"] = "9600"

        # Act
        actual = _cli_transform("AT+PORT=$(env.DEV_PORT),$(env.DEV_BAUD)")

        # Assert
        expected = "AT+PORT=COM3,9600"
        assert actual == expected, "both vars expanded"

    def test_no_placeholders_passthrough(self):
        # Act
        actual = _cli_transform("ATZ")

        # Assert
        expected = "ATZ"
        assert actual == expected, "plain string unchanged"

    def test_bare_dollar_env_not_expanded(self):
        # Act - no parens, should NOT match
        _ENV["FOO"] = "bar"
        actual = _cli_transform("$env.FOO")

        # Assert
        expected = "$env.FOO"
        assert actual == expected, "bare syntax not matched"

    def test_env_is_snapshot(self):
        # Arrange - inject a key only into os.environ
        sentinel = "_TERMAPY_SNAPSHOT_TEST"
        os.environ[sentinel] = "live"

        # Assert - _ENV was captured before the sentinel was set
        assert sentinel not in _ENV, "snapshot does not see later os.environ changes"

        # Cleanup
        os.environ.pop(sentinel, None)


class TestEnvCommands:
    def _ctx(self):
        """Create a minimal PluginContext that captures output."""
        output = []
        from termapy.plugins import IOHandle
        return PluginContext(io=IOHandle(_write=lambda t, c=None: output.append((t, c)))), output

    def test_set_adds_to_snapshot(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV.pop("MY_TEST_VAR", None)

        # Act
        result = _handler_set(ctx, "MY_TEST_VAR hello_world")

        # Assert
        assert _ENV["MY_TEST_VAR"] == "hello_world", "var added to snapshot"
        assert any("hello_world" in t for t, _ in output), "confirmation shown"
        assert result.value == "hello_world", (
            "scripting captures the value just set (mirrors /echo / /verbose)"
        )

    def test_set_overwrites_existing(self):
        # Arrange
        ctx, _ = self._ctx()
        _ENV["MY_TEST_VAR"] = "old"

        # Act
        _handler_set(ctx, "MY_TEST_VAR new")

        # Assert
        assert _ENV["MY_TEST_VAR"] == "new", "value overwritten"

    def test_set_missing_value(self):
        # Arrange
        ctx, output = self._ctx()

        # Act / Assert -- bad arity raises; the dispatcher renders the
        # usage line from the declaration (see test_usage_error.py).
        with pytest.raises(UsageError):
            _handler_set(ctx, "ONLY_NAME")

    def test_set_value_with_spaces(self):
        # Arrange
        ctx, _ = self._ctx()

        # Act
        _handler_set(ctx, "MY_PATH C:\\Program Files\\App")

        # Assert
        assert _ENV["MY_PATH"] == "C:\\Program Files\\App", "spaces preserved"

    def test_set_then_transform(self):
        # Arrange
        ctx, _ = self._ctx()
        _handler_set(ctx, "CUSTOM_PORT COM99")

        # Act
        actual = _cli_transform("connect $(env.CUSTOM_PORT)")

        # Assert
        expected = "connect COM99"
        assert actual == expected, "set var is expanded by transform"

    def test_list_single_var(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV["LIST_TEST"] = "abc"

        # Act
        result = _handler_list(ctx, "LIST_TEST")

        # Assert
        assert any("LIST_TEST=abc" in t for t, _ in output), "var shown"
        assert result.value == "abc", "value returned for scripting"

    def test_list_glob_pattern(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV["USER_ALPHA"] = "a"
        _ENV["USER_BETA"] = "b"
        _ENV.pop("UNRELATED_XYZ", None)

        # Act
        result = _handler_list(ctx, "USER_*")

        # Assert
        texts = [t for t, _ in output]
        assert any("USER_ALPHA=a" in t for t in texts), "first match shown"
        assert any("USER_BETA=b" in t for t in texts), "second match shown"
        assert not any("UNRELATED" in t for t in texts), "non-match excluded"
        # Glob path returns newline-joined NAME=VALUE so scripting gets
        # the same content the user sees, minus the indentation.
        lines = result.value.split("\n")
        assert "USER_ALPHA=a" in lines, "glob value includes first match"
        assert "USER_BETA=b" in lines, "glob value includes second match"

    def test_list_glob_no_matches(self):
        # Arrange
        ctx, output = self._ctx()

        # Act
        result = _handler_list(ctx, "ZZ_NO_EXIST_*")

        # Assert
        assert not result.success, "handler reports failure"
        assert "No variables matching" in result.error, "error returned"

    def test_list_unknown_var(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV.pop("NOPE_XYZ", None)

        # Act
        result = _handler_list(ctx, "NOPE_XYZ")

        # Assert
        assert not result.success, "handler reports failure"
        assert "not set" in result.error, "error returned"

    def test_reload_resets_snapshot(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV["SESSION_ONLY"] = "temp"

        # Act
        result = _handler_reload(ctx, "")

        # Assert - SESSION_ONLY was not in os.environ, so it's gone
        assert "SESSION_ONLY" not in _ENV, "session var cleared by reload"
        assert any("reloaded" in t.lower() for t, _ in output), "confirmation"
        assert int(result.value) == len(_ENV), (
            "reload returns the post-reload variable count for scripting"
        )


class TestMcpEnvGate:
    """Under MCP, environment access is off unless TERMAPY_MCP_ENV_ENABLED.

    The gate exists because an MCP client is a remote/automated peer and
    env vars routinely hold secrets: a bare /env would dump them all,
    and $(env.NAME) would leak them one at a time.  Interactive hosts
    (CLI/TUI) are never gated -- the marker (`_under_mcp`) is only set by
    the --mcp server entry.
    """

    def _ctx(self):
        output = []
        from termapy.plugins import IOHandle
        return (
            PluginContext(io=IOHandle(_write=lambda t, c=None: output.append((t, c)))),
            output,
        )

    def test_not_under_mcp_allows_everything(self, monkeypatch):
        # Arrange -- default process state: not under MCP.
        from termapy import env_flags

        monkeypatch.setattr(env_flags, "_under_mcp", False)
        _ENV["GATE_PORT"] = "COM5"
        ctx, _ = self._ctx()
        # Act / Assert -- transform expands, /env lists.
        assert _cli_transform("open $(env.GATE_PORT)") == "open COM5", (
            "CLI/TUI path expands env normally"
        )
        assert _handler_list(ctx, "GATE_PORT").success, "/env works off-MCP"

    def test_under_mcp_without_flag_blocks_transform(self, monkeypatch):
        # Arrange -- under MCP, opt-in flag off.
        from termapy import env_flags

        monkeypatch.setattr(env_flags, "_under_mcp", True)
        monkeypatch.setattr(env_flags, "MCP_ENV_ENABLED", False)
        _ENV["SECRET_TOKEN"] = "shhh"
        # Act / Assert -- single-var leak refused.
        with pytest.raises(ValueError, match="disabled under MCP"):
            _cli_transform("/print $(env.SECRET_TOKEN)")

    def test_under_mcp_without_flag_passes_plain_text(self, monkeypatch):
        # Arrange -- a command with no placeholder must still flow.
        from termapy import env_flags

        monkeypatch.setattr(env_flags, "_under_mcp", True)
        monkeypatch.setattr(env_flags, "MCP_ENV_ENABLED", False)
        # Act / Assert -- no $(env.) => untouched, no raise.
        assert _cli_transform("rev") == "rev", (
            "plain commands are unaffected by the env gate"
        )

    def test_under_mcp_without_flag_blocks_env_list(self, monkeypatch):
        # Arrange
        from termapy import env_flags

        monkeypatch.setattr(env_flags, "_under_mcp", True)
        monkeypatch.setattr(env_flags, "MCP_ENV_ENABLED", False)
        ctx, _ = self._ctx()
        # Act -- the mass-dump command.
        result = _handler_list(ctx, "")
        # Assert
        assert not result.success, "/env refused under MCP"
        assert "TERMAPY_MCP_ENV_ENABLED" in result.error, (
            "refusal names the opt-in flag"
        )

    def test_under_mcp_with_flag_allows(self, monkeypatch):
        # Arrange -- operator opted in.
        from termapy import env_flags

        monkeypatch.setattr(env_flags, "_under_mcp", True)
        monkeypatch.setattr(env_flags, "MCP_ENV_ENABLED", True)
        _ENV["OPTIN_PORT"] = "COM9"
        ctx, _ = self._ctx()
        # Act / Assert
        assert _cli_transform("open $(env.OPTIN_PORT)") == "open COM9", (
            "opt-in restores $(env.NAME) expansion under MCP"
        )
        assert _handler_list(ctx, "OPTIN_PORT").success, "/env works with opt-in"

    def test_mark_under_mcp_latches(self, monkeypatch):
        # Arrange -- start clean.
        from termapy import env_flags

        monkeypatch.setattr(env_flags, "_under_mcp", False)
        monkeypatch.setattr(env_flags, "MCP_ENV_ENABLED", False)
        assert env_flags.env_access_blocked() is False, "off-MCP not blocked"
        # Act -- the server entry's one-way latch.
        env_flags.mark_under_mcp()
        # Assert
        assert env_flags.env_access_blocked() is True, (
            "marking MCP mode (no flag) blocks env access"
        )
