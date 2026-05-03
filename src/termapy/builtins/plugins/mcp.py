"""Built-in plugin: /mcp.* commands -- MCP catalog and status.

Phase 4 of the MCP-server work.  Subcommands:

- ``/mcp.catalog`` -- print the same JSON the
  ``termapy://commands.json`` MCP resource serves.  The two outputs
  are byte-identical (enforced by tests).  Useful in TUI/CLI for
  inspecting what an LLM client would see.
- ``/mcp.info`` -- show MCP-mode status: catalog size, port,
  active profile metadata, capture artifact count.

For a human-readable view of the MCP-visible command list, use
``/help --mcp`` -- same filter as ``/mcp.catalog``, rendered like
``/help``.  No new sibling here: keeping the surface small.

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


def _format_destructive(names: list[str]) -> str:
    """Render the destructive-command audit value for /mcp.info.

    Empty list reads as ``0`` (clean).  Non-empty shows count + names
    so a quick eyeballing of /mcp.info catches profile drift (e.g. an
    AT+ERASE_FLASH that snuck in on the latest /include).
    """
    if not names:
        return "0"
    if len(names) <= 5:
        return f"{len(names)} ({', '.join(names)})"
    head = ", ".join(names[:5])
    return f"{len(names)} ({head}, ...)"


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Print MCP-mode status: catalog, port, profile, captures.

    Always reports counts from the **MCP perspective**, regardless of
    which host /mcp.info runs in.  In a TUI session running this
    command, the catalog count answers "what would my MCP client see?"
    -- not "what would a catalog built from this TUI's capabilities
    look like?"  That second question is uninteresting; the first is
    why someone runs /mcp.info.
    """
    from termapy.plugins import ENVIRONMENTS, format_kv_lines

    # Catalog metadata is host-independent (profile / device / transport
    # come from the active profile namespace, not capabilities).
    cat = build_catalog(ctx)
    target_count = len(cat["target_commands"])
    profile_rev = cat.get("profile_revision") or "(none)"
    profile_date = cat.get("profile_date") or "(none)"
    device_name = (cat.get("device") or {}).get("name", "(none)")
    transport = cat.get("transport") or {}
    protocol = transport.get("protocol", "(none)")
    baud = transport.get("baud_rate", "(none)")

    # Command count: always count what MCP would see, not what the
    # current host's capabilities would surface.
    mcp_caps = ENVIRONMENTS["MCP"]
    cmd_count = sum(
        1 for p in ctx.engine.plugins.values()
        if not p.needs.missing_from(mcp_caps)
    )

    # Port state.
    is_connected = ctx.is_connected()
    port_state = "connected" if is_connected else "disconnected"
    port_name = ctx.cfg.get("port", "(none)")

    # Capture artifacts.
    cap_dir = Path(ctx.cap_dir)
    cap_count = sum(1 for _ in cap_dir.iterdir()) if cap_dir.exists() else 0

    # Destructive-command audit: count entries flagged safety=destructive
    # in the active profile.  These require ``confirm=true`` on the
    # MCP tool call -- the LLM cannot run them autonomously.  Zero is
    # the expected steady state for a well-behaved profile; a non-zero
    # count is the user's chance to verify they recognize each entry.
    profile = ctx.ns("active_profile") if hasattr(ctx, "ns") else {}
    profile_cmds = profile.get("commands", {}) if isinstance(profile, dict) else {}
    destructive_names = sorted(
        name for name, spec in profile_cmds.items()
        if isinstance(spec, dict) and spec.get("safety") == "destructive"
    )

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
        ("destructive", _format_destructive(destructive_names)),
    ]
    for line in format_kv_lines(rows):
        ctx.write_markup(line)
    # Discoverability hint: many users will reach for /mcp.info first when
    # learning the MCP surface; point them at the human-readable view.
    prefix = ctx.engine.prefix
    ctx.write_markup(
        f"  [dim](Use {prefix}help --mcp for the human-readable command "
        f"list, {prefix}mcp.catalog for raw JSON.)[/]"
    )
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
