"""Diagnostic log: termapy's record of itself, one file per process, for when something goes wrong.

Not the session log.  The session log (``<name>.log``) is the transcript:
what was typed and what the device sent.  The diagnostic log is what
termapy did and what failed: the process and each session starting, config
loads, ports opening and closing, scripts and proto runs, and every exception
a broad catch stops, with its traceback.  It holds no device traffic: what
it does hold is the command line and, beside a traceback, the command that
raised.

One file per process, ``log/<stamp>-<pid>.log``, because one file shared by
processes is unsafe on Windows: an append there is a seek and then a write,
and a file another process has open cannot be renamed (decision
2026-11-05).  The folder is the config root's ``log/`` when that root
exists, else ``log/`` in the app state folder, so the log never creates a
config root; ``TERMAPY_DIAG_DIR`` overrides both.  :func:`start` prunes the
folder to the newest :data:`KEEP_FILES`, never a running process's file.

Call sites use :func:`info`, :func:`warning` and :func:`exception`, never
``logging`` itself: ``logging`` takes tens of milliseconds to import, so it
loads at the first record, and a print-and-exit flag never pays for it
(registry ``flag-paths-light``).  A record names the caller's file, line and
thread.  Before :func:`start` a record goes nowhere: the package logger's
``NullHandler`` keeps Python's last-resort handler from printing it.  The
handler sits on the ``termapy`` logger and the vendored libraries' loggers,
never on the root, so a host's own logging setup (the MCP SDK's) is left as
it was; under MCP a termapy record also reaches the SDK's stderr handler.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Hashable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging

# The logger every termapy record goes through.
LOGGER_NAME = "termapy"

# The vendored libraries' loggers (vendor/serial's URL handlers,
# vendor/xmodem.py, vendor/ymodem/Socket.py): their warnings land here too.
VENDORED_LOGGERS = ("pySerial", "xmodem", "ModemSocket")

# The folder of diagnostic logs, in the config root or the app state folder.
LOG_FOLDER = "log"

# The extension of a diagnostic log file.
LOG_SUFFIX = ".log"

# How many log files a start leaves in the folder, its own included.  High,
# because every run makes one, a -e one-shot and an MCP start included.
KEEP_FILES = 100

# A file stops taking records at this size: a failure that repeats cannot fill
# the disk.  A normal run writes a few KB; the folder's worst case is 100 MB.
MAX_BYTES = 1024 * 1024

# Names a folder to use instead of the config root's or the app state folder's.
DIAG_DIR_ENV = "TERMAPY_DIAG_DIR"

# The handler's name, so a later start finds the file already open.
HANDLER_NAME = "termapy-diag"

RECORD_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(filename)s:%(lineno)d %(message)s"

# The ``once`` keys already recorded in this process.
_once_keys: set[Hashable] = set()

# The ``once`` key of the version line, written before the first problem.
_VERSION_KEY = object()


def log_folder() -> Path:
    """Where this process's diagnostic log goes, which may not exist yet: a read creates nothing.

    ``TERMAPY_DIAG_DIR`` when set; else ``log/`` in the config root when that
    root exists (``config.cfg_dir``); else ``log/`` in the app state folder.
    A config root that does not exist yet is never made for a log, which
    is what keeps an MCP server or a quick session from leaving a
    ``termapy_cfg/`` behind.
    """
    override = os.environ.get(DIAG_DIR_ENV)
    if override:
        return Path(override)
    from termapy.config import cfg_dir

    try:
        root = cfg_dir()
    except SystemExit:
        # A --cfg-dir or TERMAPY_CFG_DIR naming no folder: the session
        # reports that itself; the log goes to the app state folder.
        root = None
    if root is not None and root.is_dir():
        return root / LOG_FOLDER
    from termapy.app_dirs import app_state_dir

    return app_state_dir() / LOG_FOLDER


def log_files() -> list[Path]:
    """The diagnostic logs in :func:`log_folder`, newest first; none when the folder does not exist yet."""
    folder = log_folder()
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"*{LOG_SUFFIX}"), reverse=True)


def start(frontend: str, config: str | None = None) -> Path | None:
    """Open this process's diagnostic log at the first call, and record that ``frontend`` started.

    Called once per session by every frontend (``TerminalHost._fire_app_start``,
    ``vt100.run_vt100_mode``, the headless proto run).  The first call opens
    the file and records the process: pid, Python, platform and command
    line.  A later one, when the process switches frontend (``entry``'s mode
    loop records the switch), records only the session.

    Args:
        frontend: The frontend starting, as ``$(FRONT_END)`` names it.
        config: The config file it starts on, ``""`` for none, for a
            frontend that runs no config load (vt100, the headless proto
            run).  None, a TerminalHost's, leaves it to the load's own
            ``config load:`` line, which heads the files the load brought in.

    Returns:
        The log file's path, or None when it could not be opened; the
        session runs on without one.
    """
    handler = _open_handler()
    if handler is None:
        return None
    if config is None:
        info("session start: %s", frontend)
    else:
        info("session start: %s, config %s", frontend, config or "(none)")
    return Path(handler.baseFilename)


def log_path() -> Path | None:
    """This process's diagnostic log file, or None before :func:`start` opened one."""
    if "logging" not in sys.modules:
        return None
    handler = _find_handler(_logger())
    return Path(handler.baseFilename) if handler is not None else None


def traceback_note() -> str:
    """The suffix a failure's one-line message carries: `` (traceback in <path>)``, or ``""`` with no log.

    So the message says where the rest of it is, and nobody has to know
    the folder.
    """
    path = log_path()
    return f" (traceback in {path})" if path is not None else ""


def info(msg: str, *args: object) -> None:
    """Record a lifecycle event: a session, a config load, a port or a run starting or ending.

    Args:
        msg: The message, ``%``-style; ``args`` fill it only when it is written.
        *args: The values for ``msg``.
    """
    _logger().info(msg, *args, stacklevel=2)


def warning(msg: str, *args: object, once: Hashable | None = None) -> None:
    """Record a failure termapy recovered from, without a traceback.

    Args:
        msg: The message, ``%``-style.
        *args: The values for ``msg``.
        once: A key for a failure that can repeat (an observer raising on
            every chunk): only the first record with that key is written.
    """
    if _first(once):
        _note_version()
        _logger().warning(msg, *args, stacklevel=2)


def exception(msg: str, *args: object, once: Hashable | None = None) -> None:
    """Record the exception being handled, with its traceback.  Call it in an ``except`` block.

    The record every broad catch makes (registry ``broad-catch-logged``):
    what the user sees is one line, and this is where the rest of it goes.

    Args:
        msg: What was being done, ``%``-style.
        *args: The values for ``msg``.
        once: A key for a failure that can repeat: only the first is written.
    """
    if _first(once):
        _note_version()
        _logger().exception(msg, *args, stacklevel=2)


def _first(once: Hashable | None) -> bool:
    """Whether a record with key ``once`` is the first of its kind; no key is always first."""
    if once is None:
        return True
    if once in _once_keys:
        return False
    _once_keys.add(once)
    return True


def _note_version() -> None:
    """Before the process's first warning or error, the termapy version.

    Read only then: ``importlib.metadata`` takes ~50 ms to load, which a CLI
    one-shot would otherwise pay on every run, and a log with no problem in
    it does not need the version.
    """
    if _find_handler(_logger()) is None or not _first(_VERSION_KEY):
        return
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("termapy")
    except PackageNotFoundError:
        installed = "unknown"
    _logger().info("termapy %s", installed)


def _logger() -> logging.Logger:
    """The package logger, set up at the first record: INFO and up, never to stderr by default."""
    import logging

    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    # Checked every time, not set once: anything that puts the level back
    # (pytest's caplog does) would otherwise drop every INFO record after.
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    return logger


def _find_handler(logger: logging.Logger) -> logging.FileHandler | None:
    """The diagnostic log's handler on ``logger``, or None before :func:`start`."""
    import logging

    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.get_name() == HANDLER_NAME:
            return handler
    return None


def _open_handler() -> logging.FileHandler | None:
    """The diagnostic log's handler: the one already open, or a new file in :func:`log_folder`."""
    import logging

    logger = _logger()
    handler = _find_handler(logger)
    if handler is not None:
        return handler
    from termapy.folders import ensure_folder
    from termapy.scripting import filename_timestamp

    folder = log_folder()
    path = folder / f"{filename_timestamp()}-{os.getpid()}{LOG_SUFFIX}"
    try:
        ensure_folder(folder)
        handler = _handler_class()(path)
    except OSError:
        return None
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter(RECORD_FORMAT))
    handler.addFilter(_keep_record)
    logger.addHandler(handler)
    for name in VENDORED_LOGGERS:
        logging.getLogger(name).addHandler(handler)
    info(
        "process start: pid %d, Python %s, %s, argv %s",
        os.getpid(),
        sys.version.split()[0],
        sys.platform,
        sys.argv[1:],
    )
    _prune(folder, keep=path)
    import atexit

    atexit.register(_note_exit)
    return handler


def _keep_record(record: logging.LogRecord) -> bool:
    """termapy's records from INFO up; a vendored library's from WARNING up."""
    import logging

    ours = record.name == LOGGER_NAME or record.name.startswith(LOGGER_NAME + ".")
    return ours or record.levelno >= logging.WARNING


def _note_exit() -> None:
    """The last line of a process that ended normally; a log without it was killed or crashed."""
    info("process exit")


def _prune(folder: Path, *, keep: Path) -> None:
    """Delete all but the newest :data:`KEEP_FILES` logs in ``folder``, never one still in use.

    A file whose pid names a running process is kept even if old: that
    process may still be writing it, and on POSIX the delete would succeed
    and lose its later records.  A pid reused by another program only keeps a
    file longer.  A file that cannot be deleted is left.
    """
    from termapy.breadcrumbs import process_token

    files = sorted(folder.glob(f"*{LOG_SUFFIX}"))
    for old in files[:-KEEP_FILES]:
        if old.name == keep.name:
            continue
        pid = _pid_of(old)
        if pid is not None and process_token(pid) is not None:
            continue
        try:
            old.unlink()
        except OSError:
            continue


def _pid_of(path: Path) -> int | None:
    """The pid in a log file's name, ``<stamp>-<pid>.log``, or None for a name of another shape."""
    _, _, pid = path.stem.rpartition("-")
    return int(pid) if pid.isdigit() else None


def _handler_class() -> type[logging.FileHandler]:
    """The diagnostic log's file handler: quiet when a write fails, and capped at :data:`MAX_BYTES`.

    Built at the first :func:`start` because ``logging`` is not imported
    before then.
    """
    import logging

    class DiagFileHandler(logging.FileHandler):
        """A ``FileHandler`` that never raises or prints its own failures, and stops at ``max_bytes``."""

        def __init__(self, filename: Path, *, max_bytes: int = MAX_BYTES) -> None:
            super().__init__(filename, encoding="utf-8")
            self.max_bytes = max_bytes
            self.full = False

        def emit(self, record: logging.LogRecord) -> None:
            """Write ``record`` unless the file is full, then note the cap once."""
            if self.full:
                return
            try:
                # FileHandler reopens a closed file outside its own guard,
                # so a failure there would reach the code that logged.
                super().emit(record)
                stream = self.stream
                if stream is not None and stream.tell() >= self.max_bytes:
                    self.full = True
                    stream.write(
                        f"diagnostic log reached {self.max_bytes} bytes; later records dropped\n"
                    )
                    self.flush()
            except (OSError, ValueError):
                # The file is gone or closed: stop writing.
                self.full = True

        def handleError(self, record: logging.LogRecord) -> None:
            """Drop a record the file cannot take: a full disk or a locked file never reaches the screen."""

    return DiagFileHandler
