"""Implementation of /log.fingerprint -- write a session fingerprint to the log.

Captures enough provenance data (OS, terminal, Python, termapy, config,
serial port state) that a log file can be read back weeks later and
the conditions of the session are unambiguous.  Writes to the session
log only; nothing hits the output window (aside from a brief confirmation).

Lives at ``termapy.log_fingerprint`` rather than as an auto-loaded
plugin because ``/log`` is an app-level hook namespace (see
``/log.clear``), and ``register_hook("log", ...)`` would wipe any
plugin entries.  Keeping the handler as a plain module lets both
frontends register it as a sibling hook after ``/log.*`` hooks are
installed.
"""

from __future__ import annotations

import os
import platform as _platform
import socket
import sys
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


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
    from pathlib import Path

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
    lines.append(_kv("  Flow control", ctx.cfg.get("flow_control", "none")))
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


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Write a full session fingerprint to the session log."""
    prefix = ctx.engine.prefix
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


# ── Public exports consumed by register_hook in cli.py and app.py ─────────────
#
# ``/log`` is an app-hook namespace already (see ``/log.clear``).  The
# two frontends install their own ``log.*`` hooks at startup; this
# handler rides along so the fingerprint command lives beside the
# other log commands instead of getting isolated as a plugin.

HANDLER = _handler
HELP = (
    "Write a full session fingerprint (OS, terminal, port params) to the session log."
)
LONG_HELP = (
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
ARGS = "{--show}"
FLAGS = {"--show": "Also echo the fingerprint to the terminal output."}
