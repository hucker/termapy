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

from termapy.defaults import JSON_STYLES, cfg_value
from termapy.plugins import (
    OUTPUT_LEVELS,
    CapabilitySet,
    CmdResult,
    Command,
    UsageError,
    parse_output_level,
)
from termapy.plugins.params import ParamSpec
from termapy.scripting import (
    SETTING_QUERY,
    SETTING_TOGGLE,
    next_in_cycle,
    parse_bool,
    parse_bool_setting,
)

if TYPE_CHECKING:
    from termapy.plugins import KvRow, PluginContext


# ── Runtime-flag toggles (session-scoped via ctx.ns("flags")) ───────────────


def _bool_setting(ctx: PluginContext, args: str, flag_name: str) -> CmdResult:
    """Query, set, or toggle a session-scoped boolean flag.

    Bare invocation QUERIES (shows state, mutates nothing); ``on``/``off``
    (any parse_bool token) sets; the explicit verb ``toggle`` flips;
    anything else errors.  A setting command never mutates on a bare
    invocation -- see the rule in CLAUDE.md.
    """
    try:
        action = parse_bool_setting(args)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    flags = ctx.ns("flags")
    if action is SETTING_QUERY:
        pass
    elif action is SETTING_TOGGLE:
        flags[flag_name] = not flags.get(flag_name, False)
    else:
        flags[flag_name] = action
    state = "on" if flags.get(flag_name) else "off"
    ctx.io.result(state)
    return CmdResult.ok(value=state)


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_echo(ctx: PluginContext, args: str) -> CmdResult:
    """Query (bare), set (on/off) or flip (toggle) device echo: what is sent to the wire shows in the pane (cfg ``echo``)."""
    # Device-command echo: echo of what's sent to the wire (bare commands
    # + /term.send).  Backed by cfg["echo"]; the flag is the session
    # state seeded from it.
    return _bool_setting(ctx, args, "echo")


def _handler_echo_repl(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle REPL echo: slash commands shown as typed, a session flag with a per-host default."""
    # REPL/slash-command echo (/cfg, /help, ...).  Distinct from device
    # echo; a session-only flag (no cfg key), default per-host (on for the
    # TUI, off for CLI/MCP -- see TerminalHost._init_flags).
    return _bool_setting(ctx, args, "echo_repl")


def _handler_color(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle color in the output, termapy's own and the device's ANSI: a session flag."""
    # Session flag the frontends read at each write: the TUI's pane and the
    # CLI's console go plain when it is off, and both strip the device's
    # SGR escapes before rendering.  Default off under --no-color.
    return _bool_setting(ctx, args, "color")


def _handler_line_no(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle line numbers in the pane (cfg ``line_no``)."""
    # Line numbers in the serial pane.  Backed by cfg["line_no"]; the flag
    # is the session state seeded from it, and what the TUI renders from
    # and /term.info reports (review 2026-10-15, D4: three stores before).
    return _bool_setting(ctx, args, "line_no")


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
        raise UsageError()
    try:
        # A device line, so it goes out paced (``ctx.serial.send``).
        ctx.serial.send(args)
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


# The keys a control character is typed with, in byte order: Ctrl+@ is 00,
# Ctrl+A..Z are 01..1A, Ctrl+[ \ ] ^ _ are 1B..1F (ASCII 64..95, less 64).
_CTRL_KEYS = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_"
# Names for keys nobody remembers as a Ctrl chord: esc is Ctrl+[.
_CTRL_NAMES = {"ESC": "["}


def _handler_ctrl(ctx: PluginContext, args: str) -> CmdResult:
    """Send one control character: ``/term.ctrl c`` is Ctrl-C, byte 03; the value is ``^C``.

    ``esc`` names Ctrl+[ (1B).  A raw byte, as ``/raw`` sends: no line
    ending, never paced.  The TUI's Ctrl+C with nothing selected and Ctrl+D
    on an empty prompt dispatch it, so a key and a script or agent send the
    same way.
    """
    typed = ctx.arg("key")
    key = _CTRL_NAMES.get(typed.upper(), typed.upper())
    if len(key) != 1 or key not in _CTRL_KEYS:
        return CmdResult.fail(
            msg=f"Invalid control key: {typed} (use a letter A-Z, one of @ [ \\ ] ^ _, or esc)"
        )
    try:
        ctx.serial.write(bytes([_CTRL_KEYS.index(key)]))
    # The same boundary catch as /term.send: a failed write is the answer.
    except (OSError, Exception) as e:  # noqa: BLE001  -- boundary catch
        return CmdResult.fail(msg=f"Send error: {e}")
    return CmdResult.ok(value=f"^{key}")


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
    if arg.lower() == "cycle":
        level = next_in_cycle(flags.get("output_level", "normal"), list(OUTPUT_LEVELS))
    else:
        level = parse_output_level(arg)
        if level is None:
            return CmdResult.fail(msg=f"Unknown level: {arg} (use {'/'.join(OUTPUT_LEVELS)}/cycle)")
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
            f"  Note: {p}term.verbose is legacy; use {p}term.output (verbose|normal).",
            "yellow",
        )
    body = args.strip()
    if not body:
        target = "term.output"
    else:
        try:
            verbose = parse_bool(body, what="value")
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        target = "term.output verbose" if verbose else "term.output normal"
    return ctx.internal.dispatch(target)


def _handler_hex(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle the hex view of received bytes."""
    return _bool_setting(ctx, args, "hex")


# ── Session cfg toggles (apply_cfg; only the editor's Save persists) ─────────


def _cfg_toggle(ctx: PluginContext, args: str, key: str) -> CmdResult:
    """Query, set, or toggle a config key that's read at display time.

    Bare invocation QUERIES (shows state, mutates nothing); ``on``/``off``
    sets; the explicit verb ``toggle`` flips; anything else errors.
    Applies via ``ctx.internal.apply_cfg`` for the session (only when the
    value actually changes); nothing here writes to disk.  The config
    editor's Save (``/cfg.edit``) is the only path that persists.
    """
    try:
        action = parse_bool_setting(args)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    current = bool(ctx.cfg.get(key, False))
    if action is SETTING_QUERY:
        new = current
    elif action is SETTING_TOGGLE:
        new = not current
    else:
        new = action
    if new != current:
        ctx.internal.apply_cfg(key, new)
    state = "on" if new else "off"
    ctx.io.result(state)
    return CmdResult.ok(value=state)


def _handler_eol_markers(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle dim CR/LF markers on received lines (cfg ``eol_markers``)."""
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
    use, and the same session-only change ``/cfg eol`` makes.  Only the
    config editor's Save (``/cfg.edit``) persists it.
    """
    arg = args.strip().lower()
    if not arg:
        current = cfg_value(ctx.cfg, "eol")
        label = _EOL_LABELS.get(current, repr(current))
        ctx.io.result(label)
        return CmdResult.ok(value=label)
    if arg == "cycle":
        cur = _EOL_LABELS.get(cfg_value(ctx.cfg, "eol"), "cr")
        arg = next_in_cycle(cur, list(_EOL_TOKENS))
    if arg not in _EOL_TOKENS:
        return CmdResult.fail(msg=f"Unknown line ending: {arg} (use {'/'.join(_EOL_TOKENS)}/cycle)")
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
        current = cfg_value(ctx.cfg, "eol_rx")
        ctx.io.result(current)
        return CmdResult.ok(value=current)
    if arg == "cycle":
        arg = next_in_cycle(cfg_value(ctx.cfg, "eol_rx"), list(_RX_NEWLINE_MODES))
    if arg not in _RX_NEWLINE_MODES:
        return CmdResult.fail(
            msg=f"Unknown receive newline: {arg} (use {'/'.join(_RX_NEWLINE_MODES)}/cycle)"
        )
    ctx.internal.apply_cfg("eol_rx", arg)
    ctx.io.result(arg)
    return CmdResult.ok(value=arg)


def _handler_json(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set how JSON prints: ``compact``, ``indent`` or ``color``.

    An enum setting: bare reports the style, a style sets it for the
    session through ``apply_cfg``, ``cycle`` advances, anything else is
    refused.  Applies to every JSON termapy prints (``/term.request``
    envelopes, ``--json``, the request echo); MCP returns the envelope as
    data and is unaffected.
    """
    arg = args.strip().lower()
    if not arg:
        current = cfg_value(ctx.cfg, "json_style")
        ctx.io.result(current)
        return CmdResult.ok(value=current)
    if arg == "cycle":
        arg = next_in_cycle(cfg_value(ctx.cfg, "json_style"), JSON_STYLES)
    if arg not in JSON_STYLES:
        return CmdResult.fail(msg=f"Unknown JSON style: {arg} (use {'/'.join(JSON_STYLES)}/cycle)")
    ctx.internal.apply_cfg("json_style", arg)
    ctx.io.result(arg)
    return CmdResult.ok(value=arg)


def _handler_timestamps(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle timestamps on received lines (cfg ``timestamps``)."""
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
    state: bool | None = None  # the level a vocabulary token named; None for toggle
    err_token = None  # None means "not specified"; "" means "user said err="
    for token in args.split():
        if token.startswith("err="):
            err_token = token[len("err=") :]
        elif token.lower() == SETTING_TOGGLE:
            state_token, state = token, None
        else:
            # Any vocabulary token sets, toggle flips -- _cfg_toggle
            # handles all three (the one-vocabulary rule in CLAUDE.md).
            try:
                state = parse_bool(token)
            except ValueError:
                return CmdResult.fail(
                    msg=f"Unknown token: {token} (use on, off, toggle, or err=<regex>)"
                )
            state_token = token

    flags = ctx.ns("flags")

    if err_token is not None:
        # Explicit session override (including "" to disable detection)
        flags["request_err_pattern_override"] = err_token
        if err_token:
            ctx.io.output(f"request_err_pattern = {err_token!r}  (session)", "green")
        else:
            ctx.io.output(
                "request_err_pattern cleared -- error detection disabled  (session)",
                "yellow",
            )
    elif state is True:
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
        current = bool(cfg_value(ctx.cfg, "request_mode"))
        return CmdResult.ok(value="on" if current else "off")
    return _cfg_toggle(ctx, args, "request_mode")


def _handler_send_bare_enter(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle whether a bare Enter sends the line ending alone (cfg ``send_bare_enter``)."""
    return _cfg_toggle(ctx, args, "send_bare_enter")


def _handler_control_keys(ctx: PluginContext, args: str) -> CmdResult:
    """Query, set or toggle whether the TUI's Ctrl+C, Ctrl+D, Ctrl+Z and Escape reach the device (cfg ``control_keys``)."""
    return _cfg_toggle(ctx, args, "control_keys")


def _handler_encoding(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the byte-decoding encoding (utf-8, latin-1, ...)."""
    name = args.strip()
    if not name:
        current = cfg_value(ctx.cfg, "encoding")
        ctx.io.result(current)
        return CmdResult.ok(value=current)
    ctx.internal.apply_cfg("encoding", name)
    ctx.io.result(name)
    return CmdResult.ok(value=name)


# ── /term.usb_db: report bundled USB-vendor database freshness ────────────


def _handler_usb_db(ctx: PluginContext, args: str) -> CmdResult:
    """Report metadata for the bundled USB vendor database.

    Reads ``usb.vendor_table_facts()``: the curated ``USB_VENDORS``
    table's size and the generated module's metadata constants.  Local
    only -- never makes a network call.

    Output (kv pairs):

      curated         number of curated short-form entries
      full_table      number of canonical entries from upstream usb.ids
      generated       date the bundled file was last regenerated
      source          where the upstream file was fetched from
      path            location of the generated file on disk

    Followed by a one-line update hint pointing at the package upgrade
    path.  A record answer (registry ``record-one-format``): the facts
    are ``data``, with ``None`` for what a missing generated module
    cannot say.  ``CmdResult.value`` is the full-table count so scripts
    can read it via .quiet/.silent.
    """
    from termapy.usb import vendor_table_facts

    data = vendor_table_facts()
    rows: list[KvRow | None] = [("curated", str(data["curated"]))]
    if data["full_table"] is None:
        rows.append(("full_table", "(missing -- reinstall termapy)"))
    else:
        rows.append(("full_table", str(data["full_table"])))
        rows.append(("generated", str(data["generated"] or "?")))
        rows.append(("source", str(data["source"] or "?")))
        # Path on disk -- helpful when the user wants to inspect the
        # bundled module or sanity-check which copy is loaded.
        rows.append(("path", str(data["path"])))

    # Update hint targets end users (PyPI installs) -- the bundled
    # data refreshes on each termapy release, so upgrading is the
    # right path for newer entries.  Maintainers update via
    # scripts/refresh_usb_ids.py during release prep, but that's a
    # repo-level workflow, not exposed to package users.
    notes = (
        ("", ""),
        (
            "To update: upgrade termapy (e.g. uv tool upgrade termapy or pip install -U termapy)",
            "dim",
        ),
    )
    return CmdResult.record(data, rows, value=str(data["full_table"] or 0), notes=notes)


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
        raise UsageError()
    ctx.io.log("#", text)
    # Return the logged text so scripts can confirm what was written.
    return CmdResult.ok(value=text)


# ── /term.info: snapshot ────────────────────────────────────────────────────


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Snapshot the current state of every /term.* toggle.

    A record answer (registry ``record-one-format``): the toggles are
    ``data`` as booleans, and read on/off in the prose.
    """
    flags = ctx.ns("flags")
    eol = cfg_value(ctx.cfg, "eol")
    data = {
        "echo": bool(flags.get("echo")),
        "echo_repl": bool(flags.get("echo_repl")),
        "color": bool(flags.get("color", True)),
        "output": str(flags.get("output_level", "normal")),
        "hex": bool(flags.get("hex")),
        "line_no": bool(flags.get("line_no")),
        "eol": _EOL_LABELS.get(eol, repr(eol)),
        "eol_rx": str(cfg_value(ctx.cfg, "eol_rx")),
        "eol_markers": bool(cfg_value(ctx.cfg, "eol_markers")),
        "timestamps": bool(cfg_value(ctx.cfg, "timestamps")),
        "send_bare_enter": bool(cfg_value(ctx.cfg, "send_bare_enter")),
        "control_keys": bool(cfg_value(ctx.cfg, "control_keys")),
        "encoding": str(cfg_value(ctx.cfg, "encoding")),
    }
    rows = [
        (key, ("on" if value else "off") if isinstance(value, bool) else value)
        for key, value in data.items()
    ]
    # The value is the snapshot as newline-joined "key=value" so scripts
    # can grep / parse it.
    snapshot = "\n".join(f"{key}={text}" for key, text in rows)
    return CmdResult.record(data, rows, value=snapshot)


# ── /term root handler: list subcommands when called bare ───────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """When /term is called bare, show the subcommand landscape."""
    arg = args.strip()
    if arg:
        p = ctx.prefix
        return CmdResult.fail(msg=f"Usage: {p}term.<subcommand>  (try {p}term.info)")
    return ctx.dispatch("help term")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="terminal",
    name="term",
    help="Terminal display / session toggles (echo, line_no, timestamps, ...).",
    long_help=(
        "Runtime display and session toggles.  Collapses what used to be\n"
        "scattered top-level commands (/echo, /line_no, /show_line_endings)\n"
        "under one namespace and adds runtime access to a handful of\n"
        "config-key toggles that previously required {prefix}cfg, plus the\n"
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
            args="{on|off|toggle}",
            help="Local echo of device commands sent to the wire (cfg echo).",
            long_help=(
                "Local echo of DEVICE commands -- the bare lines and\n"
                "{prefix}term.send text that go out on the wire.\n"
                "\n"
                "termapy splits local echo in two: this ({prefix}term.echo)\n"
                "for device commands, and {prefix}term.echo_repl for\n"
                "REPL/slash commands ({prefix}cfg, {prefix}help, ...).\n"
                "Unlike {prefix}term.echo_repl, device echo is NOT muted\n"
                "inside a script -- a tutorial script still shows the\n"
                "commands it sends to the device.\n"
                "See also: {prefix}term.echo_repl."
            ),
            handler=_handler_echo,
            cfg={"echo": "seeds the session flag at startup"},
            state=True,
        ),
        "echo_repl": Command(
            args="{on|off|toggle}",
            help="Local echo of REPL/slash commands like {prefix}cfg (session only).",
            long_help=(
                "Local echo of REPL/slash commands ({prefix}cfg, {prefix}help,\n"
                "...).  Session-only (no cfg key); default on for the TUI,\n"
                "off for the CLI (whose OS terminal already shows the typed\n"
                "line).\n"
                "\n"
                "In a script this starts OFF, so a script's slash-commands\n"
                "stay quiet (e.g. a {prefix}print banner).  A script that\n"
                "wants to show its commands re-enables it with\n"
                "{prefix}term.echo_repl on; your interactive value is\n"
                "restored when the outermost script ends.\n"
                "\n"
                "The sibling of {prefix}term.echo, which echoes DEVICE\n"
                "commands sent to the wire.  See also: {prefix}term.echo."
            ),
            handler=_handler_echo_repl,
            state=True,
        ),
        "color": Command(
            args="{on|off|toggle}",
            help="Toggle color in the output: termapy's own and the device's ANSI.",
            long_help=(
                "Color in the output pane and the CLI console: termapy's own\n"
                "markup and ANSI color (SGR) from the device.  On by default;\n"
                "off renders everything plain from the next line on (bold,\n"
                "underline and links stay) and strips the device's escapes,\n"
                "the same as --no-color, which starts the CLI with it off.\n"
                "Works in both the TUI and CLI.  Legacy alias: {prefix}color."
            ),
            handler=_handler_color,
            state=True,
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
            cfg={
                "eol": "appended to the text",
                "encoding": "encodes the text",
                "tx_char_delay_ms": "paces the characters",
                "tx_line_delay_ms": "pauses after the line",
            },
            raw_args=True,
            needs=CapabilitySet.SERIAL_CONNECTED,
        ),
        "ctrl": Command(
            help="Send a control character to the device (c is Ctrl-C, byte 03).",
            long_help=(
                "Sends the one byte a Ctrl+<key> stands for: {prefix}term.ctrl c is\n"
                "Ctrl-C (03), d is Ctrl-D (04), esc is Esc (1B, also [).  Letters\n"
                "A-Z and @ [ \\ ] ^ _ are the keys, and esc; case does not matter.\n"
                "A raw byte, as {prefix}raw sends: no line ending, never paced.\n"
                "\n"
                "In the TUI, Ctrl+C with no text selected sends Ctrl-C, Ctrl+D\n"
                "and Ctrl+Z on an empty prompt send Ctrl-D and Ctrl-Z, and\n"
                "Escape with nothing running, on an empty prompt, sends Esc;\n"
                "with text selected, Ctrl+C copies.  {prefix}term.control_keys off keeps\n"
                "the keys termapy's.  A script, a button or an MCP agent\n"
                "sends one with this command: Ctrl-C stops a running\n"
                "MicroPython program.\n"
                "\n"
                "Example:\n"
                "  {prefix}term.ctrl c"
            ),
            handler=_handler_ctrl,
            params=[
                ParamSpec(
                    "key",
                    "str",
                    positional=True,
                    required=True,
                    hint="<key>",
                    help="the key held with Ctrl: a letter, or @ [ \\ ] ^ _; or esc",
                ),
            ],
            needs=CapabilitySet.SERIAL_CONNECTED,
        ),
        "line_no": Command(
            args="{on|off|toggle}",
            help="Toggle line numbers in serial output (TUI only).",
            handler=_handler_line_no,
            cfg={"line_no": "seeds the session flag at startup"},
            needs=CapabilitySet.TUI_MODE,
            state=True,
        ),
        "line_endings": Command(
            args="{on|off|toggle}",
            help="Hidden alias for {prefix}term.eol.markers.",
            handler=_handler_eol_markers,
            cfg={"eol_markers": "set for the session; bare shows it"},
            hidden=True,
        ),
        "eol": Command(
            args="{cr|lf|crlf|none|cycle}",
            help="Show or set the line ending sent with commands (cr/lf/crlf/none).",
            long_help=(
                "Sets the line ending appended to every command sent to the\n"
                "device.  Tokens: cr (\\r), lf (\\n), crlf (\\r\\n), none.\n"
                "Bare {prefix}term.eol reports the current ending.\n"
                "\n"
                "See also: {prefix}term.eol.rx (receive-side newline) and\n"
                "{prefix}term.eol.markers (show \\r \\n markers for debugging)."
            ),
            handler=_handler_eol,
            cfg={"eol": "set for the session; bare shows it"},
            sub_commands={
                "rx": Command(
                    args="{auto|cr|lf|crlf|cycle}",
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
                        "Bare {prefix}term.eol.rx reports the current mode."
                    ),
                    handler=_handler_eol_rx,
                    cfg={"eol_rx": "set for the session; bare shows it"},
                    state=True,
                ),
                "markers": Command(
                    args="{on|off|toggle}",
                    help="Toggle dim visible \\r \\n markers in received output.",
                    long_help=(
                        "Debug aid: show the line-ending bytes inline as dim\n"
                        "\\r \\n markers so you can see exactly how the device\n"
                        "terminates lines.  Distinct from {prefix}term.eol (which\n"
                        "SETS the sent ending) and {prefix}term.eol.rx (which\n"
                        "controls how received output is split).  Legacy aliases:\n"
                        "{prefix}term.line_endings, {prefix}show_line_endings."
                    ),
                    handler=_handler_eol_markers,
                    cfg={"eol_markers": "set for the session; bare shows it"},
                    state=True,
                ),
            },
            state=True,
        ),
        "output": Command(
            args="{silent|quiet|normal|verbose|cycle}",
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
            state=True,
        ),
        "verbose": Command(
            args="{on|off}",
            help="Legacy alias for {prefix}term.output (verbose|normal).",
            handler=_handler_verbose_legacy,
            hidden=True,
            needs=CapabilitySet.INTERACTIVE,  # legacy alias
        ),
        "timestamps": Command(
            args="{on|off|toggle}",
            help="Toggle [HH:MM:SS.mmm] timestamp prefix on each line.",
            handler=_handler_timestamps,
            cfg={"timestamps": "set for the session; bare shows it"},
            state=True,
        ),
        "hex": Command(
            args="{on|off|toggle}",
            help="Toggle hex display of incoming bytes.",
            long_help=(
                "Shows received bytes as hex rows: a row ends after each\n"
                "0A, or at 16 bytes. Rows are built from the bytes as they\n"
                "arrived, so CR, LF, backspace and bytes the encoding cannot\n"
                "decode all show as themselves, and a clear-screen escape is\n"
                "shown rather than obeyed.\n"
                "\n"
                "Display only: the session log, {prefix}expect and text\n"
                "captures still get the decoded lines, and sent text is not\n"
                "shown in hex. {prefix}proto.hex sets the same flag."
            ),
            handler=_handler_hex,
            cfg={"hex": "seeds the session flag at startup"},
            state=True,
        ),
        "request": Command(
            args="{on|off|toggle}",
            help="Toggle the session's JSON request/response mode.",
            long_help=(
                "The session's structured dial: when on, EVERYTHING\n"
                "answers in JSON -- the device/termapy demarcation is\n"
                "deliberately invisible.\n"
                "\n"
                "Every answer -- device exchange or termapy command --\n"
                "uses ONE envelope shape:\n"
                "\n"
                '  {"cmd":"...","success":true,"error":"",\n'
                '   "value":"...","data":null,"output_lines":[],\n'
                '   "elapsed_s":0.065}\n'
                "\n"
                "One user command = one envelope: a command that runs\n"
                "another internally ({prefix}mem -> {prefix}mem.info)\n"
                "answers with a single envelope, the inner prose in\n"
                "output_lines -- never an envelope inside an envelope.\n"
                "\n"
                "Bare device commands (a line that doesn't start with\n"
                "{prefix}) go through the request/response executor:\n"
                "the reply text is ``value``, and a reply that parses as\n"
                "a JSON object or array also lands in ``data`` (the\n"
                "json-to-json path).  termapy commands carry structured\n"
                "``data`` when they have it ({prefix}var,\n"
                "{prefix}port.list) or their rendered answer in\n"
                "``output_lines`` when they don't ({prefix}help).\n"
                "\n"
                "In a normal-mode session the per-call form of the same\n"
                "thing is ``<cmd> --json``.  Envelopes print one per line;\n"
                "{prefix}term.json indent (or color) pretty-prints them.\n"
                "\n"
                "Input is symmetric: a JSON object with a string ``cmd``\n"
                "field is unwrapped, so callers can send either plain\n"
                'text (``get_voltage``) or JSON ({"cmd":"get_voltage"}).\n'
                "Malformed JSON or JSON without a ``cmd`` key falls back\n"
                "to plain-text send for graceful behavior.\n"
                "\n"
                "Profile-mapped commands keep their declared\n"
                "response.format -- more-specific wins.  Use this for\n"
                "ad-hoc exploration of devices, or as a baseline before\n"
                "authoring a profile."
            ),
            handler=_handler_request,
            cfg={"request_mode": "set for the session; bare shows it"},
            state=True,
        ),
        "json": Command(
            args="{compact|indent|color|cycle}",
            help="Show or set how JSON prints: one line, indented, or indented and colored.",
            long_help=(
                "How every JSON answer termapy prints looks -- the\n"
                "{prefix}term.request envelopes, ``<cmd> --json``, and the\n"
                "request echo:\n"
                "\n"
                "  compact  one line per answer (default; for consumers\n"
                "           that read stdout line by line)\n"
                "  indent   indented, two spaces per level\n"
                "  color    indented and highlighted\n"
                "\n"
                'The request echo ({"cmd": ...}) stays on one line in\n'
                "every style.  MCP gets the envelope as data, so the style\n"
                "does not touch it."
            ),
            handler=_handler_json,
            cfg={"json_style": "set for the session; bare shows it"},
            state=True,
        ),
        "send_bare_enter": Command(
            args="{on|off|toggle}",
            help="Send line ending on empty Enter (for press-to-continue prompts).",
            handler=_handler_send_bare_enter,
            cfg={"send_bare_enter": "set for the session; bare shows it"},
            state=True,
        ),
        "control_keys": Command(
            args="{on|off|toggle}",
            help="Let Ctrl+C, Ctrl+D, Ctrl+Z and Escape reach the device in the TUI (on by default).",
            long_help=(
                "On, a key the prompt has no use for goes to the device: Ctrl+C\n"
                "with no text selected sends Ctrl-C, Ctrl+D and Ctrl+Z on an\n"
                "empty prompt send Ctrl-D and Ctrl-Z, and Escape with nothing\n"
                "running, on an empty prompt, sends Esc.  Off, Ctrl+C only\n"
                "copies, Ctrl+D only deletes, Ctrl+Z does nothing, and Escape\n"
                "only stops and clears: nothing reaches the device, for one a\n"
                "stray control byte would upset.  {prefix}term.ctrl sends one\n"
                "either way.\n"
                "\n"
                "This command sets it for the session.  To keep the keys\n"
                'termapy\'s in every session, set "control_keys": false in the\n'
                "config file."
            ),
            handler=_handler_control_keys,
            cfg={"control_keys": "set for the session; bare shows it"},
            state=True,
        ),
        "encoding": Command(
            args="{name}",
            help="Show or set byte-decoding encoding (utf-8, latin-1, ascii, cp437).",
            handler=_handler_encoding,
            cfg={"encoding": "set for the session; bare shows it"},
            state=True,
        ),
        "info": Command(
            help="Snapshot the state of every {prefix}term.* toggle.",
            handler=_handler_info,
            cfg={
                "eol": "in the snapshot",
                "eol_rx": "in the snapshot",
                "eol_markers": "in the snapshot",
                "timestamps": "in the snapshot",
                "send_bare_enter": "in the snapshot",
                "control_keys": "in the snapshot",
                "encoding": "in the snapshot",
            },
            shows=True,
            state=True,
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
            state=True,
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
