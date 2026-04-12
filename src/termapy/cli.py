"""CLI serial terminal - no Textual dependency.

Provides a plain-text interactive terminal using SerialEngine, ReplEngine,
and CaptureEngine. Reads from stdin, writes to stdout, serial I/O on a
background thread.

Usage:
    termapy --cli [config] [--no-color]
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

try:
    import readline
except ImportError:
    readline = None  # Windows without pyreadline3  # ty: ignore[invalid-assignment]

from termapy.capture import CaptureEngine
from termapy.config import open_serial
from termapy.plugins import CmdResult
from termapy.repl import ReplEngine
from termapy.scripting import strip_ansi
from termapy.serial_engine import SerialEngine
from termapy.terminal_host import TerminalHost


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
    ) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self.no_color = no_color
        self.run_script = run_script
        self.term_width = term_width
        self.prefix = cfg.get("cmd_prefix", "/")

        # Ensure stdout handles unicode on Windows
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # ty: ignore[unresolved-attribute]

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
            "CFG", lambda: Path(self.config_path).stem if self.config_path else "none"
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
        self.ctx.port = lambda: (
            self.engine.serial_port.port
            if self.engine.is_connected and self.engine.serial_port
            else None
        )
        self.ctx.serial_read_raw = lambda timeout_ms=1000, frame_gap_ms=50: (
            self.engine.serial_port.read_raw(timeout_ms, frame_gap_ms)
            if self.engine.serial_port
            else b""
        )
        self.ctx.serial_drain = lambda: (
            self.engine.serial_port.drain() if self.engine.serial_port else 0
        )
        self.ctx.serial_wait_idle = lambda timeout_ms=20, max_wait_s=3.0: (
            self.engine.serial_port.wait_for_idle(timeout_ms, max_wait_s)
            if self.engine.serial_port
            else None
        )
        self.ctx.notify = lambda text, **kw: self.write(f"[notice] {text}")
        self.ctx.clear_screen = lambda: self._raw("\x1b[2J\x1b[H", end="")
        self.ctx.exit_app = lambda: None
        self.ctx.get_screen_text = lambda: ""

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
        )
        self.repl.register_hook(
            "run.profile",
            "{filename}",
            "Run a script with per-command timing.",
            self._hook_run_profile,
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
            "Clear the terminal screen (alias for /cls).",
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

    # -- Hook handlers --------------------------------------------------------

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
        script, verbose = self._parse_run_flags(script)
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
        script, verbose = self._parse_run_flags(script)
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

    def _hook_demo(self, ctx, args: str):
        """Set up and switch to demo device config."""
        from termapy.config import cfg_dir, load_config, setup_demo_config

        force = "--force" in args.lower()
        try:
            ctx.status("Setting up demo files...")
            config_path = setup_demo_config(cfg_dir(), force=force)
            ctx.status("Loading demo config...")
            cfg = load_config(str(config_path))
            # Disconnect current, switch config, reconnect
            if self.engine.is_connected:
                self.repl.fire_lifecycle("on_disconnect")
                self.engine.disconnect()
            self.repl.replace_cfg(cfg, str(config_path))
            self.config_path = str(config_path)
            self.cfg = cfg
            self._setup_context()
            self.repl.fire_lifecycle("on_config_load")
            if self.engine.connect():
                self.repl.fire_lifecycle("on_connect")
                self._start_reader()
            msg = "Switched to demo device"
            if force:
                msg += " (config reset)"
            self.status(msg, "green")
            return CmdResult.ok()
        except Exception as e:
            return CmdResult.fail(msg=f"Demo setup failed: {e}")


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

    # -- Connection (CLI-specific UI) -----------------------------------------

    def _connect(self, port: str | None = None) -> None:
        """Connect to a serial port."""
        if self.engine.is_connected:
            self.status("Already connected", "yellow")
            return
        if port:
            self.cfg["port"] = port
        if self.engine.connect():
            from termapy.config import connection_string, hardware_signals

            conn = connection_string(self.cfg)
            hw = hardware_signals(self.engine.port_obj)
            full = f"Connected: {conn}  {hw}" if hw else f"Connected: {conn}"
            self.write(full, "green")
            self.repl.fire_lifecycle("on_connect")
        else:
            self.status(f"Cannot connect to {self.cfg.get('port', '?')}", "red")

    def _disconnect(self) -> None:
        """Disconnect from the serial port."""
        if not self.engine.is_connected:
            self.write("Not connected.", "yellow")
            return
        self.repl.fire_lifecycle("on_disconnect")
        self.engine.disconnect()
        self.write("Disconnected.", "red")

    # -- Confirmation ---------------------------------------------------------

    @staticmethod
    def _confirm(message: str) -> bool:
        """Prompt for y/n confirmation on stdin."""
        try:
            answer = input(f"  {message} [y/N] ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    # -- History --------------------------------------------------------------

    def _load_history(self) -> None:
        """Load command history from the same file the TUI uses."""
        if not readline:
            return
        history_path = Path(self.config_path).parent / ".cmd_history.txt"
        try:
            for line in history_path.read_text(encoding="utf-8").splitlines()[
                -self._HISTORY_LIMIT :
            ]:
                if line.strip():
                    readline.add_history(line)  # ty: ignore[unresolved-attribute]
        except (FileNotFoundError, OSError):
            pass

    def _save_history(self) -> None:
        """Save command history to the same file the TUI uses."""
        if not readline:
            return
        history_path = Path(self.config_path).parent / ".cmd_history.txt"
        entries = [
            readline.get_history_item(i + 1)  # ty: ignore[unresolved-attribute]
            for i in range(readline.get_current_history_length())  # ty: ignore[unresolved-attribute]
        ]
        entries = [e for e in entries if e][-self._HISTORY_LIMIT :]
        try:
            history_path.write_text("\n".join(entries), encoding="utf-8")
        except OSError:
            pass

    # -- Tab completion -------------------------------------------------------

    def _setup_completion(self) -> None:
        """Set up readline tab completion for commands and script files."""
        if not readline:
            return
        scripts_dir = Path(self.config_path).parent / "run"
        file_cmds = (f"{self.prefix}run ", f"{self.prefix}run.edit ")
        repl = self.repl
        prefix = self.prefix

        matches: list[str] = []

        def _completer(text: str, state: int) -> str | None:
            nonlocal matches
            if state == 0:
                line = readline.get_line_buffer()  # ty: ignore[unresolved-attribute]
                matches = []

                # File completion for /run and /run.edit args
                for fc in file_cmds:
                    if line.startswith(fc):
                        file_partial = line[len(fc) :]
                        if scripts_dir.is_dir():
                            matches = [
                                fc + f.name
                                for f in sorted(scripts_dir.glob("*.run"))
                                if f.name.startswith(file_partial)
                            ]
                        # Only complete if exactly one match
                        if len(matches) != 1:
                            matches = []
                        return matches[0] if matches else None

                # Command completion
                if line.startswith(prefix):
                    matches = sorted(
                        f"{prefix}{name}"
                        for name in repl._plugins
                        if f"{prefix}{name}".startswith(line)
                    )
                else:
                    target_cmds = repl.ctx.ns("target_commands")
                    if target_cmds:
                        matches = sorted(
                            name
                            for name in target_cmds
                            if name.lower().startswith(line.lower())
                        )

            if state < len(matches):
                return matches[state]
            return None
        readline.set_completer(_completer)  # ty: ignore[unresolved-attribute]
        readline.parse_and_bind("tab: complete")  # ty: ignore[unresolved-attribute]
        readline.set_completer_delims("")  # ty: ignore[unresolved-attribute]

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
        """Run the interactive input loop."""
        # Readline shows REPL commands — no need to echo those.
        # Serial echo is off — we sync manually with wait_for_idle after dispatch.
        self.ctx.ns("flags")["echo"] = False
        self.cfg["echo_input"] = False
        try:
            while True:
                try:
                    from termapy.builtins.plugins.var import expand_vars

                    prompt = expand_vars(self.cfg.get("cli_prompt", "> "))
                    line = input(prompt)
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
                # Wait for device response to finish printing before
                # readline draws the next prompt. Without this, the
                # background reader prints over readline's prompt.
                if self.engine.is_connected and self.engine.serial_port:
                    self.engine.serial_port.wait_for_idle()

        except KeyboardInterrupt:
            self._raw("\nInterrupted")
        finally:
            self._save_history()
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
        if not self.engine.connect():
            port = self.cfg.get("port", "?")
            detail = self.engine.last_error
            msg = (
                f"termapy: cannot open {port}: {detail}"
                if detail
                else f"termapy: cannot open {port}"
            )
            self._err(msg)
            sys.exit(1)

        from termapy.config import connection_string, hardware_signals

        conn = connection_string(self.cfg)
        hw = hardware_signals(self.engine.port_obj)
        full = f"Connected: {conn}  {hw}" if hw else f"Connected: {conn}"
        self.write(full, "green")
        self.repl.fire_lifecycle("on_connect")

        self._load_history()
        self._setup_completion()
        self._start_reader()

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
