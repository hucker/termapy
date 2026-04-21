"""Built-in plugin: /term.* -- terminal display / session toggles.

Consolidates what used to be a scattering of top-level toggles
(/echo, /line_no, /show_line_endings, /verbose) under one namespace
for symmetry with /port.* and /cfg.*.  Also adds runtime toggles
for config keys that previously could only be set via /cfg
(/term.timestamps, /term.hex, /term.encoding, /term.send_bare_enter).

The old top-level names (/echo, /line_no, /show_line_endings,
/verbose) remain as hidden legacy aliases that forward here and
print a one-time deprecation note -- so existing scripts keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import parse_bool

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Runtime-flag toggles (session-scoped via ctx.ns("flags")) ───────────────


def _flag_toggle(ctx: PluginContext, args: str, flag_name: str) -> CmdResult:
    """Toggle or set a session-scoped flag.

    Empty args flips the current state (so the command doubles as a
    "toggle"); any recognised boolean token is an explicit set.
    """
    flags = ctx.ns("flags")
    val = parse_bool(args)
    if val is None:
        flags[flag_name] = not flags.get(flag_name, False)
    else:
        flags[flag_name] = val
    state = "on" if flags.get(flag_name) else "off"
    ctx.result(state)
    return CmdResult.ok(value=state)


def _handler_echo(ctx: PluginContext, args: str) -> CmdResult:
    return _flag_toggle(ctx, args, "echo")


def _handler_verbose(ctx: PluginContext, args: str) -> CmdResult:
    return _flag_toggle(ctx, args, "verbose")


def _handler_hex(ctx: PluginContext, args: str) -> CmdResult:
    return _flag_toggle(ctx, args, "hex_mode")


# ── Config-persisted toggles (via ctx.engine.apply_cfg) ─────────────────────


def _cfg_toggle(ctx: PluginContext, args: str, key: str) -> CmdResult:
    """Toggle or set a config key that's read at display time.

    Empty/unknown input flips the current value so the command doubles
    as a toggle; an explicit token just sets it.  Persists to the
    in-memory config via ``engine.apply_cfg``; the ConfigEditor dialog
    is the only path that writes to disk.
    """
    val = parse_bool(args)
    current = bool(ctx.cfg.get(key, False))
    new = (not current) if val is None else val
    ctx.engine.apply_cfg(key, new)
    state = "on" if new else "off"
    ctx.result(state)
    return CmdResult.ok(value=state)


def _handler_line_endings(ctx: PluginContext, args: str) -> CmdResult:
    return _cfg_toggle(ctx, args, "show_line_endings")


def _handler_timestamps(ctx: PluginContext, args: str) -> CmdResult:
    return _cfg_toggle(ctx, args, "show_timestamps")


def _handler_send_bare_enter(ctx: PluginContext, args: str) -> CmdResult:
    return _cfg_toggle(ctx, args, "send_bare_enter")


def _handler_encoding(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the byte-decoding encoding (utf-8, latin-1, ...)."""
    name = args.strip()
    if not name:
        current = ctx.cfg.get("encoding", "utf-8")
        ctx.result(current)
        return CmdResult.ok(value=current)
    ctx.engine.apply_cfg("encoding", name)
    ctx.result(name)
    return CmdResult.ok(value=name)


# ── TUI-only placeholder (app.py overrides via register_hook) ───────────────


def _handler_line_no_placeholder(ctx: PluginContext, args: str) -> CmdResult:
    """Never invoked: the TUI replaces this handler via register_hook.

    In non-TUI environments dispatch's capability gate fails before
    reaching the handler because ``tui_mode`` is not provided.
    """
    return CmdResult.fail(msg="term.line_no handler not installed")


# ── /term.info: snapshot ────────────────────────────────────────────────────


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Snapshot the current state of every /term.* toggle."""
    flags = ctx.ns("flags")
    rows = [
        ("echo",            "on" if flags.get("echo") else "off"),
        ("verbose",         "on" if flags.get("verbose") else "off"),
        ("hex",             "on" if flags.get("hex_mode") else "off"),
        ("line_no",         "on" if flags.get("line_no") else "off"),
        ("line_endings",    "on" if ctx.cfg.get("show_line_endings") else "off"),
        ("timestamps",      "on" if ctx.cfg.get("show_timestamps") else "off"),
        ("send_bare_enter", "on" if ctx.cfg.get("send_bare_enter") else "off"),
        ("encoding",        str(ctx.cfg.get("encoding", "utf-8"))),
    ]
    width = max(len(name) for name, _ in rows)
    for name, val in rows:
        ctx.write_markup(f"  [cyan]{name:<{width}}[/]  {val}")
    return CmdResult.ok()


# ── /term root handler: list subcommands when called bare ───────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """When /term is called bare, show the subcommand landscape."""
    arg = args.strip()
    if arg:
        p = ctx.engine.prefix
        return CmdResult.fail(msg=f"Usage: {p}term.<subcommand>.  Try {p}term.info.")
    return ctx.dispatch("help term")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="term",
    help="Terminal display / session toggles (echo, line_no, timestamps, ...).",
    long_help=(
        "Runtime display and session toggles.  Collapses what used to be\n"
        "scattered top-level commands (/echo, /line_no, /show_line_endings,\n"
        "/verbose) under one namespace and adds runtime access to a handful\n"
        "of config-key toggles that previously required /cfg.\n"
        "\n"
        "All toggles accept ``on|off|true|false|1|0|yes|no``; empty args\n"
        "reports the current state.\n"
        "\n"
        "Use {prefix}term.info for a full snapshot."
    ),
    handler=_handler_root,
    sub_commands={
        "echo": Command(
            args="{on|off}",
            help="Toggle echoing of sent commands in terminal output.",
            handler=_handler_echo,
        ),
        "line_no": Command(
            args="{on|off}",
            help="Toggle line numbers in serial output (TUI only).",
            handler=_handler_line_no_placeholder,
            needs=CapabilitySet(tui_mode=True),
        ),
        "line_endings": Command(
            args="{on|off}",
            help="Toggle visible \\r \\n markers in serial output.",
            handler=_handler_line_endings,
        ),
        "verbose": Command(
            args="{on|off}",
            help="Toggle verbose status output.",
            handler=_handler_verbose,
        ),
        "timestamps": Command(
            args="{on|off}",
            help="Toggle [HH:MM:SS.mmm] timestamp prefix on each line.",
            handler=_handler_timestamps,
        ),
        "hex": Command(
            args="{on|off}",
            help="Toggle hex display of incoming bytes.",
            handler=_handler_hex,
        ),
        "send_bare_enter": Command(
            args="{on|off}",
            help="Send line ending on empty Enter (for press-to-continue prompts).",
            handler=_handler_send_bare_enter,
        ),
        "encoding": Command(
            args="{name}",
            help="Show or set byte-decoding encoding (utf-8, latin-1, ascii, cp437).",
            handler=_handler_encoding,
        ),
        "info": Command(
            help="Snapshot the state of every /term.* toggle.",
            handler=_handler_info,
        ),
    },
)
