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
    "toggle"); any recognized boolean token is an explicit set.
    """
    flags = ctx.ns("flags")
    val = parse_bool(args)
    if val is None:
        flags[flag_name] = not flags.get(flag_name, False)
    else:
        flags[flag_name] = val
    state = "on" if flags.get(flag_name) else "off"
    ctx.io.result(state)
    return CmdResult.ok(value=state)


def _handler_echo(ctx: PluginContext, args: str) -> CmdResult:
    # Device-command echo: echo of what's sent to the wire (bare commands
    # + /term.send).  Backed by cfg["echo"]; the flag is the session
    # state seeded from it.
    return _flag_toggle(ctx, args, "echo")


def _handler_echo_repl(ctx: PluginContext, args: str) -> CmdResult:
    # REPL/slash-command echo (/cfg, /help, ...).  Distinct from device
    # echo; a session-only flag (no cfg key), default per-host (on for the
    # TUI, off for CLI/MCP -- see TerminalHost._init_flags).
    return _flag_toggle(ctx, args, "echo_repl")


def _handler_color(ctx: PluginContext, args: str) -> CmdResult:
    # Render device ANSI color (SGR).  Session flag, portable across the
    # TUI (strip before render when off) and CLI (strip in on_lines) --
    # was a CLI-only hook before.  Default off under --no-color.
    return _flag_toggle(ctx, args, "color")


def _handler_send(ctx: PluginContext, args: str) -> CmdResult:
    """Send literal text to the serial port (with line ending + encoding).

    The slash-command equivalent of typing a bare line in the terminal.
    The dispatcher's fallthrough branch (in ``repl.py``) rewrites bare
    user input to ``/term.send <text>`` so every action has a slash-
    command name -- helps LLMs (the catalog now contains the device-
    send primitive), helps logging (every dispatched action has a
    discoverable name), helps ``/help``/``/search`` discoverability.

    **Literal send.**  No template/var expansion, no echo.  Transforms
    happen upstream (in the fallthrough branch) so the user-typed
    bare line gets full template+var expansion BEFORE the redispatch.
    Direct calls (e.g. an LLM via MCP, a script) get literal-bytes
    semantics on purpose -- predictable, no surprises.

    For literal raw bytes WITHOUT a line ending, use ``/raw`` instead.
    """
    if not args:
        return CmdResult.fail(msg="Usage: /term.send <text>")
    if not ctx.serial.is_connected():
        return CmdResult.fail(msg="Not connected.")
    encoding = ctx.cfg.get("encoding", "utf-8")
    line_ending = ctx.cfg.get("eol", "\r")
    try:
        ctx.serial.write((args + line_ending).encode(encoding))
    # Plugin handlers can be called from many hosts; OSError covers
    # the file-descriptor / IO classes and pyserial.SerialException
    # is a subclass of Exception. Match dispatch_full's legacy catch.
    except (OSError, Exception) as e:  # noqa: BLE001  -- boundary catch
        return CmdResult.fail(msg=f"Send error: {e}")
    # SPECIAL CASE: /term.send is fire-and-forget over the wire; the
    # device's response (if any) is async and comes through /expect or
    # the read pipeline, not back via this CmdResult.  Returning the
    # sent bytes here would be a misleading "value" (input, not output).
    return CmdResult.ok(value="")


def _handler_output(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the global output level.

    Bare invocation reports the current level; an argument sets it.
    Setting ``silent`` does not echo the new state (silent means silent).
    """
    flags = ctx.ns("flags")
    arg = args.strip()
    if not arg:
        current = flags.get("output_level", "normal")
        ctx.io.result(current)
        return CmdResult.ok(value=current)
    level = parse_output_level(arg)
    if level is None:
        return CmdResult.fail(
            msg=f"Unknown level: {arg} (use {'/'.join(OUTPUT_LEVELS)})"
        )
    flags["output_level"] = level
    ctx.io.result(level)
    return CmdResult.ok(value=level)


def _handler_verbose_legacy(ctx: PluginContext, args: str) -> CmdResult:
    """Legacy /term.verbose forwarder: translate on/off and dispatch."""
    warned = ctx.ns("legacy_warned")
    if "term.verbose" not in warned:
        warned["term.verbose"] = True
        p = ctx.prefix
        ctx.io.output(
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
    result = ctx.internal.dispatch(target)
    if not result.success:
        return CmdResult(
            success=False,
            error="",
            elapsed_s=result.elapsed_s,
            value=result.value,
        )
    return result


def _handler_hex(ctx: PluginContext, args: str) -> CmdResult:
    return _flag_toggle(ctx, args, "hex")


# ── Config-persisted toggles (via ctx.internal.apply_cfg) ─────────────────────


def _cfg_toggle(ctx: PluginContext, args: str, key: str) -> CmdResult:
    """Toggle or set a config key that's read at display time.

    Empty/unknown input flips the current value so the command doubles
    as a toggle; an explicit token just sets it.  Persists to the
    in-memory config via ``ctx.internal.apply_cfg``; the ConfigEditor dialog
    is the only path that writes to disk.
    """
    val = parse_bool(args)
    current = bool(ctx.cfg.get(key, False))
    new = (not current) if val is None else val
    ctx.internal.apply_cfg(key, new)
    state = "on" if new else "off"
    ctx.io.result(state)
    return CmdResult.ok(value=state)


def _handler_eol_markers(ctx: PluginContext, args: str) -> CmdResult:
    # Show dim \r \n markers in received output for line-ending debugging.
    # cfg eol_markers.  Canonical /term.eol.markers; /term.line_endings and
    # /show_line_endings are hidden aliases.
    return _cfg_toggle(ctx, args, "eol_markers")


# Named tokens for the line ending appended to every sent command.
_EOL_TOKENS = {"cr": "\r", "lf": "\n", "crlf": "\r\n", "none": ""}
_EOL_LABELS = {v: k for k, v in _EOL_TOKENS.items()}


def _handler_eol(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the line ending appended to sent commands.

    Bare invocation reports the current ending as a token; an argument
    (``cr`` / ``lf`` / ``crlf`` / ``none``) sets it for the session via
    ``apply_cfg`` -- the same in-memory path the other /term.* toggles
    use.  Persisting to disk is still /cfg eol or the Config
    Editor; this is the quick runtime override.
    """
    arg = args.strip().lower()
    if not arg:
        current = ctx.cfg.get("eol", "\r")
        label = _EOL_LABELS.get(current, repr(current))
        ctx.io.result(label)
        return CmdResult.ok(value=label)
    if arg not in _EOL_TOKENS:
        return CmdResult.fail(
            msg=f"Unknown line ending: {arg} (use {'/'.join(_EOL_TOKENS)})"
        )
    ctx.internal.apply_cfg("eol", _EOL_TOKENS[arg])
    ctx.io.result(arg)
    return CmdResult.ok(value=arg)


# Receive-newline modes (mirror TeraTerm's Receive newline: AUTO/CR/LF/CR+LF).
# Kept in sync with serial_port.RX_NEWLINE_MODES; duplicated here so this
# builtin stays decoupled from the pyserial-carrying serial_port module.
_RX_NEWLINE_MODES = ("auto", "cr", "lf", "crlf")


def _handler_eol_rx(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set how received device output is split into lines.

    The receive-side counterpart of ``/term.eol`` (which sets the TX line
    ending) -- mirrors TeraTerm's Receive-newline selector.  Bare
    invocation reports the current mode; an argument (``auto`` / ``cr`` /
    ``lf`` / ``crlf``) sets it for the session via ``apply_cfg``.  ``auto``
    treats CR, LF, and CRLF all as line breaks and works for any device;
    the single-terminator modes are for the rare device that sends a stray
    CR or LF as data.
    """
    arg = args.strip().lower()
    if not arg:
        current = ctx.cfg.get("eol_rx", "auto")
        ctx.io.result(current)
        return CmdResult.ok(value=current)
    if arg not in _RX_NEWLINE_MODES:
        return CmdResult.fail(
            msg=f"Unknown receive newline: {arg} (use {'/'.join(_RX_NEWLINE_MODES)})"
        )
    ctx.internal.apply_cfg("eol_rx", arg)
    ctx.io.result(arg)
    return CmdResult.ok(value=arg)


def _handler_timestamps(ctx: PluginContext, args: str) -> CmdResult:
    return _cfg_toggle(ctx, args, "timestamps")


def _handler_request(ctx: PluginContext, args: str) -> CmdResult:
    """Toggle request_mode and optionally set the error-detection regex.

    Examples:
        /term.request                  query current state
        /term.request on               enable; reset err pattern to cfg default
        /term.request off              disable
        /term.request on err=^FAILED   enable + session-override the err pattern
        /term.request err=             disable error detection (session only)
        /term.request err=^FOO         set err pattern (session only)

    The persistent default lives in ``cfg.request_err_pattern``.  The
    ``err=`` token sets a **session-only** override stored in
    ``ctx.ns("flags")["request_err_pattern_override"]``; ``/term.request
    on`` without an ``err=`` clears that override so error detection
    returns to the cfg default.  This way "disable, then re-enable"
    actually re-enables.
    """
    state_token = None
    err_token = None  # None means "not specified"; "" means "user said err="
    for token in args.split():
        if token in ("on", "off"):
            state_token = token
        elif token.startswith("err="):
            err_token = token[len("err="):]
        else:
            return CmdResult.fail(
                msg=f"Unknown token: {token} (use on, off, or err=<regex>)"
            )

    flags = ctx.ns("flags")

    if err_token is not None:
        # Explicit session override (including "" to disable detection)
        flags["request_err_pattern_override"] = err_token
        if err_token:
            ctx.io.output(
                f"request_err_pattern = {err_token!r}  (session)", "green"
            )
        else:
            ctx.io.output(
                "request_err_pattern cleared -- error detection disabled  (session)",
                "yellow",
            )
    elif state_token == "on":
        # /term.request on (no err=) -> drop any session override so the
        # cfg default takes effect again.  Symmetric with how /term.request
        # off doesn't preserve a "previous" request_mode state -- 'on'
        # is a reset.
        if flags.pop("request_err_pattern_override", None) is not None:
            ctx.io.output(
                "request_err_pattern reset to cfg default  (session override cleared)",
                "dim",
            )

    if state_token is not None:
        return _cfg_toggle(ctx, state_token, "request_mode")
    # No state arg: still report state if err was changed, else query.
    if err_token is not None:
        current = bool(ctx.cfg.get("request_mode", False))
        return CmdResult.ok(value="on" if current else "off")
    return _cfg_toggle(ctx, args, "request_mode")


def _handler_send_bare_enter(ctx: PluginContext, args: str) -> CmdResult:
    return _cfg_toggle(ctx, args, "send_bare_enter")


def _handler_encoding(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the byte-decoding encoding (utf-8, latin-1, ...)."""
    name = args.strip()
    if not name:
        current = ctx.cfg.get("encoding", "utf-8")
        ctx.io.result(current)
        return CmdResult.ok(value=current)
    ctx.internal.apply_cfg("encoding", name)
    ctx.io.result(name)
    return CmdResult.ok(value=name)


# ── TUI-only placeholder (app.py overrides via register_hook) ───────────────


def _handler_line_no_placeholder(ctx: PluginContext, args: str) -> CmdResult:
    """Never invoked: the TUI replaces this handler via register_hook.

    In non-TUI environments dispatch's capability gate fails before
    reaching the handler because ``tui_mode`` is not provided.
    """
    return CmdResult.fail(msg="term.line_no handler not installed")


# ── /term.usb_db: report bundled USB-vendor database freshness ────────────


def _handler_usb_db(ctx: PluginContext, args: str) -> CmdResult:
    """Report metadata for the bundled USB vendor database.

    Reads the curated ``USB_VENDORS`` short-form table and the
    generated ``_usb_vendor_full`` module's metadata constants.  Local
    only -- never makes a network call.

    Output (kv pairs):

      curated         number of curated short-form entries
      full_table      number of canonical entries from upstream usb.ids
      generated       date the bundled file was last regenerated
      source          where the upstream file was fetched from
      path            location of the generated file on disk

    Followed by a one-line update hint pointing at the package upgrade
    path.  ``CmdResult.value`` is the full-table count so scripts can
    read it via .quiet/.silent.
    """
    from termapy.plugins import format_kv_lines
    from termapy.usb import USB_VENDORS

    rows: list[tuple[str, str]] = [
        ("curated", str(len(USB_VENDORS))),
    ]
    full_count = 0
    try:
        from termapy.usb import _vendors_full as _full

        full_count = len(_full.USB_VENDORS_FULL)
        rows.append(("full_table", str(full_count)))
        rows.append(("generated", str(getattr(_full, "GENERATED_DATE", "?"))))
        rows.append(("source", str(getattr(_full, "SOURCE_URL", "?"))))
        # Path on disk -- helpful when the user wants to inspect the
        # bundled module or sanity-check which copy is loaded.
        from pathlib import Path
        full_path = Path(_full.__file__).resolve()
        try:
            display_path = str(full_path.relative_to(Path.cwd()))
        except ValueError:
            # Generated file is outside cwd (typical for installed pkg
            # users); show absolute path.
            display_path = str(full_path)
        rows.append(("path", display_path))
    except ImportError:
        rows.append(("full_table", "(missing -- reinstall termapy)"))

    for line in format_kv_lines(rows):
        ctx.io.output_markup(line)
    # Update hint targets end users (PyPI installs) -- the bundled
    # data refreshes on each termapy release, so upgrading is the
    # right path for newer entries.  Maintainers update via
    # scripts/refresh_usb_ids.py during release prep, but that's a
    # repo-level workflow, not exposed to package users.
    ctx.io.output_markup("")
    ctx.io.output_markup(
        "  [dim]To update:[/]   upgrade termapy (e.g. "
        "[cyan]uv tool upgrade termapy[/] or [cyan]pip install -U termapy[/])"
    )
    return CmdResult.ok(value=str(full_count))


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
        return CmdResult.fail(msg=f"Usage: {ctx.prefix}term.log <text>")
    ctx.io.log("#", text)
    # Return the logged text so scripts can confirm what was written.
    return CmdResult.ok(value=text)


# ── /term.info: snapshot ────────────────────────────────────────────────────


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Snapshot the current state of every /term.* toggle."""
    from termapy.plugins import format_kv_lines

    flags = ctx.ns("flags")
    rows = [
        ("echo", "on" if flags.get("echo") else "off"),
        ("echo_repl", "on" if flags.get("echo_repl") else "off"),
        ("color", "on" if flags.get("color", True) else "off"),
        ("output", str(flags.get("output_level", "normal"))),
        ("hex", "on" if flags.get("hex") else "off"),
        ("line_no", "on" if flags.get("line_no") else "off"),
        ("eol", _EOL_LABELS.get(ctx.cfg.get("eol", "\r"), repr(ctx.cfg.get("eol", "\r")))),
        ("eol_rx", str(ctx.cfg.get("eol_rx", "auto"))),
        ("eol_markers", "on" if ctx.cfg.get("eol_markers") else "off"),
        ("timestamps", "on" if ctx.cfg.get("timestamps") else "off"),
        ("send_bare_enter", "on" if ctx.cfg.get("send_bare_enter") else "off"),
        ("encoding", str(ctx.cfg.get("encoding", "utf-8"))),
    ]
    for line in format_kv_lines(rows):
        ctx.io.output_markup(line)
    # Return the snapshot as a newline-joined "key=value" payload so
    # scripts can grep / parse it.
    return CmdResult.ok(value="\n".join(f"{k}={v}" for k, v in rows))


# ── /term root handler: list subcommands when called bare ───────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """When /term is called bare, show the subcommand landscape."""
    arg = args.strip()
    if arg:
        p = ctx.prefix
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
            help="Local echo of device commands sent to the wire (cfg echo).",
            long_help=(
                "Local echo of DEVICE commands -- the bare lines and\n"
                "{prefix}term.send text that go out on the wire.  Persisted\n"
                "via cfg echo.\n"
                "\n"
                "termapy splits local echo in two: this ({prefix}term.echo)\n"
                "for device commands, and {prefix}term.echo_repl for\n"
                "REPL/slash commands ({prefix}cfg, {prefix}help, ...).\n"
                "See also: {prefix}term.echo_repl."
            ),
            handler=_handler_echo,
        ),
        "echo_repl": Command(
            args="{on|off}",
            help="Local echo of REPL/slash commands like /cfg (session only).",
            long_help=(
                "Local echo of REPL/slash commands ({prefix}cfg, {prefix}help,\n"
                "...).  Session-only (no cfg key); default on for the TUI,\n"
                "off for the CLI (whose OS terminal already shows the typed\n"
                "line).\n"
                "\n"
                "The sibling of {prefix}term.echo, which echoes DEVICE\n"
                "commands sent to the wire.  See also: {prefix}term.echo."
            ),
            handler=_handler_echo_repl,
        ),
        "color": Command(
            args="{on|off}",
            help="Toggle rendering of device ANSI color (SGR).",
            long_help=(
                "Render ANSI color (SGR) from device output.  On by\n"
                "default; off strips color so output is plain (and, in the\n"
                "CLI, clean for piping -- the same effect as --no-color).\n"
                "Works in both the TUI and CLI.  Legacy alias: {prefix}color."
            ),
            handler=_handler_color,
        ),
        "send": Command(
            args="<text>",
            help="Send literal text to the serial port (with line ending).",
            long_help=(
                "The slash-command equivalent of typing a bare line in the\n"
                "terminal.  The dispatcher's fallthrough branch rewrites\n"
                "bare user input to {prefix}term.send <text> so every\n"
                "action has a discoverable command name -- helps LLMs,\n"
                "logging, and {prefix}help / {prefix}search.\n"
                "\n"
                "Literal send: no template/var expansion, no echo.  When\n"
                "the user types a bare line, the dispatcher applies\n"
                "transforms BEFORE rewriting to {prefix}term.send, so the\n"
                "user-facing behavior is unchanged.  Direct callers (LLMs,\n"
                "scripts) get predictable literal-bytes semantics.\n"
                "\n"
                "For raw bytes WITHOUT a line ending, use {prefix}raw."
            ),
            handler=_handler_send,
            raw_args=True,
        ),
        "line_no": Command(
            args="{on|off}",
            help="Toggle line numbers in serial output (TUI only).",
            handler=_handler_line_no_placeholder,
            needs=CapabilitySet(tui_mode=True),
        ),
        "line_endings": Command(
            args="{on|off}",
            help="Hidden alias for /term.eol.markers.",
            handler=_handler_eol_markers,
            hidden=True,
        ),
        "eol": Command(
            args="{cr|lf|crlf|none}",
            help="Show or set the line ending sent with commands (cr/lf/crlf/none).",
            long_help=(
                "Sets the line ending appended to every command sent to the\n"
                "device.  Tokens: cr (\\r), lf (\\n), crlf (\\r\\n), none.\n"
                "Bare {prefix}term.eol reports the current ending.\n"
                "\n"
                "This is a session override (in-memory, like the other\n"
                "{prefix}term.* toggles).  To persist, use {prefix}cfg "
                "eol or the Config Editor.\n"
                "\n"
                "See also: {prefix}term.eol.rx (receive-side newline) and\n"
                "{prefix}term.eol.markers (show \\r \\n markers for debugging)."
            ),
            handler=_handler_eol,
            sub_commands={
                "rx": Command(
                    args="{auto|cr|lf|crlf}",
                    help="Show or set how received output is split into lines (auto/cr/lf/crlf).",
                    long_help=(
                        "Receive-side counterpart of {prefix}term.eol -- controls\n"
                        "how incoming device output is split into lines (mirrors\n"
                        "TeraTerm's Receive newline):\n"
                        "\n"
                        "  auto   CR, LF, and CRLF all end a line (default;\n"
                        "         works for any device)\n"
                        "  cr     only CR ends a line\n"
                        "  lf     only LF ends a line\n"
                        "  crlf   only the CRLF pair ends a line\n"
                        "\n"
                        "Bare {prefix}term.eol.rx reports the current mode.  This is\n"
                        "a session override (in-memory); persist with {prefix}cfg\n"
                        "eol_rx or the Config Editor."
                    ),
                    handler=_handler_eol_rx,
                ),
                "markers": Command(
                    args="{on|off}",
                    help="Toggle dim visible \\r \\n markers in received output.",
                    long_help=(
                        "Debug aid: show the line-ending bytes inline as dim\n"
                        "\\r \\n markers so you can see exactly how the device\n"
                        "terminates lines.  Distinct from {prefix}term.eol (which\n"
                        "SETS the sent ending) and {prefix}term.eol.rx (which\n"
                        "controls how received output is split).  Persisted via\n"
                        "cfg eol_markers.  Legacy aliases: {prefix}term.line_endings,\n"
                        "{prefix}show_line_endings."
                    ),
                    handler=_handler_eol_markers,
                ),
            },
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
            needs=CapabilitySet(interactive=True),  # legacy alias
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
        "request": Command(
            args="{on|off}",
            help="Toggle request/response mode for bare device commands.",
            long_help=(
                "When on, every bare device command (a line that doesn't\n"
                "start with {prefix}) is sent through the request/response\n"
                "executor and its reply is wrapped in a JSON envelope:\n"
                "\n"
                "  {\"cmd\":\"<text>\",\"success\":true,\"error\":\"\",\n"
                "   \"elapsed_s\":0.065,\"result\":\"<response text>\"}\n"
                "\n"
                "The envelope is rendered to the terminal as a single\n"
                "line and surfaces in CmdResult.value (visible to MCP\n"
                "clients and scripts).\n"
                "\n"
                "Input is symmetric: a JSON object with a string ``cmd``\n"
                "field is unwrapped, so callers can send either plain\n"
                "text (``get_voltage``) or JSON ({\"cmd\":\"get_voltage\"}).\n"
                "Malformed JSON or JSON without a ``cmd`` key falls back\n"
                "to plain-text send for graceful behavior.\n"
                "\n"
                "Profile-mapped commands keep their declared\n"
                "response.format -- more-specific wins.  Use this for\n"
                "ad-hoc exploration of devices, or as a baseline before\n"
                "authoring a profile."
            ),
            handler=_handler_request,
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
        "usb_db": Command(
            help="Report freshness of the bundled USB vendor database.",
            long_help=(
                "Termapy ships a USB Vendor ID lookup table so it can name the\n"
                "silicon vendor for any USB-serial port -- even when the device's\n"
                "manufacturer string is missing or set to a generic driver name\n"
                "(``Microsoft`` for ``usbser.sys``, etc.).  This command reports\n"
                "what's bundled and how fresh it is.  Local read only -- never\n"
                "makes a network call.\n"
                "\n"
                "Fields:\n"
                "  curated     Hand-picked short-form names (FTDI, SiLabs, ...)\n"
                "              tuned for narrow column display.  Tried first by\n"
                "              vendor_for() so common chips keep their short names.\n"
                "  full_table  Canonical USB-IF assignments from the upstream\n"
                "              ``usb.ids`` file at linux-usb.org / its GitHub\n"
                "              mirror.  Used as a fallback when the curated\n"
                "              table doesn't cover a VID.\n"
                "  generated   Local timestamp from the last refresh run.\n"
                "  source      URL the bundled data was fetched from.\n"
                "  path        Generated module on disk.\n"
                "\n"
                "The bundled table is regenerated on every termapy release\n"
                "(release_prep step 6 pulls upstream usb.ids), so to get newer\n"
                "vendor entries: upgrade the termapy package itself.\n"
                "\n"
                "    uv tool upgrade termapy\n"
                "    pip install --upgrade termapy"
            ),
            handler=_handler_usb_db,
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
