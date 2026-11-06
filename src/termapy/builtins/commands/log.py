"""Built-in plugin: ``/log.*`` -- session-log commands, and ``/log.diag.*`` for the diagnostic log.

* ``/log.delete``      -- delete the session log file
* ``/log.diag``        -- open this run's diagnostic log (``/log.diag.show``)
* ``/log.diag.dump``   -- print this run's diagnostic log (all or an N-line slice)
* ``/log.diag.list``   -- the diagnostic logs in ``log/``, newest first
* ``/log.dump``        -- print the log to the terminal (all or an N-line slice)
* ``/log.fingerprint`` -- write a provenance fingerprint to the log
* ``/log.list``        -- the log as a one-row file list (``/help log`` ends with it)
* ``/log.show``        -- open the log in the system viewer
* ``/log.start``       -- write the log again after ``/log.stop``
* ``/log.stop``        -- stop writing the log; the file stays

The diagnostic log is termapy's record of itself (``diag_log``): one file
per run, in every frontend, the CLI included, so ``/log.diag.*`` needs no
session log.

"The session log" is the frontend's own: the TUI's ``<cfg>.log`` (or
``log_file``), the MCP server's ``<cfg>.mcp.log``, and none in the CLI.
``/log.list``, ``/log.show`` and ``/log.dump`` reach either by name, from
any frontend with a config (``ctx.internal.session_log_paths()``).
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

from termapy import diag_log
from termapy.config import open_with_system
from termapy.defaults import cfg_value
from termapy.folder_ops import dump_text_slice, file_table
from termapy.folders import LOG_SUFFIX, default_file_name, inside, is_inside
from termapy.matching import closest, did_you_mean
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import parse_count_arg

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


# ── /log.diag ─────────────────────────────────────────────────────────────────

_NO_DIAG_LOG = "No diagnostic log for this run."


def _handler_diag_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print this run's diagnostic log: all, last N (N>0), or first N (N<0) lines."""
    path = diag_log.log_path()
    if path is None:
        return CmdResult.fail(msg=_NO_DIAG_LOG)
    return dump_text_slice(ctx, path, args)


def _handler_diag_list(ctx: PluginContext, args: str) -> CmdResult:
    """The diagnostic logs in log/, newest first; a click on a name opens it."""
    return file_table(
        str(diag_log.log_folder()),
        "log",
        diag_log.log_files(),
        run=lambda record: f"log.diag.show {record['name']}",
    )


def _handler_diag_show(ctx: PluginContext, args: str) -> CmdResult:
    """Open this run's diagnostic log, or one /log.diag.list names, in the system viewer."""
    name = args.strip()
    if name:
        folder = diag_log.log_folder()
        under = inside(folder, name)
        if under is None:
            return CmdResult.fail(msg=f"Invalid log name: {name}")
        path = default_file_name(under, diag_log.LOG_SUFFIX)
    else:
        path = diag_log.log_path()
        if path is None:
            return CmdResult.fail(msg=_NO_DIAG_LOG)
    if not path.is_file():
        return CmdResult.fail(msg=f"Log file not found: {path}")
    open_with_system(str(path))
    ctx.io.result(f"  Opening {path.name}")
    return CmdResult.ok(value=path)


_DIAG_INTRO = (
    "termapy's own record of each run: its mode, config loads and the plugins they "
    "loaded, ports, scripts and tests, and the traceback of anything that went wrong."
)
_DIAG_LONG_HELP = (
    "One file per run, <date>_<time>-<pid>.log, in log/ beside the configs\n"
    "(the app state folder's log/ when there is no termapy_cfg/); the newest\n"
    f"{diag_log.KEEP_FILES} are kept.  An error that ends '(traceback in <path>)' names the file.\n"
    "It holds no device traffic: that is the session log, {prefix}log."
)
_DIAG_DUMP_HELP = "Print this run's diagnostic log; N for the last N lines, -N for the first N."
_DIAG_LIST_HELP = "List the diagnostic logs, newest first."
_DIAG_SHOW_HELP = "Open this run's diagnostic log, or a listed one, in the system viewer."


# ── /log.dump ─────────────────────────────────────────────────────────────────


def _session_log(ctx: PluginContext, name: str) -> Path | CmdResult:
    """The session log ``name`` picks from ``/log.list``, or this frontend's own for ``""``.

    A name matches a log of this config only, with or without ``.log``
    (``demo.mcp`` is ``demo.mcp.log``), so it never reaches an arbitrary
    file.  The answer is the path, or the failure to return.
    """
    if not name:
        own = ctx.internal.session_log_path()
        if own is None:
            return CmdResult.fail(
                msg=f"This frontend keeps no session log; {ctx.prefix}log.list names the config's"
            )
        return own
    logs = ctx.internal.session_log_paths()
    for path in logs:
        if name in (path.name, path.name.removesuffix(LOG_SUFFIX)):
            return path
    return CmdResult.fail(
        msg=f"Unknown log: {name}{did_you_mean(closest(name, [path.name for path in logs]))}"
    )


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print a session log, this frontend's own or one /log.list names: all, last N (N>0), or first N (N<0).

    Hand-rolled (CLAUDE.md "Declarative Command Parameters"): two optional
    positionals told apart by what they are, as ``/ss.txt``'s are
    (``scripting.parse_count_arg``): a whole number is the count, anything
    else a log's name.
    """
    try:
        name, count = parse_count_arg(args, "")
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    picked = _session_log(ctx, name)
    if isinstance(picked, CmdResult):
        return picked
    # Sandbox: cfg["log_file"] can be pointed at any file (via /cfg.auto),
    # turning /log.dump into an arbitrary-file read.  Under the MCP host,
    # refuse a log path outside the config directory.  Reading the cfg's
    # own in-sandbox logs stays fine.
    if not ctx.capabilities.filesystem_unconfined and ctx.config_path:
        if not is_inside(picked, Path(ctx.config_path).parent):
            return CmdResult.fail(
                msg=(
                    "Log file is outside the config sandbox; refusing to read "
                    "it under the MCP host. Set TERMAPY_MCP_FS_UNCONFINED=1 in "
                    "the server's shell to allow host-wide paths."
                )
            )
    if not picked.exists():
        return CmdResult.fail(msg=f"Log file not found: {picked}")

    return dump_text_slice(ctx, picked, "" if count is None else str(count))


_DUMP_HELP = "Print a session log; N for the last N lines, -N for the first N."
_DUMP_LONG_HELP = (
    "With no argument, prints this frontend's session log.  A name from\n"
    "{prefix}log.list prints that one instead: the MCP server's from the TUI or\n"
    "the CLI, say.  A signed integer N prints a slice: N>0 the last N lines\n"
    "(most recent), N<0 the first N (oldest)."
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
    ser = ctx.internal.port()
    connected = bool(ctx.serial.is_connected())
    lines.append(_kv("  Connected", connected))
    if ser is None:
        lines.append(_kv("  (port handle", "not available)"))
        return lines

    attrs = [
        ("Port Name", "port"),
        ("Baud Rate", "baudrate"),
        ("Byte Size", "bytesize"),
        ("Parity", "parity"),
        ("Stop Bits", "stopbits"),
        ("Timeout", "timeout"),
        ("Write Timeout", "write_timeout"),
        ("Inter-Byte Timeout", "inter_byte_timeout"),
        ("XON/XOFF", "xonxoff"),
        ("RTS/CTS", "rtscts"),
        ("DSR/DTR", "dsrdtr"),
    ]
    for label, attr in attrs:
        val = getattr(ser, attr, "(unavailable)")
        lines.append(_kv(f"  {label}", val))

    # Control-line state.  Each may raise if the port is in a weird
    # state: closed or lost (pyserial's SerialException is an OSError), a
    # URL handler without modem lines (NotImplementedError).  Mark it
    # unavailable.
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
        except (OSError, ValueError, NotImplementedError, AttributeError):
            lines.append(_kv(f"  {label}", "(unavailable)"))

    return lines


def _config_snapshot(ctx: PluginContext) -> list[str]:
    """Config-derived identifiers (name, path, log path)."""
    lines: list[str] = []
    cfg_path = ctx.config_path or ""
    cfg_name = Path(cfg_path).stem if cfg_path else "(none)"
    lines.append(_kv("  Config Name", cfg_name))
    lines.append(_kv("  Config Path", cfg_path or "(none)"))

    log_file = ctx.internal.session_log_path()
    lines.append(_kv("  Log File", log_file or "(none)"))

    lines.append(_kv("  Encoding", cfg_value(ctx.cfg, "encoding")))
    lines.append(_kv("  Line Ending", repr(cfg_value(ctx.cfg, "eol"))))
    lines.append(_kv("  Flow Control", ctx.cfg["serial"]["flow_control"]))
    return lines


def _runtime_flags_snapshot(ctx: PluginContext) -> list[str]:
    """Current toggles (echo, verbose, hex, etc.)."""
    lines: list[str] = []
    flags = dict(ctx.ns("flags"))
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
        _kv("Termapy Version", _termapy_version()),
        _kv("Python", _python_info()),
        _kv("Platform", _platform.platform()),
        _kv("OS Name", _platform.system()),
        _kv("OS Release", _platform.release()),
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


# ── /log.list ─────────────────────────────────────────────────────────────────


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """The config's session logs, this frontend's first; a click on a name opens it."""
    logs = ctx.internal.session_log_paths()
    files = [path for path in logs if path.is_file()]
    folder = Path(ctx.config_path).parent.name
    return file_table(f"{folder}/", "log", files, run=lambda record: f"log.show {record['name']}")


_LIST_HELP = "List the config's session logs, the TUI's and the MCP server's."
_LIST_LONG_HELP = (
    "This frontend's first, then the other's: <cfg>.log (or log_file) is the\n"
    "TUI's, <cfg>.mcp.log the MCP server's.  {prefix}log.show and {prefix}log.dump\n"
    "take a listed name; in the TUI a click on one opens it.  Only logs\n"
    "that exist are listed."
)


# ── /log.show ─────────────────────────────────────────────────────────────────


def _handler_show(ctx: PluginContext, args: str) -> CmdResult:
    """Open a session log in the system viewer: this frontend's own, or one /log.list names."""
    picked = _session_log(ctx, args.strip())
    if isinstance(picked, CmdResult):
        return picked
    if not picked.exists():
        return CmdResult.fail(msg=f"Log file not found: {picked}")
    open_with_system(str(picked))
    ctx.io.result(f"  Opening {picked.name}")
    return CmdResult.ok(value=picked)


_SHOW_HELP = "Open a session log in the system viewer, this frontend's or a listed one."
_SHOW_LONG_HELP = (
    "Launches the platform's default handler for the log file\n"
    "(Notepad / TextEdit / xdg-open) in a separate process: this frontend's\n"
    "own, or a name from {prefix}log.list.  Use {prefix}log.dump to print it\n"
    "to this terminal instead."
)


# ── /log.start, /log.stop ─────────────────────────────────────────────────────


def _handler_start(ctx: PluginContext, args: str) -> CmdResult:
    """Write the session log again after /log.stop or /log.delete; the frontend opens it."""
    path = ctx.internal.session_log_path()
    assert path is not None, "log.start declares session_log"
    try:
        started = ctx.internal.start_session_log()
    except OSError as e:
        return CmdResult.fail(msg=f"Log error: {e}")
    ctx.io.result(f"Log started: {path}" if started else f"Log already running: {path}")
    return CmdResult.ok(value=path)


_START_HELP = "Write the session log again after {prefix}log.stop or {prefix}log.delete."
_START_LONG_HELP = (
    "Opens the session log again, appending to it from a 'Log Started'\n"
    "line, or begins a new file after {prefix}log.delete.  Running it while\n"
    "the log is being written changes nothing and says so."
)


def _handler_stop(ctx: PluginContext, args: str) -> CmdResult:
    """Stop writing the session log until /log.start; the frontend closes it, the file stays."""
    path = ctx.internal.session_log_path()
    assert path is not None, "log.stop declares session_log"
    stopped = ctx.internal.stop_session_log()
    ctx.io.result(f"Log stopped: {path}" if stopped else "Log already stopped.")
    return CmdResult.ok(value=path)


_STOP_HELP = "Stop writing the session log until {prefix}log.start."
_STOP_LONG_HELP = (
    "Writes a 'Log Stopped' line, closes the session log and writes\n"
    "nothing to it until {prefix}log.start, a config switch included.  The\n"
    "file stays.  It lasts for this session: the next start of termapy\n"
    "logs as usual.  Not over MCP: an agent cannot pause the log of\n"
    "what it did."
)


# ── /log.delete ───────────────────────────────────────────────────────────────


def _handler_delete(ctx: PluginContext, args: str) -> CmdResult:
    """Delete the session log file; the frontend stops the log first, and it stays stopped."""
    path = ctx.internal.session_log_path()
    assert path is not None, "log.delete declares session_log"
    if not path.exists():
        return CmdResult.fail(msg=f"Log file not found: {path}")
    stopped = ctx.internal.stop_session_log()
    try:
        path.unlink()
    except OSError as e:
        # A failed delete changes nothing: a log it stopped is started again.
        if stopped:
            ctx.internal.start_session_log()
        return CmdResult.fail(msg=f"Delete failed: {e}")
    ctx.io.result(f"Deleted {path.name}; {ctx.prefix}log.start begins a new one")
    # The deleted path, so a script can log or confirm it.
    return CmdResult.ok(value=path)


_DELETE_HELP = "Delete the session log file."
_DELETE_LONG_HELP = (
    "Removes the session log from disk.  The TUI stops the log first,\n"
    "and it stays stopped until {prefix}log.start begins a new file.  Not\n"
    "over MCP: an agent cannot erase the log of what it did.  The CLI\n"
    "keeps no session log, so of the {prefix}log commands it has only\n"
    "{prefix}log.diag."
)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="terminal",
    name="log",
    help="Session log: start, stop, print, fingerprint, or open it.",
    intro=(
        "A transcript of this session: the commands, the device's replies and what "
        "termapy said.  {prefix}log.diag is termapy's own record of each run, with tracebacks."
    ),
    # Bare /log does what the Log button does: open the session log.
    bare_sub="show",
    help_list="list",
    long_help=(
        "Each frontend keeps its own session log beside the config: the\n"
        "TUI's <cfg>.log (or the log_file key), the MCP server's <cfg>.mcp.log,\n"
        "none in the CLI.  {prefix}log.list shows both, and {prefix}log.show and\n"
        "{prefix}log.dump open or print either by name."
    ),
    sub_commands={
        "delete": Command(
            args="",
            help=_DELETE_HELP,
            long_help=_DELETE_LONG_HELP,
            handler=_handler_delete,
            needs=CapabilitySet(session_log=True, config_loaded=True, interactive=True),
            safety="destructive",
        ),
        "diag": Command(
            help="The diagnostic log: termapy's record of each run, with tracebacks.",
            intro=_DIAG_INTRO,
            long_help=_DIAG_LONG_HELP,
            # Bare opens it, as bare /log opens the session log.
            bare_sub="show",
            help_list="list",
            sub_commands={
                "dump": Command(
                    args="{N}",
                    help=_DIAG_DUMP_HELP,
                    handler=_handler_diag_dump,
                    shows=True,
                ),
                "list": Command(
                    help=_DIAG_LIST_HELP,
                    handler=_handler_diag_list,
                    shows=True,
                    state=True,
                ),
                "show": Command(
                    args="{name}",
                    help=_DIAG_SHOW_HELP,
                    handler=_handler_diag_show,
                    shows=True,
                    needs=CapabilitySet.GUI_APPS,
                ),
            },
        ),
        "dump": Command(
            args="{name} {N}",
            help=_DUMP_HELP,
            long_help=_DUMP_LONG_HELP,
            handler=_handler_dump,
            shows=True,
            # Any frontend with a config: a name reads the TUI's or MCP's log
            # from the CLI too; with none, this frontend's (the handler says
            # when it keeps none).
            needs=CapabilitySet.CONFIG_LOADED,
        ),
        "fingerprint": Command(
            args="{--show}",
            help=_FINGERPRINT_HELP,
            long_help=_FINGERPRINT_LONG_HELP,
            handler=_handler_fingerprint,
            cfg={
                "encoding": "in the fingerprint",
                "eol": "in the fingerprint",
                "serial.flow_control": "in the fingerprint",
            },
            flags=_FINGERPRINT_FLAGS,
            # It writes to the session log: in the CLI, which keeps none, it
            # said "written to log" and wrote nowhere.
            needs=CapabilitySet.SESSION_LOG,
        ),
        "list": Command(
            help=_LIST_HELP,
            long_help=_LIST_LONG_HELP,
            handler=_handler_list,
            shows=True,
            needs=CapabilitySet.CONFIG_LOADED,
            state=True,
        ),
        "show": Command(
            args="{name}",
            help=_SHOW_HELP,
            long_help=_SHOW_LONG_HELP,
            handler=_handler_show,
            shows=True,
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
        "start": Command(
            args="",
            help=_START_HELP,
            long_help=_START_LONG_HELP,
            handler=_handler_start,
            # Interactive like /log.stop, the command it undoes: over MCP the
            # log is never stopped.
            needs=CapabilitySet(session_log=True, config_loaded=True, interactive=True),
            safety="mutable",
        ),
        "stop": Command(
            args="",
            help=_STOP_HELP,
            long_help=_STOP_LONG_HELP,
            handler=_handler_stop,
            needs=CapabilitySet(session_log=True, config_loaded=True, interactive=True),
            safety="mutable",
        ),
    },
)
