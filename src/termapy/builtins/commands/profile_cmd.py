"""Built-in plugin: /profile.* commands.

Subcommands:

- ``/profile.validate <path>`` -- schema-check a profile file (Phase 0).
- ``/profile.load <path>`` -- load a profile, set ``active_profile``
  namespace, optionally apply transport rules to the live session.
- ``/profile.info`` -- show metadata of the active profile.

Active-profile state lives in ``ctx.ns("active_profile")`` so multiple
consumers (the MCP catalog, /profile.info, future /mcp.* commands)
read from the same source.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command, format_kv_lines
from termapy.profile import (
    SERIAL_LEVEL_TRANSPORT_KEYS,
    apply_profile_transport,
    load_profile,
    validate_profile,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


_ACTIVE_PROFILE_NS = "active_profile"


def _handler_validate(ctx: PluginContext, args: str) -> CmdResult:
    """Validate a profile file against the schema and report errors."""
    path_str = args.strip()
    if not path_str:
        return CmdResult.fail(msg="Usage: /profile.validate <path>")
    path = Path(path_str)
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
    ctx.io._write(f"  Profile has {len(result.errors)} error(s):", "red")
    for err in result.errors:
        ctx.io._write(f"    - {err}", "yellow")
    return CmdResult.fail(msg=f"{len(result.errors)} validation error(s)")


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Load a profile from disk and make it the active profile.

    Validates first; refuses to load on schema errors so
    ``/profile.info`` doesn't end up with malformed data.

    Stores the profile dict in ``ctx.ns("active_profile")`` -- a
    session-scoped namespace.  Subsequent reads (catalog,
    ``/profile.info``, future MCP transport-apply) see the same data.

    Phase 4 doesn't yet apply transport rules to the live session
    (baud reconnect, line-ending swap).  That's a Phase 6 lifecycle
    addition; profile_revision/profile_date/device/transport blocks
    are *recorded* now, *applied* later.
    """
    path_str = args.strip()
    if not path_str:
        return CmdResult.fail(msg="Usage: /profile.load <path>")
    path = Path(path_str)
    if not path.exists():
        return CmdResult.fail(msg=f"Profile not found: {path}")
    try:
        profile = load_profile(path)
    except (OSError, ValueError) as e:
        return CmdResult.fail(msg=f"Parse error: {e}")
    if not isinstance(profile, dict):
        return CmdResult.fail(msg="Profile must be a JSON/TOML object")

    result = validate_profile(profile)
    if not result.ok:
        ctx.io._write(
            f"  Profile has {len(result.errors)} schema error(s); refusing to load:",
            "red",
        )
        for err in result.errors:
            ctx.io._write(f"    - {err}", "yellow")
        return CmdResult.fail(msg=f"{len(result.errors)} validation error(s)")

    ns = ctx.ns(_ACTIVE_PROFILE_NS)
    ns.clear()
    ns.update(profile)
    # Record the source path so /profile.info shows where it came from.
    ns["__source_path"] = str(path.resolve())

    # Apply transport rules to the live cfg (Phase 6).  Serial-level
    # params (baud, parity, ...) are applied but only take effect on
    # the next connect() -- pyserial doesn't hot-swap these safely.
    transport = profile.get("transport") or {}
    changes = apply_profile_transport(transport, ctx.engine.apply_cfg)
    serial_changed = [k for k in changes if k in SERIAL_LEVEL_TRANSPORT_KEYS]
    if serial_changed and ctx.serial.is_connected():
        ctx.io._write(
            "  note: serial-level params changed ("
            + ", ".join(serial_changed)
            + ") -- reconnect to take effect.",
            "yellow",
        )

    n = len(profile.get("commands", {}))
    rev = profile.get("profile_revision") or "(none)"
    ctx.io.result(f"Loaded {path.name}: {n} commands (rev {rev}).", "green")
    return CmdResult.ok(value=str(n))


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show metadata of the active profile."""
    ns = ctx.ns(_ACTIVE_PROFILE_NS)
    if not ns:
        ctx.io.result("No profile loaded.  /profile.load <path> to load one.", "yellow")
        return CmdResult.ok(value="")

    rows = [
        ("path", ns.get("__source_path", "(in-memory)")),
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
    transport = ns.get("transport") or {}
    if isinstance(transport, dict) and transport:
        if transport.get("protocol"):
            rows.append(("transport.protocol", transport["protocol"]))
        if transport.get("baud_rate"):
            rows.append(("transport.baud_rate", str(transport["baud_rate"])))
        if transport.get("line_ending_send"):
            rows.append(("transport.line_ending_send", repr(transport["line_ending_send"])))
        if transport.get("echo") is not None:
            rows.append(("transport.echo", str(transport["echo"])))
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
        "A device profile declaratively describes how a serial device "
        "speaks: transport rules (baud, line endings, prompt, echo), "
        "command catalog (typed args, response shapes), error patterns. "
        "Profiles are the input the MCP server consumes to bridge LLMs "
        "to serial devices.  See docs/profile-v2-spec.md for the spec."
    ),
    handler=None,
    sub_commands={
        "validate": Command(
            args="<path>",
            help="Validate an MCP device profile (.json or .toml).",
            long_help=(
                "Schema-validate a profile against profile.schema.json. "
                "Prints OK + command count on success, or line-numbered "
                "errors on failure.  Equivalent to 'termapy "
                "--validate-profile <path>' from the shell."
            ),
            handler=_handler_validate,
        ),
        "load": Command(
            args="<path>",
            help="Load a device profile and set the active_profile namespace.",
            long_help=(
                "Validate then load.  The profile dict is stored in "
                "ctx.ns('active_profile') so subsequent /profile.info, "
                "/mcp.catalog, and (Phase 6) transport-apply hooks read "
                "the same source.  Refuses to load on schema errors."
            ),
            handler=_handler_load,
        ),
        "info": Command(
            help="Show metadata of the active profile.",
            handler=_handler_info,
        ),
    },
)
