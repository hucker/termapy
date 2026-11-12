"""Breadcrumbs: every running termapy leaves a file saying what it holds, so ``--alive`` and ``--kill`` can find it.

One small JSON file per process, ``running/<pid>.json`` in the app state
folder (``app_dirs.app_state_dir``): the frontend, the config, the port it
has open, and a token for when the process started.  A session writes its
breadcrumb when it starts, rewrites it when it connects, disconnects or
switches config, and removes it when it exits.  A process that is killed
or crashes leaves its file behind, naming a PID that has gone or now
belongs to another program; the start token tells the two apart, so a
stale breadcrumb is never acted on (registry ``kill-verifies-identity``).
"""

from __future__ import annotations

import atexit
import json
import os
import re
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.app_dirs import app_state_dir

if TYPE_CHECKING:
    from termapy.plugins.views import Column

# The folder of breadcrumbs inside the app state folder.
RUNNING_FOLDER = "running"

# How long ``stop`` waits for a process to go before giving up on it.
_STOP_WAIT_S = 3.0

# A Windows process access right, exit code and snapshot flag, from the Windows SDK.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_TH32CS_SNAPPROCESS = 0x00000002


def running_dir() -> Path:
    """The folder of breadcrumbs, which may not exist yet: a read creates nothing."""
    return app_state_dir() / RUNNING_FOLDER


@dataclass(frozen=True)
class Crumb:
    """One running termapy, as its breadcrumb describes it.

    Attributes:
        pid: The process id.
        token: When the process started (``process_token``); a live process
            with this PID and a different token is another program.
        started: Local start time, ISO 8601 to the second, for display.
        frontend: ``textual``, ``cli``, ``mcp``, ``web`` or ``vt100``.
        config: Absolute path of the loaded config; empty when none is loaded.
        port: The config's port setting (a name or a serial-number spec).
        open_port: The port the process has open now; empty when none.
        settings: The open port's baud and frame, ``115200 8N1``; empty when none.
        port_serial: The open port's USB serial number; empty when none or unknown.
        version: The termapy version the process runs; empty for a one-shot,
            which does not pay to read it.
        parent_pid: The program that started this termapy, past its launchers
            (``starting_process``); 0 when unknown or a one-shot.
        parent_name: That program's executable name, ``powershell.exe``.
        python: The Python executable, which says which install runs it.
        cwd: The folder it started in.
        log: Its session log file; empty when it keeps none.

    Every path is absolute: the reader runs in another folder.

    A breadcrumb holds only what stays put while the session does, or changes
    at a connect or a config switch, when it is rewritten anyway: never
    anything that moves on its own.
    """

    pid: int
    token: str
    started: str
    frontend: str
    config: str = ""
    port: str = ""
    open_port: str = ""
    settings: str = ""
    port_serial: str = ""
    version: str = ""
    parent_pid: int = 0
    parent_name: str = ""
    python: str = ""
    cwd: str = ""
    log: str = ""

    @property
    def config_name(self) -> str:
        """The config's name, its file stem; empty when none is loaded."""
        return Path(self.config).stem if self.config else ""

    @property
    def summary(self) -> str:
        """One phrase naming the session: frontend, config and open port."""
        return (
            f"{self.frontend}, {self.config_name or 'no config'}, "
            f"{self.open_port or 'no port open'}"
        )


# ── Process identity ─────────────────────────────────────────────────────────


def process_token(pid: int) -> str | None:
    """A token for when process ``pid`` started, or None when it is not running.

    A PID is reused once its process exits, so a PID alone cannot say that
    a breadcrumb's process is still the one running; the PID and its start
    time together can.  Windows reads the creation time, Linux the start
    tick in /proc, macOS the start time ``ps`` reports.  A zombie, exited
    but not yet reaped, is not running.

    Args:
        pid: The process id.

    Returns:
        An opaque token, equal for the same process on every call.
    """
    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _windows_token(pid)
    if Path("/proc").is_dir():
        return _proc_token(pid)
    return _ps_token(pid)


def _windows_token(pid: int) -> str | None:
    """The process's creation time from ``GetProcessTimes``, while it is still active."""
    if sys.platform != "win32":
        # Only ever called on Windows (see ``_token``); the check tells the type
        # checker so on another platform it does not read ctypes.WinDLL.
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        if exit_code.value != _STILL_ACTIVE:
            return None
        filetimes = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(
            handle, *(ctypes.byref(filetime) for filetime in filetimes)
        ):
            return None
        creation = filetimes[0]
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def _proc_token(pid: int) -> str | None:
    """The start tick from ``/proc/<pid>/stat`` (field 22), unless the process is a zombie."""
    try:
        text = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # The fields after the ")" that ends the command name, which may itself
    # hold spaces or parentheses: state is field 3, starttime field 22.
    fields = text.rsplit(")", 1)[-1].split()
    if len(fields) < 20 or fields[0] == "Z":
        return None
    return fields[19]


def _ps_token(pid: int) -> str | None:
    """The start time ``ps`` reports, unless the process is a zombie."""
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-o", "stat=,lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    state, _, started = result.stdout.strip().partition(" ")
    if not started or state.startswith("Z"):
        return None
    return started.strip()


# ── Who started this termapy, and which version runs ─────────────────────────

# The programs that only start termapy: the termapy console script and uv.
# Python is matched by pattern (python, python3, python3.14, pythonw), which
# also covers the venv's own python.exe launcher on Windows.
_LAUNCHER_NAMES = frozenset({"termapy", "uv", "uvx"})
_PYTHON_NAME = re.compile(r"python[\d.]*w?")


def is_launcher(name: str) -> bool:
    """Whether a process name is one that only starts termapy: python, termapy, uv."""
    stem = Path(name).name.lower().removesuffix(".exe")
    return stem in _LAUNCHER_NAMES or _PYTHON_NAME.fullmatch(stem) is not None


def starting_process(pid: int, table: dict[int, tuple[int, str]]) -> tuple[int, str] | None:
    """``(pid, name)`` of the first ancestor of ``pid`` that is not a launcher, or None.

    On Windows ``uv run termapy`` is python, under the venv's python
    launcher, under termapy.exe, under uv.exe, under the shell: the shell is
    the answer.  For an MCP server it is the editor or client that started it.

    Args:
        pid: Where to start, this termapy.
        table: Every process, ``pid -> (parent pid, name)`` (``process_table``).
    """
    seen: set[int] = set()
    current = pid
    while current in table and current not in seen:
        seen.add(current)
        parent = table[current][0]
        if parent <= 0 or parent not in table:
            return None
        name = table[parent][1]
        if not is_launcher(name):
            return parent, name
        current = parent
    return None


def process_table() -> dict[int, tuple[int, str]]:
    """Every running process, ``pid -> (parent pid, name)``, from one snapshot; {} when unreadable."""
    if sys.platform == "win32":
        return _windows_process_table()
    return _ps_process_table()


def _windows_process_table() -> dict[int, tuple[int, str]]:
    """One ``CreateToolhelp32Snapshot`` of the processes: each one's parent and executable."""
    if sys.platform != "win32":
        # Only called on Windows; the check tells the type checker so.
        return {}
    import ctypes
    from ctypes import wintypes

    class _Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Entry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return {}
    table: dict[int, tuple[int, str]] = {}
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def _ps_process_table() -> dict[int, tuple[int, str]]:
    """Every process from one ``ps``: its parent and command name."""
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    table: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            table[int(fields[0])] = (int(fields[1]), Path(fields[2].strip()).name)
    return table


def installed_version() -> str:
    """The installed termapy's version, or "" when it cannot be read.

    ``importlib.metadata`` costs about a tenth of a second to load, so only
    a long-lived session asks (``leave(detailed=True)``), never a one-shot.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("termapy")
    except PackageNotFoundError:
        return ""


# ── This process's breadcrumb ────────────────────────────────────────────────


def _absolute(path: str) -> str:
    """``path`` made absolute against this process's folder; "" stays "".

    A breadcrumb is read by other processes, started in other folders, so a
    relative path in it would name the wrong file, or one that looks like
    another session's.
    """
    return os.path.abspath(path) if path else ""


class Breadcrumb:
    """This process's breadcrumb: written when its session starts and changes, removed at exit."""

    def __init__(self, pid: int | None = None) -> None:
        """Note what stays put for the whole session and costs nothing to read.

        The version and who started the process cost a tenth of a second
        and a process snapshot, so they wait for the first detailed write.

        Args:
            pid: The process; None is this one.
        """
        self.pid = os.getpid() if pid is None else pid
        self.token = process_token(self.pid) or ""
        self.started = datetime.now().isoformat(timespec="seconds")
        self.python = _absolute(sys.executable)
        try:
            self.cwd = os.getcwd()
        except OSError:
            self.cwd = ""
        self.version = ""
        self.parent_pid = 0
        self.parent_name = ""
        self._details_read = False
        self._removal_registered = False

    def _read_details(self) -> None:
        """Read the version and the program that started the process, once."""
        self._details_read = True
        self.version = installed_version()
        starter = starting_process(self.pid, process_table())
        if starter is not None:
            self.parent_pid, self.parent_name = starter

    @property
    def path(self) -> Path:
        """Where the breadcrumb is written, ``running/<pid>.json``."""
        return running_dir() / f"{self.pid}.json"

    def write(
        self,
        frontend: str,
        *,
        config: str = "",
        port: str = "",
        open_port: str = "",
        settings: str = "",
        port_serial: str = "",
        log: str = "",
        detailed: bool = False,
    ) -> None:
        """Write or rewrite the breadcrumb, and arrange its removal at exit.

        Written whole and then renamed into place, so a reader never sees half
        a file.  A failed write is swallowed: it costs only ``--kill``'s view
        of this process, never the session.

        Args:
            frontend: The frontend's name.
            config: Path of the loaded config, or empty.
            port: The config's port setting, or empty.
            open_port: The port open now, or empty.
            settings: The open port's baud and frame, or empty.
            port_serial: The open port's USB serial number, or empty.
            log: The session log file, or empty.
            detailed: Also record the version and who started the process,
                read on the first such write: a long-lived session's
                breadcrumb, never a one-shot's.
        """
        if detailed and not self._details_read:
            self._read_details()
        record = {
            "pid": self.pid,
            "token": self.token,
            "started": self.started,
            "frontend": frontend,
            "config": _absolute(config),
            "port": port,
            "open_port": open_port,
            "settings": settings,
            "port_serial": port_serial,
            "version": self.version,
            "parent_pid": self.parent_pid,
            "parent_name": self.parent_name,
            "python": self.python,
            "cwd": self.cwd,
            "log": _absolute(log),
        }
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".tmp")
            partial.write_text(json.dumps(record, indent=2), encoding="utf-8")
            os.replace(partial, path)
        except OSError:
            return
        if not self._removal_registered:
            self._removal_registered = True
            atexit.register(self.remove)

    def remove(self) -> None:
        """Delete the breadcrumb; a file already gone is fine."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


_OWN: Breadcrumb | None = None


def leave(
    frontend: str,
    *,
    config: str = "",
    port: str = "",
    open_port: str = "",
    settings: str = "",
    port_serial: str = "",
    log: str = "",
    detailed: bool = False,
) -> None:
    """Write or refresh this process's breadcrumb: one per process, whichever frontend runs.

    The first call also removes breadcrumbs that dead processes left behind.

    Args:
        frontend: The frontend's name.
        config: Path of the loaded config, or empty.
        port: The config's port setting, or empty.
        open_port: The port open now, or empty.
        settings: The open port's baud and frame, or empty.
        port_serial: The open port's USB serial number, or empty.
        log: The session log file, or empty.
        detailed: Also record the version and who started this termapy
            (``Breadcrumb.write``); a one-shot leaves it False and stays fast.
    """
    global _OWN
    if _OWN is None:
        _OWN = Breadcrumb()
        prune_stale()
    _OWN.write(
        frontend,
        config=config,
        port=port,
        open_port=open_port,
        settings=settings,
        port_serial=port_serial,
        log=log,
        detailed=detailed,
    )


# ── Reading and stopping ─────────────────────────────────────────────────────


def _crumb_files() -> list[Path]:
    """Every breadcrumb file, oldest name first; none when the folder does not exist."""
    folder = running_dir()
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def _load(path: Path) -> Crumb | None:
    """The breadcrumb in ``path``, or None when it cannot be read or is not one."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return Crumb(
            pid=int(record["pid"]),
            token=str(record["token"]),
            started=str(record["started"]),
            frontend=str(record["frontend"]),
            config=str(record.get("config") or ""),
            port=str(record.get("port") or ""),
            open_port=str(record.get("open_port") or ""),
            settings=str(record.get("settings") or ""),
            port_serial=str(record.get("port_serial") or ""),
            version=str(record.get("version") or ""),
            parent_pid=int(record.get("parent_pid") or 0),
            parent_name=str(record.get("parent_name") or ""),
            python=str(record.get("python") or ""),
            cwd=str(record.get("cwd") or ""),
            log=str(record.get("log") or ""),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _is_live(crumb: Crumb) -> bool:
    """Whether the breadcrumb's process is still the one running under its PID."""
    return bool(crumb.token) and process_token(crumb.pid) == crumb.token


def read_running() -> list[Crumb]:
    """The termapys running now, by their breadcrumbs, oldest first.

    A read: a stale breadcrumb is skipped, never deleted (``prune_stale``
    is the write).
    """
    crumbs = [crumb for crumb in map(_load, _crumb_files()) if crumb is not None]
    return sorted((crumb for crumb in crumbs if _is_live(crumb)), key=lambda crumb: crumb.started)


def prune_stale() -> int:
    """Delete the breadcrumbs whose process has gone, and any that cannot be read.

    Returns:
        How many were deleted.
    """
    pruned = 0
    for path in _crumb_files():
        crumb = _load(path)
        if crumb is not None and _is_live(crumb):
            continue
        try:
            path.unlink(missing_ok=True)
            pruned += 1
        except OSError:
            pass
    return pruned


def select(crumbs: Iterable[Crumb], target: str) -> list[Crumb]:
    """The breadcrumbs ``target`` picks: ``all``, ``others``, a port, or a config name.

    ``others`` picks the same as ``all`` here: a session that asks drops
    itself from the result (``/app.kill``), and the ``--kill`` process is
    not a session.  A port is compared the way termapy compares port names
    everywhere (``port_control.same_port``), against the port open now and
    the config's setting.  A config name is exact, case ignored; a wildcard
    makes either a glob (``matching``), so ``m3*`` picks several.

    Args:
        crumbs: The running termapys.
        target: ``all``, ``others``, a port such as ``COM3``, or a config name.

    Returns:
        The picked breadcrumbs, in the order given.
    """
    crumbs = list(crumbs)
    if target.casefold() in ("all", "others"):
        return crumbs
    from termapy.matching import is_glob, matches
    from termapy.port_control import same_port

    picked: list[Crumb] = []
    for crumb in crumbs:
        ports = [port for port in (crumb.open_port, crumb.port) if port]
        name = crumb.config_name
        if is_glob(target):
            hit = bool(name and matches(name, target)) or any(
                matches(port, target) for port in ports
            )
        else:
            hit = name.casefold() == target.casefold() or any(
                same_port(port, target) for port in ports
            )
        if hit:
            picked.append(crumb)
    return picked


def suggest(crumbs: Iterable[Crumb], target: str) -> str:
    """The did-you-mean for a target that picked nothing, from the configs and ports running.

    Returns:
        `` -- did you mean a, b?``, or ``""`` when nothing is close.
    """
    from termapy.matching import closest, did_you_mean

    crumbs = list(crumbs)
    names = {crumb.config_name for crumb in crumbs if crumb.config_name}
    names |= {crumb.open_port for crumb in crumbs if crumb.open_port}
    return did_you_mean(closest(target, sorted(names)))


def stop(crumb: Crumb) -> bool:
    """End the process a breadcrumb names, if it is still that process.

    The token is checked again first: by now the PID may belong to another
    program (registry ``kill-verifies-identity``), and then nothing is
    signaled.  Windows ends the process outright; elsewhere it is asked to
    terminate and killed if it has not gone within a few seconds.  Once the
    process is gone its breadcrumb is deleted.

    Args:
        crumb: A breadcrumb from ``read_running``.

    Returns:
        True when the process is gone, whether it was stopped here or had
        already exited.
    """
    import signal

    if _is_live(crumb):
        try:
            os.kill(crumb.pid, signal.SIGTERM)
        except OSError:
            return False
        # Windows has no SIGKILL: its SIGTERM already ends the process outright.
        force = getattr(signal, "SIGKILL", None)
        if not _gone_within(crumb, _STOP_WAIT_S) and force is not None:
            try:
                os.kill(crumb.pid, force)
            except OSError:
                return False
            _gone_within(crumb, _STOP_WAIT_S)
        if _is_live(crumb):
            return False
    try:
        (running_dir() / f"{crumb.pid}.json").unlink(missing_ok=True)
    except OSError:
        pass
    return True


def _gone_within(crumb: Crumb, seconds: float) -> bool:
    """Wait up to ``seconds`` for the breadcrumb's process to go; True once it has."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _is_live(crumb):
            return True
        time.sleep(0.05)
    return not _is_live(crumb)


# ── The sessions as a table: /app.alive and termapy --alive ──────────────────

SESSIONS_TITLE = "running termapys"


def session_record(crumb: Crumb) -> dict:
    """One running termapy as a record: a row of the table and the JSON answer.

    The paths (``config_path``, ``python``, ``cwd``, ``log``) and the port's
    serial number are the JSON's alone: they do not fit a row.
    """
    return {
        "pid": crumb.pid,
        "frontend": crumb.frontend,
        "config": crumb.config_name,
        "config_path": crumb.config,
        "port": crumb.port,
        "open_port": crumb.open_port,
        "settings": crumb.settings,
        "port_serial": crumb.port_serial,
        "version": crumb.version,
        "parent_pid": crumb.parent_pid or None,
        "parent_name": crumb.parent_name,
        "python": crumb.python,
        "cwd": crumb.cwd,
        "log": crumb.log,
        "started": crumb.started,
        "this_session": crumb.pid == os.getpid(),
    }


def _started_by(record: dict) -> str:
    """``powershell.exe 17604``: the program that started the session, or "" when unknown."""
    if record["parent_pid"] is None:
        return ""
    return f"{record['parent_name']} {record['parent_pid']}"


def session_columns() -> tuple[Column, ...]:
    """The table's columns, read by ``/app.alive`` and ``termapy --alive`` alike.

    A narrow frontend drops STARTED BY first, then STARTED, VERSION and
    SETTINGS; a column every session leaves blank is hidden.  Built on each
    call so that importing this module loads no table code.
    """
    from termapy.plugins.views import Column

    return (
        Column("PID", lambda record: str(record["pid"]), right=True),
        Column(
            "MODE",
            lambda record: record["frontend"] + (" (this)" if record["this_session"] else ""),
        ),
        Column("CONFIG", lambda record: record["config"] or "-"),
        Column("PORT", lambda record: record["open_port"] or "-"),
        Column("SETTINGS", lambda record: record["settings"], drop=1, hide_empty=True),
        Column("VERSION", lambda record: record["version"], drop=2, hide_empty=True),
        Column("STARTED", lambda record: record["started"].replace("T", " "), drop=3),
        Column("STARTED BY", _started_by, drop=4, hide_empty=True),
    )
