"""Built-in plugin: ``/log.*`` -- session-log commands.

Groups the three read-side session-log commands under one ``/log``
parent:

* ``/log.dump``        -- print the log to the terminal (all or an N-line slice)
* ``/log.fingerprint`` -- write a provenance fingerprint to the log
* ``/log.show``        -- open the log in the system viewer

The write-side ``/log.clear`` and ``/log.delete`` are registered
separately as host hooks (they need frontend state), and coexist with
this parent: registering ``log.clear`` / ``log.delete`` does not touch
the ``log`` parent or its ``dump`` / ``fingerprint`` / ``show`` children.

Lives in ``builtins/commands/`` so MCP, CLI, and TUI all share the same
handlers.
"""

from __future__ import annotations

import os
import platform as _platform
import socket
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_log_path, open_with_system
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import select_lines

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _log_path(ctx: PluginContext) -> str:
    """Resolve the session log path from ctx.cfg / ctx.config_path."""
    configured = ctx.cfg.get("log_file", "") if ctx.cfg else ""
    if configured:
        return str(Path(configured).resolve())
    if ctx.config_path:
        return cfg_log_path(ctx.config_path)
    return ""


# ── /log.dump ─────────────────────────────────────────────────────────────────


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print the session log: all, last N (N>0), or first N (N<0) lines."""
    path = _log_path(ctx)
    if not path:
        return CmdResult.fail(msg="No log file configured.")
    p = Path(path)
    if not p.exists():
        return CmdResult.fail(msg=f"Log file not found: {path}")

    n: int | None = None
    arg = args.strip()
    if arg:
        try:
            n = int(arg)
        except ValueError:
            return CmdResult.fail(
                msg=f"Usage: {ctx.engine.prefix}log.dump [N]  (N>0 last N, N<0 first N)"
            )
        if n == 0:
            return CmdResult.fail(msg="Invalid line count: 0")

    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")

    lines = select_lines(lines, n)

    for line in lines:
        ctx.io.output(line)
    return CmdResult.ok(value=str(len(lines)))


_DUMP_HELP = "Print the session log; /log.dump N for last N lines, -N for first N."
_DUMP_LONG_HELP = (
    "With no argument, prints the entire session log.  With a signed "
    "integer N, prints a slice: N>0 the last N lines (most recent), "
    "N<0 the first N (oldest) -- useful when the log is long and only "
    "one end matters."
)


# ── /log.fingerprint ──────────────────────────────────────────────────────────


def _kv(key: str, value: object, col: int = 22) -> str:
    """Align a ``key: value`` pair on a consistent column."""
    return f"{key:<{col}}{value}"


def _termapy_version() -> str:
    """Return the installed termapy version, or '(dev)' if unknown."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _get_version

    try:
        return _get_version("termapy")
    except PackageNotFoundError:
        return "(dev / source checkout)"


def _python_info() -> str:
    impl = _platform.python_implementation()
    ver = _platform.python_version()
    return f"{ver} ({impl.lower()})"


def _env_snapshot() -> list[str]:
    """Harvest the terminal-identification env vars we care about."""
    keys = [
        "TERM",
        "TERM_PROGRAM",
        "TERM_PROGRAM_VERSION",
        "COLORTERM",
        "LANG",
        "LC_ALL",
        "SHELL",
        "COMSPEC",
        "WT_SESSION",  # Windows Terminal
        "WT_PROFILE_ID",
        "ConEmuPID",  # ConEmu / Cmder
        "VSCODE_PID",
        "VSCODE_INJECTION",
        "VSCODE_IPC_HOOK_CLI",
        "SSH_CONNECTION",
        "SSH_TTY",
        "TMUX",
        "STY",  # screen
        "WSL_DISTRO_NAME",
    ]
    lines: list[str] = []
    for k in keys:
        v = os.environ.get(k)
        if v:
            lines.append(_kv(f"  {k}", v))
    if not lines:
        lines.append(_kv("  (none recognizes)", ""))
    return lines


def _serial_snapshot(ctx: PluginContext) -> list[str]:
    """Best-effort dump of the live serial port's parameters.

    Every attribute is hasattr-guarded because the port might be
    closed, might be a pyserial URL handler (loop://, rfc2217://)
    that doesn't expose every field, or might be a DEMO FakeSerial.
    """
    lines: list[str] = []
    ser = None
    try:
        ser = ctx.serial.port()  # type: ignore[operator]
    except Exception:
        ser = None

    connected = False
    try:
        connected = bool(ctx.serial.is_connected())  # type: ignore[operator]
    except Exception:
        pass

    lines.append(_kv("  Connected", connected))
    if ser is None:
        lines.append(_kv("  (port handle", "not available)"))
        return lines

    attrs = [
        ("Port name", "port"),
        ("Baud rate", "baudrate"),
        ("Byte size", "bytesize"),
        ("Parity", "parity"),
        ("Stop bits", "stopbits"),
        ("Timeout", "timeout"),
        ("Write timeout", "write_timeout"),
        ("Inter-byte timeout", "inter_byte_timeout"),
        ("XON/XOFF", "xonxoff"),
        ("RTS/CTS", "rtscts"),
        ("DSR/DTR", "dsrdtr"),
    ]
    for label, attr in attrs:
        val = getattr(ser, attr, "(unavailable)")
        lines.append(_kv(f"  {label}", val))

    # Control-line state.  Each may raise if the port is in a weird
    # state; catch narrowly and mark unavailable.
    for label, attr in [
        ("DTR", "dtr"),
        ("RTS", "rts"),
        ("CTS", "cts"),
        ("DSR", "dsr"),
        ("RI", "ri"),
        ("CD", "cd"),
    ]:
        try:
            lines.append(_kv(f"  {label}", int(bool(getattr(ser, attr)))))
        except Exception:
            lines.append(_kv(f"  {label}", "(unavailable)"))

    return lines


def _config_snapshot(ctx: PluginContext) -> list[str]:
    """Config-derived identifiers (name, path, log path)."""
    lines: list[str] = []
    cfg_path = ctx.config_path or ""
    cfg_name = Path(cfg_path).stem if cfg_path else "(none)"
    lines.append(_kv("  Config name", cfg_name))
    lines.append(_kv("  Config path", cfg_path or "(none)"))

    log_file = ctx.cfg.get("log_file", "") if ctx.cfg else ""
    if not log_file and cfg_path:
        log_file = f"{Path(cfg_path).with_suffix('.log')}"
    lines.append(_kv("  Log file", log_file or "(none)"))

    lines.append(_kv("  Encoding", ctx.cfg.get("encoding", "utf-8")))
    lines.append(_kv("  Line ending", repr(ctx.cfg.get("line_ending", "\r"))))
    lines.append(_kv("  Flow control", ctx.cfg["serial"]["flow_control"]))
    return lines


def _runtime_flags_snapshot(ctx: PluginContext) -> list[str]:
    """Current toggles (echo, verbose, hex_mode, etc.)."""
    lines: list[str] = []
    flags = {}
    try:
        flags = dict(ctx.ns("flags"))
    except Exception:
        pass
    for k in sorted(flags):
        lines.append(_kv(f"  {k}", flags[k]))
    if not flags:
        lines.append(_kv("  (no flags captured)", ""))
    return lines


def _handler_fingerprint(ctx: PluginContext, args: str) -> CmdResult:
    """Write a full session fingerprint to the session log."""
    show = ctx.flag("--show")

    lines: list[str] = [
        "=== Termapy session fingerprint ===",
        _kv("Termapy version", _termapy_version()),
        _kv("Python", _python_info()),
        _kv("Platform", _platform.platform()),
        _kv("OS name", _platform.system()),
        _kv("OS release", _platform.release()),
        _kv("Machine", _platform.machine()),
        _kv("Processor", _platform.processor() or "(unknown)"),
        _kv("Hostname", socket.gethostname()),
        "",
        "Terminal:",
        *_env_snapshot(),
        "",
        "Config:",
        *_config_snapshot(ctx),
        "",
        "Serial port:",
        *_serial_snapshot(ctx),
        "",
        "Runtime flags:",
        *_runtime_flags_snapshot(ctx),
        "=== end fingerprint ===",
    ]

    for line in lines:
        ctx.io.log("#", line)

    # Brief confirmation on screen; full content is in the log.
    ctx.io._write(f"  Fingerprint written to log ({len(lines)} lines).", "green")

    if show:
        ctx.io._write("")
        for line in lines:
            ctx.io.output(line)

    return CmdResult.ok(value=str(len(lines)))


_FINGERPRINT_HELP = (
    "Write a full session fingerprint (OS, terminal, port params) to the session log."
)
_FINGERPRINT_LONG_HELP = (
    "Captures the provenance data that makes a log file reviewable "
    "weeks later: termapy version, Python / OS / platform, terminal "
    "environment variables (TERM, TERM_PROGRAM, VS Code hints), "
    "config name + path, live serial-port parameters including "
    "control-line state, and runtime flags.\n"
    "\n"
    "Output goes to the session log only.  Use --show to echo it to "
    "the terminal as well.\n"
    "\n"
    "In CLI mode (no log file) the log call is a silent no-op; use "
    "--show to see the report on stdout."
)
_FINGERPRINT_FLAGS = {"--show": "Also echo the fingerprint to the terminal output."}


# ── /log.show ─────────────────────────────────────────────────────────────────


def _handler_show(ctx: PluginContext, args: str) -> CmdResult:
    """Open the session log in the system viewer."""
    path = _log_path(ctx)
    if not path:
        return CmdResult.fail(msg="No log file configured.")
    if not Path(path).exists():
        return CmdResult.fail(msg=f"Log file not found: {path}")
    open_with_system(path)
    ctx.io._write(f"  Opening {Path(path).name}", "green")
    return CmdResult.ok(value=path)


_SHOW_HELP = "Open the session log in the system viewer."
_SHOW_LONG_HELP = (
    "Launches the platform's default handler for the session log file "
    "(Notepad / TextEdit / xdg-open) in a separate process.  Use "
    "/log.dump to print the log to this terminal instead."
)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="log",
    help="Session log: print, fingerprint, or open it.",
    long_help=(
        "Read-side session-log commands.  /log.clear and /log.delete "
        "(write-side) are provided by the active frontend."
    ),
    sub_commands={
        "dump": Command(
            args="{N}",
            help=_DUMP_HELP,
            long_help=_DUMP_LONG_HELP,
            handler=_handler_dump,
        ),
        "fingerprint": Command(
            args="{--show}",
            help=_FINGERPRINT_HELP,
            long_help=_FINGERPRINT_LONG_HELP,
            handler=_handler_fingerprint,
            flags=_FINGERPRINT_FLAGS,
        ),
        "show": Command(
            args="",
            help=_SHOW_HELP,
            long_help=_SHOW_LONG_HELP,
            handler=_handler_show,
            needs=CapabilitySet(gui_apps=True),
        ),
    },
)
