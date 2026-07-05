"""Declarative command parameters -- parse/coerce/validate before the handler.

See ``docs/param-spec-implementation.md``.  ``ParamSpec`` sits on
``Command.params`` the way flags sit on ``Command.flags``; the dispatcher parses
params *after* flags (reusing ``scripting.parse_keywords`` for the established
``key=value`` grammar) and binds the coerced results to ``ctx.bound_params``,
which handlers read via ``ctx.arg()``.

This module is intentionally free of any ``Command``/``PluginContext`` import so
it stays a pure, unit-testable leaf: it turns a list of ``ParamSpec`` plus a raw
argument string into either a bound ``dict`` or a single error reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from termapy.scripting import parse_duration, parse_keywords


@dataclass(frozen=True)
class EnumValue:
    """One accepted value of an ``enum`` parameter.

    Attributes:
        canonical: The single spelling handlers compare against (coercion
            always returns this, regardless of which alias the user typed).
        aliases: Extra spellings accepted on input (shown only in long help).
    """

    canonical: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParamSpec:
    """Declarative spec for one command parameter.

    Declared on ``Command.params``; the dispatcher parses/coerces/validates
    before the handler runs.  See ``docs/param-spec-implementation.md`` for the
    full contract.

    Attributes:
        name: Keyword name, lowercase (``parse_keywords`` lowercases keys).
        type: One of ``TYPES`` (str/int/float/duration/enum/path/command).
        required: Fail dispatch if absent.
        default: Post-coercion value applied when an optional param is absent
            (e.g. ``0.25`` for a ``"250ms"`` duration -- NOT the raw string;
            it is bound directly, never re-coerced).
        help: One-line description; feeds ``/help`` and the MCP catalog.
        hint: Override the synopsis type-hint (e.g. ``"<name>"``, ``"<file>"``)
            when the generic per-type hint (``<value>`` for ``str``) is less
            descriptive than the parameter warrants.  Empty = use the default.
        positional: Bound from positional tokens in declared order (no
            ``name=``).  Positional values cannot contain spaces.
        rest: Consumes to end of line; at most one per command; must not also
            be positional.  Required for ``type="command"``.
        values: ``EnumValue`` entries (``enum`` only).
        min: Inclusive lower bound (``int``/``float`` only).
        max: Inclusive upper bound (``int``/``float`` only).
    """

    name: str
    type: str = "str"
    required: bool = False
    default: Any = None
    help: str = ""
    hint: str = ""
    positional: bool = False
    rest: bool = False
    values: tuple[EnumValue, ...] = ()
    min: float | None = None
    max: float | None = None


# -- Coercion -------------------------------------------------------------------

CoerceResult = tuple[bool, Any]


def _fmt_num(x: float) -> str:
    """Render a bound so ``1.0`` prints as ``1`` but ``1.5`` stays ``1.5``."""
    return str(int(x)) if float(x).is_integer() else str(x)


def _range_check(spec: ParamSpec, value: float) -> CoerceResult:
    lo, hi = spec.min, spec.max
    if lo is not None and hi is not None and not (lo <= value <= hi):
        return False, (
            f"{spec.name} must be between {_fmt_num(lo)} and {_fmt_num(hi)} "
            f"(got {_fmt_num(value)})"
        )
    if lo is not None and value < lo:
        return False, f"{spec.name} must be >= {_fmt_num(lo)} (got {_fmt_num(value)})"
    if hi is not None and value > hi:
        return False, f"{spec.name} must be <= {_fmt_num(hi)} (got {_fmt_num(value)})"
    return True, value


# str / path / command are identity (no resolution, no case-fold -- resolution
# stays in handlers where cap-dir vs scripts-dir differ).
TYPES = frozenset({"str", "int", "float", "duration", "enum", "path", "command"})
_IDENTITY = frozenset({"str", "path", "command"})
_NUMERIC = {"int": (int, "an integer"), "float": (float, "a number")}


def coerce_value(spec: ParamSpec, text: str) -> CoerceResult:
    """Coerce a raw token per the spec's type -- ``(ok, value|reason)``.

    Reason phrasing is the fixed vocabulary from param-fail-message; do not
    improvise variants.
    """
    kind = spec.type
    if kind in _IDENTITY:
        return True, text
    if kind in _NUMERIC:
        convert, noun = _NUMERIC[kind]
        try:
            value = convert(text)
        except ValueError:
            return False, f"invalid {spec.name}: {text!r} (expected {noun})"
        return _range_check(spec, value)
    if kind == "duration":
        if text == "0":
            return True, 0.0  # bare 0 is zero regardless of unit; parse_duration wants a unit
        try:
            return True, parse_duration(text)
        except ValueError:
            return False, (
                f"invalid {spec.name}: {text!r} (expected duration, e.g. 500ms, 1.5s)"
            )
    low = text.lower()  # enum
    for ev in spec.values:
        if low == ev.canonical.lower() or low in {a.lower() for a in ev.aliases}:
            return True, ev.canonical
    allowed = ", ".join(ev.canonical for ev in spec.values)
    return False, f"invalid {spec.name}: {text!r} (expected one of: {allowed})"


# -- Declaration validation (fires at load, via Command.__post_init__) ----------


def validate_param_specs(params: list[ParamSpec], command_name: str) -> None:
    """Validate a param declaration; raise ``ValueError`` on any problem.

    Called once at command construction so a broken plugin fails at load,
    loudly, rather than at first dispatch.  Command-level cross-checks that
    need other ``Command`` fields (``args`` vs ``params``, ``raw_args`` vs
    ``params``) live in ``Command.__post_init__``, not here.
    """
    if not params:
        return
    label = f"/{command_name}" if command_name else "<command>"
    seen: set[str] = set()
    rest_count = 0
    for spec in params:
        if spec.type not in TYPES:
            raise ValueError(
                f"{label}: parameter {spec.name!r} has unknown type {spec.type!r} "
                f"(expected one of: {', '.join(sorted(TYPES))})"
            )
        if spec.name != spec.name.lower():
            raise ValueError(
                f"{label}: parameter name {spec.name!r} must be lowercase"
            )
        if spec.name in seen:
            raise ValueError(f"{label}: duplicate parameter {spec.name!r}")
        seen.add(spec.name)
        if spec.rest:
            rest_count += 1
        if spec.type == "command" and not spec.rest:
            raise ValueError(
                f"{label}: command-type parameter {spec.name!r} must set rest=True "
                f"(a command consumes to end of line)"
            )
        if spec.type == "enum" and not spec.values:
            raise ValueError(f"{label}: enum parameter {spec.name!r} has no values")
        if spec.required and spec.default is not None:
            raise ValueError(
                f"{label}: required parameter {spec.name!r} must not have a default"
            )
    if rest_count > 1:
        raise ValueError(f"{label}: at most one rest=True parameter (got {rest_count})")
    # A positional-rest (a positional that consumes the whole remaining line,
    # so the value may contain spaces) must be the last positional.
    positionals = [p for p in params if p.positional]
    for spec in positionals[:-1]:
        if spec.rest:
            raise ValueError(
                f"{label}: positional-rest parameter {spec.name!r} must be the "
                f"last positional"
            )


# -- Parse (runs per dispatch, only when params is non-empty) --------------------


def parse_params(
    params: list[ParamSpec], args: str
) -> tuple[dict[str, Any], str | None]:
    """Parse ``args`` against ``params``.  Returns ``(bound, error)``.

    On success ``error`` is None and ``bound`` maps every declared param name
    to its coerced value (defaults filled in).  On the first failure ``bound``
    is empty and ``error`` is a single reason string (see param-fail-message);
    the caller prefixes ``Error: /cmd:`` and appends the synthesized ``Usage:``.
    """
    positional_specs = [p for p in params if p.positional]
    keyword_specs = [p for p in params if not p.positional]
    kw_rest = next((p for p in keyword_specs if p.rest), None)
    pos_rest = (
        positional_specs[-1]
        if positional_specs and positional_specs[-1].rest
        else None
    )

    keywords = {p.name for p in keyword_specs}
    rest_keyword = kw_rest.name if kw_rest else ""
    sections = parse_keywords(args, keywords, rest_keyword=rest_keyword)

    bound: dict[str, Any] = {}

    # Positional params.  A trailing positional-rest consumes the whole
    # remainder as one value (so a path/command/regex may contain spaces);
    # fixed positionals before it take one whitespace token each.
    positional_tokens = sections.get("_positional", "").split()
    n_fixed = len(positional_specs) - 1 if pos_rest else len(positional_specs)
    for i in range(min(n_fixed, len(positional_tokens))):
        ok, value = coerce_value(positional_specs[i], positional_tokens[i])
        if not ok:
            return {}, value
        bound[positional_specs[i].name] = value
    if pos_rest:
        remainder = " ".join(positional_tokens[n_fixed:])
        if remainder:
            ok, value = coerce_value(pos_rest, remainder)
            if not ok:
                return {}, value
            bound[pos_rest.name] = value
    elif len(positional_tokens) > len(positional_specs):
        extra = positional_tokens[len(positional_specs)]
        return {}, f"unexpected argument: {extra!r}"

    # Keyword params (the rest param is one of these).
    for spec in keyword_specs:
        if spec.name in sections:
            ok, value = coerce_value(spec, sections[spec.name])
            if not ok:
                return {}, value
            bound[spec.name] = value

    # Required-present check, then defaults for the absent optionals.
    for spec in params:
        if spec.required and spec.name not in bound:
            return {}, f"missing required parameter {spec.name!r}"
    for spec in params:
        bound.setdefault(spec.name, spec.default)

    return bound, None


# -- Help / synopsis synthesis --------------------------------------------------


def _type_hint(spec: ParamSpec) -> str:
    if spec.hint:
        return spec.hint
    if spec.type == "enum":
        return "|".join(ev.canonical for ev in spec.values)
    return {
        "duration": "<dur>",
        "int": "<N>",
        "float": "<N>",
        "path": "<path>",
        "command": "<command>",
        "str": "<value>",
    }[spec.type]


def _token(spec: ParamSpec) -> str:
    """The ``<name>`` / ``name=<hint>`` core, without optional braces."""
    return f"<{spec.name}>" if spec.positional else f"{spec.name}={_type_hint(spec)}"


def _rest_last(params: list[ParamSpec]) -> list[ParamSpec]:
    """Declared order, with the rest param (if any) moved to the end."""
    return [p for p in params if not p.rest] + [p for p in params if p.rest]


def synthesize_synopsis(params: list[ParamSpec]) -> str:
    """Build the ``args``-style synopsis line from ``params`` (rest param last)."""
    parts = []
    for p in _rest_last(params):
        core = _token(p)
        parts.append(core if p.required else f"{{{core}}}")
    return " ".join(parts)


def _format_default(spec: ParamSpec) -> str:
    if spec.default is None:
        return ""
    return f"{_fmt_num(spec.default)}s" if spec.type == "duration" else str(spec.default)


def render_parameters_block(params: list[ParamSpec]) -> list[str]:
    """Render the ``PARAMETERS`` help lines: ``name=<hint>  help (default: X)``.

    Rest param last; the name column is padded to align the help text.
    """
    ordered = _rest_last(params)
    tokens = [_token(p) for p in ordered]
    width = max((len(t) for t in tokens), default=0)
    lines: list[str] = []
    for spec, token in zip(ordered, tokens):
        notes: list[str] = []
        if spec.required:
            notes.append("required")
        if spec.rest:
            notes.append("must be last")
        if not spec.required and spec.default is not None:
            notes.append(f"default: {_format_default(spec)}")
        suffix = f" ({', '.join(notes)})" if notes else ""
        help_text = f"{spec.help}{suffix}".strip()
        lines.append(f"  {token.ljust(width)}  {help_text}".rstrip())
    return lines
