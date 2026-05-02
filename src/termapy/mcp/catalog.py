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
    cmd_list = [_command_descriptor(plugins[name], ctx) for name in sorted(plugins)]

    target_commands = []
    target_meta = ctx.ns("target_meta")
    target_ns = ctx.ns("target_commands")
    if isinstance(target_ns, dict) and target_ns:
        target_commands = [
            _target_descriptor(target_ns[n]) for n in sorted(target_ns)
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


# ── Command descriptors ─────────────────────────────────────────────────────


def _command_descriptor(plugin: PluginInfo, ctx: PluginContext) -> dict[str, Any]:
    """Convert a PluginInfo into a serializable catalog entry."""
    long_help_text = resolve_long_help(plugin, ctx)
    return {
        "name": plugin.name,
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
