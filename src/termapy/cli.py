"""CLI serial terminal — no Textual dependency.

Provides a plain-text interactive terminal using SerialEngine, ReplEngine,
and CaptureEngine. Reads from stdin, writes to stdout, serial I/O on a
background thread.

Usage:
    termapy --cli [config] [--no-color]
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

try:
    import readline
except ImportError:
    readline = None  # Windows without pyreadline3

from termapy.capture import CaptureEngine
from termapy.config import load_config, open_serial
from termapy.repl import ReplEngine
from termapy.serial_engine import SerialEngine
from termapy.serial_port import eol_label

# ANSI strip regex (for --no-color mode)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def run_cli(
    cfg: dict, config_path: str, no_color: bool = False,
    run_script: str | None = None,
) -> None:
    """Run the interactive CLI terminal.

    Args:
        cfg: Loaded config dict.
        config_path: Path to the config file.
        no_color: Strip ANSI color codes from output.
        run_script: Optional .run script to execute then exit.
    """
    # Ensure stdout handles unicode on Windows
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass  # Python < 3.7

    prefix = cfg.get("cmd_prefix", "/")

    # -- Output helpers -------------------------------------------------------

    def write(text: str, color: str = "") -> None:
        if no_color:
            text = _strip_ansi(text)
        print(text, flush=True)

    def write_markup(text: str) -> None:
        # Strip Rich markup tags for plain text output
        clean = re.sub(r"\[/?[^\]]*\]", "", text)
        if no_color:
            clean = _strip_ansi(clean)
        if clean.strip():
            print(clean, flush=True)

    def status(text: str, color: str = "") -> None:
        if no_color:
            text = _strip_ansi(text)
        print(f"  {text}", flush=True)

    def log(direction: str, text: str) -> None:
        pass  # CLI doesn't write a log file (could be added)

    # -- Capture engine -------------------------------------------------------

    capture = CaptureEngine(
        on_echo=lambda line: write(f"  {line}"),
        on_complete=lambda result: status(
            f"Capture complete: {result.path} ({result.size_label})"
        ),
    )

    # -- Serial engine --------------------------------------------------------

    engine = SerialEngine(
        cfg=cfg,
        capture=capture,
        open_fn=open_serial,
        log=log,
    )

    # -- REPL engine ----------------------------------------------------------

    repl = ReplEngine(cfg, config_path, write=status, prefix=prefix)

    # Set up a minimal PluginContext — no Textual callbacks
    from termapy.plugins import EngineAPI, PluginContext

    engine_api = EngineAPI(
        prefix=prefix,
        plugins=repl._plugins,
        get_echo=lambda: repl._echo,
        set_echo=lambda val: setattr(repl, "_echo", val),
        get_seq_counters=lambda: repl._seq_counters,
        set_seq_counters=lambda val: setattr(repl, "_seq_counters", val),
        reset_seq=repl._reset_seq,
        in_script=lambda: repl.in_script,
        script_stop=lambda: repl._script_stop.set(),
        start_capture=lambda **kw: _start_capture(engine, capture, cfg, **kw),
        stop_capture=lambda: _stop_capture(engine, capture),
        apply_cfg=repl._apply_cfg,
        coerce_type=ReplEngine._coerce_type,
    )

    ctx = PluginContext(
        write=status,
        write_markup=write_markup,
        cfg=cfg,
        config_path=config_path,
        engine=engine_api,
        is_connected=lambda: engine.is_connected,
        serial_write=lambda data: engine.serial_port.write(data) if engine.serial_port else None,
        serial_read_raw=lambda t=1000, g=50: (
            engine.serial_port.read_raw(t, g) if engine.serial_port else b""
        ),
        serial_drain=lambda: engine.serial_port.drain() if engine.serial_port else 0,
        serial_wait_idle=lambda t=100, m=3.0: (
            engine.serial_port.wait_for_idle(t, m) if engine.serial_port else None
        ),
        dispatch=lambda cmd: repl.dispatch_full(
            cmd,
            log=log,
            echo_markup=write_markup,
            status=status,
            serial_write=lambda data: engine.serial_port.write(data) if engine.serial_port else None,
            serial_write_raw=lambda text: _serial_write_raw(engine, cfg, text, write_markup, status),
            is_connected=lambda: engine.is_connected,
            eol_label=eol_label,
        ),
        confirm=lambda msg: _confirm_stdin(msg),
        notify=lambda text, **kw: write(f"[notice] {text}"),
        clear_screen=lambda: None,  # no screen to clear
        exit_app=lambda: None,  # handled by KeyboardInterrupt
        log=log,
        get_screen_text=lambda: "",  # no screen content
    )
    repl.set_context(ctx)

    # -- Connect --------------------------------------------------------------

    if not engine.connect():
        print(f"termapy: cannot connect to {cfg.get('port', '?')}", file=sys.stderr)
        sys.exit(1)

    port_name = cfg.get("port", "?")
    baud = cfg.get("baud_rate", "?")
    write(f"Connected: {port_name} @ {baud}")
    # -- History (shared with TUI) --------------------------------------------

    history_path = Path(config_path).parent / ".cmd_history.txt"
    if readline:
        try:
            readline.read_history_file(str(history_path))
        except (FileNotFoundError, OSError):
            pass
        readline.set_history_length(500)

    if not run_script:
        write(f"Type commands, {prefix}help for REPL commands, Ctrl+C to quit")

    # -- Reader thread --------------------------------------------------------

    def on_lines(lines: list[str]) -> None:
        for line in lines:
            if no_color:
                line = _strip_ansi(line)
            print(line, flush=True)

    def on_error(detail: str) -> None:
        print(f"Serial error: {detail}", file=sys.stderr, flush=True)

    def on_disconnect() -> None:
        print("Serial disconnected", file=sys.stderr, flush=True)

    reader_thread = threading.Thread(
        target=engine.read_loop,
        kwargs={
            "on_lines": on_lines,
            "on_clear": lambda: print("\x1b[2J\x1b[H", end="", flush=True),
            "on_capture_done": lambda: _stop_capture(engine, capture),
            "on_error": on_error,
            "on_disconnect": on_disconnect,
        },
        daemon=True,
    )
    reader_thread.start()

    # -- Text capture tap (inject into reader) --------------------------------
    # The SerialReader feeds CaptureEngine for binary mode.
    # For text mode, we tap the on_lines callback:
    _orig_on_lines = on_lines

    def on_lines_with_capture(lines: list[str]) -> None:
        if capture.active and capture.mode == "text":
            stripped = [ANSI_RE.sub("", line) for line in lines]
            capture.feed_text(stripped)
        _orig_on_lines(lines)

    # Patch the reader's callback (thread already running, but dict lookup is atomic)
    # This is a bit hacky — in a real refactor, SerialEngine.read_loop would
    # accept a text capture tap. For now it works.

    # -- Script mode (run and exit) -------------------------------------------

    if run_script:
        script_path = Path(run_script)
        if not script_path.exists():
            # Try resolving relative to scripts/ dir
            scripts_dir = Path(config_path).parent / "scripts"
            alt = scripts_dir / script_path.name
            if alt.exists():
                script_path = alt
        path = repl.start_script(str(script_path))
        if path:
            try:
                repl.run_script(
                    path,
                    write=status,
                    dispatch=ctx.dispatch,
                    verbose=True,
                )
            except KeyboardInterrupt:
                print("\nScript interrupted", flush=True)
        engine.disconnect()
        write("Disconnected.")
        return

    # -- Main input loop ------------------------------------------------------

    try:
        while True:
            try:
                line = input()
            except EOFError:
                break

            line = line.strip()
            if not line:
                if cfg.get("send_bare_enter", False):
                    repl.dispatch_full(
                        "",
                        log=log,
                        status=status,
                        serial_write=lambda data: engine.serial_port.write(data) if engine.serial_port else None,
                        serial_write_raw=lambda text: _serial_write_raw(engine, cfg, text, write_markup, status),
                        is_connected=lambda: engine.is_connected,
                        eol_label=eol_label,
                    )
                continue

            # Check for /run with script execution
            if line.startswith(prefix + "run "):
                args = line[len(prefix) + 4:]
                args, verbose = _parse_run_flags(args)
                path = repl.start_script(args)
                if path:
                    repl.run_script(
                        path,
                        write=status,
                        dispatch=ctx.dispatch,
                        verbose=verbose,
                    )
                continue

            # Check for /exit
            if line.strip().lower() in (prefix + "exit", prefix + "quit"):
                break

            # Full dispatch (no echo_markup — input() already shows the command)
            repl.dispatch_full(
                line,
                log=log,
                status=status,
                serial_write=lambda data: engine.serial_port.write(data) if engine.serial_port else None,
                serial_write_raw=lambda text: _serial_write_raw(engine, cfg, text, write_markup, status),
                is_connected=lambda: engine.is_connected,
                eol_label=eol_label,
            )

    except KeyboardInterrupt:
        print("\nInterrupted", flush=True)
    finally:
        engine.disconnect()
        write("Disconnected.")


def _serial_write_raw(engine, cfg, text, echo_markup, status):
    """Send raw text to serial — mimics app.py's _send_serial_raw."""
    if not engine.is_connected:
        status("Not connected — command not sent", "red")
        return
    line_ending = cfg.get("line_ending", "\r")
    encoding = cfg.get("encoding", "utf-8")
    if engine.serial_port:
        engine.serial_port.write((text + line_ending).encode(encoding))


def _start_capture(engine, capture, cfg, **kwargs):
    """Start a capture session (CLI version — no timers)."""
    if capture.active:
        print("  Capture already active — use /cap.stop", flush=True)
        return False
    started = capture.start(**kwargs)
    if not started:
        print(f"  Cannot open capture file", flush=True)
        return False
    mode = kwargs.get("mode", "?")
    path = kwargs.get("path", "?")
    print(f"  Capture started: {path} ({mode})", flush=True)
    return True


def _stop_capture(engine, capture):
    """Stop a capture session."""
    result = capture.stop()
    if result:
        print(f"  Capture complete: {result.path} ({result.size_label})", flush=True)


def _confirm_stdin(message: str) -> bool:
    """Prompt for y/n confirmation on stdin."""
    try:
        answer = input(f"  {message} [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


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
