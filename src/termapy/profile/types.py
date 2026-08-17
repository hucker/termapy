"""Profile-local user-defined types for the v2 device profile schema.

A v2 device profile may declare a top-level ``types`` block — a map of
named user-defined types that ``typed_args[i].type`` can reference.
This lets profile authors express device-specific argument vocabularies
(e.g. mag3 accepts ``on/off/true/false/yes/no/1/0/high/low`` for a bool;
a strict AT modem accepts only ``0/1``) without forking the schema.

Six ``kind`` values are recognized in the first cut:

  - ``enum``        — exact-match against a ``values`` list
  - ``int_range``   — coerce to int, check ``min <= v <= max``
  - ``float_range`` — coerce to float, same bounds check
  - ``str_length``  — coerce to str, check len() against min_len/max_len
  - ``pattern``     — ``re.fullmatch`` against a regex
  - ``format_spec`` — parse via ``protocol.parse_format_spec``; the
                      validator is a stub today (always passes).  The
                      schema accepts it and the registry caches the
                      parsed columns so authors can declare it now;
                      full validation lands when needed without
                      schema or catalog surgery.

Five **builtins** stay available for ``typed_args[i].type`` without any
``types`` block declaration:

  - ``int``, ``float``, ``bool``, ``hex``, ``str``

A custom type cannot shadow a builtin — the registry rejects it at
load time.

This module is pure: no I/O, no MCP imports, no Textual deps.  The
dispatch hook in ``mcp/server.py`` imports ``TypeRegistry`` and calls
``validate`` per typed_arg.  See ``authoring-profiles.md`` for the
author-facing reference.

Prior art: this is a direct descendent of YANG's leaf-type declarations
(used by NETCONF/RESTCONF for network devices since 2010) and SCPI's
``<numeric>``/``<discrete>``/``<boolean>`` argument types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from termapy.response_parsers import _coerce as _coerce_builtin

# Names that may not be used as custom type names — they always resolve
# to the corresponding builtin coercion.
BUILTINS: frozenset[str] = frozenset({"int", "float", "bool", "hex", "str"})

# Valid ``kind`` values for a user-defined type.  Mirrored from the
# schema; ``schema_kinds()`` is the single source of truth for tests.
_KINDS: frozenset[str] = frozenset({
    "enum",
    "int_range",
    "float_range",
    "str_length",
    "pattern",
    "format_spec",
})


def schema_kinds() -> tuple[str, ...]:
    """Return the canonical list of recognized ``kind`` values.

    Used by tests to assert schema/registry agreement.  Ordering is
    arbitrary but stable.
    """
    return tuple(sorted(_KINDS))


@dataclass(frozen=True)
class TypeDef:
    """A resolved user-defined or builtin type.

    Built via ``TypeRegistry.from_profile``.  All fields are optional
    except ``name`` and ``kind`` — only the fields relevant to the
    ``kind`` are populated.

    Attributes:
        name: The type name (key in the profile's ``types`` block, or
            a builtin like ``"int"``).
        kind: Discriminator. One of "builtin", "enum", "int_range",
            "float_range", "str_length", "pattern", "format_spec".
        values: For ``enum`` — the allowed values (stringified).
        min, max: For ``int_range`` / ``float_range`` bounds.
        min_len, max_len: For ``str_length`` bounds.  Either may be
            None to mean "no limit on this side."
        regex: For ``pattern`` — the regex source.
        compiled_regex: For ``pattern`` — the compiled regex, or None
            if compilation failed (build_error will be set).
        spec: For ``format_spec`` — the raw spec string (used in the
            catalog so the LLM can read the field layout).
        columns: For ``format_spec`` — the parsed ColumnSpec tuple
            (cached at registry build time).  Empty if spec parse
            failed.
        help: Optional human-readable description.
        build_error: Non-empty if the type definition failed at build
            time (bad regex, unparseable spec, etc.).  ``validate``
            returns this as a failure so the issue surfaces at the
            first call instead of crashing the registry build.
    """

    name: str
    kind: str
    values: tuple = field(default_factory=tuple)
    min: float | None = None
    max: float | None = None
    min_len: int | None = None
    max_len: int | None = None
    regex: str = ""
    compiled_regex: Any = None  # re.Pattern or None
    spec: str = ""
    columns: tuple = field(default_factory=tuple)
    help: str = ""
    build_error: str = ""


@dataclass(frozen=True)
class ValidationOutcome:
    """Result of validating one bound argument against a TypeDef.

    Attributes:
        ok: True on success.
        value: The (possibly normalized) value, on success.  For
            builtins this is the coerced Python type.  For other kinds
            this is the original raw value passed in.  Future
            send-template re-rendering may consult ``value`` to
            produce a canonical wire form.
        error: Human-readable failure message, on ok=False.
    """

    ok: bool
    value: Any = None
    error: str = ""


class ProfileTypeError(ValueError):
    """Raised at registry build time on irrecoverable type definitions.

    Typical causes: a custom type whose name shadows a builtin, or a
    type entry that doesn't match its schema-declared kind.  Caught at
    the profile-load layer and surfaced as a profile-load warning.
    """


def _build_typedef(name: str, raw: dict) -> TypeDef:
    """Construct a TypeDef from one entry in the profile's ``types`` block.

    Validation errors are captured on the TypeDef as ``build_error``
    rather than raising, so a single bad type doesn't prevent the rest
    of the profile from loading.  The exception is collisions with
    builtin names, which are fatal (raised as ``ProfileTypeError``).
    """
    if name in BUILTINS:
        raise ProfileTypeError(
            f"Custom type {name!r} collides with a builtin "
            f"({', '.join(sorted(BUILTINS))}); rename it."
        )
    kind = raw.get("kind", "")
    help_text = raw.get("help", "")
    if kind not in _KINDS:
        return TypeDef(
            name=name, kind=kind or "?", help=help_text,
            build_error=(
                f"unknown kind {kind!r}; "
                f"valid: {', '.join(schema_kinds())}"
            ),
        )

    if kind == "enum":
        values = raw.get("values", [])
        if not values:
            return TypeDef(
                name=name, kind=kind, help=help_text,
                build_error="enum type requires non-empty 'values'",
            )
        # Stringify for uniform comparison — schema allows
        # str/number/bool members.
        return TypeDef(
            name=name, kind=kind, help=help_text,
            values=tuple(str(value) for value in values),
        )

    if kind in ("int_range", "float_range"):
        if "min" not in raw or "max" not in raw:
            return TypeDef(
                name=name, kind=kind, help=help_text,
                build_error=f"{kind} requires both 'min' and 'max'",
            )
        return TypeDef(
            name=name, kind=kind, help=help_text,
            min=float(raw["min"]), max=float(raw["max"]),
        )

    if kind == "str_length":
        mn = raw.get("min_len")
        mx = raw.get("max_len")
        if mn is None and mx is None:
            return TypeDef(
                name=name, kind=kind, help=help_text,
                build_error="str_length requires 'min_len' or 'max_len'",
            )
        return TypeDef(
            name=name, kind=kind, help=help_text,
            min_len=int(mn) if mn is not None else None,
            max_len=int(mx) if mx is not None else None,
        )

    if kind == "pattern":
        regex = raw.get("regex", "")
        if not regex:
            return TypeDef(
                name=name, kind=kind, help=help_text,
                build_error="pattern requires 'regex'",
            )
        try:
            compiled = re.compile(regex)
        except re.error as exc:
            return TypeDef(
                name=name, kind=kind, help=help_text, regex=regex,
                build_error=f"invalid regex: {exc}",
            )
        return TypeDef(
            name=name, kind=kind, help=help_text,
            regex=regex, compiled_regex=compiled,
        )

    if kind == "format_spec":
        spec = raw.get("spec", "")
        if not spec:
            return TypeDef(
                name=name, kind=kind, help=help_text,
                build_error="format_spec requires 'spec'",
            )
        # Parse via protocol.parse_format_spec once at build time so
        # the catalog can surface the column layout and a future
        # validator can read the parsed columns instead of the raw
        # string.  Failure is captured non-fatally.
        try:
            from termapy.protocol import parse_format_spec
            columns = tuple(parse_format_spec(spec))
        except Exception as exc:  # noqa: BLE001 -- protocol boundary
            return TypeDef(
                name=name, kind=kind, help=help_text, spec=spec,
                build_error=f"invalid format_spec: {exc}",
            )
        return TypeDef(
            name=name, kind=kind, help=help_text,
            spec=spec, columns=columns,
        )

    # Unreachable given the _KINDS membership check above; included to
    # quiet type-checkers and to catch future kinds added to the schema
    # without a handler here.
    return TypeDef(
        name=name, kind=kind, help=help_text,
        build_error=f"no validator for kind {kind!r}",
    )


class TypeRegistry:
    """Lookup + validation for a profile's named types.

    Construct via ``TypeRegistry.from_profile(profile)``.  The
    registry knows the five builtins ``int``/``float``/``bool``/
    ``hex``/``str`` plus whatever the profile's ``types`` block
    declares.  A custom type whose definition is invalid still
    appears in the registry but ``validate`` returns its
    ``build_error`` on every call — surfacing the bad definition
    when the LLM tries to use the type, instead of silently
    discarding it at load.

    Lookup precedence in ``validate``:
      1. Builtin name → coerce via ``response_parsers._coerce``.
      2. Custom name present in the registry → dispatch by ``kind``.
      3. Unknown name → ValidationOutcome with a "no such type" error.
    """

    def __init__(self, types: dict[str, TypeDef]) -> None:
        self._types = dict(types)

    @classmethod
    def from_profile(cls, profile: dict | None) -> "TypeRegistry":
        """Build a registry from a profile dict (or empty if None / no block).

        Profiles without a ``types`` block produce an empty registry;
        the validator still handles builtin types via the precedence
        chain above.
        """
        if not isinstance(profile, dict):
            return cls({})
        types_block = profile.get("types") or {}
        if not isinstance(types_block, dict):
            return cls({})
        built: dict[str, TypeDef] = {}
        for name, raw in types_block.items():
            if not isinstance(raw, dict):
                # Ill-formed entry — store a build-error TypeDef so the
                # author sees the problem when they try to use it.
                built[name] = TypeDef(
                    name=name, kind="?",
                    build_error=(
                        f"type definition must be an object, "
                        f"got {type(raw).__name__}"
                    ),
                )
                continue
            built[name] = _build_typedef(name, raw)
        return cls(built)

    def resolve(self, name: str) -> TypeDef | None:
        """Return the TypeDef for ``name``, synthesizing builtins.

        Returns None when ``name`` is neither a builtin nor a custom
        type in the profile — the caller can treat this as a profile-
        author mistake.
        """
        if name in BUILTINS:
            return TypeDef(name=name, kind="builtin")
        return self._types.get(name)

    def all(self) -> dict[str, TypeDef]:
        """Return a copy of the custom type map (builtins excluded).

        Used by the catalog generator to emit the ``types`` block.
        Builtins aren't included because they're not per-profile data
        — every consumer knows them.
        """
        return dict(self._types)

    def validate(self, type_name: str, raw: Any) -> ValidationOutcome:
        """Validate ``raw`` against the named type.

        Returns ``ValidationOutcome(ok=True, value=normalized)`` on
        success or ``ValidationOutcome(ok=False, error=msg)`` on
        failure.  Never raises.
        """
        # Builtin precedence.
        if type_name in BUILTINS:
            return self._validate_builtin(type_name, raw)

        td = self._types.get(type_name)
        if td is None:
            return ValidationOutcome(
                ok=False,
                error=f"unknown type {type_name!r}; "
                      f"add it to the profile's 'types' block or use a "
                      f"builtin ({', '.join(sorted(BUILTINS))})",
            )
        if td.build_error:
            return ValidationOutcome(
                ok=False,
                error=f"type {type_name!r} has a definition error: "
                      f"{td.build_error}",
            )
        return self._validate_custom(td, raw)

    # ── builtin dispatch ────────────────────────────────────────

    def _validate_builtin(self, name: str, raw: Any) -> ValidationOutcome:
        """Coerce via response_parsers._coerce; success iff type matches."""
        coerced = _coerce_builtin(str(raw), name)
        # _coerce returns the raw string on coercion failure -- detect
        # that by checking whether we got a meaningful Python type.
        if name == "int" and isinstance(coerced, int) and not isinstance(coerced, bool):
            return ValidationOutcome(ok=True, value=coerced)
        if name == "float" and isinstance(coerced, float):
            return ValidationOutcome(ok=True, value=coerced)
        if name == "hex" and isinstance(coerced, int) and not isinstance(coerced, bool):
            return ValidationOutcome(ok=True, value=coerced)
        if name == "bool" and isinstance(coerced, bool):
            return ValidationOutcome(ok=True, value=coerced)
        if name == "str":
            return ValidationOutcome(ok=True, value=str(raw))
        # Coercion produced the raw string back -- failure.
        return ValidationOutcome(
            ok=False,
            error=f"cannot coerce {raw!r} to builtin {name!r}",
        )

    # ── custom-kind dispatch ────────────────────────────────────

    def _validate_custom(self, td: TypeDef, raw: Any) -> ValidationOutcome:
        if td.kind == "enum":
            actual = str(raw)
            if actual in td.values:
                return ValidationOutcome(ok=True, value=actual)
            allowed = ", ".join(td.values)
            return ValidationOutcome(
                ok=False,
                error=f"got {actual!r}; allowed: {allowed}",
            )

        if td.kind == "int_range":
            try:
                v = int(str(raw), 0)  # base-0 accepts 0x.., 0b.., 0o.., 10
            except (TypeError, ValueError):
                return ValidationOutcome(
                    ok=False,
                    error=f"got {raw!r}; expected int in "
                          f"[{int(td.min or 0)}, {int(td.max or 0)}]",
                )
            if td.min is not None and v < td.min:
                return ValidationOutcome(
                    ok=False,
                    error=f"got {v}; minimum is {int(td.min)}",
                )
            if td.max is not None and v > td.max:
                return ValidationOutcome(
                    ok=False,
                    error=f"got {v}; maximum is {int(td.max)}",
                )
            return ValidationOutcome(ok=True, value=v)

        if td.kind == "float_range":
            try:
                v = float(str(raw))
            except (TypeError, ValueError):
                return ValidationOutcome(
                    ok=False,
                    error=f"got {raw!r}; expected float in "
                          f"[{td.min}, {td.max}]",
                )
            if td.min is not None and v < td.min:
                return ValidationOutcome(
                    ok=False,
                    error=f"got {v}; minimum is {td.min}",
                )
            if td.max is not None and v > td.max:
                return ValidationOutcome(
                    ok=False,
                    error=f"got {v}; maximum is {td.max}",
                )
            return ValidationOutcome(ok=True, value=v)

        if td.kind == "str_length":
            s = str(raw)
            n = len(s)
            if td.min_len is not None and n < td.min_len:
                return ValidationOutcome(
                    ok=False,
                    error=f"length {n} below minimum {td.min_len}",
                )
            if td.max_len is not None and n > td.max_len:
                return ValidationOutcome(
                    ok=False,
                    error=f"length {n} above maximum {td.max_len}",
                )
            return ValidationOutcome(ok=True, value=s)

        if td.kind == "pattern":
            if td.compiled_regex is None:
                return ValidationOutcome(
                    ok=False,
                    error=f"pattern type {td.name!r} has no compiled regex",
                )
            s = str(raw)
            if td.compiled_regex.fullmatch(s) is None:
                return ValidationOutcome(
                    ok=False,
                    error=f"got {s!r}; must fullmatch {td.regex!r}",
                )
            return ValidationOutcome(ok=True, value=s)

        if td.kind == "format_spec":
            # Stub: schema accepts it, registry parsed it at build, but
            # full byte-level validation isn't wired up yet.  Authors
            # can declare format_spec types today; this returns success
            # so calls don't break, and the catalog still surfaces the
            # parsed column layout for the LLM's benefit.
            # TODO: validate ``raw`` against td.columns using a wrapped
            # apply_format() call once the binary-arg use case lands.
            return ValidationOutcome(ok=True, value=raw)

        # Unreachable -- _build_typedef would have set build_error.
        return ValidationOutcome(
            ok=False,
            error=f"no validator for kind {td.kind!r}",
        )


def typedef_to_catalog(td: TypeDef) -> dict[str, Any]:
    """Render a TypeDef as a JSON-safe dict for the catalog.

    Strips fields that are not relevant to the kind so the LLM sees a
    minimal, kind-shaped description.  ``compiled_regex`` and other
    runtime-only fields are omitted.
    """
    base: dict[str, Any] = {"name": td.name, "kind": td.kind}
    if td.help:
        base["help"] = td.help
    if td.kind == "enum":
        base["values"] = list(td.values)
    elif td.kind in ("int_range", "float_range"):
        base["min"] = td.min
        base["max"] = td.max
    elif td.kind == "str_length":
        if td.min_len is not None:
            base["min_len"] = td.min_len
        if td.max_len is not None:
            base["max_len"] = td.max_len
    elif td.kind == "pattern":
        base["regex"] = td.regex
    elif td.kind == "format_spec":
        base["spec"] = td.spec
    if td.build_error:
        base["build_error"] = td.build_error
    return base
