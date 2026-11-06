"""Session log: a frontend's transcript of a config's sessions, written by one class in every frontend.

The TUI writes ``<cfg>.log`` (or the ``log_file`` key) and the MCP server
``<cfg>.mcp.log`` beside it, through :class:`SessionLog`, so the two read
alike: every line is ``[YYYY-mm-dd HH:MM:SS.mmm] <marker> <text>``, and each
session is framed by :data:`SESSION_START` and :data:`SESSION_END`.  They are
two files because a TUI and an MCP server can run on one config at once,
and a file two processes append to is unsafe on Windows (decision
2026-11-05).  The CLI keeps none.

Markers:

- ``>`` sent: a command line, or a line to the device
- ``<`` received from the device
- ``#`` termapy's own output and status
- ``$`` an MCP request, with the output level it ran at
- ``!`` a failure the MCP host recorded beside its answer

Not the diagnostic log (``diag_log``), which is termapy's record of itself
and holds no device traffic.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final, TextIO

# A session's first and last line in each session log it writes.  A log
# whose last marker is a start (this one or Log Started) is a session that
# did not end cleanly.
SESSION_START: Final[str] = f"{' Session Start ':-^60s}"
SESSION_END: Final[str] = f"{' Session End ':-^60s}"
# The two ends of a gap inside a session: /log.stop writes the first before
# the log is let go, /log.start the second when it is opened again.
LOG_STOPPED: Final[str] = f"{' Log Stopped ':-^60s}"
LOG_STARTED: Final[str] = f"{' Log Started ':-^60s}"

# The marker a frontend's own output and status lines carry.
STATUS_MARKER: Final[str] = "#"


def entry_text(line: str) -> str:
    """A log line's text, without its timestamp and marker: ``[...] # Session Start`` -> ``Session Start``."""
    _, _, rest = line.partition("] ")
    return rest.split(" ", 1)[1] if " " in rest else ""


class SessionLog:
    """One session log file: open from a marker line, timestamped lines, closed after an end marker.

    Writes come from the serial reader and from dispatch workers while
    ``/log.stop`` or a config switch closes the file on another thread, so
    every write and the close take one lock, and a write to a closed log is
    dropped.  A write that fails (a full disk, a file gone) is dropped too:
    the transcript never stops the session.
    """

    def __init__(self) -> None:
        """Start closed, with nothing to tee to."""
        self._fh: TextIO | None = None
        self._lock = threading.Lock()
        # Called with every line, open file or not: MCP's --mcp-verbose
        # copy to stderr, which must work before a config is loaded.
        self.tee: Callable[[str], None] | None = None

    @property
    def fh(self) -> TextIO | None:
        """The open file, or None while the log is closed."""
        return self._fh

    @property
    def path(self) -> Path | None:
        """The open file's path, or None while the log is closed."""
        fh = self._fh
        return Path(fh.name) if fh is not None else None

    def open(self, path: Path, marker: str = SESSION_START) -> None:
        """Open ``path`` for appending and write ``marker``; a log already open stays as it is.

        Raises:
            OSError: The file cannot be opened.
        """
        with self._lock:
            if self._fh is not None:
                return
            self._fh = open(path, "a", encoding="utf-8")
        self.write(STATUS_MARKER, [marker])

    def close(self) -> None:
        """Close the file without a marker: ``/log.stop`` writes its own first."""
        with self._lock:
            fh, self._fh = self._fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass  # a file that cannot flush on close loses only its tail

    def end(self, marker: str = SESSION_END) -> None:
        """Write ``marker`` into the open log, then close it; a closed log writes nothing."""
        if self._fh is not None:
            self.write(STATUS_MARKER, [marker])
        self.close()

    def write(self, marker: str, texts: list[str]) -> None:
        """Write ``texts`` as lines, one shared timestamp, one write and one flush.

        Lines that arrived in one read share their moment, which is if
        anything more honest than stamping each a few microseconds apart.
        """
        if not texts:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        lines = [f"[{stamp}] {marker} {text}" for text in texts]
        with self._lock:
            fh = self._fh
            if fh is not None:
                try:
                    fh.write("".join(f"{line}\n" for line in lines))
                    fh.flush()
                except (OSError, ValueError):
                    pass  # a disk error, or a file closed under the handle
        tee = self.tee
        if tee is not None:
            for line in lines:
                tee(line)
