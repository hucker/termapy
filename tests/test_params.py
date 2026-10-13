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
        """Two params with the same name fail at declaration, naming the duplicate."""
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="duplicate parameter 'x'"):
            validate_param_specs([ParamSpec("x"), ParamSpec("x")], "cmd")

    def test_two_rest_params(self):
        """Only one param may consume the rest of the line."""
        with pytest.raises(ValueError, match="at most one rest"):
            validate_param_specs(
                [ParamSpec("a", rest=True), ParamSpec("b", rest=True)], "cmd"
            )

    def test_positional_rest_is_allowed(self):
        """A single positional rest is legal; /os and /grep take their whole line this way."""
        # A single positional-rest (whole-line value, may contain spaces) is
        # valid -- it's how /os, /grep, /profile.validate take their argument.
        validate_param_specs([ParamSpec("cmd", positional=True, rest=True)], "os")

    def test_positional_rest_must_be_last(self):
        """A positional after a positional rest is refused, since the rest would eat it."""
        with pytest.raises(ValueError, match="must be the last positional"):
            validate_param_specs(
                [ParamSpec("a", positional=True, rest=True),
                 ParamSpec("b", positional=True)],
                "cmd",
            )

    def test_enum_without_values(self):
        """An enum param with no values is refused rather than accepting nothing."""
        with pytest.raises(ValueError, match="enum parameter 'm' has no values"):
            validate_param_specs([ParamSpec("m", "enum")], "cmd")

    def test_optional_bool_must_declare_a_default(self):
        """bool-one-vocabulary: an optional bool param says what absence means at declaration, or fails at load."""
        with pytest.raises(ValueError, match=r"optional bool parameter 'x' must declare a default"):
            validate_param_specs([ParamSpec("x", "bool")], "cmd")
        validate_param_specs([ParamSpec("x", "bool", default=False)], "cmd")
        validate_param_specs([ParamSpec("x", "bool", required=True)], "cmd")

    def test_required_with_default(self):
        """A required param may not carry a default; the two contradict each other."""
        with pytest.raises(ValueError, match="must not have a default"):
            validate_param_specs([ParamSpec("a", required=True, default="x")], "cmd")

    def test_command_type_requires_rest(self):
        """A command-typed param must be rest, because a command can contain spaces."""
        with pytest.raises(ValueError, match="must set rest=True"):
            validate_param_specs([ParamSpec("c", "command")], "cmd")

    def test_unknown_type(self):
        """A misspelled or unknown param type fails at load, naming the type."""
        with pytest.raises(ValueError, match="unknown type 'blob'"):
            validate_param_specs([ParamSpec("a", "blob")], "cmd")

    def test_uppercase_name_rejected(self):
        """Param names must be lowercase, matching how keywords are typed."""
        with pytest.raises(ValueError, match="must be lowercase"):
            validate_param_specs([ParamSpec("Count")], "cmd")

    def test_command_args_and_params_mutually_exclusive(self):
        """A Command takes a hand-written args synopsis or params, never both."""
        # Command.__post_init__ enforces the cross-field rule.
        with pytest.raises(ValueError, match="not both"):
            Command("h", name="c", args="<x>", params=[ParamSpec("a")])

    def test_command_params_on_raw_args_allowed(self):
        """raw_args and params can coexist; raw_args only skips $(VAR) expansion."""
        # raw_args skips only the $(VAR) transform step, NOT param parsing, so
        # a raw_args command may declare params -- values arrive literal, which
        # is what raw_args wants (e.g. /cap.wire keeps cmd= unexpanded).
        cmd = Command("h", name="c", raw_args=True, params=[ParamSpec("a", default="")])
        assert cmd.raw_args is True, "raw_args + params coexist"

    def test_valid_declaration_builds(self):
        """A params command leaves args empty, to be synthesized at registration."""
        # Arrange / Act -- should not raise
        cmd = Command("h", name="c", params=[ParamSpec("a", default="x")])

        # Assert
        actual = cmd.args
        assert actual == "", "params command keeps args empty (synthesized at load)"


# -- param-types ----------------------------------------------------------------


class TestCoercion:
    """param-types: each type's str -> value coercion and its error text."""

    def test_int_ok(self):
        """A decimal string coerces to an int."""
        actual = coerce_value(ParamSpec("n", "int"), "42")
        assert actual == (True, 42), "int coerces to int"

    def test_int_bad(self):
        """A non-numeric int value fails with the fixed expected-an-integer message."""
        ok, reason = coerce_value(ParamSpec("n", "int"), "x")
        assert ok is False, "non-int rejected"
        assert reason == "invalid n: 'x' (expected an integer)", "fixed message"

    def test_int_range_low(self):
        """With only min set, a low value names just the lower bound."""
        ok, reason = coerce_value(ParamSpec("n", "int", min=1), "0")
        assert (ok, reason) == (False, "n must be >= 1 (got 0)"), "min enforced"

    def test_int_range_between(self):
        """With min and max both set, an out-of-range value names both bounds."""
        ok, reason = coerce_value(ParamSpec("n", "int", min=1, max=10), "20")
        assert reason == "n must be between 1 and 10 (got 20)", "both-bounds message"

    def test_float_ok(self):
        """A decimal string coerces to a float."""
        actual = coerce_value(ParamSpec("f", "float"), "1.5")
        assert actual == (True, 1.5), "float coerces"

    def test_duration_ok(self):
        """A duration with a unit coerces to float seconds."""
        ok, value = coerce_value(ParamSpec("t", "duration"), "250ms")
        assert (ok, value) == (True, 0.25), "duration -> float seconds"

    def test_duration_bad(self):
        """An unparseable duration fails with a hint naming the duration type."""
        ok, reason = coerce_value(ParamSpec("t", "duration"), "2x")
        assert ok is False, "bad duration rejected"
        assert "expected duration" in reason, "duration hint in message"

    def test_duration_bare_zero(self):
        """Bare 0 is accepted as zero seconds without a unit; /cap.poll delay=0 needs it."""
        # A bare "0" is unambiguously zero (parse_duration itself wants a unit);
        # /cap.poll delay=0 relies on this.
        actual = coerce_value(ParamSpec("t", "duration"), "0")
        assert actual == (True, 0.0), "bare 0 duration -> 0.0"

    def test_enum_alias_returns_canonical(self):
        """An enum alias in any case binds the canonical value, not the alias typed."""
        spec = ParamSpec(
            "m", "enum", values=(EnumValue("new", ("n",)), EnumValue("append", ("a",)))
        )
        actual = coerce_value(spec, "A")
        assert actual == (True, "append"), "alias 'A' -> canonical 'append', case-insensitive"

    def test_enum_miss(self):
        """An enum miss lists only the canonical values, not the aliases."""
        spec = ParamSpec("m", "enum", values=(EnumValue("new"), EnumValue("append")))
        ok, reason = coerce_value(spec, "xx")
        assert reason == "invalid m: 'xx' (expected one of: new, append)", "lists canonicals"

    def test_path_and_command_are_identity(self):
        """Path and command values pass through verbatim: no case folding, no resolution."""
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
        """Keywords coerce to their types and the rest keyword keeps its spaces to end of line."""
        bound, err = parse_params(self._ping(), "count=3 timeout=1s cmd=AT+FOO BAR")
        assert err is None, "valid input parses"
        expected = {"count": 3, "timeout": 1.0, "cmd": "AT+FOO BAR"}
        assert bound == expected, "coerced + rest-consumed-to-EOL"

    def test_defaults_fill_absent(self):
        """Omitted optional params bind their defaults, so handlers never see a missing key."""
        bound, err = parse_params(self._ping(), "cmd=AT")
        assert (err, bound) == (None, {"cmd": "AT", "count": 1, "timeout": 0.25}), \
            "absent optionals get their coerced defaults"

    def test_key_equals_value_spacing(self):
        """Spaces around = are tolerated, as in key = value."""
        # parse_keywords normalizes `key = value`; keep that grammar.
        bound, err = parse_params(self._ping(), "count = 2 cmd=AT")
        assert bound["count"] == 2, "spaces around = tolerated"

    def test_case_insensitive_keyword(self):
        """An uppercase keyword binds to its lowercase param."""
        bound, _ = parse_params(self._ping(), "COUNT=2 cmd=AT")
        assert bound["count"] == 2, "keyword match is case-insensitive"

    def test_missing_required(self):
        """A missing required param gives the fixed missing-required message."""
        _, err = parse_params(self._ping(), "count=2")
        assert err == "missing required parameter 'cmd'", "required-absent message"

    def test_first_coercion_failure_short_circuits(self):
        """Parsing stops at the first bad value and reports only that one."""
        _, err = parse_params(self._ping(), "count=x cmd=AT")
        assert err == "invalid count: 'x' (expected an integer)", "first failure wins"

    def test_unexpected_positional(self):
        """A stray bare token where no positional is declared is an error, not dropped."""
        spec = [ParamSpec("cmd", "command", required=True, rest=True)]
        _, err = parse_params(spec, "stray cmd=AT")
        assert err == "unexpected argument: 'stray'", "extra positional rejected"

    def test_positional_binding(self):
        """A positional binds by order and can be mixed with keyword params."""
        spec = [
            ParamSpec("file", "path", positional=True, required=True),
            ParamSpec("timeout", "duration", default=0.5),
        ]
        bound, err = parse_params(spec, "out.txt timeout=1s")
        assert (err, bound) == (None, {"file": "out.txt", "timeout": 1.0}), \
            "positional bound in order, keyword alongside"

    def test_positional_rest_keeps_spaces(self):
        """A positional rest binds the whole remaining line, spaces included."""
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
        """Optional params are braced, required ones bare, and the rest param comes last."""
        params = [
            ParamSpec("count", "int", default=1),
            ParamSpec("timeout", "duration", default=0.25),
            ParamSpec("cmd", "command", required=True, rest=True),
        ]
        actual = synthesize_synopsis(params)
        expected = "{count=<N>} {timeout=<dur>} cmd=<command>"
        assert actual == expected, "optionals braced, rest last, required bare"

    def test_enum_synopsis_lists_canonicals(self):
        """An enum's synopsis hint lists its canonical values joined by |."""
        params = [
            ParamSpec(
                "mode", "enum", default="new",
                values=(EnumValue("new"), EnumValue("append")),
            )
        ]
        actual = synthesize_synopsis(params)
        assert actual == "{mode=new|append}", "enum hint is canonical|canonical"

    def test_hint_override_beats_generic_type_hint(self):
        """hint= replaces the generic <value> placeholder in the synopsis."""
        # A str param's generic hint is <value>; hint= makes it descriptive.
        params = [ParamSpec("var", "str", default="x", hint="<name>")]
        actual = synthesize_synopsis(params)
        assert actual == "{var=<name>}", "hint= overrides the generic <value> str hint"

    def test_parameters_block_shows_defaults_and_flags(self):
        """The PARAMETERS block shows defaults and marks a required rest param."""
        params = [
            ParamSpec("count", "int", default=1, help="number of pings"),
            ParamSpec("cmd", "command", required=True, rest=True, help="command to send"),
        ]
        actual = [line.strip() for line in render_parameters_block(params)]
        expected = [
            "count=<N>      number of pings (default: 1)",
            "cmd=<command>  command to send (required, must be last)",
        ]
        assert actual == expected, "default annotated, rest+required annotated"


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
        """A command without params gets its raw args string untouched by the parser."""
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
        """The handler reads typed values through ctx.arg, not raw strings."""
        # Arrange
        eng = _engine()
        seen: dict = {}

        def handler(ctx, args):
            seen["count"] = ctx.arg("count")
            seen["timeout"] = ctx.arg("timeout")
            seen["cmd"] = ctx.arg("cmd")
            return CmdResult.ok(value="")

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
        """Bad input fails in dispatch with a usage line; the handler never runs."""
        # Arrange
        eng = _engine()
        ran = {"handler": False}

        def handler(ctx, args):
            ran["handler"] = True
            return CmdResult.ok(value="")

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
        """Bound params are restored after a nested ctx.dispatch, not cleared."""
        # Arrange -- outer reads ctx.arg() AFTER dispatching inner (the case
        # the active_flags set-then-clear pattern would break).
        eng = _engine()
        seen: dict = {}

        def inner(ctx, args):
            seen["inner_x"] = ctx.arg("x")
            return CmdResult.ok(value="")

        def outer(ctx, args):
            seen["outer_before"] = ctx.arg("x")
            ctx.dispatch("inner x=inner")
            seen["outer_after"] = ctx.arg("x")  # must still be the outer's value
            return CmdResult.ok(value="")

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


# -- Variadic positionals -------------------------------------------------------


class TestVariadic:
    """A variadic positional binds every remaining token as a list.

    ``rest`` joins the tail into ONE string; ``variadic`` keeps the elements
    apart.  Both are tail consumers, so a command may declare only one.
    """

    def test_binds_list_of_tokens(self):
        """Each remaining token becomes its own list element."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, error = parse_params(params, "A B C")

        # Assert
        assert error is None, "a variadic accepts several tokens"
        assert bound["frames"] == ["A", "B", "C"], "each token is one element"

    def test_single_token_still_a_list(self):
        """One token still binds a list, so handlers never branch on the count."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, _ = parse_params(params, "A")

        # Assert -- shape is stable, so handlers never branch on count
        assert bound["frames"] == ["A"], "one token binds a one-element list"

    def test_absent_binds_empty_list(self):
        """An optional variadic with no tokens binds an empty list, not None."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, error = parse_params(params, "")

        # Assert
        assert error is None, "an optional variadic may be absent"
        assert bound["frames"] == [], "absent binds an empty list rather than None"

    def test_absent_binds_a_fresh_list_each_call(self):
        """The empty default is a new list per parse, so mutations cannot leak."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act -- mutating one dispatch's list must not leak into the next
        first, _ = parse_params(params, "")
        first["frames"].append("leaked")
        second, _ = parse_params(params, "")

        # Assert
        assert second["frames"] == [], "each parse gets its own list"

    def test_required_variadic_rejects_empty(self):
        """A required variadic must get at least one token; an empty list is missing."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True, required=True)]

        # Act
        _, error = parse_params(params, "")

        # Assert -- "present" for a variadic means non-empty, since it always binds
        assert error == "missing required parameter 'frames'", (
            "a required variadic must actually receive a token"
        )

    def test_elements_are_coerced_individually(self):
        """Each element is coerced to the declared type, not only the first."""
        # Arrange
        params = [ParamSpec("nums", "int", positional=True, variadic=True)]

        # Act
        bound, error = parse_params(params, "1 2 3")

        # Assert
        assert error is None, "integer elements coerce"
        assert bound["nums"] == [1, 2, 3], "every element is coerced, not just the first"

    def test_bad_element_fails_with_its_own_reason(self):
        """A bad element in the middle fails with the standard coercion message."""
        # Arrange
        params = [ParamSpec("nums", "int", positional=True, variadic=True)]

        # Act
        _, error = parse_params(params, "1 x 3")

        # Assert
        assert error == "invalid nums: 'x' (expected an integer)", (
            "a bad element fails using the standard coercion vocabulary"
        )

    def test_range_check_applies_per_element(self):
        """min and max are checked on every element of the list."""
        # Arrange
        params = [ParamSpec("nums", "int", positional=True, variadic=True,
                            min=0, max=10)]

        # Act
        _, error = parse_params(params, "1 99")

        # Assert
        assert error == "nums must be between 0 and 10 (got 99)", (
            "min/max bounds apply to each element"
        )

    def test_fixed_positionals_bind_before_the_variadic(self):
        """A fixed positional before a variadic takes one token; the variadic gets the rest."""
        # Arrange
        params = [
            ParamSpec("out", positional=True, required=True),
            ParamSpec("sources", positional=True, variadic=True, required=True),
        ]

        # Act
        bound, error = parse_params(params, "dest.bin a.bin b.bin")

        # Assert
        assert error is None, "a fixed positional may precede a variadic"
        actual = (bound["out"], bound["sources"])
        expected = ("dest.bin", ["a.bin", "b.bin"])
        assert actual == expected, "the fixed positional takes exactly one token"

    def test_composes_with_a_rest_keyword(self):
        """A variadic positional and a rest keyword can both be declared and both bind."""
        # Arrange -- a variadic POSITIONAL and a rest KEYWORD are different
        # slots, so they coexist; this is what gives /proto.crc.detect both
        # its "one frame per argument" and its "one spaced frame" forms
        params = [
            ParamSpec("frames", positional=True, variadic=True),
            ParamSpec("frame", rest=True),
        ]

        # Act
        bound, error = parse_params(params, "frame=01 03 00 0a")

        # Assert
        assert error is None, "variadic and keyword-rest compose"
        actual = (bound["frames"], bound["frame"])
        expected = ([], "01 03 00 0a")
        assert actual == expected, "the rest keyword keeps its spaces"

    def test_synopsis_marks_repetition(self):
        """The synopsis shows a variadic as <name>... with a trailing ellipsis."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True, required=True)]

        # Act / Assert
        assert synthesize_synopsis(params) == "<frames>...", "repetition shows as ..."

    def test_help_block_notes_repeatable(self):
        """The PARAMETERS block marks a variadic as repeatable."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True, required=True,
                            help="a frame")]

        # Act
        lines = render_parameters_block(params)

        # Assert
        assert "repeatable" in lines[0], "the PARAMETERS block flags repetition"


class TestVariadicDeclarationValidation:
    """A malformed variadic declaration fails at load, not at first dispatch."""

    @pytest.mark.parametrize("params,fragment", [
        ([ParamSpec("x", variadic=True)], "must be positional"),
        ([ParamSpec("x", positional=True, rest=True, variadic=True)],
         "cannot set both rest and variadic"),
        ([ParamSpec("x", positional=True, variadic=True, default="z")],
         "must not have a default"),
        ([ParamSpec("a", positional=True, variadic=True),
          ParamSpec("b", positional=True, rest=True)], "at most one tail positional"),
        ([ParamSpec("a", positional=True, variadic=True),
          ParamSpec("b", positional=True)], "must be the last positional"),
    ])
    def test_rejected(self, params, fragment):
        """Each invalid variadic combination is refused with its own specific reason."""
        # Act / Assert
        with pytest.raises(ValueError, match=fragment):
            validate_param_specs(params, "t")


# -- $(*NAME) dereference -------------------------------------------------------


class TestResolveDeref:
    """``$(*NAME)`` resolves per token, so its arity is exactly 1.

    ``$(NAME)`` is spliced into the line BEFORE it is split, so its arity is
    0..N depending on the value.  These pin the difference.
    """

    VARS = {"P": "01 03 00 0a", "EMPTY": "", "MULTI": "a\nb", "PLAIN": "x"}

    def _deref(self, ref):
        return self.VARS.get(ref)

    def test_value_with_spaces_is_one_argument(self):
        """A $(*NAME) value with spaces stays one argument instead of splitting."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, _ = parse_params(params, "$(*P) TAIL", deref=self._deref)

        # Assert -- the whole point: a spliced "01 03 00 0a" would have forked
        # into four arguments, giving five elements instead of two
        assert bound["frames"] == ["01 03 00 0a", "TAIL"], (
            "a dereferenced value stays one argument whatever it contains"
        )

    def test_empty_value_is_a_present_empty_argument(self):
        """An empty $(*NAME) value still fills one slot, unlike a $(NAME) splice."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, _ = parse_params(params, "$(*EMPTY)", deref=self._deref)

        # Assert -- a splice would have contributed ZERO arguments here
        assert bound["frames"] == [""], "an empty value still occupies one slot"

    def test_newline_survives(self):
        """A newline in a dereferenced value reaches the argument intact."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, _ = parse_params(params, "$(*MULTI)", deref=self._deref)

        # Assert
        assert bound["frames"] == ["a\nb"], (
            "deref is the only way to get a newline into an argument"
        )

    def test_unknown_name_fails(self):
        """An unknown $(*NAME) is an error, so a typo is caught instead of sent."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        _, error = parse_params(params, "$(*nope)", deref=self._deref)

        # Assert
        assert error == "unknown variable: 'nope'", (
            "a deref asserts the name exists, so a typo fails loudly"
        )

    def test_embedded_reference_fails(self):
        """$(*NAME) inside a larger token is an error, not passed on as a literal."""
        # Arrange
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        _, error = parse_params(params, "x$(*P)y", deref=self._deref)

        # Assert -- silently treating it as a literal would send "x$(*P)y" to
        # a device or a filename, which is never what was meant
        assert error is not None and "invalid reference" in error, (
            "a partial reference is an error"
        )

    def test_malformed_reference_passes_through(self):
        """A token that only looks like the sigil, such as a regex, stays literal."""
        # Arrange -- a regex containing the sigil is not a reference
        params = [ParamSpec("pattern", positional=True)]

        # Act
        bound, error = parse_params(params, r"\$\(\*", deref=self._deref)

        # Assert
        assert error is None, "a token that is not a well-formed reference is literal"
        assert bound["pattern"] == r"\$\(\*", "it passes through untouched"

    def test_applies_to_fixed_positional_and_keyword(self):
        """Fixed positionals and keyword values dereference too, not only variadics."""
        # Arrange
        params = [ParamSpec("out", positional=True), ParamSpec("note")]

        # Act
        bound, _ = parse_params(params, "$(*P) note=$(*PLAIN)", deref=self._deref)

        # Assert
        actual = (bound["out"], bound["note"])
        expected = ("01 03 00 0a", "x")
        assert actual == expected, "every token-scoped slot dereferences"

    def test_not_applied_to_rest_values(self):
        """A rest value is a whole line, so $(*NAME) in it stays literal."""
        # Arrange -- a rest value is a whole LINE, not an argument, so it has
        # no arity to guarantee; $(NAME) is the right tool there
        params = [ParamSpec("cmd", "command", rest=True)]

        # Act
        bound, _ = parse_params(params, "cmd=$(*P)", deref=self._deref)

        # Assert
        assert bound["cmd"] == "$(*P)", "a rest value stays literal"

    def test_resolved_value_is_never_rescanned(self):
        """Dereferencing goes one level; a value that looks like a reference stays data."""
        # Arrange -- a value that itself looks like a reference
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, _ = parse_params(params, "$(*IND)", deref={"IND": "$(*P)"}.get)

        # Assert -- resolution is one level; the result is data
        assert bound["frames"] == ["$(*P)"], "a resolved value is not re-resolved"

    def test_disabled_by_default(self):
        """With no deref callback, as for raw_args commands, $(*NAME) stays literal."""
        # Arrange -- deref=None is what raw_args commands get
        params = [ParamSpec("frames", positional=True, variadic=True)]

        # Act
        bound, error = parse_params(params, "$(*P)")

        # Assert
        assert error is None, "no deref means no dereference errors either"
        assert bound["frames"] == ["$(*P)"], "the token stays literal"


# ── bool: the parse_bool vocabulary as a first-class type ───────────────────


class TestBoolType:
    """``bool`` params accept every scripting.parse_bool token, so keyword
    toggles match the setting commands' vocabulary instead of a bare enum."""

    @pytest.mark.parametrize("token, expected", [
        ("on", True), ("TRUE", True), ("yes", True), ("1", True), ("y", True),
        ("off", False), ("false", False), ("No", False), ("0", False), ("f", False),
    ])
    def test_coerces_the_full_vocabulary(self, token, expected):
        """Every parse_bool token in any case coerces to a real True or False."""
        spec = ParamSpec("flag", "bool", default=True)
        ok, value = coerce_value(spec, token)
        assert ok is True, f"{token!r} is a boolean"
        assert value is expected, "coerced to a real bool"

    def test_unknown_token_fails_with_the_vocabulary(self):
        """An unknown token fails and the message lists the accepted vocabulary."""
        ok, reason = coerce_value(ParamSpec("flag", "bool", default=True), "maybe")
        assert ok is False, "an unknown token errors, never silently means False"
        assert reason == "invalid flag: 'maybe' (expected a boolean: on/off/true/false/yes/no/1/0/y/n/t/f)", (
            "the error teaches the accepted vocabulary"
        )

    def test_synopsis_hint_is_the_canonical_pair(self):
        """A bool param shows as on|off in the synopsis."""
        spec = ParamSpec("addr", "bool", default=True)
        assert synthesize_synopsis([spec]) == "{addr=on|off}", "reads like the enum it replaces"
