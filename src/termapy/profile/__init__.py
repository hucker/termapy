"""Device-profile schema, loader, validator, and type registry.

This subpackage is the **standalone, reusable kernel** for the v2
device-profile contract.  It has no termapy-engine, MCP, Textual, or
pyserial dependencies and can be imported as a library by any program
that wants to:

  - Validate a JSON/TOML profile against the canonical schema.
  - Load a profile into a dict (or the convenience ``Profile`` wrapper).
  - Build a ``TypeRegistry`` from the profile and validate one or more
    bound argument values before dispatching the command on the wire.
  - Compare two profile candidates with the ``precedence`` comparator
    (revision wins, then date, then a "device-fetched beats hand-
    authored" tiebreaker).

Public API (re-exported for one-import convenience)::

    from termapy.profile import (
        # schema + load/save/validate
        SCHEMA_PATH, Profile, ValidationResult,
        load_profile, save_profile, validate_profile, precedence,
        apply_profile_transport,

        # user-defined types + validators
        BUILTINS, TypeDef, ValidationOutcome, TypeRegistry,
        ProfileTypeError, schema_kinds, typedef_to_catalog,
    )

Typical third-party use -- dispatching a command from a device::

    from termapy.profile import load_profile, validate_profile, TypeRegistry

    profile = load_profile("acme_modem.profile.json")
    vr = validate_profile(profile)
    if not vr.ok:
        raise SystemExit(vr.errors)

    registry = TypeRegistry.from_profile(profile)
    cmd_spec = profile["commands"]["SET_BAUD"]
    for ta in cmd_spec.get("typed_args", []):
        outcome = registry.validate(ta["type"], user_args[ta["name"]])
        if not outcome.ok:
            raise ValueError(outcome.error)
        user_args[ta["name"]] = outcome.value  # normalized

    serial_write(cmd_spec["send_template"].format(**user_args))

Files in this package:

  - ``schema.json``  -- the canonical JSON Schema (Draft 2020-12).
  - ``loader.py``    -- load/save/validate/precedence + ``Profile``
                        dataclass.
  - ``types.py``     -- user-defined ``TypeRegistry`` (enum / int_range /
                        float_range / str_length / pattern / format_spec
                        kinds) plus builtin int/float/bool/hex/str.

Dependencies outside the package boundary (purely local imports, no
SDK / I/O / TUI surface):

  - ``termapy.response_parsers`` -- the ``_coerce`` helper for builtin
    type coercion (lifted here unchanged so third-party callers don't
    need to vendor it themselves).
  - ``termapy.protocol`` -- lazy import in ``types.py`` for the
    ``format_spec`` kind; only triggered if a profile actually uses
    that kind.  Library users who don't need binary-field types pay
    nothing for this.
"""

from __future__ import annotations

from termapy.profile.loader import (
    Profile,
    SCHEMA_PATH,
    SERIAL_LEVEL_TRANSPORT_KEYS,
    ValidationResult,
    apply_profile_transport,
    load_profile,
    precedence,
    save_profile,
    validate_profile,
)
from termapy.profile.matcher import (
    match_profile_command,
    template_to_regex,
)
from termapy.profile.types import (
    BUILTINS,
    ProfileTypeError,
    TypeDef,
    TypeRegistry,
    ValidationOutcome,
    schema_kinds,
    typedef_to_catalog,
)

__all__ = [
    # loader
    "Profile",
    "SCHEMA_PATH",
    "SERIAL_LEVEL_TRANSPORT_KEYS",
    "ValidationResult",
    "apply_profile_transport",
    "load_profile",
    "precedence",
    "save_profile",
    "validate_profile",
    # matcher
    "match_profile_command",
    "template_to_regex",
    # types
    "BUILTINS",
    "ProfileTypeError",
    "TypeDef",
    "TypeRegistry",
    "ValidationOutcome",
    "schema_kinds",
    "typedef_to_catalog",
]
