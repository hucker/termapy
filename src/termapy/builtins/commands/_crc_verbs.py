"""Generate typed ``/proto.crc.*`` commands from crcglot's ``VERBS`` manifest.

crcglot owns the CRC knowledge end to end: ``crcglot.VERBS`` declares each verb's
parameters (types, enums, defaults, help) and ``crcglot.call_verb(name, **params)``
executes a verb from those same manifest param names, returning a JSON-ready wire
dict (the shape ``VERBS[name].result_fields`` documents).

This module renders that into termapy ``Command`` objects: typed
``params=[ParamSpec(...)]`` derived from the manifest, plus one generic handler
that just forwards to ``call_verb``.  The only things termapy owns are the two
legitimate frontend concerns -- folding the mutually-exclusive input
representations into a single ``<frame>`` positional, and how to display the
result.  Everything else (types, enum values, defaults, help, execution, result
shaping) comes from crcglot, so there is nothing to keep in sync by hand.

See ``project_crcglot_verbs_integration`` and ``docs/param-spec-implementation.md``.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

import crcglot

from termapy.plugins import CmdResult, Command
from termapy.plugins.params import EnumValue, ParamSpec

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# crcglot manifest type -> termapy ParamSpec type.  boolean / object /
# array[string] / union have no ParamSpec equivalent yet; they are skipped and
# reported so a verb that needs them stays a documented hand-rolled holdout.
_TYPE_MAP = {"string": "str", "integer": "int"}


def _enum_values(choices: tuple) -> tuple[EnumValue, ...]:
    """crcglot ChoiceInfo[] -> termapy EnumValue[] (canonical names, no aliases)."""
    return tuple(EnumValue(c.name) for c in choices)


def _input_group(verb: Any):
    """The required mutually-exclusive input-representation group, if any.

    crcglot models "supply the frame as hex OR text OR base64" as a required
    ``ExclusiveGroup``.  termapy folds that into a single ``<frame>`` positional
    and forwards it as the group's first member (e.g. ``packet_hex``).
    """
    return next(
        (g for g in verb.mutually_exclusive
         if g.required and any("packet" in p or "data" in p for p in g.params)),
        None,
    )


def build_params(verb: Any) -> tuple[list[ParamSpec], str | None, list[str]]:
    """Map a crcglot ``VerbSpec`` to termapy ParamSpecs.

    Returns ``(params, input_param, skipped)`` where ``input_param`` is the
    manifest param name that the folded ``<frame>`` positional forwards to
    (None if the verb has no input group), and ``skipped`` lists params whose
    manifest type has no ParamSpec equivalent yet.
    """
    group = _input_group(verb)
    input_names = set(group.params) if group else set()
    input_param = group.params[0] if group else None

    specs: list[ParamSpec] = []
    if group:
        specs.append(ParamSpec(
            name="frame", type="str", positional=True, rest=True, required=True,
            hint="<frame>",
            help="captured frame (payload + trailing CRC), as hex bytes",
        ))

    skipped: list[str] = []
    for p in verb.params:
        if p.name in input_names:
            continue
        if p.choices:
            specs.append(ParamSpec(
                name=p.name, type="enum", values=_enum_values(p.choices),
                default=p.default, help=p.help))
        elif p.type in _TYPE_MAP:
            specs.append(ParamSpec(
                name=p.name, type=_TYPE_MAP[p.type], required=p.required,
                default=p.default, help=p.help))
        else:
            skipped.append(f"{p.name} ({p.type})")
    return specs, input_param, skipped


def _make_handler(
    verb_name: str, input_param: str | None, keyword_names: list[str]
) -> Callable[[PluginContext, str], CmdResult]:
    """A generic handler: collect declared params -> call_verb -> render the dict."""

    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        params: dict[str, Any] = {}
        if input_param is not None:
            params[input_param] = ctx.arg("frame")
        for name in keyword_names:
            value = ctx.arg(name)
            if value is not None:
                params[name] = value
        try:
            result = crcglot.call_verb(verb_name, **params)
        except (crcglot.CrcglotError, ValueError) as e:
            return CmdResult.fail(msg=f"CRC {verb_name} error: {e}")
        # The wire dict is already JSON-ready (bytes hexed, enums stringified,
        # keys matching VERBS.result_fields).  Pretty-print for humans; return
        # the compact JSON as the value so scripts / MCP get the full structure.
        ctx.io.output(json.dumps(result, indent=2))
        return CmdResult.ok(value=json.dumps(result))

    return _handler


def build_crc_verb_command(verb_name: str) -> Command:
    """Build a fully typed ``Command`` for one crcglot verb, all from the manifest."""
    verb = crcglot.VERBS[verb_name]
    specs, input_param, _skipped = build_params(verb)
    keyword_names = [s.name for s in specs if not s.positional]
    return Command(
        params=specs,
        help=verb.summary,
        long_help=verb.description,
        handler=_make_handler(verb_name, input_param, keyword_names),
    )
