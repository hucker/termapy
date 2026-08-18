"""Device profile: declarative description of a serial device.

A profile describes a device's command catalog: typed args, help text,
request/response patterns, error patterns, and version metadata.

Wire-level settings (baud rate, byte size, parity, line endings,
encoding, NDJSON field routing, ...) DO NOT live in the profile --
they live in the user's cfg file.  The cfg points to the profile via
``profile_path``; the profile does not point back.  Rationale: a
profile is the device's contract (commands + help); the cfg is the
user's session (how their hardware is wired).  Conflating the two
forced silent cfg-overwrite behavior on profile load.

The profile is the *spec*; termapy's MCP bridge is the *reference
implementation*.  Other tools can consume the same schema.

**Format handling:**

- **JSON is canonical.** Schema validation, cache files, wire format
  (when devices emit profiles via ``/include cmd=help``), reference
  examples -- all JSON.
- **TOML is accepted on load** as an author convenience (raw regex
  strings, comments, multi-line help text are easier in TOML).
- **Save always writes JSON.**  No format preservation; a ``.toml``
  source that goes through a save cycle becomes ``.json``.

The ``Profile`` dataclass is just a wrapper around the parsed dict
with convenience accessors and the precedence comparator.  Internal
representation is the dict, not the dataclass -- consumers can work
with either shape.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Schema location, exported so callers (validators, REPL plugins,
# external tools) can load it without hardcoding the path.
SCHEMA_PATH = Path(__file__).parent / "schema.json"


# ── Loading and saving ──────────────────────────────────────────────────────


def load_profile(path: str | Path) -> dict:
    """Load a profile from disk.  Accepts ``.json`` or ``.toml``.

    Sniffs by extension; for unknown extensions, tries TOML then JSON.
    Returns a dict in the schema's shape.

    Raises:
        FileNotFoundError: ``path`` doesn't exist.
        ValueError: file is neither valid JSON nor valid TOML.
    """
    p = Path(path)
    raw = p.read_bytes()
    suffix = p.suffix.lower()
    if suffix == ".toml":
        return tomllib.loads(raw.decode("utf-8"))
    if suffix == ".json":
        return json.loads(raw)
    # Unknown extension: try TOML first (common for hand-authored), fall
    # back to JSON.  If both fail, raise with both errors so the user
    # can see what went wrong.
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as toml_err:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as json_err:
            raise ValueError(
                f"Profile is neither valid JSON nor TOML "
                f"(json: {json_err}; toml: {toml_err})"
            ) from None


def save_profile(profile: dict, path: str | Path, *, indent: int = 2) -> None:
    """Write a profile to disk as JSON.  Always JSON, regardless of extension."""
    Path(path).write_text(json.dumps(profile, indent=indent), encoding="utf-8")


# ── Validation ──────────────────────────────────────────────────────────────

# Canonical value vocabularies.  These are deliberately NOT enforced as
# hard schema enums: the compatibility policy (see schema description and
# docs/profile-spec.md) is that unrecognized values degrade with defined
# semantics instead of failing the load, so a profile written for a newer
# minor spec revision still works on an older host.  The lint layer
# (validation warnings) flags non-canonical values for authors.
RESPONSE_FORMATS: tuple[str, ...] = (
    "none", "text", "literal", "lines", "regex", "json",
)
SAFETY_TIERS: tuple[str, ...] = ("safe", "readonly", "mutable", "destructive")
COERCION_NAMES: tuple[str, ...] = ("int", "float", "bool", "hex", "str")


@dataclass
class ValidationResult:
    """Outcome of schema validation against the profile schema.

    ``errors`` block the load; ``warnings`` never do.  Warnings carry
    the forward-compat lint findings: unknown fields (tolerated per the
    compatibility policy) and non-canonical values that will degrade at
    dispatch (unrecognized format/safety/kind/coercion names).
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_profile(profile: dict) -> ValidationResult:
    """Validate a profile dict against ``profile.schema.json``.

    Uses ``jsonschema`` if installed; otherwise applies a small built-in
    structural check that covers the highest-leverage rules (required
    fields, wrong shapes).  The fallback is intentionally narrower than
    full schema validation -- if a user wants strict checks they can
    ``pip install jsonschema``.

    Either way, the result also carries lint ``warnings`` from
    :func:`collect_warnings` -- unknown fields and non-canonical values
    are tolerated on load (forward compatibility) but surfaced so
    authors catch typos.
    """
    # Catch the most-cited migration failure before jsonschema's
    # "not.required" produces an unreadable error message.  Users
    # carrying pre-v18 profiles with a transport block should get a
    # one-line pointer at the cfg replacement, not a schema dump.
    if isinstance(profile, dict) and "transport" in profile:
        return ValidationResult(
            ok=False,
            errors=[
                "transport: block is no longer supported; move wire-level "
                "settings to your cfg file (baud_rate, line_ending, encoding, "
                "etc.).  NDJSON support: set cfg `protocol: \"ndjson\"`."
            ],
        )
    try:
        import jsonschema
    except ImportError:
        result = _builtin_validate(profile)
    else:
        result = _jsonschema_validate(profile, jsonschema)
    if isinstance(profile, dict):
        result.warnings.extend(collect_warnings(profile))
    return result


def _jsonschema_validate(profile: dict, jsonschema: Any) -> ValidationResult:
    """Full schema validation via the optional ``jsonschema`` package."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(profile), key=lambda e: list(e.absolute_path))
    if not errors:
        return ValidationResult(ok=True)
    msgs = []
    for error in errors:
        loc = "/".join(str(p) for p in error.absolute_path) or "<root>"
        msgs.append(f"{loc}: {error.message}")
    return ValidationResult(ok=False, errors=msgs)


def _builtin_validate(profile: dict) -> ValidationResult:
    """Minimal structural validator for when jsonschema isn't installed.

    Catches the high-leverage structural mistakes: missing required
    fields, wrong shapes, unsupported profile_version.  Not a full
    replacement for jsonschema -- callers who want strict checks should
    install it.  Vocabulary checks (safety tiers, response formats) are
    NOT errors here: per the compatibility policy those values degrade
    with defined semantics, so they surface as warnings via
    :func:`collect_warnings` instead.
    """
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ValidationResult(ok=False, errors=["root: profile must be an object"])

    pv = profile.get("profile_version")
    if pv is not None and pv != 2:
        errors.append(f"profile_version: expected 2, got {pv!r}")

    # The transport block was retired: wire-level settings live in the
    # cfg, not the profile.  Reject so old hand-rolled profiles get a
    # clear error instead of silent ignore.
    if "transport" in profile:
        errors.append(
            "transport: block is no longer supported; move wire-level "
            "settings to your cfg file (baud_rate, line_ending, encoding, "
            "etc.).  NDJSON support: set cfg `protocol: \"ndjson\"`."
        )

    commands = profile.get("commands")
    if commands is not None:
        if not isinstance(commands, dict):
            errors.append("commands: must be an object")
        else:
            for name, entry in commands.items():
                if not isinstance(entry, dict):
                    errors.append(f"commands/{name}: must be an object")
                    continue
                if "help" not in entry:
                    errors.append(f"commands/{name}: missing required 'help'")
                enabled = entry.get("enabled")
                if enabled is not None and not isinstance(enabled, bool):
                    errors.append(
                        f"commands/{name}/enabled: must be a boolean, got {type(enabled).__name__}"
                    )

    return ValidationResult(ok=not errors, errors=errors)


# ── Forward-compat lint warnings ────────────────────────────────────────────

_known_keys_cache: dict[str, frozenset[str]] | None = None


def _known_keys() -> dict[str, frozenset[str]]:
    """Per-object-level known property names, extracted from the schema.

    The schema is the single source of truth for field names; deriving
    the sets here keeps the lint walker from drifting when the schema
    gains fields.  Returns empty sets (=> no unknown-key warnings) if
    the schema file is unreadable -- lint must never block a load.
    """
    global _known_keys_cache
    if _known_keys_cache is not None:
        return _known_keys_cache
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        props = schema.get("properties", {})
        defs = schema.get("$defs", {})

        def _props_of(node: dict) -> frozenset[str]:
            return frozenset(node.get("properties", {}).keys())

        _known_keys_cache = {
            "root": frozenset(props.keys()),
            "device": _props_of(props.get("device", {})),
            "error_detection": _props_of(props.get("error_detection", {})),
            "command": _props_of(defs.get("command", {})),
            "typed_arg": _props_of(defs.get("typed_arg", {})),
            "type_def": _props_of(defs.get("type_def", {})),
            "response": _props_of(defs.get("response", {})),
        }
    except (OSError, ValueError):
        _known_keys_cache = {}
    return _known_keys_cache


def _is_extension_key(key: Any) -> bool:
    """True for author-blessed extension keys that never warn.

    ``$``-prefixed keys serve tooling conventions (``$schema``);
    ``x_``/``x-`` prefixes are the documented extension namespace.
    """
    return isinstance(key, str) and (
        key.startswith("$") or key.startswith("x_") or key.startswith("x-")
    )


def _suggest(name: str, candidates: Any) -> str:
    """Return ``; did you mean 'x'?`` for the closest candidate, or ``""``.

    Spelling errors are the main way a tolerated-unknown slips past an
    author (a misspelled ``safety`` key silently un-gates a command),
    so every warning about an unknown name carries the nearest
    canonical one when the edit distance is plausibly a typo.
    """
    import difflib

    matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
    return f"; did you mean {matches[0]!r}?" if matches else ""


def _warn_unknown(obj: Any, level: str, loc: str, out: list[str]) -> None:
    """Append a warning per unknown key in ``obj`` at schema ``level``."""
    known = _known_keys().get(level)
    if not known or not isinstance(obj, dict):
        return
    for k in obj:
        if k not in known and not _is_extension_key(k):
            out.append(
                f"{loc}: unknown field {k!r} "
                f"(ignored by this host{_suggest(k, known)})"
            )


def _warn_coercions(types_map: Any, loc: str, out: list[str]) -> None:
    """Warn per non-canonical coercion name; such values degrade to str."""
    if not isinstance(types_map, dict):
        return
    for group, coercion in types_map.items():
        if isinstance(coercion, str) and coercion.lower() not in COERCION_NAMES:
            out.append(
                f"{loc}/{group}: unrecognized coercion {coercion!r} "
                f"(value will stay a raw string"
                f"{_suggest(coercion, COERCION_NAMES)})"
            )


def _warn_command(name: str, spec: Any, loc: str, out: list[str]) -> None:
    """Lint one command entry (and nested subcommands, recursively)."""
    if not isinstance(spec, dict):
        return
    _warn_unknown(spec, "command", loc, out)
    safety = spec.get("safety")
    if isinstance(safety, str) and safety not in SAFETY_TIERS:
        out.append(
            f"{loc}/safety: unrecognized tier {safety!r} "
            f"(treated as destructive: confirmation required"
            f"{_suggest(safety, SAFETY_TIERS)})"
        )
    for i, ta in enumerate(spec.get("typed_args") or []):
        _warn_unknown(ta, "typed_arg", f"{loc}/typed_args/{i}", out)
    response = spec.get("response")
    if isinstance(response, dict):
        rloc = f"{loc}/response"
        _warn_unknown(response, "response", rloc, out)
        fmt = response.get("format")
        if isinstance(fmt, str) and fmt not in RESPONSE_FORMATS:
            out.append(
                f"{rloc}/format: unrecognized format {fmt!r} "
                f"(treated as 'text': raw response string"
                f"{_suggest(fmt, RESPONSE_FORMATS)})"
            )
        _warn_coercions(response.get("types"), f"{rloc}/types", out)
        _warn_coercions(response.get("line_types"), f"{rloc}/line_types", out)
    subs = spec.get("subcommands")
    if isinstance(subs, dict):
        for sub_name, sub_spec in subs.items():
            _warn_command(
                sub_name, sub_spec, f"{loc}/subcommands/{sub_name}", out
            )


def collect_warnings(profile: dict) -> list[str]:
    """Forward-compat lint pass: unknown fields + non-canonical values.

    Everything reported here is TOLERATED at load time -- the warnings
    exist so authors catch typos and know which degrade rule applies.
    Extension keys (``$schema``, ``x_*``, ``x-*``) never warn.
    """
    out: list[str] = []
    if not isinstance(profile, dict):
        return out
    _warn_unknown(profile, "root", "<root>", out)
    _warn_unknown(profile.get("device"), "device", "device", out)
    err = profile.get("error_detection")
    if isinstance(err, dict):
        _warn_unknown(err, "error_detection", "error_detection", out)
        _warn_coercions(err.get("types"), "error_detection/types", out)
    types_block = profile.get("types")
    if isinstance(types_block, dict):
        from termapy.profile.types import schema_kinds

        kinds = schema_kinds()
        for tname, tdef in types_block.items():
            tloc = f"types/{tname}"
            _warn_unknown(tdef, "type_def", tloc, out)
            kind = tdef.get("kind") if isinstance(tdef, dict) else None
            if isinstance(kind, str) and kind not in kinds:
                out.append(
                    f"{tloc}/kind: unrecognized kind {kind!r} (profile loads; "
                    f"args of this type refuse at dispatch"
                    f"{_suggest(kind, kinds)})"
                )
    commands = profile.get("commands")
    if isinstance(commands, dict):
        for name, spec in commands.items():
            _warn_command(name, spec, f"commands/{name}", out)
    return out


# ── Precedence ──────────────────────────────────────────────────────────────


def _parse_revision(rev: str | None) -> tuple[int, int, int]:
    """Parse a semver-ish ``profile_revision`` into a comparable tuple.

    Missing or unparseable revisions sort as ``(0, 0, 0)`` -- they lose
    to any versioned candidate (per the precedence rules).
    """
    if not rev or not isinstance(rev, str):
        return (0, 0, 0)
    parts = rev.split("-", 1)[0].split(".")
    out: list[int] = []
    for p in parts[:3]:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return (out[0], out[1], out[2])


def _parse_date(date_str: str | None) -> str:
    """Normalize ``profile_date`` for ISO-string lexicographic compare.

    Missing dates sort as empty string -- they lose to any dated
    candidate.  We don't parse to datetime; lexicographic on ``YYYY-MM-DD``
    is identical to chronological for valid ISO dates.
    """
    if not date_str or not isinstance(date_str, str):
        return ""
    return date_str


def precedence(
    a: dict, b: dict, *, a_source: str = "", b_source: str = ""
) -> int:
    """Compare two profiles for cache-first lookup.

    Returns:
        -1 if a < b (b wins), 0 if equal, 1 if a > b (a wins).

    Rules (in order):
        1. Higher ``profile_revision`` wins (semver compare, 0.0.0 if missing).
        2. Newer ``profile_date`` wins (ISO compare, empty if missing).
        3. If revs and dates equal, ``b_source == "device"`` wins -- the
           hand-authored side hasn't bumped, so the device's view is
           more authoritative.

    Args:
        a, b: Profile dicts.
        a_source, b_source: Optional source hints.  Use ``"device"`` for
            device-fetched, ``"hand"`` (or anything non-device) for
            hand-authored.  Only used in tie-breaking.
    """
    a_rev = _parse_revision(a.get("profile_revision"))
    b_rev = _parse_revision(b.get("profile_revision"))
    if a_rev > b_rev:
        return 1
    if a_rev < b_rev:
        return -1
    a_date = _parse_date(a.get("profile_date"))
    b_date = _parse_date(b.get("profile_date"))
    if a_date > b_date:
        return 1
    if a_date < b_date:
        return -1
    # Tiebreaker: device-fetched wins when the user hasn't bumped.
    if a_source == "device" and b_source != "device":
        return 1
    if b_source == "device" and a_source != "device":
        return -1
    return 0


# ── Convenience wrapper ─────────────────────────────────────────────────────


@dataclass
class Profile:
    """Convenience wrapper around a profile dict.

    Most code should work with the dict directly (it's the schema's
    shape).  This wrapper exists for callers that want typed accessors
    or attached path/source metadata.
    """

    data: dict
    path: Path | None = None
    source: str = ""  # "hand", "device", "bundled", or whatever the caller wants

    @classmethod
    def load(cls, path: str | Path, source: str = "hand") -> Profile:
        """Load a profile and wrap it.  See :func:`load_profile`."""
        p = Path(path)
        return cls(data=load_profile(p), path=p, source=source)

    def save(self, path: str | Path | None = None) -> None:
        """Save as JSON.  Uses ``self.path`` if no path given."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("No path provided and no original path on profile")
        save_profile(self.data, target)

    def validate(self) -> ValidationResult:
        """Schema-validate this profile."""
        return validate_profile(self.data)

    @property
    def revision(self) -> str:
        """Return ``profile_revision``, or empty string if absent."""
        return str(self.data.get("profile_revision") or "")

    @property
    def date(self) -> str:
        """Return ``profile_date``, or empty string if absent."""
        return str(self.data.get("profile_date") or "")

    @property
    def commands(self) -> dict[str, Any]:
        """Return the commands dict, or empty if absent."""
        c = self.data.get("commands")
        return c if isinstance(c, dict) else {}

    @property
    def device(self) -> dict[str, Any]:
        """Return the device block, or empty if absent."""
        d = self.data.get("device")
        return d if isinstance(d, dict) else {}

    @property
    def error_detection(self) -> dict[str, Any]:
        """Return the error_detection block, or empty if absent."""
        e = self.data.get("error_detection")
        return e if isinstance(e, dict) else {}
