"""CLI serial terminal - no Textual dependency.

Provides a plain-text interactive terminal using SerialEngine, ReplEngine,
and CaptureEngine. Reads from stdin, writes to stdout, serial I/O on a
background thread.

Usage:
    termapy --cli [config] [--no-color]
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

try:
    import readline
except ImportError:
    readline = None  # Windows without pyreadline3

from termapy.capture import CaptureEngine
from termapy.config import load_config, open_serial, open_with_system
from termapy.plugins import EngineAPI, PluginContext
from termapy.repl import ReplEngine
from termapy.scripting import strip_ansi
from termapy.serial_engine import SerialEngine
from termapy.serial_port import eol_label


class CLITerminal:
    """Plain-text serial terminal - no Textual dependency.

    Owns the serial engine, REPL engine, capture engine, and Rich console.
    Registers CLI-specific hooks for /delay, /color, /run.

    Args:
        cfg: Loaded config dict.
        config_path: Path to the config file.
        no_color: Strip ANSI color codes from output.
        run_script: Optional .run script to execute then exit.
    """

    _HISTORY_LIMIT = 30

    def __init__(
        self, cfg: dict, config_path: str,
        no_color: bool = False, run_script: str | None = None,
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
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
            cfg=cfg, capture=self.capture, open_fn=open_serial, log=self._log,
        )
        self.repl = ReplEngine(cfg, config_path, write=self.status, prefix=self.prefix)

        from termapy.builtins.plugins.var import set_launch_var
        set_launch_var("FRONT_END", "cli")
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

    def _log(self, direction: str, text: str) -> None:
        """Log callback - CLI doesn't write a log file."""
        pass

    # -- Context and hooks ----------------------------------------------------

    def _setup_context(self) -> None:
        """Build PluginContext and EngineAPI, wire to REPL."""
        engine_api = EngineAPI(
            prefix=self.prefix,
            plugins=self.repl._plugins,
            get_echo=lambda: self.repl._echo,
            set_echo=lambda val: setattr(self.repl, "_echo", val),
            get_seq_counters=lambda: self.repl._seq_counters,
            set_seq_counters=lambda val: setattr(self.repl, "_seq_counters", val),
            reset_seq=self.repl._reset_seq,
            in_script=lambda: self.repl.in_script,
            script_stop=lambda: self.repl._script_stop.set(),
            start_capture=lambda **kw: self._start_capture(**kw),
            stop_capture=lambda: self._stop_capture(),
            apply_cfg=self.repl._apply_cfg,
            coerce_type=ReplEngine._coerce_type,
            connect=lambda port=None: self._connect(port),
            disconnect=lambda: self._disconnect(),
            apply_port_effects=lambda effects: self._apply_port_effects(effects),
        )

        self.ctx = PluginContext(
            write=self.status,
            write_markup=self.write_markup,
            cfg=self.cfg,
            config_path=self.config_path,
            engine=engine_api,
            port=lambda: (
                self.engine.serial_port.port
                if self.engine.is_connected and self.engine.serial_port
                else None
            ),
            is_connected=lambda: self.engine.is_connected,
            serial_write=lambda data: (
                self.engine.serial_port.write(data)
                if self.engine.serial_port else None
            ),
            serial_send=lambda text: (
                self.engine.serial_port.write(
                    (text + self.cfg.get("line_ending", "\r"))
                    .encode(self.cfg.get("encoding", "utf-8"))
                )
                if self.engine.serial_port else None
            ),
            serial_read_raw=lambda timeout_ms=1000, frame_gap_ms=50: (
                self.engine.serial_port.read_raw(timeout_ms, frame_gap_ms)
                if self.engine.serial_port else b""
            ),
            serial_drain=lambda: (
                self.engine.serial_port.drain()
                if self.engine.serial_port else 0
            ),
            serial_claim=lambda: setattr(self.engine, "proto_active", True),
            serial_release=lambda: setattr(self.engine, "proto_active", False),
            serial_wait_idle=lambda timeout_ms=20, max_wait_s=3.0: (
                self.engine.serial_port.wait_for_idle(timeout_ms, max_wait_s)
                if self.engine.serial_port else None
            ),
            dispatch=lambda cmd: self._dispatch(cmd),
            confirm=lambda msg: self._confirm(msg),
            notify=lambda text, **kw: self.write(f"[notice] {text}"),
            clear_screen=lambda: None,
            open_file=lambda path: open_with_system(str(path)),
            exit_app=lambda: None,
            log=self._log,
            get_screen_text=lambda: "",
        )
        self.repl.set_context(self.ctx)

    def _register_hooks(self) -> None:
        """Register CLI-specific hooks for /delay, /color, /run."""
        self.repl.register_hook(
            "delay", "<duration>",
            "Wait for duration with progress bar (e.g. 500ms, 1.5s).",
            self._hook_delay, source="app",
        )
        self.repl.register_hook(
            "delay.quiet", "<duration>",
            "Wait silently (no progress bar or output).",
            self._hook_delay_quiet, source="app",
        )
        self.repl.register_hook(
            "color", "{on|off}",
            "Show or toggle color output.",
            self._hook_color, source="app",
        )
        self.repl.register_hook(
            "run", "{filename}",
            "Run a script file, or list available scripts.",
            self._hook_run, source="app",
        )
        self.repl.register_hook(
            "run.profile", "{filename}",
            "Run a script with per-command timing.",
            self._hook_run_profile, source="app",
        )
        self.repl.register_hook(
            "demo", "",
            "Set up and switch to the demo device config.",
            self._hook_demo, source="app",
        )
        self.repl.register_hook(
            "demo.force", "",
            "Reset demo config to defaults.",
            lambda ctx, args: self._hook_demo(ctx, "--force"),
            source="app",
        )

    # -- Hook handlers --------------------------------------------------------

    def _hook_delay(self, ctx, args: str):
        """Wait with progress bar (>=1s) or silently (<1s).
        Shows elapsed/total time and sub-character resolution bar.
        Ctrl+C cancels."""
        from termapy.scripting import CmdResult, parse_duration

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
            print(f"\r  Delay cancelled.{' ' * 30}", flush=True)
        return CmdResult.ok()

    def _hook_delay_quiet(self, ctx, args: str):
        """Wait silently - no progress bar, no output.
        For scripts where delay output would clutter results."""
        from termapy.scripting import CmdResult, parse_duration
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
        from termapy.scripting import CmdResult
        val = args.strip().lower()
        if val in ("on", "1", "true"):
            self.console.no_color = False
            self.status("Color enabled.", "green")
        elif val in ("off", "0", "false"):
            self.console.no_color = True
            self.status("Color disabled.")
        else:
            state = "on" if not self.console.no_color else "off"
            self.status(f"Color: {state}")
        return CmdResult.ok()

    def _hook_run(self, ctx, args: str):
        """Run a script file or list available scripts."""
        from termapy.scripting import CmdResult
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
        script, verbose = _parse_run_flags(script)
        path = self.repl.start_script(script)
        if path:
            self.repl.run_script(
                path, write=self.status, dispatch=self.ctx.dispatch, verbose=verbose,
            )
            return CmdResult.ok()
        return CmdResult.fail(msg=f"Script not found: {script}")

    def _hook_run_profile(self, ctx, args: str):
        """Run a script with per-command timing."""
        from termapy.scripting import CmdResult

        script = args.strip()
        if not script:
            self.status("Usage: /run.profile <script>", "red")
            return CmdResult.fail(msg="Usage: /run.profile <script>")
        script, verbose = _parse_run_flags(script)
        path = self.repl.start_script(script)
        if path:
            self.repl.run_script(
                path, write=self.status, dispatch=self.ctx.dispatch,
                profile=True, verbose=verbose,
            )
            return CmdResult.ok()
        return CmdResult.fail(msg=f"Script not found: {script}")

    def _hook_demo(self, ctx, args: str):
        """Set up and switch to demo device config."""
        from termapy.config import cfg_dir, load_config, setup_demo_config
        from termapy.scripting import CmdResult

        force = "--force" in args.lower()
        try:
            ctx.status("Setting up demo files...")
            config_path = setup_demo_config(cfg_dir(), force=force)
            ctx.status("Loading demo config...")
            cfg = load_config(str(config_path))
            # Disconnect current, switch config, reconnect
            if self.engine.is_connected:
                self.engine.disconnect()
            self.repl.replace_cfg(cfg, str(config_path))
            self.config_path = str(config_path)
            self.cfg = cfg
            self._setup_context()
            if self.engine.connect():
                self._start_reader()
            msg = "Switched to demo device"
            if force:
                msg += " (config reset)"
            self.status(msg, "green")
            return CmdResult.ok()
        except Exception as e:
            return CmdResult.fail(msg=f"Demo setup failed: {e}")

    # -- Progress bar ---------------------------------------------------------

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
            print(
                f"\r  [{bar}] {int(elapsed)}s/{int(seconds)}s",
                end="", flush=True,
            )
            time.sleep(0.25)
        bar = full_ch * width
        print(f"\r  [{bar}] {int(seconds)}s/{int(seconds)}s", end="", flush=True)
        msg = f"Delay {label} done."
        print(f"\r  {msg}{' ' * (width + 10 - len(msg))}", flush=True)

    # -- Serial helpers -------------------------------------------------------

    def _dispatch(self, cmd: str):
        """Route a command through the full dispatch pipeline."""
        return self.repl.dispatch_full(
            cmd,
            log=self._log,
            echo_markup=self.write_markup,
            status=self.status,
            serial_write=lambda data: (
                self.engine.serial_port.write(data)
                if self.engine.serial_port else None
            ),
            serial_write_raw=lambda text: self._serial_write_raw(text),
            is_connected=lambda: self.engine.is_connected,
            eol_label=eol_label,
        )

    def _serial_write_raw(self, text: str) -> None:
        """Send raw text to serial - mimics app.py's _send_serial_raw."""
        if not self.engine.is_connected:
            self.status("Not connected - command not sent", "red")
            return
        line_ending = self.cfg.get("line_ending", "\r")
        encoding = self.cfg.get("encoding", "utf-8")
        if self.engine.serial_port:
            self.engine.serial_port.write((text + line_ending).encode(encoding))

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
        else:
            self.status(f"Cannot connect to {self.cfg.get('port', '?')}", "red")

    def _disconnect(self) -> None:
        """Disconnect from the serial port."""
        if not self.engine.is_connected:
            self.write("Not connected", "yellow")
            return
        self.engine.disconnect()
        self.write("Disconnected.", "red")

    def _apply_port_effects(self, effects: dict) -> None:
        """Apply port_control side effects."""
        if effects.get("cfg_update"):
            for key, val in effects["cfg_update"].items():
                self.repl._cfg_data[key] = val

    # -- Capture helpers ------------------------------------------------------

    def _start_capture(self, **kwargs) -> bool:
        """Start a capture session."""
        if self.capture.active:
            self.status("Capture already active - use /cap.stop")
            return False
        started = self.capture.start(**kwargs)
        if not started:
            self.status("Cannot open capture file")
            return False
        mode = kwargs.get("mode", "?")
        path = kwargs.get("path", "?")
        self.status(f"Capture started: {path} ({mode})")
        return True

    def _stop_capture(self) -> None:
        """Stop a capture session."""
        result = self.capture.stop()
        if result:
            self.status(f"Capture complete: {result.path} ({result.size_label})")

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
                -self._HISTORY_LIMIT:
            ]:
                if line.strip():
                    readline.add_history(line)
        except (FileNotFoundError, OSError):
            pass

    def _save_history(self) -> None:
        """Save command history to the same file the TUI uses."""
        if not readline:
            return
        history_path = Path(self.config_path).parent / ".cmd_history.txt"
        entries = [
            readline.get_history_item(i + 1)
            for i in range(readline.get_current_history_length())
        ]
        entries = [e for e in entries if e][-self._HISTORY_LIMIT:]
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

        def _completer(text: str, state: int) -> str | None:
            if state == 0:
                line = readline.get_line_buffer()
                _completer.matches = []

                # File completion for /run and /run.edit args
                for fc in file_cmds:
                    if line.startswith(fc):
                        file_partial = line[len(fc):]
                        if scripts_dir.is_dir():
                            _completer.matches = [
                                fc + f.name
                                for f in sorted(scripts_dir.glob("*.run"))
                                if f.name.startswith(file_partial)
                            ]
                        # Only complete if exactly one match
                        if len(_completer.matches) != 1:
                            _completer.matches = []
                        return _completer.matches[0] if _completer.matches else None

                # Command completion
                if line.startswith(prefix):
                    _completer.matches = sorted(
                        f"{prefix}{name}"
                        for name in repl._plugins
                        if f"{prefix}{name}".startswith(line)
                    )

            if state < len(_completer.matches):
                return _completer.matches[state]
            return None

        _completer.matches = []
        readline.set_completer(_completer)
        readline.parse_and_bind("tab: complete")
        readline.set_completer_delims("")

    # -- Reader thread --------------------------------------------------------

    def _start_reader(self) -> None:
        """Start the background serial reader thread."""
        def on_lines(lines: list[str]) -> None:
            for line in lines:
                if self.no_color:
                    line = strip_ansi(line)
                print(line, flush=True)

        reader_thread = threading.Thread(
            target=self.engine.read_loop,
            kwargs={
                "on_lines": on_lines,
                "on_clear": lambda: print("\x1b[2J\x1b[H", end="", flush=True),
                "on_capture_done": lambda: self._stop_capture(),
                "on_error": lambda detail: print(
                    f"Serial error: {detail}", file=sys.stderr, flush=True
                ),
                "on_disconnect": lambda: print(
                    "Serial disconnected", file=sys.stderr, flush=True
                ),
            },
            daemon=True,
        )
        reader_thread.start()

    # -- Run modes ------------------------------------------------------------

    def _run_script_mode(self) -> None:
        """Execute a .run script and exit."""
        script_path = Path(self.run_script)
        if not script_path.exists():
            scripts_dir = Path(self.config_path).parent / "run"
            alt = scripts_dir / script_path.name
            if alt.exists():
                script_path = alt
        path = self.repl.start_script(str(script_path))
        if path:
            try:
                self.repl.run_script(
                    path, write=self.status, dispatch=self.ctx.dispatch, verbose=True,
                )
            except KeyboardInterrupt:
                print("\nScript interrupted", flush=True)
        self.engine.disconnect()
        self.write("Disconnected.", "red")

    def _run_interactive(self) -> None:
        """Run the interactive input loop."""
        # Readline shows input — no need to echo commands
        self.repl._echo = False
        self.write(
            f"Type commands, {self.prefix}help for REPL commands, Ctrl+C to quit",
            "dim",
        )
        try:
            while True:
                try:
                    line = input(self.cfg.get("cli_prompt", "> "))
                except EOFError:
                    break

                line = line.strip()
                if not line:
                    if self.cfg.get("send_bare_enter", False):
                        self._dispatch("")
                    continue

                if line.lower() in (self.prefix + "exit", self.prefix + "quit"):
                    break

                self._dispatch(line)

        except KeyboardInterrupt:
            print("\nInterrupted", flush=True)
        finally:
            self._save_history()
            self.engine.disconnect()
            self.write("Disconnected.", "red")

    # -- Entry point ----------------------------------------------------------

    def run(self) -> None:
        """Connect, start reader, and run in script or interactive mode."""
        if not self.engine.connect():
            print(
                f"termapy: cannot connect to {self.cfg.get('port', '?')}",
                file=sys.stderr,
            )
            sys.exit(1)

        from termapy.config import connection_string, hardware_signals
        conn = connection_string(self.cfg)
        hw = hardware_signals(self.engine.port_obj)
        full = f"Connected: {conn}  {hw}" if hw else f"Connected: {conn}"
        self.write(full, "green")

        self._load_history()
        self._setup_completion()
        self._start_reader()

        if self.run_script:
            self._run_script_mode()
        else:
            self._run_interactive()


def _parse_run_flags(args: str) -> tuple[str, bool]:
    """Extract -v/--verbose from /run args."""
    tokens = args.split()
    verbose = False
    clean = []
    for tok in tokens:
        if tok in ("-v", "--verbose"):
            verbose = True
        else:
            clean.append(tok)
    return " ".join(clean), verbose
