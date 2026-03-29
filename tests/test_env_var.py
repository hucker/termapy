"""Tests for the env_var built-in transform and commands."""

import os

import pytest

from termapy.builtins.plugins.env_var import (
    _ENV, _cli_transform, _handler_list, _handler_reload, _handler_set,
)
from termapy.plugins import PluginContext


class TestEnvVarTransform:
    def test_expands_known_var(self):
        # Arrange
        _ENV["TEST_PORT"] = "COM7"

        # Act
        actual = _cli_transform("connect $(env.TEST_PORT)")

        # Assert
        expected = "connect COM7"
        assert actual == expected  # known var expanded

    def test_unknown_var_raises(self):
        # Arrange — ensure var does not exist
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
        assert actual == expected  # fallback value used

    def test_fallback_ignored_when_set(self):
        # Arrange
        _ENV["MY_PORT"] = "COM7"

        # Act
        actual = _cli_transform("connect $(env.MY_PORT|COM1)")

        # Assert
        expected = "connect COM7"
        assert actual == expected  # real value used, fallback ignored

    def test_empty_fallback(self):
        # Arrange
        _ENV.pop("EMPTY_FB", None)

        # Act
        actual = _cli_transform("prefix$(env.EMPTY_FB|)suffix")

        # Assert
        expected = "prefixsuffix"
        assert actual == expected  # empty fallback produces empty string

    def test_multiple_vars(self):
        # Arrange
        _ENV["DEV_PORT"] = "COM3"
        _ENV["DEV_BAUD"] = "9600"

        # Act
        actual = _cli_transform("AT+PORT=$(env.DEV_PORT),$(env.DEV_BAUD)")

        # Assert
        expected = "AT+PORT=COM3,9600"
        assert actual == expected  # both vars expanded

    def test_no_placeholders_passthrough(self):
        # Act
        actual = _cli_transform("ATZ")

        # Assert
        expected = "ATZ"
        assert actual == expected  # plain string unchanged

    def test_bare_dollar_env_not_expanded(self):
        # Act — no parens, should NOT match
        _ENV["FOO"] = "bar"
        actual = _cli_transform("$env.FOO")

        # Assert
        expected = "$env.FOO"
        assert actual == expected  # bare syntax not matched

    def test_env_is_snapshot(self):
        # Arrange — inject a key only into os.environ
        sentinel = "_TERMAPY_SNAPSHOT_TEST"
        os.environ[sentinel] = "live"

        # Assert — _ENV was captured before the sentinel was set
        assert sentinel not in _ENV  # snapshot does not see later os.environ changes

        # Cleanup
        os.environ.pop(sentinel, None)


class TestEnvCommands:
    def _ctx(self):
        """Create a minimal PluginContext that captures output."""
        output = []
        return PluginContext(write=lambda t, c=None: output.append((t, c))), output

    def test_set_adds_to_snapshot(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV.pop("MY_TEST_VAR", None)

        # Act
        _handler_set(ctx, "MY_TEST_VAR hello_world")

        # Assert
        assert _ENV["MY_TEST_VAR"] == "hello_world"  # var added to snapshot
        assert any("hello_world" in t for t, _ in output)  # confirmation shown

    def test_set_overwrites_existing(self):
        # Arrange
        ctx, _ = self._ctx()
        _ENV["MY_TEST_VAR"] = "old"

        # Act
        _handler_set(ctx, "MY_TEST_VAR new")

        # Assert
        assert _ENV["MY_TEST_VAR"] == "new"  # value overwritten

    def test_set_missing_value(self):
        # Arrange
        ctx, output = self._ctx()

        # Act
        result = _handler_set(ctx, "ONLY_NAME")

        # Assert
        assert not result.success  # handler reports failure
        assert "Usage" in result.error  # usage error returned

    def test_set_value_with_spaces(self):
        # Arrange
        ctx, _ = self._ctx()

        # Act
        _handler_set(ctx, "MY_PATH C:\\Program Files\\App")

        # Assert
        assert _ENV["MY_PATH"] == "C:\\Program Files\\App"  # spaces preserved

    def test_set_then_transform(self):
        # Arrange
        ctx, _ = self._ctx()
        _handler_set(ctx, "CUSTOM_PORT COM99")

        # Act
        actual = _cli_transform("connect $(env.CUSTOM_PORT)")

        # Assert
        expected = "connect COM99"
        assert actual == expected  # set var is expanded by transform

    def test_list_single_var(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV["LIST_TEST"] = "abc"

        # Act
        _handler_list(ctx, "LIST_TEST")

        # Assert
        assert any("LIST_TEST=abc" in t for t, _ in output)  # var shown

    def test_list_glob_pattern(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV["USER_ALPHA"] = "a"
        _ENV["USER_BETA"] = "b"
        _ENV.pop("UNRELATED_XYZ", None)

        # Act
        _handler_list(ctx, "USER_*")

        # Assert
        texts = [t for t, _ in output]
        assert any("USER_ALPHA=a" in t for t in texts)  # first match shown
        assert any("USER_BETA=b" in t for t in texts)  # second match shown
        assert not any("UNRELATED" in t for t in texts)  # non-match excluded

    def test_list_glob_no_matches(self):
        # Arrange
        ctx, output = self._ctx()

        # Act
        result = _handler_list(ctx, "ZZNOEXIST_*")

        # Assert
        assert not result.success  # handler reports failure
        assert "No variables matching" in result.error  # error returned

    def test_list_unknown_var(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV.pop("NOPE_XYZ", None)

        # Act
        result = _handler_list(ctx, "NOPE_XYZ")

        # Assert
        assert not result.success  # handler reports failure
        assert "not set" in result.error  # error returned

    def test_reload_resets_snapshot(self):
        # Arrange
        ctx, output = self._ctx()
        _ENV["SESSION_ONLY"] = "temp"

        # Act
        _handler_reload(ctx, "")

        # Assert — SESSION_ONLY was not in os.environ, so it's gone
        assert "SESSION_ONLY" not in _ENV  # session var cleared by reload
        assert any("reloaded" in t.lower() for t, _ in output)  # confirmation
