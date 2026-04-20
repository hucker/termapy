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
from termapy.config import open_serial
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

        # Device command completion
        target_cmds = self._repl.ctx.ns("target_commands")
        if target_cmds:
            for name in sorted(target_cmds):
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
        term_width: int | None = None,
        zero_config: bool = False,
    ) -> None:
        """Create a CLI terminal frontend.

        Args:
            cfg: Config dict (owned by the engine).
            config_path: Path to the JSON config file, or ``""`` for
                zero-config mode (no config file, in-memory defaults).
            no_color: Strip Rich color markup.
            run_script: Optional .run path; if given, runs script and exits.
            term_width: Optional terminal width override.
            zero_config: True when the app started without a config file.
                Triggers the welcome banner + port list on ``run()`` and
                skips the automatic initial connect so the user can pick
                a port interactively.
        """
        self.cfg = cfg
        self.config_path = config_path
        self.no_color = no_color
        self.run_script = run_script
        self.term_width = term_width
        self.zero_config = zero_config
        self.prefix = cmd_prefix(cfg)
        self._xfer_cancel = threading.Event()

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
                f"Capture complete: {result.path} ({result.size_label})"
            ),
        )
        self.engine = SerialEngine(
            cfg=cfg,
            capture=self.capture,
            open_fn=open_serial,
            log=self._log,
        )
        self.repl = ReplEngine(cfg, config_path, write=self.status, prefix=self.prefix)

        from termapy.builtins.plugins.var import (
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
        """Write text to stdout via Rich console."""
        if color:
            self.console.print(f"[{color}]{text}[/]")
        else:
            self.console.print(text)

    def write_markup(self, text: str) -> None:
        """Write Rich markup text to stdout."""
        self.console.print(text)

    def status(self, text: str, color: str = "") -> None:
        """Write an indented status message."""
        if color:
            self.console.print(f"  [{color}]{text}[/]")
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
        """Build PluginContext and EngineAPI, wire to REPL."""
        engine_api = self._build_engine_api()

        self.ctx = self._build_plugin_context(engine_api)
        # CLI-specific callbacks
        self.ctx.notify = lambda text, **kw: self.write(f"[notice] {text}")
        self.ctx.clear_screen = lambda: self._raw("\x1b[2J\x1b[H", end="")
        self.ctx.exit_app = lambda: None
        self.ctx.get_screen_text = lambda: ""
        # CLI provides only the baseline; no TUI features (dialogs,
        # screen capture, status bar, toast notifications).  block_until
        # is added dynamically by the script runner when running a .run
        # file in CLI mode -- see ReplEngine._effective_capabilities.
        self.ctx.capabilities = CapabilitySet()

        self.repl.set_context(self.ctx)
        self._init_flags(echo=False)

    def _register_hooks(self) -> None:
        """Register CLI-specific hooks for /delay, /color, /run."""
        self.repl.register_hook(
            "delay",
            "<duration>",
            "Wait for duration with progress bar (e.g. 500ms, 1.5s).",
            self._hook_delay,
            source="app",
        )
        self.repl.register_hook(
            "delay.quiet",
            "<duration>",
            "Wait silently (no progress bar or output).",
            self._hook_delay_quiet,
            source="app",
        )
        self.repl.register_hook(
            "color",
            "{on|off}",
            "Show or toggle color output.",
            self._hook_color,
            source="app",
        )
        self.repl.register_hook(
            "run",
            "{filename}",
            "Run a script file, or list available scripts.",
            self._hook_run,
            source="app",
            flags={
                "--verbose": "Show each command and its result as the script runs.",
                "-v": "--verbose",
            },
        )
        self.repl.register_hook(
            "run.profile",
            "{filename}",
            "Run a script with per-command timing.",
            self._hook_run_profile,
            source="app",
            flags={
                "--verbose": "Show each command and its result as the script runs.",
                "-v": "--verbose",
            },
        )
        # /run is a hook (not a plugin) because it needs CLI-specific
        # script-running machinery; register the standard folder
        # subcommands (list/explore/show/dump) as sibling hooks so
        # every folder-owning command gets the same shape regardless
        # of whether its root is a plugin or a hook.
        from termapy.folder_ops import build_folder_subcommands

        for sub_name, sub_cmd in build_folder_subcommands("run").items():
            # build_folder_subcommands always populates handler; guard
            # anyway so ty doesn't complain about the Optional type.
            if sub_cmd.handler is None:
                continue
            self.repl.register_hook(
                f"run.{sub_name}",
                sub_cmd.args,
                sub_cmd.help,
                sub_cmd.handler,
                source="app",
            )
        self.repl.register_hook(
            "demo",
            "",
            "Set up and switch to the demo device config.",
            self._hook_demo,
            source="app",
        )
        self.repl.register_hook(
            "demo.force",
            "",
            "Reset demo config to defaults.",
            lambda ctx, args: self._hook_demo(ctx, "--force"),
            source="app",
        )
        self.repl.register_hook(
            "clr",
            "",
            "Clear the terminal screen (alias for {prefix}cls).",
            lambda ctx, args: (ctx.clear_screen(), CmdResult.ok())[-1],
            source="app",
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
        )
        self.repl.register_hook(
            "log.clear",
            "",
            "Delete the session log file.",
            self._hook_log_clear,
            source="app",
        )
        self.repl.register_hook(
            "tui",
            "",
            "Switch to TUI mode.",
            lambda ctx, args: CmdResult.ok(),  # handled in _run_interactive
            source="app",
        )
        self.repl.register_hook(
            "cli",
            "",
            "Already in CLI mode.",
            lambda ctx, args: CmdResult.ok(),
            source="app",
        )
        self.repl.register_hook(
            "cli.completion",
            "{on|off}",
            "Show or toggle CLI tab completion, auto-suggest, and help toolbar.",
            self._hook_cli_completion,
            source="app",
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
        else:
            state = "on" if self.cfg.get("cli_completion", True) else "off"
            self.status(f"CLI completion: {state}")
        return CmdResult.ok()

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
        return CmdResult.ok()

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
        return CmdResult.ok()

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
        else:
            state = "on" if not self.console.no_color else "off"
            self.status(f"Color: {state}")
        return CmdResult.ok()

    def _hook_run(self, ctx, args: str):
        """Run a script file or list available scripts."""

        script = args.strip()
        if not script:
            scripts_dir = Path(self.config_path).parent / "run"
            if not scripts_dir.is_dir():
                self.status("No run/ directory found.")
                return CmdResult.ok()
            files = sorted(scripts_dir.glob("*.run"))
            if not files:
                self.status("No .run files found in run/")
                return CmdResult.ok()
            self.status("Available scripts:")
            for f in files:
                self.status(f"  {f.name}")
            return CmdResult.ok()
        verbose = ctx.flag("--verbose")
        path, result = self.repl.start_script(script)
        if path:
            self.repl.run_script(
                path,
                write=self.status,
                dispatch=self.ctx.dispatch,
                verbose=verbose,
            )
        return result

    def _hook_run_profile(self, ctx, args: str):
        """Run a script with per-command timing."""

        script = args.strip()
        if not script:
            self.status("Usage: /run.profile <script>", "red")
            return CmdResult.fail(msg="Usage: /run.profile <script>")
        verbose = ctx.flag("--verbose")
        path, result = self.repl.start_script(script)
        if path:
            self.repl.run_script(
                path,
                write=self.status,
                dispatch=self.ctx.dispatch,
                profile=True,
                verbose=verbose,
            )
        return result

    def _switch_to_cfg_path(self, config_path: str) -> CmdResult:
        """Disconnect, load the cfg at the given path, and reconnect.

        Shared machinery for both ``/demo`` and the ``/cfg.load``
        subcommand.  The caller is responsible for resolving a
        user-facing name into a concrete path (and for any cfg-creation
        side effects like ``setup_demo_config``).

        Returns ``CmdResult.ok(value=path)`` on success.  Any OSError
        or JSON decode error becomes ``CmdResult.fail(msg=...)`` so
        the caller can surface the failure through its frontend
        without unwinding the exception.
        """
        from termapy.config import load_config

        try:
            cfg = load_config(config_path)
        except (OSError, ValueError) as e:
            return CmdResult.fail(msg=f"Cannot load config: {e}")

        if self.engine.is_connected:
            self.repl.fire_lifecycle("on_disconnect")
            self.engine.disconnect()
        self.repl.replace_cfg(cfg, config_path)
        self.config_path = config_path
        self.cfg = cfg
        self._setup_context()
        self.repl.fire_lifecycle("on_config_load")
        # auto_connect lives in the newly-loaded cfg -- if the user's
        # saved config wants the port opened on start, honour it here.
        if cfg.get("auto_connect") and self.engine.connect():
            self.repl.fire_lifecycle("on_connect")
            self._start_reader()
        return CmdResult.ok(value=config_path)

    def _load_config(self, name: str) -> CmdResult:
        """Resolve a user-supplied config name and switch to it.

        Accepts a bare name ("myproj"), a relative path
        ("termapy_cfg/myproj"), or an absolute path.  Uses the same
        resolution chain as the ``[config]`` positional CLI argument
        so "what works on the command line works in the REPL."

        Returns ``CmdResult.fail`` when the name can't be resolved,
        when the file can't be read, or when JSON parsing fails.
        """
        from termapy.config_resolve import resolve_config

        path = resolve_config(name)
        if path is None:
            return CmdResult.fail(
                msg=f"No config matching {name!r}. "
                f"Try {self.prefix}cfg.configs to list available."
            )
        result = self._switch_to_cfg_path(path)
        if result.success:
            self.status(f"Loaded config: {Path(path).stem}", "green")
        return result

    def _hook_demo(self, ctx, args: str):
        """Set up and switch to demo device config."""
        from termapy.config import cfg_dir, setup_demo_config

        force = "--force" in args.lower()
        try:
            ctx.status("Setting up demo files...")
            config_path = str(setup_demo_config(cfg_dir(), force=force))
        except OSError as e:
            return CmdResult.fail(msg=f"Demo setup failed: {e}")

        ctx.status("Loading demo config...")
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
        self.engine.disconnect()
        self.write("Disconnected.", "red")

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
                    from termapy.builtins.plugins.var import expand_vars

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
            self.engine.disconnect()
            self.write("Disconnected.", "red")

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
        # user type /port.open <name> to pick one.  Every other path --
        # a real config file, --demo, --run with an inferred config --
        # has a non-empty cfg["port"] by contract, so the else branch
        # here just connects as before.
        if self.zero_config:
            self._show_zero_config_welcome()
        else:
            if not self._connect():
                sys.exit(1)

        # Show hint before on_connect_cmd so it appears first
        if not self.run_script:
            self.write(
                f"Type commands, {self.prefix}help for REPL commands, Ctrl+C to quit",
                "dim",
            )

        # Auto-import target commands if configured
        if self.cfg.get("device_json_cmd", "") and not self.run_script:
            self._dispatch(self.repl.cmd("include"))

        # Run on_connect_cmd (same as TUI does after connecting)
        auto_cmd = self.cfg.get("on_connect_cmd", "")
        if auto_cmd and not self.run_script:
            parts = auto_cmd.replace("\\n", "\n").split("\n")
            for cmd in parts:
                cmd = cmd.strip()
                if cmd:
                    self._dispatch(cmd)
                    if self.engine.is_connected and self.engine.serial_port:
                        self.engine.serial_port.wait_for_idle()

        if self.run_script:
            self._run_script_mode(self.run_script)
        else:
            self._run_interactive()
        self.repl.fire_lifecycle("on_app_stop")
        return self.switch_to

    def _show_zero_config_welcome(self) -> None:
        """Print the zero-config welcome banner + port list + hint.

        Called from ``run()`` when ``zero_config`` is set.  Lists the
        currently-available serial ports (via ``port_control.list_ports``)
        and shows the user what ``/port.open`` invocation would use them
        with the built-in defaults (115200 N81 cr noecho).  The user then
        types the actual ``/port.open`` to connect.
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
                f"Try:  {self.prefix}port.open {first_port}",
                "green",
            )
            self.write(
                f"Or:   {self.prefix}port.open {first_port} 9600 N81 crlf echo",
                "dim",
            )
        else:
            self.write(
                f"Try:  {self.prefix}port.open DEMO    "
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
