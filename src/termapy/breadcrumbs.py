"""Breadcrumbs: every running termapy leaves a file saying what it holds, so ``--kill`` can find it.

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
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from termapy.app_dirs import app_state_dir

# The folder of breadcrumbs inside the app state folder.
RUNNING_FOLDER = "running"

# How long ``stop`` waits for a process to go before giving up on it.
_STOP_WAIT_S = 3.0

# A Windows process access right and exit code, from the Windows SDK.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


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
        config: Path of the loaded config; empty when none is loaded.
        port: The config's port setting (a name or a serial-number spec).
        open_port: The port the process has open now; empty when none.
    """

    pid: int
    token: str
    started: str
    frontend: str
    config: str = ""
    port: str = ""
    open_port: str = ""

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


# ── This process's breadcrumb ────────────────────────────────────────────────


class Breadcrumb:
    """This process's breadcrumb: written when its session starts and changes, removed at exit."""

    def __init__(self, pid: int | None = None) -> None:
        self.pid = os.getpid() if pid is None else pid
        self.token = process_token(self.pid) or ""
        self.started = datetime.now().isoformat(timespec="seconds")
        self._removal_registered = False

    @property
    def path(self) -> Path:
        """Where the breadcrumb is written, ``running/<pid>.json``."""
        return running_dir() / f"{self.pid}.json"

    def write(
        self, frontend: str, *, config: str = "", port: str = "", open_port: str = ""
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
        """
        record = {
            "pid": self.pid,
            "token": self.token,
            "started": self.started,
            "frontend": frontend,
            "config": config,
            "port": port,
            "open_port": open_port,
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


def leave(frontend: str, *, config: str = "", port: str = "", open_port: str = "") -> None:
    """Write or refresh this process's breadcrumb: one per process, whichever frontend runs.

    The first call also removes breadcrumbs that dead processes left behind.

    Args:
        frontend: The frontend's name.
        config: Path of the loaded config, or empty.
        port: The config's port setting, or empty.
        open_port: The port open now, or empty.
    """
    global _OWN
    if _OWN is None:
        _OWN = Breadcrumb()
        prune_stale()
    _OWN.write(frontend, config=config, port=port, open_port=open_port)


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
    """The breadcrumbs ``target`` picks: ``all``, a port, or a config name.

    A port is compared the way termapy compares port names everywhere
    (``port_control.same_port``), against the port open now and the
    config's setting.  A config name is exact, case ignored; a wildcard
    makes either a glob (``matching``), so ``m3*`` picks several.

    Args:
        crumbs: The running termapys.
        target: ``all``, a port such as ``COM3``, or a config name.

    Returns:
        The picked breadcrumbs, in the order given.
    """
    crumbs = list(crumbs)
    if target.casefold() == "all":
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
