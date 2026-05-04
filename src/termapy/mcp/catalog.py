"""Build JSON catalog/state snapshots from a running PluginContext.

The catalog is the LLM's symbol table: every termapy command, its
help text, its arg spec, its safety/capability flags.  Served as the
``termapy://commands.json`` MCP resource AND printable via
``/mcp.catalog`` (Phase 4) -- the two outputs are byte-identical.

This module has no SDK dependencies.  Pure functions that walk
``ctx.engine.plugins`` (a dict of PluginInfo) and emit serializable
dicts.  Importable in any context (REPL plugin, MCP server, tests).
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import TYPE_CHECKING, Any

from termapy.plugins import CapabilitySet, resolve_long_help

if TYPE_CHECKING:
    from termapy.plugins import PluginContext, PluginInfo

CATALOG_SCHEMA_VERSION: int = 1


# ── Catalog ─────────────────────────────────────────────────────────────────


def build_catalog(ctx: PluginContext) -> dict[str, Any]:
    """Return the catalog dict served by ``termapy://commands.json``.

    Walks every registered plugin and produces a stable, sorted list
    of command descriptors.  Pulls the active device profile blocks
    (transport / device / error_detection / revision / date) when a
    profile is loaded; emits empty placeholders otherwise.

    Args:
        ctx: A live PluginContext.  Must have ``ctx.engine.plugins``
            populated (every running termapy host satisfies this).

    Returns:
        Dict suitable for ``json.dumps(..., indent=2, sort_keys=False)``.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("termapy")
        except PackageNotFoundError:
            ver = "unknown"
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib
        ver = "unknown"

    plugins = ctx.engine.plugins
    # Filter to commands the LLM can actually invoke meaningfully:
    # ``needs`` not satisfied by ``ctx.capabilities`` -- the capability
    # gate would reject these at dispatch.  The MCP host advertises
    # neither ``interactive`` nor ``gui_apps``, so commands that
    # require a human at a terminal (/grep, /cls, /seq, ...) or a
    # local desktop (/edit, /help.open, ...) are filtered out
    # automatically.  TUI-only commands (/term.line_no via tui_mode,
    # /confirm via confirm_dialog) drop out the same way.
    #
    # The ``hidden`` flag is intentionally NOT a filter.  ``hidden``
    # is a UI-discoverability concern (don't list in /help) used for
    # legacy aliases -- it doesn't speak to MCP appropriateness.
    # Hidden plugins still appear in the catalog with ``hidden: true``
    # so MCP clients that want to skip them can do it themselves.
    capabilities = getattr(ctx, "capabilities", None)
    cmd_list: list[dict[str, Any]] = []
    for name in sorted(plugins):
        plugin = plugins[name]
        if capabilities is not None and plugin.needs.missing_from(capabilities):
            continue
        cmd_list.append(_command_descriptor(plugin, ctx))

    target_commands = []
    target_meta = ctx.ns("target_meta")
    target_ns = ctx.ns("target_commands")
    if isinstance(target_ns, dict) and target_ns:
        # Filter out enabled=False entries so disabled commands never
        # appear in the LLM-facing catalog.  Disabled entries still
        # exist in target_commands (visible to /help <cmd> for the
        # human supervisor) but the bot doesn't see them.  Default
        # True keeps existing curated/v2-published manifests visible.
        target_commands = [
            _target_descriptor(target_ns[n])
            for n in sorted(target_ns)
            if getattr(target_ns[n], "enabled", True)
        ]

    # Profile blocks come from a future "active profile" namespace; v1
    # leaves them empty until /profile.load lands in Phase 4.  Schema
    # consumers (Claude, codegen) treat absent or empty as "not set."
    active_profile = ctx.ns("active_profile")
    if not isinstance(active_profile, dict):
        active_profile = {}

    return {
        "schema": CATALOG_SCHEMA_VERSION,
        "version": ver,
        "prefix": ctx.engine.prefix,
        "profile_revision": active_profile.get("profile_revision", ""),
        "profile_date": active_profile.get("profile_date", ""),
        "device": active_profile.get("device", {}),
        "transport": active_profile.get("transport", {}),
        "error_detection": active_profile.get("error_detection", {}),
        "commands": cmd_list,
        "target_commands": target_commands,
        "target_meta": dict(target_meta) if isinstance(target_meta, dict) else {},
    }


def catalog_json(ctx: PluginContext, *, indent: int = 2) -> str:
    """Serialize the catalog to JSON.  Stable formatting (sort_keys=False).

    The MCP resource handler and the ``/mcp.catalog`` REPL command both
    call this so they emit byte-identical JSON; the parity is enforced
    by tests.
    """
    return json.dumps(build_catalog(ctx), indent=indent, sort_keys=False)


# ── Device state (the LLM-as-debugger "where am I" view) ────────────────────


DEVICE_STATE_SCHEMA_VERSION: int = 1


def build_device_state(
    ctx: PluginContext,
    *,
    last_command: dict | None = None,
    expect_history: list | None = None,
    async_events: list | None = None,
    async_errors: list | None = None,
    banner_seen: bool = False,
    banner_text: str = "",
) -> dict[str, Any]:
    """Snapshot of everything the bridge knows about the device right now.

    Served by the ``termapy://device_state.json`` MCP resource.  This
    is the LLM-as-debugger view: port state, active profile, recent
    commands, capture artifacts, async events.  Refreshed on every
    read -- there's no caching.

    MCP-specific fields (last_command, expect_history, async_events,
    async_errors) are passed in by the host because they live on the
    MCPHost instance.  Profile and port state come from ctx + engine.

    Args:
        ctx: Live PluginContext.
        last_command: Last run_command invocation, or None if no calls
            have been made yet.  Shape:
            ``{"cmd": str, "success": bool, "elapsed_s": float,
               "at": "<ISO 8601>"}``.
        expect_history: Recent /expect calls (list of dicts).  Empty
            list when none.
        async_events: Async device events captured between calls.
            Empty until Phase 5+ NDJSON pipeline.
        async_errors: Unsolicited errors captured between calls.
            Empty until Phase 5+.

    Returns:
        Dict suitable for ``json.dumps(..., indent=2)``.
    """
    from pathlib import Path

    # Port state.
    port_obj = ctx.port() if ctx.is_connected() else None
    port_info: dict[str, Any] = {
        "name": ctx.cfg.get("port", ""),
        "open": ctx.is_connected(),
    }
    if port_obj is not None:
        baud = getattr(port_obj, "baudrate", None)
        if baud is not None:
            port_info["baud"] = baud
        # 8N1 string: "<bytesize><parity><stopbits>"
        bs = getattr(port_obj, "bytesize", None)
        par = getattr(port_obj, "parity", None)
        sb = getattr(port_obj, "stopbits", None)
        if all(v is not None for v in (bs, par, sb)):
            sb_str = "1" if sb == 1 else ("1.5" if sb == 1.5 else "2")
            port_info["params"] = f"{bs}{par}{sb_str}"

    # Active profile metadata (commands omitted -- they're in commands.json).
    active = ctx.ns("active_profile") or {}
    profile_info: dict[str, Any] = {}
    if active:
        profile_info = {
            "path": active.get("__source_path", ""),
            "revision": active.get("profile_revision", ""),
            "date": active.get("profile_date", ""),
            "command_count": len(active.get("commands", {})),
            "source": "hand-authored" if active.get("__source_path") else "in-memory",
        }
    device_info: dict[str, Any] = {}
    device = active.get("device") if isinstance(active, dict) else None
    if isinstance(device, dict) and device:
        device_info = {
            "name": device.get("name", ""),
            "prompt": device.get("prompt", ""),
            "banner_seen": bool(banner_seen),
            "banner_text": banner_text or "",
        }

    # Capture artifacts (live read of cap_dir).
    captures: list[dict[str, Any]] = []
    cap_dir = Path(ctx.cap_dir) if ctx.cap_dir else None
    if cap_dir and cap_dir.exists():
        for p in sorted(cap_dir.iterdir()):
            if p.is_file():
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                captures.append(
                    {
                        "name": p.name,
                        "bytes": size,
                        "uri": f"termapy://capture/{p.name}",
                    }
                )

    return {
        "schema": DEVICE_STATE_SCHEMA_VERSION,
        "port": port_info,
        "profile": profile_info,
        "device": device_info,
        "last_command": last_command or {},
        "captures": captures,
        "expect_history": list(expect_history or []),
        "async_events": list(async_events or []),
        "async_errors": list(async_errors or []),
    }


def device_state_json(ctx: PluginContext, **kwargs: Any) -> str:
    """Serialize device_state to JSON (indent=2, stable order)."""
    return json.dumps(
        build_device_state(ctx, **kwargs), indent=2, sort_keys=False
    )


# ── Command descriptors ─────────────────────────────────────────────────────


def _command_descriptor(plugin: PluginInfo, ctx: PluginContext) -> dict[str, Any]:
    """Convert a PluginInfo into a serializable catalog entry.

    ``name`` includes the REPL prefix (e.g. ``/help``) so consumers --
    especially LLMs reading the catalog as their symbol table --
    can drop the value straight into ``run_command(...)`` without
    needing to remember to combine it with the top-level ``prefix``
    field.  Disambiguation between termapy commands and device
    commands becomes literally visible: prefixed names are termapy
    REPL commands, unprefixed entries in ``target_commands`` are
    device commands sent verbatim.
    """
    long_help_text = resolve_long_help(plugin, ctx)
    return {
        "name": ctx.engine.prefix + plugin.name,
        "args": plugin.args or "",
        "help": plugin.help or "",
        "long_help": long_help_text,
        "flags": dict(plugin.flags) if plugin.flags else {},
        "needs": _needs_list(plugin.needs),
        "hidden": bool(plugin.hidden),
        "source": plugin.source or "built-in",
        "raw_args": bool(plugin.raw_args),
    }


def _target_descriptor(target: Any) -> dict[str, Any]:
    """Convert a TargetCommand (device-imported help) into a catalog entry."""
    out: dict[str, Any] = {
        "name": getattr(target, "name", ""),
        "args": getattr(target, "args", ""),
        "help": getattr(target, "help", ""),
        "long_help": getattr(target, "long_help", ""),
        "flags": dict(getattr(target, "flags", {}) or {}),
    }
    # v2 fields (from Phase 2): only include when present (non-default).
    typed_args = getattr(target, "typed_args", None)
    if typed_args:
        out["typed_args"] = list(typed_args)
    send_template = getattr(target, "send_template", "")
    if send_template:
        out["send_template"] = send_template
    response = getattr(target, "response", None)
    if response:
        out["response"] = dict(response)
    safety = getattr(target, "safety", "safe")
    if safety and safety != "safe":
        out["safety"] = safety
    rate_limit = getattr(target, "rate_limit_hz", 0.0)
    if rate_limit:
        out["rate_limit_hz"] = rate_limit
    timeout_ms = getattr(target, "timeout_ms", 0)
    if timeout_ms:
        out["timeout_ms"] = timeout_ms
    return out


def _needs_list(needs: CapabilitySet | None) -> list[str]:
    """Return the list of capability names that ``needs`` requires.

    Baseline capabilities (default True) are listed only when explicitly
    required and missing from the default set.  Restrictive capabilities
    (default False) appear when the command sets them True.

    The output is the LLM-facing summary: "this command needs <X>."
    """
    if needs is None:
        return []
    default = CapabilitySet()
    out: list[str] = []
    for f in fields(needs):
        # If the command's needs differ from the default for this field
        # in a way that means "more required" -- include the field name.
        # For baseline (default True) capabilities, "needs == False" never
        # makes sense; we treat them as always required when present.
        # For restrictive (default False), "needs == True" means required.
        cmd_val = getattr(needs, f.name)
        def_val = getattr(default, f.name)
        if cmd_val is True and def_val is False:
            out.append(f.name)
    return sorted(out)
