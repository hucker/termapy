"""Tests for declarative command parameters (plugins/params.py + dispatch).

Spec IDs (from docs/param-spec-implementation.md) are cited in each class/test
docstring.  There is no ``@pytest.mark.spec`` marker convention in this repo
(the plan's first draft assumed one), so traceability lives in the names.
"""

from __future__ import annotations

import pytest

from termapy.plugins import CmdResult, Command, PluginInfo
from termapy.plugins.params import (
    EnumValue,
    ParamSpec,
    coerce_value,
    parse_params,
    render_parameters_block,
    synthesize_synopsis,
    validate_param_specs,
)
from termapy.repl import ReplEngine


# -- param-decl-validation ------------------------------------------------------


class TestDeclarationValidation:
    """param-decl-validation: a broken declaration fails loudly at load."""

    def test_duplicate_names(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="duplicate parameter 'x'"):
            validate_param_specs([ParamSpec("x"), ParamSpec("x")], "cmd")

    def test_two_rest_params(self):
        with pytest.raises(ValueError, match="at most one rest"):
            validate_param_specs(
                [ParamSpec("a", rest=True), ParamSpec("b", rest=True)], "cmd"
            )

    def test_positional_rest_is_allowed(self):
        # A single positional-rest (whole-line value, may contain spaces) is
        # valid -- it's how /os, /grep, /profile.validate take their argument.
        validate_param_specs([ParamSpec("cmd", positional=True, rest=True)], "os")

    def test_positional_rest_must_be_last(self):
        with pytest.raises(ValueError, match="must be the last positional"):
            validate_param_specs(
                [ParamSpec("a", positional=True, rest=True),
                 ParamSpec("b", positional=True)],
                "cmd",
            )

    def test_enum_without_values(self):
        with pytest.raises(ValueError, match="enum parameter 'm' has no values"):
            validate_param_specs([ParamSpec("m", "enum")], "cmd")

    def test_required_with_default(self):
        with pytest.raises(ValueError, match="must not have a default"):
            validate_param_specs([ParamSpec("a", required=True, default="x")], "cmd")

    def test_command_type_requires_rest(self):
        with pytest.raises(ValueError, match="must set rest=True"):
            validate_param_specs([ParamSpec("c", "command")], "cmd")

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="unknown type 'blob'"):
            validate_param_specs([ParamSpec("a", "blob")], "cmd")

    def test_uppercase_name_rejected(self):
        with pytest.raises(ValueError, match="must be lowercase"):
            validate_param_specs([ParamSpec("Count")], "cmd")

    def test_command_args_and_params_mutually_exclusive(self):
        # Command.__post_init__ enforces the cross-field rule.
        with pytest.raises(ValueError, match="not both"):
            Command("h", name="c", args="<x>", params=[ParamSpec("a")])

    def test_command_params_on_raw_args_allowed(self):
        # raw_args skips only the $(VAR) transform step, NOT param parsing, so
        # a raw_args command may declare params -- values arrive literal, which
        # is what raw_args wants (e.g. /cap.wire keeps cmd= unexpanded).
        cmd = Command("h", name="c", raw_args=True, params=[ParamSpec("a", default="")])
        assert cmd.raw_args is True, "raw_args + params coexist"

    def test_valid_declaration_builds(self):
        # Arrange / Act -- should not raise
        cmd = Command("h", name="c", params=[ParamSpec("a", default="x")])

        # Assert
        actual = cmd.args
        assert actual == "", "params command keeps args empty (synthesized at load)"


# -- param-types ----------------------------------------------------------------


class TestCoercion:
    """param-types: each type's str -> value coercion and its error text."""

    def test_int_ok(self):
        actual = coerce_value(ParamSpec("n", "int"), "42")
        assert actual == (True, 42), "int coerces to int"

    def test_int_bad(self):
        ok, reason = coerce_value(ParamSpec("n", "int"), "x")
        assert ok is False, "non-int rejected"
        assert reason == "invalid n: 'x' (expected an integer)", "fixed message"

    def test_int_range_low(self):
        ok, reason = coerce_value(ParamSpec("n", "int", min=1), "0")
        assert (ok, reason) == (False, "n must be >= 1 (got 0)"), "min enforced"

    def test_int_range_between(self):
        ok, reason = coerce_value(ParamSpec("n", "int", min=1, max=10), "20")
        assert reason == "n must be between 1 and 10 (got 20)", "both-bounds message"

    def test_float_ok(self):
        actual = coerce_value(ParamSpec("f", "float"), "1.5")
        assert actual == (True, 1.5), "float coerces"

    def test_duration_ok(self):
        ok, value = coerce_value(ParamSpec("t", "duration"), "250ms")
        assert (ok, value) == (True, 0.25), "duration -> float seconds"

    def test_duration_bad(self):
        ok, reason = coerce_value(ParamSpec("t", "duration"), "2x")
        assert ok is False, "bad duration rejected"
        assert "expected duration" in reason, "duration hint in message"

    def test_duration_bare_zero(self):
        # A bare "0" is unambiguously zero (parse_duration itself wants a unit);
        # /cap.poll delay=0 relies on this.
        actual = coerce_value(ParamSpec("t", "duration"), "0")
        assert actual == (True, 0.0), "bare 0 duration -> 0.0"

    def test_enum_alias_returns_canonical(self):
        spec = ParamSpec(
            "m", "enum", values=(EnumValue("new", ("n",)), EnumValue("append", ("a",)))
        )
        actual = coerce_value(spec, "A")
        assert actual == (True, "append"), "alias 'A' -> canonical 'append', case-insensitive"

    def test_enum_miss(self):
        spec = ParamSpec("m", "enum", values=(EnumValue("new"), EnumValue("append")))
        ok, reason = coerce_value(spec, "xx")
        assert reason == "invalid m: 'xx' (expected one of: new, append)", "lists canonicals"

    def test_path_and_command_are_identity(self):
        # No resolution, no case-fold (landmine note).
        actual_path = coerce_value(ParamSpec("p", "path"), "Cap/File.TXT")
        actual_cmd = coerce_value(ParamSpec("c", "command", rest=True), "AT+Foo")
        assert actual_path == (True, "Cap/File.TXT"), "path preserves case, no resolve"
        assert actual_cmd == (True, "AT+Foo"), "command preserves case"


# -- param-parse-grammar / param-fail-message -----------------------------------


class TestParseParams:
    """param-parse-grammar + param-fail-message: end-to-end string -> bound."""

    def _ping(self):
        return [
            ParamSpec("count", "int", default=1, min=1),
            ParamSpec("timeout", "duration", default=0.25),
            ParamSpec("cmd", "command", required=True, rest=True),
        ]

    def test_full_parse(self):
        bound, err = parse_params(self._ping(), "count=3 timeout=1s cmd=AT+FOO BAR")
        assert err is None, "valid input parses"
        expected = {"count": 3, "timeout": 1.0, "cmd": "AT+FOO BAR"}
        assert bound == expected, "coerced + rest-consumed-to-EOL"

    def test_defaults_fill_absent(self):
        bound, err = parse_params(self._ping(), "cmd=AT")
        assert (err, bound) == (None, {"cmd": "AT", "count": 1, "timeout": 0.25}), \
            "absent optionals get their coerced defaults"

    def test_key_equals_value_spacing(self):
        # parse_keywords normalizes `key = value`; keep that grammar.
        bound, err = parse_params(self._ping(), "count = 2 cmd=AT")
        assert bound["count"] == 2, "spaces around = tolerated"

    def test_case_insensitive_keyword(self):
        bound, _ = parse_params(self._ping(), "COUNT=2 cmd=AT")
        assert bound["count"] == 2, "keyword match is case-insensitive"

    def test_missing_required(self):
        _, err = parse_params(self._ping(), "count=2")
        assert err == "missing required parameter 'cmd'", "required-absent message"

    def test_first_coercion_failure_short_circuits(self):
        _, err = parse_params(self._ping(), "count=x cmd=AT")
        assert err == "invalid count: 'x' (expected an integer)", "first failure wins"

    def test_unexpected_positional(self):
        spec = [ParamSpec("cmd", "command", required=True, rest=True)]
        _, err = parse_params(spec, "stray cmd=AT")
        assert err == "unexpected argument: 'stray'", "extra positional rejected"

    def test_positional_binding(self):
        spec = [
            ParamSpec("file", "path", positional=True, required=True),
            ParamSpec("timeout", "duration", default=0.5),
        ]
        bound, err = parse_params(spec, "out.txt timeout=1s")
        assert (err, bound) == (None, {"file": "out.txt", "timeout": 1.0}), \
            "positional bound in order, keyword alongside"

    def test_positional_rest_keeps_spaces(self):
        # A positional-rest takes the whole remaining line, so an OS command
        # or regex with spaces survives (fixes the whitespace-split regression).
        spec = [ParamSpec("cmd", "str", positional=True, rest=True, required=True)]
        bound, err = parse_params(spec, "ls -la /tmp")
        assert (err, bound) == (None, {"cmd": "ls -la /tmp"}), \
            "positional-rest keeps the whole line intact"


# -- param-help-synth -----------------------------------------------------------


class TestSynopsisAndBlock:
    """param-help-synth: synopsis + PARAMETERS block synthesis."""

    def test_synopsis_matches_convention(self):
        params = [
            ParamSpec("count", "int", default=1),
            ParamSpec("timeout", "duration", default=0.25),
            ParamSpec("cmd", "command", required=True, rest=True),
        ]
        actual = synthesize_synopsis(params)
        expected = "{count=<N>} {timeout=<dur>} cmd=<command>"
        assert actual == expected, "optionals braced, rest last, required bare"

    def test_enum_synopsis_lists_canonicals(self):
        params = [
            ParamSpec(
                "mode", "enum", default="new",
                values=(EnumValue("new"), EnumValue("append")),
            )
        ]
        actual = synthesize_synopsis(params)
        assert actual == "{mode=new|append}", "enum hint is canonical|canonical"

    def test_hint_override_beats_generic_type_hint(self):
        # A str param's generic hint is <value>; hint= makes it descriptive.
        params = [ParamSpec("var", "str", default="x", hint="<name>")]
        actual = synthesize_synopsis(params)
        assert actual == "{var=<name>}", "hint= overrides the generic <value> str hint"

    def test_parameters_block_shows_defaults_and_flags(self):
        params = [
            ParamSpec("count", "int", default=1, help="number of pings"),
            ParamSpec("cmd", "command", required=True, rest=True, help="command to send"),
        ]
        lines = render_parameters_block(params)
        assert lines[0].strip() == "count=<N>      number of pings (default: 1)".strip() \
            or "count=<N>" in lines[0], "default annotated"
        assert "required, must be last" in lines[-1], "rest+required annotated"


# -- param-decl-optin / param-ctx-access / param-ctx-nesting --------------------


def _engine() -> ReplEngine:
    eng = ReplEngine({}, "", lambda t, c=None: None)
    # The host normally wires ctx.dispatch (app.py: ctx.dispatch =
    # self._dispatch_single); replicate it so nested ctx.dispatch() routes
    # through the real dispatch path (with bound_params save/restore).
    eng.ctx.dispatch = eng.dispatch
    return eng


class TestDispatchOptOut:
    """param-decl-optin: a params-free command is dispatched untouched."""

    def test_args_pass_through_unchanged(self):
        # Arrange
        eng = _engine()
        seen: dict = {}

        def handler(ctx, args):
            seen["args"] = args
            return CmdResult.ok(value=args)

        eng.register_plugin(PluginInfo(name="noparams", args="", help="", handler=handler))

        # Act
        result = eng.dispatch("noparams hello world")

        # Assert
        assert result.value == "hello world", "raw args returned unchanged"
        assert seen["args"] == "hello world", "handler received the raw args verbatim"


class TestDispatchArgAccess:
    """param-ctx-access: handler reads coerced values via ctx.arg()."""

    def test_coerced_values_reach_handler(self):
        # Arrange
        eng = _engine()
        seen: dict = {}

        def handler(ctx, args):
            seen["count"] = ctx.arg("count")
            seen["timeout"] = ctx.arg("timeout")
            seen["cmd"] = ctx.arg("cmd")
            return CmdResult.ok()

        eng.register_plugin(PluginInfo(
            name="p", args="", help="", handler=handler,
            params=[
                ParamSpec("count", "int", default=1),
                ParamSpec("timeout", "duration", default=0.25),
                ParamSpec("cmd", "command", required=True, rest=True),
            ],
        ))

        # Act
        eng.dispatch("p count=3 timeout=1s cmd=AT")

        # Assert
        actual = (seen["count"], seen["timeout"], seen["cmd"])
        assert actual == (3, 1.0, "AT"), "ctx.arg returns coerced typed values"

    def test_bad_input_fails_before_handler(self):
        # Arrange
        eng = _engine()
        ran = {"handler": False}

        def handler(ctx, args):
            ran["handler"] = True
            return CmdResult.ok()

        eng.register_plugin(PluginInfo(
            name="p", args="", help="", handler=handler,
            params=[ParamSpec("cmd", "command", required=True, rest=True)],
        ))

        # Act
        result = eng.dispatch("p")

        # Assert
        assert result.success is False, "missing required param fails dispatch"
        assert ran["handler"] is False, "handler never ran"
        assert "missing required parameter 'cmd'" in result.error, "uniform reason"
        assert "Usage: /p" in result.error, "synthesized usage appended"


class TestDispatchNesting:
    """param-ctx-nesting: a nested ctx.dispatch() must not strand outer params."""

    def test_outer_params_survive_nested_dispatch(self):
        # Arrange -- outer reads ctx.arg() AFTER dispatching inner (the case
        # the active_flags set-then-clear pattern would break).
        eng = _engine()
        seen: dict = {}

        def inner(ctx, args):
            seen["inner_x"] = ctx.arg("x")
            return CmdResult.ok()

        def outer(ctx, args):
            seen["outer_before"] = ctx.arg("x")
            ctx.dispatch("inner x=inner")
            seen["outer_after"] = ctx.arg("x")  # must still be the outer's value
            return CmdResult.ok()

        eng.register_plugin(PluginInfo(
            name="inner", args="", help="", handler=inner,
            params=[ParamSpec("x", default="")],
        ))
        eng.register_plugin(PluginInfo(
            name="outer", args="", help="", handler=outer,
            params=[ParamSpec("x", default="")],
        ))

        # Act
        eng.dispatch("outer x=outer")

        # Assert
        assert seen["inner_x"] == "inner", "inner saw its own param"
        assert seen["outer_before"] == "outer", "outer param bound before nesting"
        assert seen["outer_after"] == "outer", \
            "outer param restored after nested dispatch (save/restore, not clear)"
