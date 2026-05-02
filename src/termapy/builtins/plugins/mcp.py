"""Built-in plugin: /mcp.* commands -- MCP catalog and status.

Phase 4 of the MCP-server work.  Subcommands:

- ``/mcp.catalog`` -- print the same JSON the
  ``termapy://commands.json`` MCP resource serves.  The two outputs
  are byte-identical (enforced by tests).  Useful in TUI/CLI for
  inspecting what an LLM client would see.
- ``/mcp.info`` -- show MCP-mode status: catalog size, port,
  active profile metadata, capture artifact count.

Both commands work in any frontend (TUI, CLI, MCP); they don't
require ``--mcp`` to be running.  ``/mcp.catalog`` is a generic
"dump all commands as JSON" tool that's useful regardless.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.mcp.catalog import build_catalog, catalog_json
from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── /mcp.catalog ────────────────────────────────────────────────────────────


def _handler_catalog(ctx: PluginContext, args: str) -> CmdResult:
    """Print the JSON catalog -- same content as termapy://commands.json.

    Args (optional):
        --pretty  / --compact     Indented (default) or compact JSON.

    Returns:
        CmdResult.ok(value=<json string>) so scripts can capture it.
    """
    compact = ctx.flag("--compact") if hasattr(ctx, "flag") else False
    indent = None if compact else 2
    text = catalog_json(ctx, indent=indent if indent is not None else 2)
    if compact:
        # catalog_json uses indent=2 by default; rebuild compact via
        # the same builder for byte-exact match with the resource (which
        # always uses indent=2).  When the user opts for compact, do
        # a plain json.dumps without indent.
        import json

        text = json.dumps(build_catalog(ctx), indent=None, sort_keys=False)
    for line in text.splitlines():
        ctx.output(line)
    return CmdResult.ok(value=text)


# ── /mcp.info ───────────────────────────────────────────────────────────────


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Print MCP-mode status: catalog, port, profile, captures."""
    cat = build_catalog(ctx)
    cmd_count = len(cat["commands"])
    target_count = len(cat["target_commands"])
    profile_rev = cat.get("profile_revision") or "(none)"
    profile_date = cat.get("profile_date") or "(none)"
    device_name = (cat.get("device") or {}).get("name", "(none)")
    transport = cat.get("transport") or {}
    protocol = transport.get("protocol", "(none)")
    baud = transport.get("baud_rate", "(none)")

    # Port state.
    is_connected = ctx.is_connected()
    port_state = "connected" if is_connected else "disconnected"
    port_name = ctx.cfg.get("port", "(none)")

    # Capture artifacts.
    cap_dir = Path(ctx.cap_dir)
    cap_count = sum(1 for _ in cap_dir.iterdir()) if cap_dir.exists() else 0

    # Match the format_kv_lines() style other /*.info commands use.
    from termapy.plugins import format_kv_lines

    rows = [
        ("port", f"{port_name} ({port_state})"),
        ("commands", str(cmd_count)),
        ("target_commands", str(target_count)),
        ("profile_revision", profile_rev),
        ("profile_date", profile_date),
        ("device", device_name),
        ("protocol", protocol),
        ("baud_rate", str(baud)),
        ("captures", str(cap_count)),
    ]
    for line in format_kv_lines(rows):
        ctx.output(line)
    return CmdResult.ok(value=str(cmd_count))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="mcp",
    help="MCP catalog and status (Model Context Protocol).",
    long_help=(
        "MCP-mode tools.  /mcp.catalog dumps the JSON command catalog\n"
        "(same content as the termapy://commands.json MCP resource).\n"
        "/mcp.info shows MCP-mode state: catalog size, port, active\n"
        "profile metadata, capture artifact count.\n"
        "\n"
        "Both commands work in any frontend (TUI/CLI/MCP); they don't\n"
        "require --mcp to be running.  See also: /profile, /port.info,\n"
        "/term.info."
    ),
    handler=None,
    sub_commands={
        "catalog": Command(
            help="Print the MCP command catalog as JSON.",
            handler=_handler_catalog,
        ),
        "info": Command(
            help="Show MCP-mode status (catalog/profile/port/captures).",
            handler=_handler_info,
        ),
    },
)
