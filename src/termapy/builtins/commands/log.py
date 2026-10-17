"""Built-in plugin: ``/log.*`` -- session-log commands.

* ``/log.delete``      -- delete the session log file
* ``/log.dump``        -- print the log to the terminal (all or an N-line slice)
* ``/log.fingerprint`` -- write a provenance fingerprint to the log
* ``/log.show``        -- open the log in the system viewer

"The session log" is the frontend's own: the TUI's ``<cfg>.log`` (or
``log_file``), the MCP server's ``mcp/session.log``, and none in the CLI.
Every command here asks ``ctx.internal.session_log_path()`` which file
that is (registry ``session-log-one-owner``).  Until 2026-10-16 six
places worked it out for themselves, so over MCP ``/log.dump`` read the
TUI's log rather than the agent's own (review 2026-10-15, D3).
``/log.clear`` is the legacy name of ``/log.delete`` (``legacy.py``).
"""

from __future__ import annotations

import os
import platform as _platform
import socket
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import open_with_system
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
from termapy.scripting import select_lines

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def dump_text_slice(ctx: PluginContext, path: Path, args: str) -> CmdResult:
    """Print a text file: all lines, last N (N>0), or first N (N<0).

    The shared body of ``/log.dump`` and ``/mcp.log.dump`` -- each does its
    own path resolution + sandbox guard, then hands the resolved path
    here.  Returns the line count via ``value=`` for scripting.

    Args:
        ctx: Plugin context for output.
        path: Resolved, already-authorized file path.
        args: Raw arg string -- empty (all), or a signed integer count.
    """
    n: int | None = None
    arg = args.strip()
    if arg:
        try:
            n = int(arg)
        except ValueError:
            raise UsageError(f"Invalid count: {arg!r}  (N>0 last N, N<0 first N)") from None
        if n == 0:
            return CmdResult.fail(msg="Invalid line count: 0")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    lines = select_lines(lines, n)
    for line in lines:
        ctx.io.output(line)
    return CmdResult.ok(value=str(len(lines)))


# ── /log.dump ─────────────────────────────────────────────────────────────────


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print the session log: all, last N (N>0), or first N (N<0) lines."""
    p = ctx.internal.session_log_path()
    assert p is not None, "/log.dump declares session_log"
    # Sandbox: cfg["log_file"] can be pointed at any file (via /cfg.auto),
    # turning /log.dump into an arbitrary-file read.  Under the MCP host,
    # refuse a log path outside the config directory.  Reading the cfg's
    # own in-sandbox log stays fine.
    if not ctx.capabilities.filesystem_unconfined and ctx.config_path:
        sandbox = Path(ctx.config_path).parent.resolve()
        try:
            inside = p.resolve().is_relative_to(sandbox)
        except OSError:
            inside = False
        if not inside:
            return CmdResult.fail(
                msg=(
                    "Log file is outside the config sandbox; refusing to read "
                    "it under the MCP host. Set TERMAPY_MCP_FS_UNCONFINED=1 in "
                    "the server's shell to allow host-wide paths."
                )
            )
    if not p.exists():
        return CmdResult.fail(msg=f"Log file not found: {p}")

    return dump_text_slice(ctx, p, args)


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
    from importlib.metadata import PackageNotFoundError, version as _get_version

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
        ser = ctx.internal.port()
    except Exception:
        ser = None

    connected = False
    try:
        connected = bool(ctx.serial.is_connected())
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

    log_file = ctx.internal.session_log_path()
    lines.append(_kv("  Log file", log_file or "(none)"))

    lines.append(_kv("  Encoding", ctx.cfg.get("encoding", "utf-8")))
    lines.append(_kv("  Line ending", repr(ctx.cfg.get("eol", "\r"))))
    lines.append(_kv("  Flow control", ctx.cfg["serial"]["flow_control"]))
    return lines


def _runtime_flags_snapshot(ctx: PluginContext) -> list[str]:
    """Current toggles (echo, verbose, hex, etc.)."""
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
    ctx.io.result(f"  Fingerprint written to log ({len(lines)} lines).")

    if show:
        ctx.io.output("")
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
    path = ctx.internal.session_log_path()
    assert path is not None, "/log.show declares session_log"
    if not path.exists():
        return CmdResult.fail(msg=f"Log file not found: {path}")
    open_with_system(str(path))
    ctx.io.result(f"  Opening {path.name}")
    return CmdResult.ok(value=path)


_SHOW_HELP = "Open the session log in the system viewer."
_SHOW_LONG_HELP = (
    "Launches the platform's default handler for the session log file "
    "(Notepad / TextEdit / xdg-open) in a separate process.  Use "
    "/log.dump to print the log to this terminal instead."
)


# ── /log.delete ───────────────────────────────────────────────────────────────


def _handler_delete(ctx: PluginContext, args: str) -> CmdResult:
    """Delete the session log file; the frontend lets go of it first."""
    path = ctx.internal.session_log_path()
    assert path is not None, "/log.delete declares session_log"
    if not path.exists():
        return CmdResult.fail(msg=f"Log file not found: {path}")
    ctx.internal.close_session_log()
    try:
        path.unlink()
    except OSError as e:
        return CmdResult.fail(msg=f"Delete failed: {e}")
    ctx.io.result(f"Deleted {path.name}")
    # The deleted path, so a script can log or confirm it.
    return CmdResult.ok(value=path)


_DELETE_HELP = "Delete the session log file."
_DELETE_LONG_HELP = (
    "Removes the session log from disk (the TUI closes it first).  Not\n"
    "over MCP: an agent cannot erase the log of what it did.  The CLI\n"
    "keeps no session log, so it has no /log commands."
)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="log",
    help="Session log: print, fingerprint, or open it.",
    # Bare /log does what the Log button does: open the session log.
    bare_sub="show",
    long_help=(
        "The session log is the frontend's own: the TUI's <cfg>.log (or\n"
        "the log_file key), the MCP server's mcp/session.log, none in the\n"
        "CLI."
    ),
    sub_commands={
        "delete": Command(
            args="",
            help=_DELETE_HELP,
            long_help=_DELETE_LONG_HELP,
            handler=_handler_delete,
            needs=CapabilitySet(session_log=True, config_loaded=True, interactive=True),
        ),
        "dump": Command(
            args="{N}",
            help=_DUMP_HELP,
            long_help=_DUMP_LONG_HELP,
            handler=_handler_dump,
            needs=CapabilitySet.SESSION_LOG,
        ),
        "fingerprint": Command(
            args="{--show}",
            help=_FINGERPRINT_HELP,
            long_help=_FINGERPRINT_LONG_HELP,
            handler=_handler_fingerprint,
            flags=_FINGERPRINT_FLAGS,
            # It writes to the session log: in the CLI, which keeps none, it
            # said "written to log" and wrote nowhere.
            needs=CapabilitySet.SESSION_LOG,
        ),
        "show": Command(
            args="",
            help=_SHOW_HELP,
            long_help=_SHOW_LONG_HELP,
            handler=_handler_show,
            needs=CapabilitySet(session_log=True, config_loaded=True, gui_apps=True),
        ),
    },
)
