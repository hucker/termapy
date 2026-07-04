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
from typing import Any, Callable

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
    positional: bool = False
    rest: bool = False
    values: tuple[EnumValue, ...] = ()
    min: float | None = None
    max: float | None = None


# -- Coercion -------------------------------------------------------------------
# Each coercer takes (spec, text) and returns (ok, value) on success or
# (False, reason) on failure.  Reason phrasing is the fixed vocabulary from
# param-fail-message -- do not improvise variants.

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


def _coerce_str(spec: ParamSpec, text: str) -> CoerceResult:
    return True, text


def _coerce_int(spec: ParamSpec, text: str) -> CoerceResult:
    try:
        value = int(text)
    except ValueError:
        return False, f"invalid {spec.name}: {text!r} (expected an integer)"
    return _range_check(spec, value)


def _coerce_float(spec: ParamSpec, text: str) -> CoerceResult:
    try:
        value = float(text)
    except ValueError:
        return False, f"invalid {spec.name}: {text!r} (expected a number)"
    return _range_check(spec, value)


def _coerce_duration(spec: ParamSpec, text: str) -> CoerceResult:
    try:
        seconds = parse_duration(text)
    except ValueError:
        return False, (
            f"invalid {spec.name}: {text!r} (expected duration, e.g. 500ms, 1.5s)"
        )
    return True, seconds


def _coerce_enum(spec: ParamSpec, text: str) -> CoerceResult:
    low = text.lower()
    for ev in spec.values:
        if low == ev.canonical.lower() or low in {a.lower() for a in ev.aliases}:
            return True, ev.canonical
    canon = ", ".join(ev.canonical for ev in spec.values)
    return False, f"invalid {spec.name}: {text!r} (expected one of: {canon})"


# ``path`` and ``command`` are identity strings (no resolution / no case-fold);
# resolution stays in handlers where cap-dir vs scripts-dir differ.
COERCERS: dict[str, Callable[[ParamSpec, str], CoerceResult]] = {
    "str": _coerce_str,
    "int": _coerce_int,
    "float": _coerce_float,
    "duration": _coerce_duration,
    "enum": _coerce_enum,
    "path": _coerce_str,
    "command": _coerce_str,
}

TYPES = frozenset(COERCERS)


def coerce_value(spec: ParamSpec, text: str) -> CoerceResult:
    """Coerce a raw token per the spec's type.  Returns ``(ok, value|reason)``."""
    return COERCERS[spec.type](spec, text)


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
            if spec.positional:
                raise ValueError(
                    f"{label}: parameter {spec.name!r} cannot be both rest and positional"
                )
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
    rest_spec = next((p for p in params if p.rest), None)

    keywords = {p.name for p in keyword_specs}
    rest_keyword = rest_spec.name if rest_spec else ""
    sections = parse_keywords(args, keywords, rest_keyword=rest_keyword)

    bound: dict[str, Any] = {}

    # Positional params, bound in declared order from the leftover tokens.
    positional_tokens = sections.get("_positional", "").split()
    for i, spec in enumerate(positional_specs):
        if i >= len(positional_tokens):
            break
        ok, value = coerce_value(spec, positional_tokens[i])
        if not ok:
            return {}, value
        bound[spec.name] = value
    if len(positional_tokens) > len(positional_specs):
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


def _synopsis_token(spec: ParamSpec) -> str:
    if spec.positional:
        core = f"<{spec.name}>"
    else:
        core = f"{spec.name}={_type_hint(spec)}"
    return core if spec.required else f"{{{core}}}"


def synthesize_synopsis(params: list[ParamSpec]) -> str:
    """Build the ``args``-style synopsis line from ``params``.

    Non-rest params in declared order, the rest param last (matching the
    existing ``Command.args`` convention).
    """
    head = [_synopsis_token(p) for p in params if not p.rest]
    tail = [_synopsis_token(p) for p in params if p.rest]
    return " ".join(head + tail)


def _format_default(spec: ParamSpec) -> str:
    value = spec.default
    if value is None:
        return ""
    if spec.type == "duration":
        return f"{_fmt_num(value)}s"
    return str(value)


def render_parameters_block(params: list[ParamSpec]) -> list[str]:
    """Render the ``PARAMETERS`` help lines: ``name=<hint>  help (default: X)``.

    Rest param rendered last; the name column is padded to align the help text.
    """
    ordered = [p for p in params if not p.rest] + [p for p in params if p.rest]
    tokens = [
        f"{p.name}={_type_hint(p)}" if not p.positional else f"<{p.name}>"
        for p in ordered
    ]
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
