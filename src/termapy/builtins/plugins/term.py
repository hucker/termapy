"""Built-in plugin: /term.* -- terminal display / session toggles.

Consolidates what used to be a scattering of top-level toggles
(/echo, /line_no, /show_line_endings) under one namespace for symmetry
with /port.* and /cfg.*.  Also adds runtime toggles for config keys
that previously could only be set via /cfg (/term.timestamps,
/term.hex, /term.encoding, /term.send_bare_enter), plus the
/term.output level dial that controls how loud commands are.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import (
    OUTPUT_LEVELS,
    CapabilitySet,
    CmdResult,
    Command,
    parse_output_level,
)
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


def _handler_output(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the global output level.

    Bare invocation reports the current level; an argument sets it.
    Setting ``silent`` does not echo the new state (silent means silent).
    """
    flags = ctx.ns("flags")
    arg = args.strip()
    if not arg:
        current = flags.get("output_level", "normal")
        ctx.result(current)
        return CmdResult.ok(value=current)
    level = parse_output_level(arg)
    if level is None:
        return CmdResult.fail(
            msg=f"Unknown level: {arg} (use {'/'.join(OUTPUT_LEVELS)})"
        )
    flags["output_level"] = level
    ctx.result(level)
    return CmdResult.ok(value=level)


def _handler_verbose_legacy(ctx: PluginContext, args: str) -> CmdResult:
    """Legacy /term.verbose forwarder: translate on/off and dispatch."""
    warned = ctx.ns("legacy_warned")
    if "term.verbose" not in warned:
        warned["term.verbose"] = True
        p = ctx.engine.prefix
        ctx.write(
            f"  Note: {p}term.verbose is legacy; use "
            f"{p}term.output (verbose|normal).",
            "yellow",
        )
    body = args.strip()
    if not body:
        target = "term.output"
    else:
        val = parse_bool(body)
        if val is True:
            target = "term.output verbose"
        elif val is False:
            target = "term.output normal"
        else:
            return CmdResult.fail(msg=f"Invalid: {body} (use on or off)")
    result = ctx.engine.dispatch(target)
    if not result.success:
        return CmdResult(
            success=False,
            error="",
            elapsed_s=result.elapsed_s,
            value=result.value,
        )
    return result


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


# ── /term.log: write to the session log without echoing to screen ──────────


def _handler_log(ctx: PluginContext, args: str) -> CmdResult:
    """Append a line to the session log without printing it to the terminal.

    Useful for annotating long sessions with markers, timestamps, or
    events you want to see when reviewing the log later but don't
    want cluttering the screen while the session is running.

    In CLI mode (no log file), this is a no-op -- the bridge's
    ``ctx.log`` lambda absorbs the call silently.
    """
    text = args.strip()
    if not text:
        return CmdResult.fail(msg=f"Usage: {ctx.engine.prefix}term.log <text>")
    ctx.log("#", text)
    return CmdResult.ok()


# ── /term.info: snapshot ────────────────────────────────────────────────────


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Snapshot the current state of every /term.* toggle."""
    flags = ctx.ns("flags")
    rows = [
        ("echo",            "on" if flags.get("echo") else "off"),
        ("output",          str(flags.get("output_level", "normal"))),
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
        "scattered top-level commands (/echo, /line_no, /show_line_endings)\n"
        "under one namespace and adds runtime access to a handful of\n"
        "config-key toggles that previously required /cfg, plus the\n"
        "{prefix}term.output level dial.\n"
        "\n"
        "Toggles accept ``on|off|true|false|1|0|yes|no``; {prefix}term.output\n"
        "takes a level name (silent/quiet/normal/verbose).  Empty args\n"
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
        "output": Command(
            args="{silent|quiet|normal|verbose}",
            help="Show or set the global output level (silent/quiet/normal/verbose).",
            long_help=(
                "Controls how loud commands are.  Each level adds a channel\n"
                "to the previous one:\n"
                "\n"
                "  silent   show nothing (CmdResult.value still returns)\n"
                "  quiet    show command results only\n"
                "  normal   show results + bulk output (default)\n"
                "  verbose  show results + output + status/progress chatter\n"
                "\n"
                "Override per-call with ``cmd --<level>`` or ``cmd.<level>``\n"
                "(e.g. ``{prefix}port.list.quiet`` or ``{prefix}cap show foo --silent``)."
            ),
            handler=_handler_output,
        ),
        "verbose": Command(
            args="{on|off}",
            help="Legacy alias for /term.output (verbose|normal).",
            handler=_handler_verbose_legacy,
            hidden=True,
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
        "log": Command(
            args="<text>",
            help="Append a line to the session log without echoing to screen.",
            long_help=(
                "Annotate the session log with a marker the user types but\n"
                "doesn't want to see in the terminal output.  Useful for\n"
                "recording events, timestamps, and notes that are only\n"
                "interesting when reviewing the log after the fact.\n"
                "\n"
                "CLI mode has no log file -- the command is a silent no-op\n"
                "there (returns success, writes nothing)."
            ),
            handler=_handler_log,
        ),
    },
)
