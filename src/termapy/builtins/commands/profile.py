"""Built-in plugin: /profile.* commands.

Subcommands:

- ``/profile.validate <path>`` -- schema-check a profile file.
- ``/profile.load <path>`` -- load a profile from a file.
- ``/profile.load cmd=<command>`` -- fetch a profile from the connected
  device and install it as the active profile.
- ``/profile.load`` (no args) -- reload the current source (file or cmd).
- ``/profile.save`` -- write the active profile to a file (defaults to
  ``<cfg_dir>/<cfg_name>.profile.json`` so the next connect auto-loads it).
- ``/profile.save <path>`` -- save to an explicit path.
- ``/profile.unload`` -- clear the active profile.
- ``/profile.info`` -- show metadata of the active profile.

Active-profile state lives in ``ctx.ns("active_profile")`` so multiple
consumers (the MCP catalog, /profile.info, future /mcp.* commands)
read from the same source.  Two internal fields on the namespace
track where the profile came from for the no-args reload:

- ``__source_path`` (str) -- absolute path of the loaded profile file.
- ``__source_cmd`` (str) -- device command used to fetch the profile.

Exactly one of those two is set at a time; ``/profile.unload`` and
``/profile.load`` clear both before populating one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command, UsageError, format_kv_lines
from termapy.plugins.params import ParamSpec
from termapy.profile import (
    load_profile,
    save_profile,
    validate_profile,
)
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


_ACTIVE_PROFILE_NS = "active_profile"

# Default timeout for a device-fetch profile load.  Long enough for a
# multi-KB JSON dump at 9600 baud on a slow device; short enough that
# a wrong cmd= name fails the call without an obviously stuck terminal.
_DEFAULT_FETCH_TIMEOUT_MS = 3000


# ── shared helpers ─────────────────────────────────────────────────────────


def _apply_profile(
    ctx: PluginContext,
    profile: dict,
    *,
    source_path: str = "",
    source_cmd: str = "",
    label: str,
) -> CmdResult:
    """Validate ``profile`` and install it in the ``active_profile`` namespace.

    Single code path used by load-from-file and load-from-device so the
    behavior after a successful parse is identical regardless of source.
    Wire-level settings live in cfg; the profile only declares the
    device's command catalog.
    """
    if not isinstance(profile, dict):
        return CmdResult.fail(msg="Profile must be a JSON/TOML object")

    result = validate_profile(profile)
    if not result.ok:
        ctx.io.output(
            f"  Profile has {len(result.errors)} schema error(s); "
            f"refusing to load:",
            "red",
        )
        for err in result.errors:
            ctx.io.output(f"    - {err}", "yellow")
        return CmdResult.fail(msg=f"{len(result.errors)} validation error(s)")

    ns = ctx.ns(_ACTIVE_PROFILE_NS)
    ns.clear()
    ns.update(profile)
    # Track which source path or cmd produced the active profile so a
    # bare /profile.load can reload from the same place.  Exactly one
    # is set at a time.
    if source_path:
        ns["__source_path"] = source_path
    if source_cmd:
        ns["__source_cmd"] = source_cmd

    n = len(profile.get("commands", {}))
    rev = profile.get("profile_revision") or "(none)"
    ctx.io.result(f"Loaded {label}: {n} commands (rev {rev}).", "green")
    return CmdResult.ok(value=str(n))


def _read_profile_json(ctx: PluginContext, timeout_ms: int) -> dict | None:
    """Read serial data and extract the first valid JSON object.

    Accumulates bytes until a json.loads() at the first ``{`` succeeds,
    or the timeout expires.  Same algorithm /include used; lifted here
    so commit C can delete include.py.
    """
    buf = b""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        chunk = ctx.serial.read_raw(timeout_ms=remaining_ms)
        if not chunk:
            break
        buf += chunk
        text = buf.decode(ctx.cfg.get("encoding", "utf-8"), errors="replace")
        start = text.find("{")
        if start < 0:
            continue
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue
    return None


def _default_save_path(ctx: PluginContext) -> Path | None:
    """Return ``<cfg_dir>/<cfg_name>.profile.json`` or None if no cfg.

    Uses the config path attached to the plugin context so the default
    co-locates the saved profile with the cfg that drove the session.
    """
    cfg_path = getattr(ctx, "config_path", "") or ""
    if not cfg_path:
        return None
    cfg_file = Path(cfg_path)
    cfg_dir = cfg_file.parent
    cfg_stem = cfg_file.stem
    return cfg_dir / f"{cfg_stem}.profile.json"


# ── handlers ───────────────────────────────────────────────────────────────


def _handler_validate(ctx: PluginContext, args: str) -> CmdResult:
    """Validate a profile file against the schema and report errors."""
    path = Path(ctx.arg("path"))
    if not path.exists():
        return CmdResult.fail(msg=f"Profile not found: {path}")
    try:
        profile = load_profile(path)
    except (OSError, ValueError) as e:
        return CmdResult.fail(msg=f"Parse error: {e}")
    result = validate_profile(profile)
    if result.ok:
        n = len(profile.get("commands", {})) if isinstance(profile, dict) else 0
        ctx.io.result(f"Valid profile.  {n} commands.", "green")
        return CmdResult.ok(value=str(n))
    ctx.io.output(f"  Profile has {len(result.errors)} error(s):", "red")
    for err in result.errors:
        ctx.io.output(f"    - {err}", "yellow")
    return CmdResult.fail(msg=f"{len(result.errors)} validation error(s)")


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Three-way dispatch on the args shape:

      /profile.load                     -> reload current source
      /profile.load <path>              -> load from file
      /profile.load cmd=<command>       -> fetch from device

    Optional kw on cmd= form: ``timeout=<duration>`` overrides the
    default 3 s fetch timeout (matches the duration-string format used
    by other timeout cfgs: ``500ms``, ``2s``, etc.).
    """
    args = args.strip()

    # No args -> reload current source (whichever was used last).
    if not args:
        ns = ctx.ns(_ACTIVE_PROFILE_NS)
        src_path = ns.get("__source_path", "")
        src_cmd = ns.get("__source_cmd", "")
        if src_path:
            return _load_from_file(ctx, src_path)
        if src_cmd:
            return _load_from_device(
                ctx, src_cmd, _DEFAULT_FETCH_TIMEOUT_MS,
            )
        return CmdResult.fail(
            msg=(
                "No profile loaded; nothing to reload.  Pass a path "
                "or cmd=<command>."
            ),
        )

    # cmd= form -> device fetch.  Anything containing = needs the
    # keyword parser; a bare path can't contain = legally.
    if "=" in args:
        try:
            kw = parse_keywords(args, {"cmd", "timeout"}, rest_keyword="cmd")
        except ValueError as e:
            return CmdResult.fail(msg=f"Usage error: {e}")
        cmd = (kw.get("cmd") or "").strip()
        if not cmd:
            raise UsageError("cmd= given without a command")
        timeout_ms = _DEFAULT_FETCH_TIMEOUT_MS
        timeout_arg = kw.get("timeout")
        if timeout_arg:
            try:
                timeout_ms = int(parse_duration(timeout_arg) * 1000)
            except ValueError as e:
                return CmdResult.fail(msg=f"Invalid timeout: {e}")
        return _load_from_device(ctx, cmd, timeout_ms)

    # Otherwise treat as a file path.
    return _load_from_file(ctx, args)


def _load_from_file(ctx: PluginContext, path_str: str) -> CmdResult:
    """Load a profile from disk and install it as the active profile."""
    # Reading an arbitrary path is a parse/existence oracle under MCP;
    # contain to the sandbox unless the operator opted out.
    ctx.fs.guard_external_path(path_str, "Profile path")
    path = Path(path_str)
    if not path.exists():
        return CmdResult.fail(msg=f"Profile not found: {path}")
    try:
        profile = load_profile(path)
    except (OSError, ValueError) as e:
        return CmdResult.fail(msg=f"Parse error: {e}")
    return _apply_profile(
        ctx, profile,
        source_path=str(path.resolve()),
        label=path.name,
    )


def _load_from_device(
    ctx: PluginContext, cmd: str, timeout_ms: int,
) -> CmdResult:
    """Send ``cmd`` to the device, read JSON back, install as profile."""
    if not ctx.serial.is_connected():
        return CmdResult.fail(msg="Not connected.")

    with ctx.serial.io():
        ctx.serial.drain()
        ctx.serial.send(cmd)
        data = _read_profile_json(ctx, timeout_ms)

    if data is None:
        return CmdResult.fail(
            msg=(
                f"No valid JSON received from {cmd!r} within "
                f"{timeout_ms / 1000:.1f}s."
            ),
        )
    return _apply_profile(
        ctx, data,
        source_cmd=cmd,
        label=f"device:{cmd}",
    )


def _handler_save(ctx: PluginContext, args: str) -> CmdResult:
    """Write the active profile to a file.

    Default path: ``<cfg_dir>/<cfg_stem>.profile.json`` so the next
    connect auto-loads it via the ``profile_path``/file-existence path.

    Warns if every command in the active profile has ``enabled: false``
    (typical for a freshly-fetched device dump before the engineer has
    audited each entry) -- saving is fine, but it's a useful nudge
    that the audit is pending.
    """
    ns = ctx.ns(_ACTIVE_PROFILE_NS)
    if not ns:
        return CmdResult.fail(msg="No profile loaded.")
    args = args.strip()
    if args:
        # Contain to the config sandbox under MCP; the default path below
        # (co-located with the cfg) is always in-sandbox.
        ctx.fs.guard_external_path(args, "Save path")
        path = Path(args)
    else:
        default = _default_save_path(ctx)
        if default is None:
            raise UsageError(
                "No config loaded; can't derive a default path"
            )
        path = default

    # Strip internal __source_* fields before writing.  They're
    # runtime hints, not part of the profile contract.
    out = {k: v for k, v in ns.items() if not k.startswith("__")}

    try:
        save_profile(out, path)
    except (OSError, ValueError) as e:
        return CmdResult.fail(msg=f"Write error: {e}")

    commands = out.get("commands") or {}
    all_disabled = bool(commands) and all(
        isinstance(spec, dict) and spec.get("enabled") is False
        for spec in commands.values()
    )
    n = len(commands)
    ctx.io.result(f"Wrote {path.name}: {n} commands.", "green")
    if all_disabled:
        ctx.io.output(
            "  note: every command has enabled=false; nothing will "
            "dispatch via the profile until you audit each entry.",
            "yellow",
        )
    return CmdResult.ok(value=str(n))


def _handler_unload(ctx: PluginContext, args: str) -> CmdResult:
    """Clear the active profile, leaving the session unattached."""
    ns = ctx.ns(_ACTIVE_PROFILE_NS)
    if not ns:
        ctx.io.result("No profile loaded.", "yellow")
        return CmdResult.ok(value="0")
    n = len(ns.get("commands") or {})
    ns.clear()
    ctx.io.result(f"Unloaded profile ({n} commands).", "green")
    return CmdResult.ok(value=str(n))


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show metadata of the active profile."""
    ns = ctx.ns(_ACTIVE_PROFILE_NS)
    if not ns:
        ctx.io.result(
            "No profile loaded.  /profile.load <path> to load one.",
            "yellow",
        )
        return CmdResult.ok(value="")

    source_path = ns.get("__source_path", "")
    source_cmd = ns.get("__source_cmd", "")
    source_display: str
    if source_path:
        source_display = source_path
    elif source_cmd:
        source_display = f"device:{source_cmd}"
    else:
        source_display = "(in-memory)"

    rows = [
        ("source", source_display),
        ("profile_version", str(ns.get("profile_version", "(none)"))),
        ("profile_revision", ns.get("profile_revision", "(none)")),
        ("profile_date", ns.get("profile_date", "(none)")),
    ]
    device = ns.get("device") or {}
    if isinstance(device, dict) and device:
        rows.append(("device.name", device.get("name", "")))
        if device.get("vendor"):
            rows.append(("device.vendor", device["vendor"]))
        if device.get("startup_banner"):
            rows.append(("device.startup_banner", device["startup_banner"]))
    cmd_count = len(ns.get("commands") or {})
    rows.append(("commands", str(cmd_count)))

    for line in format_kv_lines(rows):
        ctx.io.output(line)

    return CmdResult.ok(value=str(cmd_count))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="profile",
    help="Device profile commands (MCP profile schema, validator, loader).",
    long_help=(
        "A device profile declaratively describes a serial device's "
        "command catalog: typed args, help text, response shapes, error "
        "patterns.  Wire-level settings (baud, line endings, encoding, "
        "ndjson) live in the cfg file; the profile describes the device "
        "side only.  Profiles are the input the MCP server consumes to "
        "bridge LLMs to serial devices.  See "
        "help/authoring-profiles.md for the spec."
    ),
    handler=None,
    sub_commands={
        "validate": Command(
            help="Validate an MCP device profile (.json or .toml).",
            long_help=(
                "Schema-validate a profile against profile.schema.json. "
                "Prints OK + command count on success, or line-numbered "
                "errors on failure.  Equivalent to 'termapy "
                "--validate-profile <path>' from the shell."
            ),
            handler=_handler_validate,
            params=[
                ParamSpec(
                    "path", "path", positional=True, rest=True, required=True,
                    help="profile file to validate (.json or .toml)",
                ),
            ],
        ),
        # NOT migrated to declarative params, deliberately (see
        # docs/param-spec-implementation.md, Phase 3 note): /profile.load is a
        # three-way shape dispatch (empty=reload / bare-path=file /
        # cmd==device) and its "{path|cmd=<command>}" synopsis expresses a
        # mutual exclusion the synthesized synopsis can't -- params would parse
        # it but document it as independent optionals and shift edge
        # disambiguation.  Hand-rolled parsing stays; this is the escape hatch.
        "load": Command(
            args="{path|cmd=<command>}",
            help=(
                "Load a device profile.  Path = file; cmd=<command> "
                "fetches from the device; no args = reload current source."
            ),
            long_help=(
                "Three forms:\n"
                "\n"
                "  /profile.load <path>            -- load from file\n"
                "  /profile.load cmd=<command>     -- fetch from device\n"
                "  /profile.load                   -- reload current source\n"
                "\n"
                "The cmd= form sends <command> to the connected device, "
                "reads a v2 profile JSON response, and installs it as the "
                "active profile.  Optional 'timeout=<duration>' overrides "
                "the default 3 s fetch timeout (e.g. timeout=500ms, "
                "timeout=10s).  Validates against the schema before "
                "installing; refuses to load on errors.  Transport rules "
                "are applied to the live cfg."
            ),
            handler=_handler_load,
        ),
        "save": Command(
            args="{path}",
            help=(
                "Write the active profile to a file (default: "
                "<cfg_dir>/<cfg_stem>.profile.json)."
            ),
            long_help=(
                "Saves the active profile dict to disk so the next connect "
                "auto-loads it via the profile_path/file-existence path.\n"
                "\n"
                "Default path is <cfg_dir>/<cfg_stem>.profile.json so the "
                "saved file co-locates with the cfg that drove the session "
                "and gets picked up automatically on reconnect.\n"
                "\n"
                "Warns if every command in the profile has enabled=false "
                "(typical for a freshly-fetched device dump before audit) "
                "-- saving is fine, just a nudge that audit is pending."
            ),
            handler=_handler_save,
        ),
        "unload": Command(
            help="Clear the active profile.",
            long_help=(
                "Empties the active_profile namespace.  The session "
                "remains connected; subsequent bare commands fall "
                "through to /term.send (literal-bytes path, no "
                "profile-aware dispatch)."
            ),
            handler=_handler_unload,
        ),
        "info": Command(
            help="Print metadata of the active profile.",
            handler=_handler_info,
        ),
    },
)
