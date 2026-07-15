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

from termapy.config import open_with_system
from termapy.mcp.catalog import build_catalog, catalog_json
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
from termapy.scripting import select_lines

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
    """Print MCP-mode status: catalog, port, profile, captures.

    Always reports counts from the **MCP perspective**, regardless of
    which host /mcp.info runs in.  In a TUI session running this
    command, the catalog count answers "what would my MCP client see?"
    -- not "what would a catalog built from this TUI's capabilities
    look like?"  That second question is uninteresting; the first is
    why someone runs /mcp.info.
    """
    from termapy.plugins import ENVIRONMENTS, format_kv_lines

    # Catalog metadata is host-independent (profile + device come from
    # the active profile namespace, not capabilities).  Wire-level
    # values (protocol, baud) live in the cfg now.
    cat = build_catalog(ctx)
    device_count = len(cat.get("device_commands", []))
    profile_rev = cat.get("profile_revision") or "(none)"
    profile_date = cat.get("profile_date") or "(none)"
    device_name = (cat.get("device") or {}).get("name", "(none)")
    protocol = ctx.cfg.get("protocol", "text")
    baud = ctx.cfg["serial"]["baud_rate"]

    # Command count: always count what MCP would see, not what the
    # current host's capabilities would surface.
    mcp_caps = ENVIRONMENTS["MCP"]
    cmd_count = sum(
        1 for p in ctx.internal.plugins.values()
        if not p.needs.missing_from(mcp_caps)
    )

    # Port state.
    is_connected = ctx.serial.is_connected()
    port_state = "connected" if is_connected else "disconnected"
    port_name = ctx.cfg["serial"]["port"] or "(none)"

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
        name for name, spec in profile_cmds.items()
        if isinstance(spec, dict) and spec.get("safety") == "destructive"
    )
    # Enabled audit: when the profile contains any disabled entries the
    # engineer is mid-audit (typical for a freshly-drafted profile from
    # a legacy help dump).  Show the split so it's obvious how much
    # remains to review.  Hide the row entirely when no profile is
    # loaded -- otherwise it's just noise saying "0 of 0".
    profile_enabled = sum(
        1 for spec in profile_cmds.values()
        if isinstance(spec, dict) and spec.get("enabled", True)
    )
    profile_total = len(profile_cmds)
    profile_disabled = profile_total - profile_enabled

    rows = [
        ("port", f"{port_name} ({port_state})"),
        ("commands", str(cmd_count)),
        ("device_commands", str(device_count)),
        ("profile_revision", profile_rev),
        ("profile_date", profile_date),
        ("device", device_name),
        ("protocol", protocol),
        ("baud_rate", str(baud)),
        ("captures", str(cap_count)),
        ("destructive", _format_destructive(destructive_names)),
    ]
    if profile_total:
        rows.append((
            "profile_enabled",
            f"{profile_enabled} of {profile_total}"
            + (f" ({profile_disabled} drafts pending review)"
               if profile_disabled else " (all enabled)"),
        ))
    for line in format_kv_lines(rows):
        ctx.io.output_markup(line)
    # Discoverability hint: many users will reach for /mcp.info first when
    # learning the MCP surface; point them at the human-readable view.
    prefix = ctx.prefix
    ctx.io.output_markup(
        f"  [dim](Use {prefix}help --mcp for the human-readable command "
        f"list, {prefix}mcp.catalog for raw JSON.)[/]"
    )
    return CmdResult.ok(value=str(cmd_count))


# ── /mcp.log ────────────────────────────────────────────────────────────────


def _mcp_log_path(ctx: PluginContext) -> Path:
    """Resolve the MCP session log path.

    Lives at ``<cfg_dir>/mcp/session.log`` where cfg_dir is the parent
    of the active config file.  For zero-config sessions, falls back
    to ``<cwd>/mcp/session.log``.  Mirrors ``MCPHost._resolve_mcp_dir``
    in mcp/server.py -- duplicated rather than imported because mcp/
    server.py has heavy imports (FastMCP) we don't want to pull into
    a bare /mcp.log call from CLI/TUI.

    Returns the path even when the file doesn't exist; callers check.
    """
    if ctx.config_path:
        return Path(ctx.config_path).parent / "mcp" / "session.log"
    return Path.cwd() / "mcp" / "session.log"


def _handler_log(ctx: PluginContext, args: str) -> CmdResult:
    """Open the MCP session log in the system viewer.

    The log is written by termapy's MCP server (``--mcp``) to
    ``<cfg_dir>/mcp/session.log`` and contains every dispatch, every
    tool result, every TX/RX line, with timestamps.  Useful when
    debugging an LLM session that didn't behave the way you expected
    -- the log shows exactly what the LLM sent and what came back,
    in order.

    Mirrors ``/log.show`` for the regular session log.  Same idiom,
    different file.
    """
    path = _mcp_log_path(ctx)
    if not path.exists():
        return CmdResult.fail(
            msg=(
                f"MCP session log not found: {path}\n"
                "Run termapy --mcp to start an MCP server (it writes the log)."
            )
        )
    open_with_system(str(path))
    ctx.io.output(f"  Opening {path.name}", "green")
    return CmdResult.ok(value=path)


def _handler_log_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print the MCP session log to the terminal (all, or an N-line slice).

    With no argument prints the entire log; with a signed integer N
    prints a slice: N>0 the last N lines (most recent), N<0 the first
    N (oldest).  Useful when you want to see the log inline rather than
    launching an external viewer -- particularly in CLI/SSH sessions
    where /mcp.log can't open anything.

    Mirrors ``/log.dump`` for the regular session log (same count scheme).
    """
    path = _mcp_log_path(ctx)
    if not path.exists():
        return CmdResult.fail(msg=f"MCP session log not found: {path}")

    n: int | None = None
    arg = args.strip()
    if arg:
        try:
            n = int(arg)
        except ValueError:
            raise UsageError(
                f"Invalid count: {arg!r}  (N>0 last N, N<0 first N)"
            ) from None
        if n == 0:
            return CmdResult.fail(msg="Invalid line count: 0")

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")

    lines = select_lines(lines, n)

    for line in lines:
        ctx.io.output(line)
    return CmdResult.ok(value=str(len(lines)))


def _handler_log_path(ctx: PluginContext, args: str) -> CmdResult:
    """Print the MCP session log path (without opening it).

    Useful for piping into other tools (``tail -f $(...)``, etc.)
    or for confirming where the log will land before starting an
    MCP server.  Reports the path whether or not the file exists,
    with a marker noting absence.
    """
    path = _mcp_log_path(ctx)
    marker = "" if path.exists() else "  (not yet created)"
    ctx.io.output(f"{path}{marker}")
    return CmdResult.ok(value=path)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="mcp",
    help="MCP catalog and status (Model Context Protocol).",
    long_help=(
        "MCP-mode tools.  /mcp.catalog dumps the JSON command catalog\n"
        "(same content as the termapy://commands.json MCP resource).\n"
        "/mcp.info shows MCP-mode state: catalog size, port, active\n"
        "profile metadata, capture artifact count.  /mcp.log opens the\n"
        "MCP session log written by termapy --mcp; /mcp.log.dump prints\n"
        "it inline; /mcp.log.path reports its location.\n"
        "\n"
        "Both commands work in any frontend (TUI/CLI/MCP); they don't\n"
        "require --mcp to be running.  See also: /profile, /port.info,\n"
        "/term.info, /log.show (for the regular session log)."
    ),
    handler=None,
    sub_commands={
        "catalog": Command(
            help="Print the MCP command catalog as JSON.",
            handler=_handler_catalog,
        ),
        "info": Command(
            help="Print MCP-mode status (catalog/profile/port/captures).",
            handler=_handler_info,
        ),
        "log": Command(
            help="Open the MCP session log in the system viewer.",
            handler=_handler_log,
            needs=CapabilitySet(gui_apps=True),
            sub_commands={
                "dump": Command(
                    args="{N}",
                    help="Print the MCP session log; N>0 last N lines, N<0 first N.",
                    handler=_handler_log_dump,
                ),
                "path": Command(
                    help="Print the MCP session log file path.",
                    handler=_handler_log_path,
                ),
            },
        ),
    },
)
