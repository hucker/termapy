"""CLI serial terminal - no Textual dependency.

Provides a plain-text interactive terminal using SerialEngine, ReplEngine,
and CaptureEngine. Reads from stdin, writes to stdout, serial I/O on a
background thread.  The interactive prompt (prompt_toolkit) is
``termapy.cli_prompt``, imported only when a prompt is built, so a
one-shot (``-e``, ``--run``) does not load it.

Usage:
    termapy --cli [config] [--no-color]
"""

from __future__ import annotations

import sys
import textwrap
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.capture import CaptureEngine, format_capture_result
from termapy.cli_flags import confirm_on_stdin
from termapy.config import (
    CONFIG_LOAD_ERRORS,
    cfg_dir,
    cfg_history_path,
    load_config,
    on_connect_commands,
    open_serial,
)
from termapy.config_resolve import find_config, infer_config_from_run_file, resolve_config
from termapy.defaults import cmd_prefix
from termapy.markup import escape_markup
from termapy.plugins import CapabilitySet, CmdResult
from termapy.repl import ReplEngine
from termapy.scripting import (
    SETTING_QUERY,
    SETTING_TOGGLE,
    parse_bool_setting,
    render_progress_bar,
    strip_ansi,
)
from termapy.serial_engine import SerialEngine
from termapy.terminal_host import TerminalHost

if TYPE_CHECKING:
    from prompt_toolkit import PromptSession


class CLITerminal(TerminalHost):
    """Plain-text serial terminal - no Textual dependency.

    Owns the serial engine, REPL engine, capture engine, and Rich console.
    Registers CLI-specific hooks for /delay, /color, /run.

    Args:
        cfg: Loaded config dict.
        config_path: Path to the config file.
        no_color: Strip ANSI color codes from output.
        run_script: Optional .run script to execute then exit.
    """

    def __init__(
        self,
        cfg: dict,
        config_path: str,
        no_color: bool = False,
        run_script: str | None = None,
        exec_cmd: str | None = None,
        term_width: int | None = None,
        zero_config: bool = False,
        output_level: str | None = None,
    ) -> None:
        """Create a CLI terminal frontend.

        Args:
            cfg: Config dict (owned by the engine).
            config_path: Path to the JSON config file, or ``""`` for
                zero-config mode (no config file, in-memory defaults).
            no_color: Strip Rich color markup.
            run_script: Optional .run path; if given, runs script and exits.
            exec_cmd: Optional single command string; if given, dispatches
                it once and exits with status 0/1.  Mutually exclusive
                with ``run_script`` (entry.py enforces).
            term_width: Optional terminal width override.
            zero_config: True when the app started without a config file.
                Triggers the welcome banner + port list on ``run()`` and
                skips the automatic initial connect so the user can pick
                a port interactively.
            output_level: Initial output level (silent/quiet/normal/verbose)
                from CLI flags.  None means use the default.
        """
        self.cfg = cfg
        self.config_path = config_path
        self.no_color = no_color
        self.run_script = run_script
        self.exec_cmd = exec_cmd
        self.term_width = term_width
        self.zero_config = zero_config
        self.output_level = output_level
        self.prefix = cmd_prefix(cfg)
        self._xfer_cancel = threading.Event()
        self._exec_exit_code = 0
        # Set by /exit and /tui (_request_exit); the prompt loop ends on it.
        self._exit_requested = False
        self.switch_to: str | None = None

        # In exec mode the output is meant for piping/scripting, so
        # suppress the input echo ("demo> AT+VER") that's helpful in
        # interactive use but noise in captured stdout.  The connect
        # banner is suppressed separately in terminal_host._connect().
        if self.exec_cmd:
            self.cfg["echo"] = False

        # Ensure stdout handles unicode on Windows.  sys.stdout is typed as
        # IO[str] (no reconfigure) but is actually TextIOWrapper at runtime.
        # Test harnesses sometimes replace it with StringIO, so we hasattr
        # instead of isinstance -- only real TextIOWrapper instances need
        # the recode.
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if (
            reconfigure
            and sys.stdout.encoding
            and sys.stdout.encoding.lower() not in ("utf-8", "utf8")
        ):
            reconfigure(encoding="utf-8", errors="replace")

        # Rich console for colored output
        from rich.console import Console

        # soft_wrap: never inject hard newlines into long lines.  On a live
        # terminal the emulator wraps visually (looks identical); piped
        # output keeps each logical line intact, which is what makes
        # ``-e "/port.list --json" | jq`` (and grep over long device
        # lines) work.  Tables are pre-fitted to term_width upstream, so
        # their rendering is unchanged.
        self.console = Console(
            no_color=no_color,
            highlight=False,
            width=term_width,
            soft_wrap=True,
        )

        # Engines
        self._capture = CaptureEngine(
            on_echo=lambda line: self.write(f"  {line}"),
            on_complete=lambda result: self.status(format_capture_result(result)[0]),
        )
        self._engine = SerialEngine(
            cfg=cfg,
            capture=self.capture,
            open_fn=open_serial,
            log=self._log,
        )
        self.repl = ReplEngine(cfg, config_path, write=self.status)

        from termapy.variables import (
            register_cfg_vars,
            set_context_var,
            set_launch_var,
        )

        set_launch_var("FRONT_END", "cli")
        set_context_var(
            "CFG",
            lambda: Path(self.config_path).stem if self.config_path else "termapy",
        )
        register_cfg_vars(
            get_config_path=lambda: self.config_path,
            get_cfg=lambda: self.cfg,
            get_log_path=lambda: str(self.session_log_path() or ""),
        )
        self._setup_context()
        self._register_hooks()

    # -- Output ---------------------------------------------------------------

    def write(self, text: str, color: str = "") -> None:
        """Write text to stdout via Rich console.

        ``text`` is plain content either way: escaped inside the color
        wrapper, printed with ``markup=False`` without one, so a literal
        ``[...]`` in it always shows.  Callers that want true Rich markup
        should use ``write_markup`` instead.
        """
        if color:
            self.console.print(f"[{color}]{escape_markup(text)}[/]")
        else:
            self.console.print(text, markup=False)

    def write_markup(self, text: str) -> None:
        """Write Rich markup text to stdout."""
        self.console.print(text)

    def status(self, text: str, color: str = "") -> None:
        """Write an indented status message.

        The two-space gutter marks termapy's own output apart from raw
        device bytes (which print flush-left).  It is applied to *every*
        line so a multi-line message -- an ``Error:`` with its ``Usage:``
        continuation, or a ``/cfg.dump`` block -- stays aligned instead
        of leaving continuation lines flush-left at column 0.

        ``text`` is plain content, escaped or printed without markup as
        in ``write``.  Avoids a cascade failure when an error message
        contains literal ``[/]`` (e.g. a Rich MarkupError repeated as
        ``err_msg``).
        """
        indented = textwrap.indent(text, "  ")
        if color:
            self.console.print(f"[{color}]{escape_markup(indented)}[/]")
        else:
            self.console.print(indented, markup=False)

    def _raw(self, text: str, end: str = "\n") -> None:
        """Write raw text to stdout, bypassing Rich markup."""
        f = self.console.file
        f.write(text + end)
        f.flush()

    def _err(self, text: str) -> None:
        """Write text to stderr."""
        sys.stderr.write(text + "\n")
        sys.stderr.flush()

    def _log(self, direction: str, text: str) -> None:
        """Log callback - CLI doesn't write a log file."""
        pass

    # -- Context and hooks ----------------------------------------------------

    def _setup_context(self) -> None:
        """Build PluginContext and InternalHandle, wire to REPL."""
        internal_handle = self._build_internal_handle()
        internal_handle.delay_progress = self._delay_progress

        self.ctx = self._build_plugin_context(internal_handle)
        # CLI-specific callbacks
        self.ctx.io.notify = lambda text, **kw: self.write(f"[notice] {text}")
        self.ctx.io.clear_screen = lambda: self._raw("\x1b[2J\x1b[H", end="")
        self.ctx.ui._exit_app_impl = self._request_exit
        self.ctx.ui._get_screen_text_impl = lambda: ""
        # CLI provides interactive (a human at a terminal -- including
        # over SSH) and gui_apps when a local desktop is available.  No
        # TUI features (dialogs, screen capture, status bar, toast
        # notifications).  block_until is added dynamically by the script
        # runner when running a .run file in CLI mode -- see
        # ReplEngine._effective_capabilities.
        from termapy.plugins import detect_gui_apps

        gui = detect_gui_apps()
        self.ctx.capabilities = CapabilitySet(
            interactive=True,
            gui_apps=gui,
            # The CLI's only editor is the system one: it needs a desktop.
            file_editor=gui,
            # ctx.ui.confirm is the y/n prompt on stdin (_confirm): /confirm
            # works in a CLI script, and a piped run reads No and stops.
            confirm_dialog=True,
            # The operator is the caller on their own machine: no sandbox.
            filesystem_unconfined=True,
            network_egress=True,
        )
        self.ctx.environment = "CLI"  # ENVIRONMENTS column; /help marks it (current)
        self.ctx.sync_capabilities()  # push caps into fs/ui handle snapshots

        self.repl.set_context(self.ctx)
        # CLI: REPL echo off by default -- the OS terminal already shows the
        # typed line, so app-echo would duplicate it (scripts opt in with
        # /term.echo_repl).  Device echo follows cfg echo.  Color off
        # when --no-color so device ANSI is stripped from piped output.
        self._init_flags(echo_repl=False, color=not self.no_color)
        if self.output_level is not None:
            self.ctx.ns("flags")["output_level"] = self.output_level

    def _register_hooks(self) -> None:
        """Register the CLI's own commands."""
        # /term.color (device-color toggle) is now a portable builtin
        # backed by flags["color"], with /color as its legacy alias in
        # legacy.py -- so it works in the TUI too, not just the CLI.
        # /run and its sub_commands (.help, .legacy, .list, .dump,
        # .show, .explore, .profile) are owned by the run.py builtin; the
        # host only wires ctx.internal.run_script via TerminalHost.
        self.repl.register_hook(
            "help.open",
            "{topic}",
            "Open help in browser.",
            self._hook_help_open,
            source="app",
            needs=CapabilitySet.GUI_APPS,
        )
        # The /log.* family, /log.delete and its legacy /log.clear included,
        # is the log builtin's (builtins/commands/log.py, legacy.py).
        self.repl.register_hook(
            "tui",
            "",
            "Switch to TUI mode.",
            self._hook_tui,
            source="app",
            needs=CapabilitySet.INTERACTIVE,
        )
        self.repl.register_hook(
            "cli",
            "",
            "Already in CLI mode.",
            # SPECIAL CASE: no-op when /cli runs while already in CLI mode.
            lambda ctx, args: CmdResult.ok(value=""),
            source="app",
            needs=CapabilitySet.INTERACTIVE,
        )
        self.repl.register_hook(
            "cli.completion",
            "{on|off|toggle}",
            "Show or toggle CLI tab completion, auto-suggest, and help toolbar.",
            self._hook_cli_completion,
            source="app",
            needs=CapabilitySet.INTERACTIVE,
        )
        # Historically, CLI registered placeholder hooks for TUI-only
        # commands (/line_no and friends) so users got a clear "Only
        # available in /tui mode." error rather than "Unknown command".
        # That role is now played by the capability model: TUI-only
        # commands declare ``needs=CapabilitySet.TUI_MODE`` and
        # dispatch reports the missing capability uniformly.  No CLI-side
        # stubs needed.

    # -- Hook handlers --------------------------------------------------------

    def _request_exit(self, switch_to: str | None = None) -> None:
        """End the session once the current command returns (``ctx.ui.exit_app``).

        ``/exit`` asks for it, and ``/tui`` with ``switch_to="tui"``, which
        the mode loop opens next.  A running script stops before its next
        line, and the prompt loop ends after the dispatch that asked.  Until
        2026-10-16 both worked only typed at the prompt: from a ``.run``
        file or ``-e``, ``/exit`` did nothing and the script went on (review
        2026-10-15, D10).
        """
        self._exit_requested = True
        if switch_to is not None:
            self.switch_to = switch_to
        self.repl._script_stop.set()

    def _hook_tui(self, ctx, args: str) -> CmdResult:
        """``/tui``: leave the CLI for the TUI, on the current config."""
        self._request_exit("tui")
        # SPECIAL CASE: the session ends after this returns; no caller
        # reads the value.
        return CmdResult.ok(value="")

    def _hook_cli_completion(self, ctx, args: str):
        """Query, set, or toggle CLI completion (tab completion, auto-suggest, toolbar).

        A setting command: bare QUERIES, a vocabulary token sets, ``toggle``
        flips, anything else fails -- see the rule in CLAUDE.md.
        """
        try:
            action = parse_bool_setting(args)
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        current = bool(self.cfg.get("cli_completion", True))
        if action is SETTING_QUERY:
            self.status(f"CLI completion: {'on' if current else 'off'}")
            return CmdResult.ok(value="on" if current else "off")
        enabled = (not current) if action is SETTING_TOGGLE else action
        self.cfg["cli_completion"] = enabled
        if enabled:
            self._session = self._build_session()
            self.status("CLI completion enabled.", "green")
        else:
            self._session = None
            self.status("CLI completion disabled.")
        return CmdResult.ok(value="on" if enabled else "off")

    # ``_hook_run`` and ``_hook_run_help`` are gone -- ``/run`` and
    # ``/run.help`` are now owned by the ``run.py`` built-in.
    # ``_run_script`` lives on ``TerminalHost`` so the built-in
    # ``/run`` handler can use ``ctx.internal.run_script`` uniformly
    # across CLI and MCP.  No CLI-specific override needed.

    # -- Progress bar (CLI-specific: blocking sleep with bar) -----------------

    _DELAY_BAR_WIDTH = 30

    def _delay_progress(self, elapsed: float, total: float, done: bool) -> None:
        """Draw /delay's progress as a bar redrawn in place, and erase it when done.

        Only for a delay typed at the prompt: inside a script or a one-shot
        run (--run / --exec) the CLI draws no bar, so piped output carries
        no carriage-return redraws.  The wait and the done line are /delay's.
        """
        if self.repl.in_script or self.is_oneshot:
            return
        if done:
            full = render_progress_bar(
                total, total, self._DELAY_BAR_WIDTH, ascii_only=self.no_color
            )
            self._raw("\r" + " " * (len(full) + 2) + "\r", end="")
            return
        bar = render_progress_bar(elapsed, total, self._DELAY_BAR_WIDTH, ascii_only=self.no_color)
        self._raw(f"\r  {bar}", end="")

    # -- Connection uses TerminalHost._connect / _disconnect -------------------
    # CLI has no additional UI to update on connect/disconnect, so the
    # base class implementation is used as-is.

    # -- Confirmation ---------------------------------------------------------

    def _confirm(self, message: str) -> bool:
        """Prompt for y/n confirmation on stdin (``cli_flags.confirm_on_stdin``)."""
        return confirm_on_stdin(message)

    # -- Prompt session (history + tab completion via prompt_toolkit) ----------

    def _history_path(self) -> str:
        """Delegate to the single owner in config.cfg_history_path."""
        return cfg_history_path(self.config_path)

    def _build_session(self) -> PromptSession | None:
        """Create a prompt_toolkit session with history and tab completion.

        Returns None when stdout is not a terminal, or when
        ``cli_completion`` is disabled in the config.

        Deliberately does NOT use ``bottom_toolbar``: prompt_toolkit
        reserves a full-width row for the toolbar whenever the kwarg
        is set, and that row renders as a visible white band even when
        the callback returns an empty string.  Worse, it scrolls with
        the rest of the prompt-rendered region and leaves artifacts in
        the scrollback.  Tab-completion via the completer+dropdown is
        the feature users actually want; the toolbar was redundant
        visual chrome.
        """
        if not sys.stdout.isatty():
            return None
        if not self.cfg.get("cli_completion", True):
            return None
        # Imported here, not at the top: prompt_toolkit costs ~0.2 s, and a
        # one-shot (-e, --run) never builds a prompt.
        from termapy.cli_prompt import build_session

        return build_session(self.repl, self.prefix, self._history_path())

    # -- Reader thread --------------------------------------------------------

    def _start_reader(self) -> None:
        """Start the background serial reader thread."""

        def on_lines(lines: list[str]) -> None:
            color_on = self.repl.ctx.ns("flags").get("color", True)
            plain = [strip_ansi(line) for line in lines]
            for shown in lines if color_on else plain:
                self._raw(shown)
            # Expect watcher and text capture, the same as every frontend.
            self._feed_rx_consumers(lines, plain)

        def on_hex(rows: list[str], lines: list[str]) -> None:
            for row in rows:
                self._raw(row)
            self._feed_rx_consumers(lines, [strip_ansi(line) for line in lines])

        def on_disconnect() -> None:
            self._err("Serial disconnected")
            self._port_lost()

        # The engine owns the thread so disconnect() can join it; the CLI
        # used to start one here and drop the handle, leaving teardown
        # nothing to wait on.
        self.engine.start_reader(
            on_lines=on_lines,
            on_clear=lambda: self._raw("\x1b[2J\x1b[H", end=""),
            on_capture_done=lambda: self._stop_capture(),
            on_error=lambda detail: self._err(f"Serial error: {detail}"),
            on_disconnect=on_disconnect,
            on_hex=on_hex,
            hex_view=self._hex_view,
        )

    # -- Run modes ------------------------------------------------------------

    @property
    def is_oneshot(self) -> bool:
        """True for --run and --exec -- non-interactive, exits when done.

        Interactive modes (TUI, CLI REPL) run the cfg's connect-time
        autorun (``on_connect_cmd``, the help banner).  One-shot modes
        suppress those -- the user expects only the script or exec'd
        command to produce output.
        """
        return bool(self.run_script or self.exec_cmd)

    def _run_script_mode(self, run_script: str) -> None:
        """Execute a .run script and exit."""
        script_path = Path(run_script)
        if not script_path.exists():
            scripts_dir = Path(self.config_path).parent / "run"
            alt = scripts_dir / script_path.name
            if alt.exists():
                script_path = alt
        path, _ = self.repl.start_script(str(script_path))
        if path:
            try:
                self.repl.run_script(
                    path,
                    write=self.status,
                    dispatch=self.ctx.dispatch,
                    verbose=True,
                )
            except KeyboardInterrupt:
                self._raw("\nScript interrupted")
        actual = getattr(self.engine.port_obj, "port", "") or self.cfg["serial"]["port"]
        self.engine.disconnect()
        msg = f"Disconnected: {actual}" if actual else "Disconnected."
        self.write(msg, "red")

    def _run_exec_mode(self, command: str) -> None:
        """Dispatch one command and exit.  Sets self._exec_exit_code.

        Bare device text is dispatched asynchronously: ``ctx.dispatch``
        returns after writing TX bytes, but the device's response
        arrives later via the background reader thread.  We watch the
        reader directly via ``ctx.serial.rx_observer``: every chunk of
        received bytes resets a "last arrival" clock, and we exit
        once the rx stream has been quiet for ``_EXEC_IDLE_GAP_S``
        (default 500ms).  Capped at ``_EXEC_MAX_WAIT_S`` (60s) so a
        runaway device can't hang the pipeline indefinitely.

        Why an observer instead of ``serial_port.wait_for_idle()``:
        NOT because ``wait_for_idle`` is broken any more -- it used to
        poll ``in_waiting``, which the reader keeps drained to zero, and
        so returned mid-response; it now keys off the same reader clock
        this observer does (finding T10).  The remaining difference is
        WHERE THE CLOCK STARTS.  This loop resets ``last_arrival`` at
        dispatch time, so it always waits at least one idle gap for the
        device to begin answering.  ``wait_for_idle`` reads the engine's
        last-RX stamp, which may pre-date the command entirely -- on a
        device that has not started replying yet it would return
        immediately and truncate the piped output.  Swapping to it needs
        a "not before now" floor first.  Same pattern ``/cap.wire`` uses.

        No "Disconnected." chrome banner -- exec mode is the piping
        mode, and chrome bytes corrupt captured stdout.
        """
        import time as _time

        last_arrival = [_time.monotonic()]

        def _bump_last_rx(_data: bytes) -> None:
            last_arrival[0] = _time.monotonic()

        try:
            with self.ctx.serial.rx_observer(_bump_last_rx):
                result = self.ctx.dispatch(command)
                deadline = _time.monotonic() + self._EXEC_MAX_WAIT_S
                while _time.monotonic() < deadline:
                    if (_time.monotonic() - last_arrival[0]) >= self._EXEC_IDLE_GAP_S:
                        break
                    _time.sleep(0.01)
        except KeyboardInterrupt:
            self._raw("\nInterrupted")
            self._exec_exit_code = 130  # SIGINT convention
            self.engine.disconnect()
            return
        self._exec_exit_code = 0 if result.success else 1
        self.engine.disconnect()

    # Tuning knobs for _run_exec_mode.  Class constants so they're
    # easy to find / override in a subclass or test, and so the docstring
    # references stay accurate without magic numbers in the body.
    _EXEC_IDLE_GAP_S = 0.5  # quiet period that signals "device done"
    _EXEC_MAX_WAIT_S = 60.0  # hard cap so runaway streams can't hang us

    def _run_interactive(self) -> None:
        """Run the interactive input loop.

        Wraps the prompt loop in ``patch_stdout()`` so that output
        written by the background reader thread (via Rich's
        ``console.print`` -> stdout) is buffered by prompt_toolkit and
        inserted *above* the active prompt line instead of colliding
        with it.  Without this, a long device response (~40 lines of
        help text) races with prompt_toolkit's redraw and leaves the
        prompt overlaid mid-stream.

        ``raw=True`` passes ANSI escape codes through untouched so
        Rich's color markup renders correctly.  ``patch_stdout``
        replaces ``sys.stdout`` (and ``sys.stderr``) with a proxy that
        captures writes from any thread and schedules them into
        prompt_toolkit's layout.
        """
        # prompt_toolkit shows REPL commands - no need to echo those.
        # patch_stdout handles the main/reader-thread output ordering
        # structurally, so no wait_for_idle dance is needed between
        # prompts.
        self.ctx.ns("flags")["echo"] = False
        self.cfg["echo"] = False
        self._session = self._build_session()

        def _loop() -> None:
            while True:
                try:
                    from termapy.variables import expand_vars

                    prompt = expand_vars(self.cfg.get("cli_prompt", "> "))
                    s = self._session
                    line = s.prompt(prompt) if s else input(prompt)
                except EOFError:
                    break

                line = line.strip()
                if not line:
                    if self.cfg.get("send_bare_enter", False):
                        self._dispatch("")
                    continue

                # /quit is the prompt's own word for /exit (no command).
                if line.lower() == self.prefix + "quit":
                    break

                self._dispatch(line)
                if self._exit_requested:
                    break

        # patch_stdout requires an actual TTY (it needs console
        # dimensions and cursor ops).  Only wrap the loop when a
        # prompt_toolkit session was built -- that condition already
        # mirrors "stdout is a TTY and cli_completion is on".  For
        # pipe / captured-stdout invocations (tests, --run piped
        # output, etc.) fall back to the plain loop.
        try:
            if self._session is not None:
                # raw=True passes ANSI escape codes through so Rich's
                # color markup renders correctly in reader-thread
                # output.  Without raw=True, prompt_toolkit line-buffers
                # and renders the ESC byte as literal "?".  The menu
                # reservation that used to leave a blank band below
                # the prompt is disabled via reserve_space_for_menu=0
                # in _build_session.
                from prompt_toolkit.patch_stdout import patch_stdout

                with patch_stdout(raw=True):
                    _loop()
            else:
                _loop()
        except KeyboardInterrupt:
            self._raw("\nInterrupted")
        finally:
            actual = getattr(self.engine.port_obj, "port", "") or self.cfg["serial"]["port"]
            self.engine.disconnect()
            msg = f"Disconnected: {actual}" if actual else "Disconnected."
            self.write(msg, "red")

    # -- Entry point ----------------------------------------------------------

    def run(self) -> str | None:
        """Connect, start reader, and run in script or interactive mode.

        Returns:
            Mode to switch to ("tui") or None for normal exit.
        """
        self.repl.fire_lifecycle("on_app_start")

        # Zero-config mode: show a welcome banner with available ports,
        # skip the initial connect (no port selected yet), and let the
        # user type /port.connect <name> to pick one.  Every other path --
        # a real config file, --demo, --run with an inferred config --
        # has a non-empty cfg["serial"]["port"] by contract, so the else branch
        # here just connects as before.
        if self.zero_config:
            self._show_zero_config_welcome()
        else:
            if not self._connect():
                sys.exit(1)

        # Show hint before on_connect_cmd so it appears first.  All
        # connect-time autorun (banner, on_connect_cmd) is gated on
        # is_oneshot: interactive modes run them; --run and --exec
        # suppress them so piped/captured stdout contains only the
        # user's script or command output.
        if not self.is_oneshot:
            self.write(
                f"Type commands, {self.prefix}help for REPL commands, Ctrl+C to quit",
                "dim",
            )

        # Run on_connect_cmd (same as TUI does after connecting), then
        # the CLI-only cli_on_connect_cmd for any extras specific to
        # the plain-text frontend.
        if not self.is_oneshot:
            for _, cmd in on_connect_commands(self.cfg, "cli_on_connect_cmd"):
                self._dispatch(cmd)
                if self.engine.is_connected and self.engine.serial_port:
                    self.engine.serial_port.wait_for_idle()

        if self.run_script:
            self._run_script_mode(self.run_script)
        elif self.exec_cmd:
            self._run_exec_mode(self.exec_cmd)
        else:
            self._run_interactive()
        self.repl.fire_lifecycle("on_app_stop")
        return self.switch_to

    def _show_zero_config_welcome(self) -> None:
        """Print the zero-config welcome banner + port list + hint.

        Called from ``run()`` when ``zero_config`` is set.  Lists the
        currently-available serial ports (via ``port_control.list_ports``)
        and shows the user what ``/port.connect`` invocation would use them
        with the built-in defaults (115200 N81 cr noecho).  The user then
        types the actual ``/port.connect`` to connect.
        """
        from termapy import port_control

        self.write("Welcome to termapy.  No config found.", "cyan")
        self.write("")
        self.write("Available ports:", "bold")
        msgs, _ = port_control.list_ports()
        for text, color in msgs:
            self.write(text, color or "")
        self.write("")
        self.write("Defaults: 115200 N81 cr noecho", "dim")
        self.write("")
        first_port = self._first_available_port()
        if first_port:
            self.write(
                f"Try:  {self.prefix}port.connect {first_port}",
                "green",
            )
            self.write(
                f"Or:   {self.prefix}port.connect {first_port} 9600 N81 crlf echo",
                "dim",
            )
        else:
            self.write(
                f"Try:  {self.prefix}port.connect DEMO    "
                "-- no hardware ports; use the built-in simulator",
                "green",
            )
        self.write(
            f"      {self.prefix}port.list          -- re-list ports",
            "dim",
        )
        self.write(
            f"      {self.prefix}port.chip *        -- richer port info",
            "dim",
        )
        self.write(
            f"      {self.prefix}help               -- all commands",
            "dim",
        )
        self.write("")

    def _first_available_port(self) -> str:
        """Return the first device name from ``comports()``, or ``""``.

        Used only for the zero-config welcome banner's "Try:" hint.
        """
        try:
            from serial.tools.list_ports import comports

            ports = sorted(comports(), key=lambda p: p.device)
            return ports[0].device if ports else ""
        except Exception:
            return ""


def _run_cli_mode(args) -> str | None:
    """Run in CLI mode - plain text terminal, no TUI.

    Lives in ``cli.py`` (not ``app.py``) so that selecting CLI mode does
    not transitively import Textual.  Invoked by ``entry.main()``.

    Returns:
        Mode to switch to ("tui") or None for normal exit.
    """
    run_script = getattr(args, "run", None)
    exec_cmd = getattr(args, "exec_cmd", None)

    # If a positional arg is a .run file, treat it as --run
    if args.config and args.config.endswith(".run") and not run_script:
        run_script = args.config
        args.config = None

    if args.demo:
        from termapy.config import setup_demo_config

        config_path = str(setup_demo_config(cfg_dir(), force=True))
    elif run_script and not args.config:
        # Infer config from the .run file's location
        config_path = infer_config_from_run_file(run_script)
        if not config_path:
            print(
                f"termapy: cannot infer config from {Path(run_script).resolve()}",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.config:
        config_path = resolve_config(args.config)
        if config_path is None:
            print(
                f"termapy: config not found: {Path(args.config).resolve()}",
                file=sys.stderr,
            )
            print(
                "  Use --demo to create a demo config, or specify a .cfg file.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        path, _ = find_config()
        if not path:
            # Zero-config CLI: no config file anywhere.  Start the REPL
            # with an in-memory DEFAULT_CFG and let the user pick a port
            # interactively via /port.connect.  This replaces the previous
            # "no config found -- exit" behavior for interactive use.
            # --run without an inferrable config still errors (handled
            # above), since scripting without a config is ambiguous.
            from termapy.defaults import default_cfg

            cfg = default_cfg()
            cli = CLITerminal(
                cfg,
                config_path="",
                no_color=args.no_color,
                run_script=run_script,
                exec_cmd=exec_cmd,
                term_width=getattr(args, "term_width", None),
                zero_config=True,
                output_level=getattr(args, "output_level", None),
            )
            result = cli.run()
            if exec_cmd:
                sys.exit(cli._exec_exit_code)
            if result:
                args.config = cli.config_path
            return result
        config_path = path

    try:
        cfg = load_config(config_path)
    except CONFIG_LOAD_ERRORS as e:
        print(f"termapy: failed to load config: {e}", file=sys.stderr)
        sys.exit(1)

    cli = CLITerminal(
        cfg,
        config_path,
        no_color=args.no_color,
        run_script=run_script,
        exec_cmd=exec_cmd,
        term_width=getattr(args, "term_width", None),
        output_level=getattr(args, "output_level", None),
    )
    result = cli.run()
    if exec_cmd:
        sys.exit(cli._exec_exit_code)
    if result:
        args.config = cli.config_path
    return result
