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

from termapy.plugins import CmdResult, Command, UsageError
from termapy.plugins.params import EnumValue, ParamSpec

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# crcglot manifest type -> termapy ParamSpec type.  boolean / object /
# array[string] / union have no ParamSpec equivalent yet; they are skipped and
# reported so a verb that needs them stays a documented hand-rolled holdout.
_TYPE_MAP = {"string": "str", "integer": "int"}


def _enum_values(choices: tuple) -> tuple[EnumValue, ...]:
    """crcglot ChoiceInfo[] -> termapy EnumValue[] (canonical names, no aliases)."""
    return tuple(EnumValue(choice.name) for choice in choices)


def _input_group(verb: Any):
    """The required mutually-exclusive input-representation group, if any.

    crcglot models "supply the frame as hex OR text OR base64" as a required
    ``ExclusiveGroup``.  termapy folds that into a single ``<frame>`` positional
    and forwards it as the group's first member (e.g. ``packet_hex``).
    """
    return next(
        (g for g in verb.mutually_exclusive
         if g.required and any("packet" in param or "data" in param for param in g.params)),
        None,
    )


def _frames_param(verb: Any, group: Any) -> str | None:
    """The input group's frame-LIST member, when it has exactly one.

    crcglot models "one or more frames" as an ``array[string]`` member of the
    required input group (``packets`` on ``detect``).  termapy renders that as a
    variadic positional -- one frame per argument token.  A group with two array
    members would be ambiguous, so it keeps the scalar fold instead.
    """
    if group is None:
        return None
    arrays = [param.name for param in verb.params
              if param.name in group.params and param.type == "array[string]"]
    return arrays[0] if len(arrays) == 1 else None


def build_params(verb: Any) -> tuple[list[ParamSpec], dict[str, str], list[str]]:
    """Map a crcglot ``VerbSpec`` to termapy ParamSpecs.

    Returns ``(params, input_map, skipped)``.  ``input_map`` maps each bound
    argument name to the manifest param it forwards to -- ``{"frame": <scalar>}``
    for a verb that takes one frame, plus ``{"frames": <array>}`` when the verb
    also accepts a list.  ``skipped`` lists params whose manifest type has no
    ParamSpec equivalent yet.

    A verb accepting a list gets BOTH forms, because they serve different
    inputs: ``<frames>...`` binds one frame per argument (so several captured
    frames intersect to eliminate a coincidental match), while ``frame=`` runs
    to end of line (so a single frame pasted from a log keeps its spaces).
    crcglot's own ExclusiveGroup rejects supplying both, so termapy does not
    police that itself.
    """
    group = _input_group(verb)
    input_names = set(group.params) if group else set()
    frames_param = _frames_param(verb, group)
    input_map: dict[str, str] = {}

    specs: list[ParamSpec] = []
    if group:
        input_map["frame"] = group.params[0]
        if frames_param:
            input_map["frames"] = frames_param
            specs.append(ParamSpec(
                name="frames", type="str", positional=True, variadic=True,
                help="captured frame (payload + trailing CRC), as hex bytes",
            ))
            specs.append(ParamSpec(
                name="frame", type="str", rest=True,
                help=(
                    "a single frame that contains spaces, e.g. frame=01 03 00 0a "
                    "(must be the last argument)"
                ),
            ))
        else:
            specs.append(ParamSpec(
                name="frame", type="str", positional=True, rest=True, required=True,
                hint="<frame>",
                help="captured frame (payload + trailing CRC), as hex bytes",
            ))

    skipped: list[str] = []
    for param in verb.params:
        if param.name in input_names:
            continue
        if param.choices:
            specs.append(ParamSpec(
                name=param.name, type="enum", values=_enum_values(param.choices),
                default=param.default, help=param.help))
        elif param.type in _TYPE_MAP:
            specs.append(ParamSpec(
                name=param.name, type=_TYPE_MAP[param.type], required=param.required,
                default=param.default, help=param.help))
        else:
            skipped.append(f"{param.name} ({param.type})")
    return specs, input_map, skipped


def _make_handler(
    verb_name: str, input_map: dict[str, str], keyword_names: list[str]
) -> Callable[[PluginContext, str], CmdResult]:
    """A generic handler: collect declared params -> call_verb -> render the dict."""

    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        params: dict[str, Any] = {}
        # Forward only the input form the user actually supplied -- passing an
        # empty one alongside a real one would trip crcglot's "not both"
        # exclusion, which is crcglot's rule to enforce and ours to surface.
        for arg_name, manifest_name in input_map.items():
            value = ctx.arg(arg_name)
            if value:
                params[manifest_name] = value
        # Supplying NOTHING is an arity error, which termapy owns: crcglot
        # would answer in manifest names ("packet_hex / packet_text ...")
        # that no termapy user ever types.  UsageError renders the synopsis,
        # which names the real argument forms instead.
        if input_map and not params:
            raise UsageError()
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


# How termapy's argument grammar maps onto a multi-frame verb.  This is a
# frontend concern (crcglot has no notion of argument tokens), so termapy owns
# the wording and appends it to the manifest's own description.
_FRAMES_NOTE = """

ARGUMENTS: each argument is one frame.  Bytes within a frame may be separated
by commas or colons (01,03,00,0a) -- spaces separate frames.  To pass a single
space-separated frame, use frame=01 03 00 0a as the LAST argument.  A frame
captured into a variable is passed whole with $(*NAME)."""


def build_crc_verb_command(verb_name: str) -> Command:
    """Build a fully typed ``Command`` for one crcglot verb, all from the manifest."""
    verb = crcglot.VERBS[verb_name]
    specs, input_map, _skipped = build_params(verb)
    keyword_names = [s.name for s in specs if not s.positional and s.name != "frame"]
    long_help = verb.description
    if "frames" in input_map:
        long_help += _FRAMES_NOTE
    return Command(
        params=specs,
        help=verb.summary,
        long_help=long_help,
        handler=_make_handler(verb_name, input_map, keyword_names),
    )
