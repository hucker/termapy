"""Device profile: declarative description of a serial device.

A profile describes how a device speaks: its transport rules (baud,
line endings, prompt, echo), its command catalog (with typed args and
response shapes), its error patterns, and version metadata.

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


@dataclass
class ValidationResult:
    """Outcome of schema validation against the profile schema."""

    ok: bool
    errors: list[str] = field(default_factory=list)


# ── Transport-apply: profile.transport -> live config ─────────────────────


# Mapping from profile transport keys to termapy cfg keys.  Most are 1:1;
# `line_ending_send` is the special case (the cfg name predates v2).
_TRANSPORT_KEY_MAP: dict[str, str] = {
    "baud_rate": "baud_rate",
    "byte_size": "byte_size",
    "parity": "parity",
    "stop_bits": "stop_bits",
    "flow_control": "flow_control",
    "encoding": "encoding",
    "inter_command_delay_ms": "inter_command_delay_ms",
    "default_response_timeout_ms": "default_response_timeout_ms",
    "line_ending_send": "line_ending",
}

# Serial-level params: changes to these only take effect on the next
# ``engine.connect()``.  Bridge code that sees changes to any of these
# while connected can warn the user.
SERIAL_LEVEL_TRANSPORT_KEYS: frozenset[str] = frozenset(
    {"baud_rate", "byte_size", "parity", "stop_bits", "flow_control"}
)


def apply_profile_transport(
    transport: dict[str, Any],
    apply_cfg: Any,
) -> dict[str, tuple[Any, Any]]:
    """Apply a profile's ``transport`` block to the live config.

    Walks the recognized fields in ``transport`` and calls ``apply_cfg``
    for each change.  ``apply_cfg`` is the engine's per-key updater
    (see ``ReplEngine._apply_cfg``).  Returns a dict of changes
    ``{cfg_key: (old, new)}`` so callers can warn about serial-level
    params that need a reconnect.

    Termapy-level params (``line_ending``, ``encoding``,
    ``inter_command_delay_ms``) take effect immediately.  Serial-level
    params (``baud_rate``, ``byte_size``, ``parity``, ``stop_bits``,
    ``flow_control``) are applied to ``cfg`` but only consumed by the
    next ``engine.connect()`` -- pyserial doesn't safely allow hot-
    swapping these on an open port.

    NDJSON-only fields (``protocol``, ``field_routing``) are NOT
    written to ``cfg`` -- the bridge reads them from the active
    profile namespace directly.

    Args:
        transport: The ``transport`` block from a loaded profile dict.
        apply_cfg: Callable matching ``ReplEngine._apply_cfg`` signature
            ``(key: str, value: Any) -> None``.  Plugin handlers use
            ``ctx.engine.apply_cfg``.

    Returns:
        ``{cfg_key: (old_value, new_value)}`` for every key that changed.
    """
    if not isinstance(transport, dict):
        return {}
    changes: dict[str, tuple[Any, Any]] = {}
    # apply_cfg's caller side maintains the cfg dict; we don't have it
    # here, so we don't dedup against the current value.  A no-op
    # apply_cfg is a cheap session log line in the worst case.
    for tkey, ckey in _TRANSPORT_KEY_MAP.items():
        if tkey in transport:
            new_val = transport[tkey]
            apply_cfg(ckey, new_val)
            changes[ckey] = (None, new_val)
    return changes


def validate_profile(profile: dict) -> ValidationResult:
    """Validate a profile dict against ``profile.schema.json``.

    Uses ``jsonschema`` if installed; otherwise applies a small built-in
    structural check that covers the highest-leverage rules (required
    fields, enum constraints).  The fallback is intentionally narrower
    than full schema validation -- if a user wants strict checks they
    can ``pip install jsonschema``.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return _builtin_validate(profile)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(profile), key=lambda e: list(e.absolute_path))
    if not errors:
        return ValidationResult(ok=True)
    msgs = []
    for e in errors:
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        msgs.append(f"{loc}: {e.message}")
    return ValidationResult(ok=False, errors=msgs)


def _builtin_validate(profile: dict) -> ValidationResult:
    """Minimal structural validator for when jsonschema isn't installed.

    Catches the high-leverage mistakes: bad enum values, missing
    required fields, wrong top-level types.  Not a full replacement for
    jsonschema -- callers who want strict checks should install it.
    """
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ValidationResult(ok=False, errors=["root: profile must be an object"])

    pv = profile.get("profile_version")
    if pv is not None and pv not in (1, 2):
        errors.append(f"profile_version: expected 1 or 2, got {pv!r}")

    transport = profile.get("transport")
    if transport is not None:
        if not isinstance(transport, dict):
            errors.append("transport: must be an object")
        else:
            proto = transport.get("protocol")
            if proto is not None and proto not in ("text", "ndjson"):
                errors.append(
                    f"transport/protocol: expected 'text' or 'ndjson', got {proto!r}"
                )
            parity = transport.get("parity")
            if parity is not None and parity not in ("N", "E", "O", "M", "S"):
                errors.append(f"transport/parity: invalid value {parity!r}")
            fc = transport.get("flow_control")
            if fc is not None and fc not in ("none", "rtscts", "xonxoff", "manual"):
                errors.append(f"transport/flow_control: invalid value {fc!r}")

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
                safety = entry.get("safety")
                if safety is not None and safety not in (
                    "safe",
                    "readonly",
                    "mutable",
                    "destructive",
                ):
                    errors.append(
                        f"commands/{name}/safety: invalid value {safety!r}"
                    )
                response = entry.get("response")
                if response is not None and isinstance(response, dict):
                    fmt = response.get("format")
                    if fmt is not None and fmt not in (
                        "none",
                        "literal",
                        "lines",
                        "regex",
                        "json",
                    ):
                        errors.append(
                            f"commands/{name}/response/format: "
                            f"invalid value {fmt!r}"
                        )

    return ValidationResult(ok=not errors, errors=errors)


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
    def transport(self) -> dict[str, Any]:
        """Return the transport block, or empty if absent."""
        t = self.data.get("transport")
        return t if isinstance(t, dict) else {}

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
