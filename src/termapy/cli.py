"""CLI serial terminal - no Textual dependency.

Provides a plain-text interactive terminal using SerialEngine, ReplEngine,
and CaptureEngine. Reads from stdin, writes to stdout, serial I/O on a
background thread.

Usage:
    termapy --cli [config] [--no-color]
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

from termapy.capture import CaptureEngine
from termapy.config import CONFIG_LOAD_ERRORS, cfg_dir, load_config, open_serial
from termapy.config_resolve import find_config, infer_config_from_run_file, resolve_config
from termapy.defaults import cmd_prefix
from termapy.plugins import CapabilitySet, CmdResult
from termapy.repl import ReplEngine
from termapy.scripting import strip_ansi
from termapy.serial_engine import SerialEngine
from termapy.terminal_host import TerminalHost


def _menu_rows_for_terminal() -> int:
    """Scale the completion-dropdown row reservation to terminal height.

    Prompt-toolkit reserves rows below the prompt for the completion
    dropdown, which appears as a visible empty band when idle.  Tall
    terminals can afford a generous dropdown; very short ones should
    give up completion entirely before the band eats half the
    screen.

      - height >= 40 rows  -> 8 rows  (the prompt-toolkit default)
      - height >= 10 rows  -> 4 rows  (still scannable, smaller gap)
      - height < 10 rows   -> 0 rows  (no dropdown, no gap)

    Read once at PromptSession construction; prompt-toolkit caches the
    value internally.
    """
    try:
        lines = shutil.get_terminal_size().lines
    except (OSError, ValueError):
        return 4
    if lines >= 40:
        return 8
    if lines >= 10:
        return 4
    return 0


class _TermapyCompleter(Completer):
    """Tab completer for REPL commands, device commands, and script files."""

    def __init__(self, repl: ReplEngine, prefix: str, config_path: str) -> None:
        self._repl = repl
        self._prefix = prefix
        self._scripts_dir = Path(config_path).parent / "run"
        self._file_cmds = (f"{prefix}run ", f"{prefix}run.edit ")

    def get_completions(self, document, complete_event):  # noqa: ARG002
        """Yield completions for the current input."""
        line = document.text

        # File completion for /run and /run.edit args
        for fc in self._file_cmds:
            if line.startswith(fc):
                partial = line[len(fc):]
                if self._scripts_dir.is_dir():
                    for f in sorted(self._scripts_dir.glob("*.run")):
                        if f.name.startswith(partial):
                            yield Completion(f.name, start_position=-len(partial))
                return

        # REPL command completion
        if line.startswith(self._prefix):
            for name in sorted(self._repl._plugins):
                full = f"{self._prefix}{name}"
                if full.startswith(line):
                    yield Completion(full, start_position=-len(line))
            return

        # Device command completion -- pulls from the active profile's
        # commands dict.
        active = self._repl.ctx.ns("active_profile")
        profile_cmds = active.get("commands") if isinstance(active, dict) else None
        if isinstance(profile_cmds, dict) and profile_cmds:
            for name in sorted(profile_cmds):
                if name.lower().startswith(line.lower()):
                    yield Completion(name, start_position=-len(line))


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

        # In exec mode the output is meant for piping/scripting, so
        # suppress the input echo ("demo> AT+VER") that's helpful in
        # interactive use but noise in captured stdout.  The connect
        # banner is suppressed separately in terminal_host._connect().
        if self.exec_cmd:
            self.cfg["echo_input"] = False

        # Ensure stdout handles unicode on Windows.  sys.stdout is typed as
        # IO[str] (no reconfigure) but is actually TextIOWrapper at runtime.
        # Test harnesses sometimes replace it with StringIO, so we hasattr
        # instead of isinstance -- only real TextIOWrapper instances need
        # the recode.
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure and sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            reconfigure(encoding="utf-8", errors="replace")

        # Rich console for colored output
        from rich.console import Console

        self.console = Console(no_color=no_color, highlight=False, width=term_width)

        # Engines
        self.capture = CaptureEngine(
            on_echo=lambda line: self.write(f"  {line}"),
            on_complete=lambda result: self.status(
                f"Capture aborted: {result.error} ({result.path})"
                if result.error
                else f"Capture complete: {result.path} ({result.size_label})"
            ),
        )
        self.engine = SerialEngine(
            cfg=cfg,
            capture=self.capture,
            open_fn=open_serial,
            log=self._log,
        )
        self.repl = ReplEngine(cfg, config_path, write=self.status, prefix=self.prefix)

        from termapy.builtins.commands.var import (
            register_cfg_vars,
            set_context_var,
            set_launch_var,
        )
        from termapy.config import cfg_log_path

        set_launch_var("FRONT_END", "cli")
        set_context_var(
            "CFG",
            lambda: Path(self.config_path).stem if self.config_path else "termapy",
        )
        register_cfg_vars(
            get_config_path=lambda: self.config_path,
            get_cfg=lambda: self.cfg,
            get_log_path=lambda: cfg_log_path(self.config_path)
            if self.config_path
            else "",
        )
        self._setup_context()
        self._register_hooks()

    # -- Output ---------------------------------------------------------------

    def write(self, text: str, color: str = "") -> None:
        """Write text to stdout via Rich console.

        When ``color`` is given, ``text`` is treated as plain content
        and any literal ``[...]`` inside it is escaped so it doesn't
        collide with the wrapper.  Callers that want true Rich markup
        should use ``write_markup`` instead.
        """
        if color:
            from rich.markup import escape

            self.console.print(f"[{color}]{escape(text)}[/]")
        else:
            self.console.print(text)

    def write_markup(self, text: str) -> None:
        """Write Rich markup text to stdout."""
        self.console.print(text)

    def status(self, text: str, color: str = "") -> None:
        """Write an indented status message.

        When ``color`` is given, ``text`` is treated as plain content
        and escaped before the wrapper (see ``write``).  Avoids a
        cascade failure when an error message contains literal
        ``[/]`` (e.g. a Rich MarkupError repeated as ``err_msg``).
        """
        if color:
            from rich.markup import escape

            self.console.print(f"  [{color}]{escape(text)}[/]")
        else:
            self.console.print(f"  {text}")

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

        self.ctx = self._build_plugin_context(internal_handle)
        # CLI-specific callbacks
        self.ctx.io.notify = lambda text, **kw: self.write(f"[notice] {text}")
        self.ctx.io.clear_screen = lambda: self._raw("\x1b[2J\x1b[H", end="")
        self.ctx.ui._exit_app_impl = lambda: None
        self.ctx.ui._get_screen_text_impl = lambda: ""
        # CLI provides interactive (a human at a terminal -- including
        # over SSH) and gui_apps when a local desktop is available.  No
        # TUI features (dialogs, screen capture, status bar, toast
        # notifications).  block_until is added dynamically by the script
        # runner when running a .run file in CLI mode -- see
        # ReplEngine._effective_capabilities.
        from termapy.plugins import detect_gui_apps
        self.ctx.capabilities = CapabilitySet(
            interactive=True,
            gui_apps=detect_gui_apps(),
        )

        self.repl.set_context(self.ctx)
        self._init_flags(echo=False)
        if self.output_level is not None:
            self.ctx.ns("flags")["output_level"] = self.output_level

    def _register_hooks(self) -> None:
        """Register CLI-specific hooks for /delay, /term.color, /run."""
        self.repl.register_hook(
            "delay",
            "<duration>",
            "Wait for duration with progress bar (e.g. 500ms, 1.5s).",
            self._hook_delay,
            source="app",
        )
        self.repl.register_hook(
            "delay.silent",
            "<duration>",
            "Wait silently (no progress bar or output).",
            self._hook_delay_quiet,
            source="app",
        )
        from termapy.legacy import make_forwarder
        self.repl.register_hook(
            "delay.quiet",
            "<duration>",
            "Legacy alias for /delay.silent.",
            make_forwarder("delay.quiet", "delay.silent"),
            source="app",
            hidden=True,
        )
        # /term.color is the canonical name -- it's a display toggle
        # sibling to /term.echo, /term.line_no, /term.timestamps, etc.
        # The bare /color was the original top-level name; it stays as
        # a hidden alias so older scripts and shell aliases keep
        # working.
        self.repl.register_hook(
            "term.color",
            "{on|off}",
            "Show or toggle color output.",
            self._hook_color,
            source="app",
        )
        self.repl.register_hook(
            "color",
            "{on|off}",
            "Legacy alias for /term.color.",
            make_forwarder("color", "term.color"),
            source="app",
            hidden=True,
        )
        # /run and its sub_commands (.help, .legacy, .list, .dump,
        # .show, .explore) are owned by the run.py builtin; the host
        # only wires ctx.internal.run_script via TerminalHost.  /run.profile.*
        # is still a hook because the runner is host-specific.
        from termapy.run_profile_hooks import register_run_profile_hooks

        register_run_profile_hooks(self)
        self.repl.register_hook(
            "demo",
            "",
            "Set up and switch to the demo device config.",
            self._hook_demo,
            source="app",
            needs=CapabilitySet(interactive=True),
        )
        self.repl.register_hook(
            "demo.force",
            "",
            "Reset demo config to defaults.",
            lambda ctx, args: self._hook_demo(ctx, "--force"),
            source="app",
            needs=CapabilitySet(interactive=True),
        )
        self.repl.register_hook(
            "clr",
            "",
            "Clear the terminal screen (alias for {prefix}cls).",
            # SPECIAL CASE: clearing the screen produces no scriptable value.
            lambda ctx, args: (ctx.io.clear_screen(), CmdResult.ok(value=""))[-1],
            source="app",
            needs=CapabilitySet(interactive=True),
        )
        self.repl.register_hook(
            "raw",
            "<text>",
            "Send raw text to serial (no transforms or line ending).",
            self._hook_raw,
            source="app",
        )
        self.repl.register_hook(
            "help.open",
            "{topic}",
            "Open help in browser.",
            self._hook_help_open,
            source="app",
            needs=CapabilitySet(gui_apps=True),
        )
        self.repl.register_hook(
            "log.delete",
            "",
            "Delete the session log file.",
            self._hook_log_delete,
            source="app",
        )
        # /log.clear is a hidden legacy alias -- "clear" means "empty
        # visible state," and "delete" is the canonical verb for
        # removing on-disk files.
        self.repl.register_hook(
            "log.clear",
            "",
            "Legacy alias for /log.delete.",
            make_forwarder("log.clear", "log.delete"),
            source="app",
            hidden=True,
        )
        # /log.show, /log.dump, /log.fingerprint live as builtin
        # plugins in termapy/builtins/commands/log_*.py so MCP gets
        # them too -- no hook registration needed here.
        self.repl.register_hook(
            "tui",
            "",
            "Switch to TUI mode.",
            # SPECIAL CASE: mode switch is intercepted in _run_interactive
            # before this lambda returns; value="" is a placeholder that
            # never actually reaches a script capture site.
            lambda ctx, args: CmdResult.ok(value=""),
            source="app",
            needs=CapabilitySet(interactive=True),
        )
        self.repl.register_hook(
            "cli",
            "",
            "Already in CLI mode.",
            # SPECIAL CASE: no-op when /cli runs while already in CLI mode.
            lambda ctx, args: CmdResult.ok(value=""),
            source="app",
            needs=CapabilitySet(interactive=True),
        )
        self.repl.register_hook(
            "cli.completion",
            "{on|off}",
            "Show or toggle CLI tab completion, auto-suggest, and help toolbar.",
            self._hook_cli_completion,
            source="app",
            needs=CapabilitySet(interactive=True),
        )
        # Historically, CLI registered placeholder hooks for TUI-only
        # commands (/line_no and friends) so users got a clear "Only
        # available in /tui mode." error rather than "Unknown command".
        # That role is now played by the capability model: TUI-only
        # commands declare ``needs=CapabilitySet(tui_mode=True)`` and
        # dispatch reports the missing capability uniformly.  No CLI-side
        # stubs needed.

    # -- Hook handlers --------------------------------------------------------

    def _hook_cli_completion(self, ctx, args: str):
        """Show or toggle CLI completion (tab completion, auto-suggest, toolbar)."""
        from termapy.scripting import parse_bool

        val = parse_bool(args)
        if val is True:
            self.cfg["cli_completion"] = True
            self._session = self._build_session()
            self.status("CLI completion enabled.", "green")
        elif val is False:
            self.cfg["cli_completion"] = False
            self._session = None
            self.status("CLI completion disabled.")
        state = "on" if self.cfg.get("cli_completion", True) else "off"
        if val is None:
            self.status(f"CLI completion: {state}")
        return CmdResult.ok(value=state)

    def _hook_delay(self, ctx, args: str):
        """Wait with progress bar (>=1s) or silently (<1s).
        Shows elapsed/total time and sub-character resolution bar.
        Ctrl+C cancels."""
        from termapy.scripting import parse_duration

        try:
            seconds = parse_duration(args)
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        try:
            if seconds < 1:
                time.sleep(seconds)
                self.status(f"Delay {args.strip()} done.")
            else:
                self._draw_progress_bar(seconds, args.strip())
        except KeyboardInterrupt:
            self._raw(f"\r  Delay cancelled.{' ' * 30}")
        return CmdResult.ok(value=str(seconds))

    def _hook_delay_quiet(self, ctx, args: str):
        """Wait silently - no progress bar, no output.
        For scripts where delay output would clutter results."""
        from termapy.scripting import parse_duration

        try:
            seconds = parse_duration(args)
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            pass
        return CmdResult.ok(value=str(seconds))

    def _hook_color(self, ctx, args: str):
        """Toggle color output on/off."""
        from termapy.scripting import parse_bool

        val = parse_bool(args)
        if val is True:
            self.console.no_color = False
            self.status("Color enabled.", "green")
        elif val is False:
            self.console.no_color = True
            self.status("Color disabled.")
        state = "on" if not self.console.no_color else "off"
        if val is None:
            self.status(f"Color: {state}")
        return CmdResult.ok(value=state)

    # ``_hook_run`` and ``_hook_run_help`` are gone -- ``/run`` and
    # ``/run.help`` are now owned by the ``run.py`` built-in.
    # ``_run_script`` lives on ``TerminalHost`` so the built-in
    # ``/run`` handler can use ``ctx.internal.run_script`` uniformly
    # across CLI and MCP.  No CLI-specific override needed.

    def _prof_dir(self) -> Path | None:
        """Return the prof/ directory for the current config, or None.

        Used by ``/run.profile.show``, ``/run.profile.dump``,
        ``/run.profile.explore``, ``/run.profile.list``.  Mirrors
        ``SerialTerminal._prof_dir`` so the shared
        ``run_profile_hooks`` handlers work unchanged.
        """
        if not self.config_path:
            return None
        return Path(self.config_path).parent / "prof"

    def _hook_demo(self, ctx, args: str):
        """Set up and switch to demo device config."""
        from termapy.config import cfg_dir, setup_demo_config

        force = "--force" in args.lower()
        try:
            ctx.io.status("Setting up demo files...")
            config_path = str(setup_demo_config(cfg_dir(), force=force))
        except OSError as e:
            return CmdResult.fail(msg=f"Demo setup failed: {e}")

        ctx.io.status("Loading demo config...")
        result = self._switch_to_cfg_path(config_path)
        if not result.success:
            return result
        msg = "Switched to demo device"
        if force:
            msg += " (config reset)"
        self.status(msg, "green")
        return result


    # -- Progress bar (CLI-specific: blocking sleep with bar) -----------------

    def _draw_progress_bar(self, seconds: float, label: str) -> None:
        """Draw a progress bar with sub-character resolution.
        Uses Unicode blocks in color mode, ASCII in no-color mode."""
        width = 30
        if self.no_color:
            _SUB = " .-=#"  # ASCII: 4 sub-steps per cell
        else:
            _SUB = " \u2591\u2592\u2593\u2588"  # Unicode: ░▒▓█
        sub_n = len(_SUB) - 1
        sub_steps = width * sub_n
        full_ch = _SUB[-1]
        t0 = time.perf_counter()
        while True:
            elapsed = time.perf_counter() - t0
            if elapsed >= seconds:
                break
            frac = elapsed / seconds
            # Cap at sub_steps - 1 so bar never looks 100% before done
            pos = min(frac * sub_steps, sub_steps - 1)
            full = int(pos // sub_n)
            partial = int(pos % sub_n)
            bar = full_ch * full
            if full < width:
                bar += _SUB[partial] + " " * (width - full - 1)
            self._raw(
                f"\r  [{bar}] {int(elapsed)}s/{int(seconds)}s",
                end="",
            )
            time.sleep(0.25)
        bar = full_ch * width
        self._raw(f"\r  [{bar}] {int(seconds)}s/{int(seconds)}s", end="")
        msg = f"Delay {label} done."
        self._raw(f"\r  {msg}{' ' * (width + 10 - len(msg))}")

    # -- Connection uses TerminalHost._connect / _disconnect -------------------
    # CLI has no additional UI to update on connect/disconnect, so the
    # base class implementation is used as-is.

    # -- Confirmation ---------------------------------------------------------

    def _confirm(self, message: str) -> bool:
        """Prompt for y/n confirmation on stdin."""
        try:
            answer = input(f"  {message} [y/N] ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    # -- Prompt session (history + tab completion via prompt_toolkit) ----------

    def _history_path(self) -> str:
        """Return the path to the history file (matches TUI path)."""
        if self.config_path:
            p = Path(self.config_path)
            return str(p.parent / f"{p.stem}.history")
        return str(Path.cwd() / ".cmd_history.txt")

    def _build_session(self) -> PromptSession | None:
        """Create a prompt_toolkit session with history and tab completion.

        Returns None when stdout is not a terminal, or when
        ``cli_completion`` is disabled in the config.

        Deliberately does NOT use ``bottom_toolbar``: prompt_toolkit
        reserves a full-width row for the toolbar whenever the kwarg
        is set, and that row renders as a visible white band even when
        the callback returns an empty string.  Worse, it scrolls with
        the rest of the prompt-rendered region and leaves artefacts in
        the scrollback.  Tab-completion via the completer+dropdown is
        the feature users actually want; the toolbar was redundant
        visual chrome.
        """
        if not sys.stdout.isatty():
            return None
        if not self.cfg.get("cli_completion", True):
            return None

        return PromptSession(
            history=FileHistory(self._history_path()),
            completer=_TermapyCompleter(self.repl, self.prefix, self.config_path),
            auto_suggest=AutoSuggestFromHistory(),
            # Prompt-toolkit reserves space below the prompt for the
            # completion dropdown, which renders as a visible empty
            # band when nothing's being typed.  Scale the reservation
            # to the terminal height so tall windows get a roomy
            # dropdown and short windows aren't dominated by dead
            # space (or give up completion entirely when there's
            # simply no room).  Read once at startup -- prompt_toolkit
            # captures this value at PromptSession construction.
            reserve_space_for_menu=_menu_rows_for_terminal(),
        )

    # -- Reader thread --------------------------------------------------------

    def _start_reader(self) -> None:
        """Start the background serial reader thread."""

        def on_lines(lines: list[str]) -> None:
            for line in lines:
                if self.no_color:
                    line = strip_ansi(line)
                self._raw(line)

        reader_thread = threading.Thread(
            target=self.engine.read_loop,
            kwargs={
                "on_lines": on_lines,
                "on_clear": lambda: self._raw("\x1b[2J\x1b[H", end=""),
                "on_capture_done": lambda: self._stop_capture(),
                "on_error": lambda detail: self._err(f"Serial error: {detail}"),
                "on_disconnect": lambda: self._err("Serial disconnected"),
            },
            daemon=True,
        )
        reader_thread.start()

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
        ``wait_for_idle`` polls ``in_waiting`` on a 10ms timer.
        Between lines of a streamed response, the reader has often
        drained the OS buffer to 0 already, so a sample taken in
        that window sees 0 bytes and the idle clock ages even though
        the response is still in flight.  The observer fires on every
        actual reader-thread read, so the clock resets on real
        line-by-line streaming pacing -- matches the same pattern
        ``/cap.wire`` uses.

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
    _EXEC_IDLE_GAP_S = 0.5   # quiet period that signals "device done"
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
        Rich's colour markup renders correctly.  ``patch_stdout``
        replaces ``sys.stdout`` (and ``sys.stderr``) with a proxy that
        captures writes from any thread and schedules them into
        prompt_toolkit's layout.
        """
        # prompt_toolkit shows REPL commands - no need to echo those.
        # patch_stdout handles the main/reader-thread output ordering
        # structurally, so no wait_for_idle dance is needed between
        # prompts.
        self.ctx.ns("flags")["echo"] = False
        self.cfg["echo_input"] = False
        self._session = self._build_session()

        def _loop() -> None:
            while True:
                try:
                    from termapy.builtins.commands.var import expand_vars

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

                if line.lower() in (self.prefix + "exit", self.prefix + "quit"):
                    break
                if line.lower() == self.prefix + "tui":
                    self.switch_to = "tui"
                    break

                self._dispatch(line)

        # patch_stdout requires an actual TTY (it needs console
        # dimensions and cursor ops).  Only wrap the loop when a
        # prompt_toolkit session was built -- that condition already
        # mirrors "stdout is a TTY and cli_completion is on".  For
        # pipe / captured-stdout invocations (tests, --run piped
        # output, etc.) fall back to the plain loop.
        try:
            if self._session is not None:
                # raw=True passes ANSI escape codes through so Rich's
                # colour markup renders correctly in reader-thread
                # output.  Without raw=True, prompt_toolkit line-buffers
                # and renders the ESC byte as literal "?".  The menu
                # reservation that used to leave a blank band below
                # the prompt is disabled via reserve_space_for_menu=0
                # in _build_session.
                with patch_stdout(raw=True):
                    _loop()
            else:
                _loop()
        except KeyboardInterrupt:
            self._raw("\nInterrupted")
        finally:
            actual = (
                getattr(self.engine.port_obj, "port", "")
                or self.cfg["serial"]["port"]
            )
            self.engine.disconnect()
            msg = f"Disconnected: {actual}" if actual else "Disconnected."
            self.write(msg, "red")

    # -- Entry point ----------------------------------------------------------

    def run(self) -> str | None:
        """Connect, start reader, and run in script or interactive mode.

        Returns:
            Mode to switch to ("tui") or None for normal exit.
        """
        self.switch_to: str | None = None
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
        connect_cmd_sources = [
            self.cfg.get("on_connect_cmd", ""),
            self.cfg.get("cli_on_connect_cmd", ""),
        ]
        if not self.is_oneshot:
            for source in connect_cmd_sources:
                if not source:
                    continue
                for cmd in source.replace("\\n", "\n").split("\n"):
                    cmd = cmd.strip()
                    if cmd:
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
            # "no config found -- exit" behaviour for interactive use.
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
