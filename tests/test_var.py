"""Tests for the user-defined variables plugin (var.py)."""

import json
from pathlib import Path

import pytest

from termapy.builtins.plugins.var import (
    _VARS,
    _VAR_REF_RE,
    check_bare_dollar,
    clear_vars,
    expand_vars,
    rewrite_assignment,
    set_start_time_vars,
)
from termapy.plugins import DirectiveInfo, DirectiveResult
from termapy.repl import ReplEngine


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
        actual = expand_vars("AT+PORT=$(PORT)")
        expected = "AT+PORT=COM7"

        # Assert — known variable is expanded
        assert actual == expected

    def test_expand_unknown_var_left_as_is(self):
        # Act
        actual = expand_vars("$(UNKNOWN) stays")
        expected = "$(UNKNOWN) stays"

        # Assert — unknown variable left as literal
        assert actual == expected

    def test_expand_multiple_vars(self):
        # Arrange
        _VARS["A"] = "hello"
        _VARS["B"] = "world"

        # Act
        actual = expand_vars("$(A) $(B)")
        expected = "hello world"

        # Assert — both variables expanded
        assert actual == expected

    def test_expand_mixed_known_unknown(self):
        # Arrange
        _VARS["KNOWN"] = "yes"

        # Act
        actual = expand_vars("$(KNOWN) and $(MISSING)")
        expected = "yes and $(MISSING)"

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
        actual = expand_vars("$(port) is set")
        expected = "COM7 is set"

        # Assert — lowercase variable names work
        assert actual == expected

    def test_case_sensitive(self):
        # Arrange
        _VARS["PORT"] = "COM7"

        # Act
        actual = expand_vars("$(port) stays")
        expected = "$(port) stays"

        # Assert — $(port) != $(PORT) (case-sensitive)
        assert actual == expected

    def test_var_with_underscore(self):
        # Arrange
        _VARS["MY_VAR"] = "value"

        # Act
        actual = expand_vars("$(MY_VAR)")
        expected = "value"

        # Assert — underscores in name work
        assert actual == expected

    def test_var_with_digits(self):
        # Arrange
        _VARS["REG0"] = "100"

        # Act
        actual = expand_vars("$(REG0)")
        expected = "100"

        # Assert — digits in name work
        assert actual == expected

    def test_var_adjacent_to_text(self):
        # Arrange
        _VARS["PORT"] = "COM7"

        # Act — var followed by text with no separator
        actual = expand_vars("port=$(PORT),baud=115200")
        expected = "port=COM7,baud=115200"

        # Assert — expanded correctly at boundary
        assert actual == expected

    def test_var_adjacent_to_text_no_ambiguity(self):
        # Arrange
        _VARS["PORT"] = "COM"

        # Act — $(PORT) followed by digits: unambiguous thanks to parens
        actual = expand_vars("$(PORT)7")
        expected = "COM7"

        # Assert — closing paren terminates the name cleanly
        assert actual == expected

    def test_bare_dollar_not_expanded(self):
        # Arrange
        _VARS["PORT"] = "COM7"

        # Act — bare $PORT (no parens) is NOT expanded
        actual = expand_vars("$PORT")
        expected = "$PORT"

        # Assert — bare syntax ignored
        assert actual == expected


# ── expand_vars escape (\$) ─────────────────────────────────────────────────


class TestExpandVarsEscape:
    """Tests for \\$ escape preventing variable expansion."""

    def test_escaped_dollar_not_expanded(self):
        # Arrange
        _VARS["PORT"] = "COM7"

        # Act
        actual = expand_vars("\\$(PORT)")
        expected = "$(PORT)"

        # Assert — escaped $ left as literal
        assert actual == expected

    def test_escaped_dollar_unknown_var(self):
        # Act
        actual = expand_vars("\\$(UNKNOWN)")
        expected = "$(UNKNOWN)"

        # Assert — escaped unknown var left as literal
        assert actual == expected

    def test_mixed_escaped_and_expanded(self):
        # Arrange
        _VARS["A"] = "hello"
        _VARS["B"] = "world"

        # Act
        actual = expand_vars("$(A) and \\$(B)")
        expected = "hello and $(B)"

        # Assert — $(A) expanded, \$(B) left as literal
        assert actual == expected

    def test_escaped_dollar_in_serial_context(self):
        # Act
        actual = expand_vars("AT+CMD=\\$50")
        expected = "AT+CMD=$50"

        # Assert — escaped $ in serial-like command
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
    """Tests for the $(NAME) pattern matching."""

    @pytest.mark.parametrize("text,expected_name", [
        ("$(PORT)", "PORT"),
        ("$(port)", "port"),
        ("$(A)", "A"),
        ("$(MY_VAR)", "MY_VAR"),
        ("$(my_var)", "my_var"),
        ("$(REG0)", "REG0"),
        ("$(_PRIVATE)", "_PRIVATE"),
    ])
    def test_valid_var_names(self, text, expected_name):
        # Act
        m = _VAR_REF_RE.search(text)

        # Assert — matches and captures correct name
        assert m is not None
        assert m.group(1) == expected_name

    @pytest.mark.parametrize("text", [
        "$(123)",        # starts with digit
        "$PORT",         # bare $NAME (no parens)
    ])
    def test_invalid_var_names(self, text):
        # Act
        m = _VAR_REF_RE.search(text)

        # Assert — does not match as a user variable
        assert m is None


# ── rewrite_assignment ───────────────────────────────────────────────────────


class TestRewriteAssignment:
    """Tests for the $(VAR) = value line rewriting."""

    def test_basic_assignment(self):
        # Act
        actual = rewrite_assignment("$(PORT) = COM7")
        expected = "var.set PORT COM7"

        # Assert — rewritten to var.set command
        assert actual == expected

    def test_no_spaces(self):
        # Act
        actual = rewrite_assignment("$(PORT)=COM7")
        expected = "var.set PORT COM7"

        # Assert
        assert actual == expected

    def test_extra_spaces(self):
        # Act
        actual = rewrite_assignment("$(PORT)  =  COM7")
        expected = "var.set PORT COM7"

        # Assert
        assert actual == expected

    def test_value_with_spaces(self):
        # Act
        actual = rewrite_assignment("$(MSG) = hello world")
        expected = "var.set MSG hello world"

        # Assert — value includes everything after =
        assert actual == expected

    def test_lowercase_rewritten(self):
        # Act
        actual = rewrite_assignment("$(port) = COM7")
        expected = "var.set port COM7"

        # Assert — lowercase works
        assert actual == expected

    def test_no_value_not_rewritten(self):
        # Act
        actual = rewrite_assignment("$(PORT) =")

        # Assert — empty value rejected
        assert actual is None

    def test_no_dollar_not_rewritten(self):
        # Act
        actual = rewrite_assignment("PORT = COM7")

        # Assert — needs $() syntax
        assert actual is None

    def test_bare_dollar_not_rewritten(self):
        # Act
        actual = rewrite_assignment("$PORT = COM7")

        # Assert — bare $NAME not rewritten (needs parens)
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


# ── check_bare_dollar ──────────────────────────────────────────────────────


class TestCheckBareDollar:
    """Tests for the bare $NAME = value warning."""

    def test_bare_assignment_warns(self):
        # Act
        actual = check_bare_dollar("$PORT = COM7")

        # Assert — returns warning message
        assert actual is not None
        assert "$(PORT)" in actual

    def test_bare_no_spaces_warns(self):
        # Act
        actual = check_bare_dollar("$PORT=COM7")

        # Assert — warns even without spaces
        assert actual is not None
        assert "$(PORT)" in actual

    def test_proper_syntax_no_warning(self):
        # Act
        actual = check_bare_dollar("$(PORT) = COM7")

        # Assert — proper syntax does not warn
        assert actual is None

    def test_serial_command_no_warning(self):
        # Act
        actual = check_bare_dollar("AT+PORT=COM7")

        # Assert — AT command does not warn
        assert actual is None

    def test_plain_text_no_warning(self):
        # Act
        actual = check_bare_dollar("hello world")

        # Assert — no warning
        assert actual is None


# ── set_start_time_vars ─────────────────────────────────────────────────────


class TestSessionTimeVars:
    """Tests for $(SESSION_DATE), $(SESSION_TIME), $(SESSION_DATETIME)."""

    def test_sets_session_date(self):
        # Act
        set_start_time_vars()

        # Assert — SESSION_DATE is YYYY-MM-DD format
        assert "SESSION_DATE" in _VARS
        assert len(_VARS["SESSION_DATE"]) == 10  # YYYY-MM-DD
        assert _VARS["SESSION_DATE"][4] == "-"

    def test_sets_session_time(self):
        # Act
        set_start_time_vars()

        # Assert — SESSION_TIME is HH:MM:SS format
        assert "SESSION_TIME" in _VARS
        assert len(_VARS["SESSION_TIME"]) == 8  # HH:MM:SS
        assert _VARS["SESSION_TIME"][2] == ":"

    def test_sets_session_datetime(self):
        # Act
        set_start_time_vars()

        # Assert — SESSION_DATETIME contains both date and time
        assert "SESSION_DATETIME" in _VARS
        assert _VARS["SESSION_DATE"] in _VARS["SESSION_DATETIME"]
        assert _VARS["SESSION_TIME"] in _VARS["SESSION_DATETIME"]

    def test_cleared_by_clear_vars(self):
        """Verify clear_vars removes session vars (they must be re-set explicitly)."""
        # Arrange
        set_start_time_vars()

        # Act
        clear_vars()

        # Assert — clear_vars removes everything including session vars
        assert "SESSION_DATE" not in _VARS
        assert "SESSION_TIME" not in _VARS
        assert "SESSION_DATETIME" not in _VARS

    def test_expandable_in_strings(self):
        # Arrange
        set_start_time_vars()

        # Act
        actual = expand_vars("Started: $(SESSION_DATETIME)")

        # Assert — session vars expanded like any other variable
        assert "$(SESSION_DATETIME)" not in actual
        assert _VARS["SESSION_DATETIME"] in actual


class TestDynamicTimeVars:
    """Tests for $(DATE), $(TIME), $(DATETIME) — dynamic (current clock)."""

    def test_date_expands_without_being_set(self):
        # Act — no set_start_time_vars, $(DATE) still expands
        actual = expand_vars("today: $(DATE)")

        # Assert — expanded to current date
        assert "$(DATE)" not in actual
        assert "today: " in actual
        assert len(actual) == len("today: ") + 10  # YYYY-MM-DD

    def test_time_expands_without_being_set(self):
        # Act
        actual = expand_vars("now: $(TIME)")

        # Assert — expanded to current time
        assert "$(TIME)" not in actual
        assert actual[len("now: ") + 2] == ":"  # HH:MM:SS format

    def test_datetime_expands_without_being_set(self):
        # Act
        actual = expand_vars("ts: $(DATETIME)")

        # Assert — expanded to current datetime
        assert "$(DATETIME)" not in actual

    def test_user_var_overrides_dynamic(self):
        # Arrange — user sets $(DATE) explicitly
        _VARS["DATE"] = "custom-date"

        # Act
        actual = expand_vars("$(DATE)")

        # Assert — user-defined value takes precedence over dynamic
        assert actual == "custom-date"

    def test_dynamic_not_in_vars_dict(self):
        # Assert — dynamic vars are NOT stored in _VARS
        assert "DATE" not in _VARS
        assert "TIME" not in _VARS
        assert "DATETIME" not in _VARS

        # Act — but they still expand
        actual = expand_vars("$(DATE) $(TIME) $(DATETIME)")

        # Assert — all three expanded
        assert "$(DATE)" not in actual
        assert "$(TIME)" not in actual
        assert "$(DATETIME)" not in actual


# ── DirectiveResult (isolated) ───────────────────────────────────────────────


class TestDirectiveResult:
    """Tests for the DirectiveResult dataclass."""

    def test_default_is_none_action(self):
        # Act
        actual = DirectiveResult()

        # Assert — defaults to no-op
        assert actual.action == "none"
        assert actual.payload == ""

    def test_rewrite_result(self):
        # Act
        actual = DirectiveResult("rewrite", "var.set PORT COM7")

        # Assert
        assert actual.action == "rewrite"
        assert actual.payload == "var.set PORT COM7"

    def test_warn_result(self):
        # Act
        actual = DirectiveResult("warn", "Something wrong")

        # Assert
        assert actual.action == "warn"
        assert actual.payload == "Something wrong"

    def test_error_result(self):
        # Act
        actual = DirectiveResult("error", "Bad input")

        # Assert
        assert actual.action == "error"
        assert actual.payload == "Bad input"


# ── run_directives (isolated with manual registration) ───────────────────────


class TestRunDirectivesIsolated:
    """Test run_directives with manually registered directives."""

    def test_no_directives_returns_none_action(self, tmp_path):
        """With no directives registered, returns 'none' action."""
        # Arrange
        engine, _ = _make_engine(tmp_path)
        engine._directives.clear()

        # Act
        actual = engine.run_directives("anything")

        # Assert
        assert actual.action == "none"

    def test_matching_directive_returns_its_result(self, tmp_path):
        """A directive that matches returns its DirectiveResult."""
        # Arrange
        engine, _ = _make_engine(tmp_path)
        engine._directives.clear()
        engine.register_directive(DirectiveInfo(
            name="test",
            help="Test directive.",
            handler=lambda line: DirectiveResult("rewrite", "replaced")
            if line == "match" else None,
        ))

        # Act
        actual = engine.run_directives("match")

        # Assert
        assert actual.action == "rewrite"
        assert actual.payload == "replaced"

    def test_non_matching_directive_returns_none_action(self, tmp_path):
        """A directive that doesn't match falls through to 'none'."""
        # Arrange
        engine, _ = _make_engine(tmp_path)
        engine._directives.clear()
        engine.register_directive(DirectiveInfo(
            name="test",
            help="Test directive.",
            handler=lambda line: None,
        ))

        # Act
        actual = engine.run_directives("anything")

        # Assert
        assert actual.action == "none"

    def test_first_matching_directive_wins(self, tmp_path):
        """When multiple directives match, the first one wins."""
        # Arrange
        engine, _ = _make_engine(tmp_path)
        engine._directives.clear()
        engine.register_directive(DirectiveInfo(
            name="first",
            help="First.",
            handler=lambda line: DirectiveResult("rewrite", "first"),
        ))
        engine.register_directive(DirectiveInfo(
            name="second",
            help="Second.",
            handler=lambda line: DirectiveResult("rewrite", "second"),
        ))

        # Act
        actual = engine.run_directives("anything")

        # Assert — first directive wins
        assert actual.payload == "first"


# ── Directive integration (with builtins) ────────────────────────────────────


def _make_engine(tmp_path):
    """Create a ReplEngine with a temp config for directive testing."""
    cfg_dir = tmp_path / "test_cfg"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "test.cfg"
    cfg_path.write_text(json.dumps({"config_version": 8}))
    for sub in ("plugin", "ss", "run"):
        (cfg_dir / sub).mkdir()
    output = []
    engine = ReplEngine(
        {"config_version": 8}, str(cfg_path),
        lambda t, c=None: output.append((t, c)),
    )
    return engine, output


class TestDirectiveIntegration:
    """Verify var_assign directive is loaded and wired through run_directives."""

    def test_var_assign_directive_loaded(self, tmp_path):
        """The var_assign directive should be registered by the builtin loader."""
        # Arrange
        engine, _ = _make_engine(tmp_path)

        # Assert — at least one directive registered
        assert len(engine._directives) >= 1  # directive registered
        names = [d.name for d in engine._directives]
        assert "var_assign" in names  # var_assign specifically

    def test_run_directives_rewrites_assignment(self, tmp_path):
        """run_directives should rewrite $(VAR) = value to var.set."""
        # Arrange
        engine, _ = _make_engine(tmp_path)

        # Act
        actual = engine.run_directives("$(PORT) = COM7")

        # Assert — rewritten to var.set command
        assert actual.action == "rewrite"
        assert actual.payload == "var.set PORT COM7"

    def test_run_directives_returns_none_for_normal_input(self, tmp_path):
        """run_directives should return 'none' action for non-directive lines."""
        # Arrange
        engine, _ = _make_engine(tmp_path)

        # Act
        actual = engine.run_directives("AT+INFO")

        # Assert — no directive matched
        assert actual.action == "none"

    def test_run_directives_warns_on_bare_dollar(self, tmp_path):
        """run_directives should return a warn result for bare $VAR = syntax."""
        # Arrange
        engine, _ = _make_engine(tmp_path)

        # Act
        actual = engine.run_directives("$PORT = COM7")

        # Assert — warning with helpful message
        assert actual.action == "warn"
        assert "$(PORT)" in actual.payload  # suggests correct syntax

    def test_run_directives_errors_on_empty_value(self, tmp_path):
        """run_directives should return error for $(VAR) = with no value."""
        # Arrange
        engine, _ = _make_engine(tmp_path)

        # Act
        actual = engine.run_directives("$(PORT) =")

        # Assert — error, not sent to device
        assert actual.action == "error"
        assert "requires a value" in actual.payload
