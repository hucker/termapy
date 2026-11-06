"""Built-in plugin: /mcp.* commands -- MCP catalog and status.

Phase 4 of the MCP-server work.  Subcommands:

- ``/mcp.catalog`` -- print the same JSON the
  ``termapy://commands.json`` MCP resource serves.  The two outputs
  are byte-identical (enforced by tests).  Useful in TUI/CLI for
  inspecting what an LLM client would see.
- ``/mcp.info`` -- show MCP-mode status: this process's PID, catalog
  size, port, active profile metadata, capture artifact count.

For a human-readable view of the MCP-visible command list, use
``/help --mcp`` -- same filter as ``/mcp.catalog``, rendered like
``/help``.  No new sibling here: keeping the surface small.

Both commands work in any frontend (TUI, CLI, MCP); they don't
require ``--mcp`` to be running.  ``/mcp.catalog`` is a generic
"dump all commands as JSON" tool that's useful regardless.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.defaults import cfg_value
from termapy.mcp.catalog import build_catalog, catalog_json
from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import KvRow, PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


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
        ctx.io.output(line)
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
    """Print MCP-mode status: PID, catalog, port, profile, captures.

    A record answer (registry ``record-one-format``): an MCP client gets
    the facts as ``data`` at any output level.  The PID leads because
    every termapy MCP server has the same command line, and the PID is
    how an agent finds the one it is talking to in ``tasklist`` / ``ps``.

    Always reports counts from the **MCP perspective**, regardless of
    which host /mcp.info runs in.  In a TUI session running this
    command, the catalog count answers "what would my MCP client see?"
    -- not "what would a catalog built from this TUI's capabilities
    look like?"  That second question is uninteresting; the first is
    why someone runs /mcp.info.
    """
    from termapy.plugins import ENVIRONMENTS

    # Catalog metadata is host-independent (profile + device come from
    # the active profile namespace, not capabilities).  Wire-level
    # values (protocol, baud) live in the cfg now.
    cat = build_catalog(ctx)
    device_count = len(cat.get("device_commands", []))
    profile_rev = cat.get("profile_revision") or None
    profile_date = cat.get("profile_date") or None
    device_name = (cat.get("device") or {}).get("name")
    protocol = cfg_value(ctx.cfg, "protocol")
    baud = ctx.cfg["serial"]["baud_rate"]

    # Command count: always count what MCP would see, not what the
    # current host's capabilities would surface.
    mcp_caps = ENVIRONMENTS["MCP"]
    cmd_count = sum(
        1 for value in ctx.internal.plugins.values() if not value.needs.missing_from(mcp_caps)
    )

    # Port state.
    is_connected = ctx.serial.is_connected()
    port_state = "connected" if is_connected else "disconnected"
    port_name = ctx.cfg["serial"]["port"] or None

    # Capture artifacts.
    cap_dir = Path(ctx.fs.cap_dir)
    cap_count = sum(1 for _ in cap_dir.iterdir()) if cap_dir.exists() else 0

    # Destructive-command audit: count entries flagged safety=destructive
    # in the active profile.  These require ``confirm=true`` on the
    # MCP tool call -- the LLM cannot run them autonomously.  Zero is
    # the expected steady state for a well-behaved profile; a non-zero
    # count is the user's chance to verify they recognize each entry.
    profile = ctx.ns("active_profile") if hasattr(ctx, "ns") else {}
    profile_cmds = profile.get("commands", {}) if isinstance(profile, dict) else {}
    destructive_names = sorted(
        name
        for name, spec in profile_cmds.items()
        if isinstance(spec, dict) and spec.get("safety") == "destructive"
    )
    # Enabled audit: when the profile contains any disabled entries the
    # engineer is mid-audit (typical for a freshly-drafted profile from
    # a legacy help dump).  Show the split so it's obvious how much
    # remains to review.  Hide the row entirely when no profile is
    # loaded -- otherwise it's just noise saying "0 of 0".
    profile_enabled = sum(
        1 for spec in profile_cmds.values() if isinstance(spec, dict) and spec.get("enabled", True)
    )
    profile_total = len(profile_cmds)
    profile_disabled = profile_total - profile_enabled

    data = {
        "pid": os.getpid(),
        "port": port_name,
        "connected": is_connected,
        "commands": cmd_count,
        "device_commands": device_count,
        "profile_revision": profile_rev,
        "profile_date": profile_date,
        "device": device_name,
        "protocol": protocol,
        "baud_rate": baud,
        "captures": cap_count,
        "destructive": destructive_names,
        "profile_commands": profile_total,
        "profile_enabled": profile_enabled,
    }
    rows: list[KvRow | None] = [
        ("pid", str(data["pid"])),
        ("port", f"{port_name or '(none)'} ({port_state})"),
        ("commands", str(cmd_count)),
        ("device_commands", str(device_count)),
        ("profile_revision", profile_rev or "(none)"),
        ("profile_date", profile_date or "(none)"),
        ("device", device_name or "(none)"),
        ("protocol", protocol),
        ("baud_rate", str(baud)),
        ("captures", str(cap_count)),
        ("destructive", _format_destructive(destructive_names)),
    ]
    if profile_total:
        rows.append(
            (
                "profile_enabled",
                f"{profile_enabled} of {profile_total}"
                + (
                    f" ({profile_disabled} drafts pending review)"
                    if profile_disabled
                    else " (all enabled)"
                ),
            )
        )
    # Discoverability hint: many users will reach for /mcp.info first when
    # learning the MCP surface; point them at the human-readable view.
    hint = (
        f"(Use {ctx.prefix}help --mcp for the human-readable command list, "
        f"{ctx.prefix}mcp.catalog for raw JSON.)"
    )
    return CmdResult.record(data, rows, value=str(cmd_count), notes=((hint, "dim"),))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="advanced",
    name="mcp",
    maturity="preview",
    help="MCP catalog and status (Model Context Protocol).",
    intro=(
        "The MCP server lets an AI agent drive termapy and the device. These commands"
        " show what it offers and what it has done."
    ),
    long_help=(
        "{prefix}mcp.catalog dumps the JSON command catalog\n"
        "(same content as the termapy://commands.json MCP resource).\n"
        "{prefix}mcp.info shows MCP-mode state: this process's pid, catalog\n"
        "size, port, active profile metadata, capture artifact count.\n"
        "The MCP server's session log, <cfg>.mcp.log, is one of the config's\n"
        "session logs: {prefix}log.list lists it, and {prefix}log.show and\n"
        "{prefix}log.dump open or print it by name.\n"
        "\n"
        "Every termapy MCP server has the same command line, so the pid\n"
        "is how to find a particular one in tasklist or ps.  On Windows\n"
        "under uv it is the interpreter, whose parent is a launcher;\n"
        "ending it (taskkill /F /PID <pid>, or kill <pid>) ends the\n"
        "launchers too and frees termapy.exe.\n"
        "\n"
        "Both commands work in any frontend (TUI/CLI/MCP); they don't\n"
        "require --mcp to be running.  See also: {prefix}profile, {prefix}port.info,\n"
        "{prefix}term.info, {prefix}log.list."
    ),
    handler=None,
    sub_commands={
        "catalog": Command(
            help="Print the MCP command catalog as JSON.",
            handler=_handler_catalog,
            shows=True,
        ),
        "info": Command(
            help="Print MCP-mode status (pid/catalog/profile/port/captures).",
            handler=_handler_info,
            cfg={
                "protocol": "in the summary",
                "serial.baud_rate": "in the summary",
                "serial.port": "the port the state line names",
            },
            shows=True,
        ),
    },
)
