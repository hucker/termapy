"""Tests for the user-defined variables plugin (var.py)."""

import pytest

from termapy.builtins.plugins.var import (
    _VARS,
    _VAR_REF_RE,
    clear_vars,
    expand_vars,
    rewrite_assignment,
)


@pytest.fixture(autouse=True)
def _clean_vars():
    """Clear variables before and after each test."""
    _VARS.clear()
    yield
    _VARS.clear()


# ── expand_vars ──────────────────────────────────────────────────────────────


class TestExpandVars:
    """Tests for variable expansion in strings."""

    def test_expand_known_var(self):
        # Arrange
        _VARS["PORT"] = "COM7"

        # Act
        actual = expand_vars("AT+PORT=$PORT")
        expected = "AT+PORT=COM7"

        # Assert — known variable is expanded
        assert actual == expected

    def test_expand_unknown_var_left_as_is(self):
        # Act
        actual = expand_vars("$UNKNOWN stays")
        expected = "$UNKNOWN stays"

        # Assert — unknown variable left as literal
        assert actual == expected

    def test_expand_multiple_vars(self):
        # Arrange
        _VARS["A"] = "hello"
        _VARS["B"] = "world"

        # Act
        actual = expand_vars("$A $B")
        expected = "hello world"

        # Assert — both variables expanded
        assert actual == expected

    def test_expand_mixed_known_unknown(self):
        # Arrange
        _VARS["KNOWN"] = "yes"

        # Act
        actual = expand_vars("$KNOWN and $MISSING")
        expected = "yes and $MISSING"

        # Assert — known expanded, unknown left
        assert actual == expected

    def test_no_vars_in_string(self):
        # Act
        actual = expand_vars("no variables here")
        expected = "no variables here"

        # Assert — string unchanged
        assert actual == expected

    def test_lowercase_var(self):
        # Arrange
        _VARS["port"] = "COM7"

        # Act
        actual = expand_vars("$port is set")
        expected = "COM7 is set"

        # Assert — lowercase variable names work
        assert actual == expected

    def test_case_sensitive(self):
        # Arrange
        _VARS["PORT"] = "COM7"

        # Act
        actual = expand_vars("$port stays")
        expected = "$port stays"

        # Assert — $port != $PORT (case-sensitive)
        assert actual == expected

    def test_var_with_underscore(self):
        # Arrange
        _VARS["MY_VAR"] = "value"

        # Act
        actual = expand_vars("$MY_VAR")
        expected = "value"

        # Assert — underscores in name work
        assert actual == expected

    def test_var_with_digits(self):
        # Arrange
        _VARS["REG0"] = "100"

        # Act
        actual = expand_vars("$REG0")
        expected = "100"

        # Assert — digits in name work
        assert actual == expected

    def test_var_adjacent_to_text(self):
        # Arrange
        _VARS["PORT"] = "COM7"

        # Act — var followed by non-word char
        actual = expand_vars("port=$PORT,baud=115200")
        expected = "port=COM7,baud=115200"

        # Assert — expanded correctly at boundary
        assert actual == expected


# ── clear_vars ───────────────────────────────────────────────────────────────


class TestClearVars:
    """Tests for clearing variables."""

    def test_clear_removes_all(self):
        # Arrange
        _VARS["A"] = "1"
        _VARS["B"] = "2"

        # Act
        clear_vars()

        # Assert — all variables removed
        assert _VARS == {}

    def test_clear_empty_is_ok(self):
        # Act — no error on empty dict
        clear_vars()

        # Assert
        assert _VARS == {}


# ── Variable name regex ──────────────────────────────────────────────────────


class TestVarNameRegex:
    """Tests for the $VAR pattern matching."""

    @pytest.mark.parametrize("text,expected_name", [
        ("$PORT", "PORT"),
        ("$port", "port"),
        ("$A", "A"),
        ("$MY_VAR", "MY_VAR"),
        ("$my_var", "my_var"),
        ("$REG0", "REG0"),
        ("$_PRIVATE", "_PRIVATE"),
    ])
    def test_valid_var_names(self, text, expected_name):
        # Act
        m = _VAR_REF_RE.search(text)

        # Assert — matches and captures correct name
        assert m is not None
        assert m.group(1) == expected_name

    @pytest.mark.parametrize("text", [
        "$123",        # starts with digit
        "$(env.X)",    # env var syntax
    ])
    def test_invalid_var_names(self, text):
        # Act
        m = _VAR_REF_RE.search(text)

        # Assert — does not match as a user variable
        assert m is None


# ── rewrite_assignment ───────────────────────────────────────────────────────


class TestRewriteAssignment:
    """Tests for the $VAR = value line rewriting."""

    def test_basic_assignment(self):
        # Act
        actual = rewrite_assignment("$PORT = COM7")
        expected = "var.set PORT COM7"

        # Assert — rewritten to var.set command
        assert actual == expected

    def test_no_spaces(self):
        # Act
        actual = rewrite_assignment("$PORT=COM7")
        expected = "var.set PORT COM7"

        # Assert
        assert actual == expected

    def test_extra_spaces(self):
        # Act
        actual = rewrite_assignment("$PORT  =  COM7")
        expected = "var.set PORT COM7"

        # Assert
        assert actual == expected

    def test_value_with_spaces(self):
        # Act
        actual = rewrite_assignment("$MSG = hello world")
        expected = "var.set MSG hello world"

        # Assert — value includes everything after =
        assert actual == expected

    def test_lowercase_rewritten(self):
        # Act
        actual = rewrite_assignment("$port = COM7")
        expected = "var.set port COM7"

        # Assert — lowercase works
        assert actual == expected

    def test_no_value_not_rewritten(self):
        # Act
        actual = rewrite_assignment("$PORT =")

        # Assert — empty value rejected
        assert actual is None

    def test_no_dollar_not_rewritten(self):
        # Act
        actual = rewrite_assignment("PORT = COM7")

        # Assert — needs $ prefix
        assert actual is None

    def test_repl_command_not_rewritten(self):
        # Act
        actual = rewrite_assignment("/var.set PORT COM7")

        # Assert — REPL command not matched
        assert actual is None

    def test_serial_command_not_rewritten(self):
        # Act
        actual = rewrite_assignment("AT+PORT=COM7")

        # Assert — AT command not matched
        assert actual is None
