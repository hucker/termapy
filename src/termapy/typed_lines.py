"""The lines typed at the prompt, in order, per config: what ``/run.last`` replays.

The history file cannot say what the last few commands were: it is deduped
at submit (typing A, B, A stores B, A), so it is neither complete nor in
order.  This list is: every line typed at the prompt, in the order typed,
with whether it succeeded.  It is kept per config, as the history is, and in
memory only: a config switch shows that config's lines, switching back
brings the earlier ones back while the process lives, and a restart starts
every list empty.  The history file's format does not change.

Pure, no Textual.  The engine owns one (``ReplEngine.typed_lines``) and
``TerminalHost._dispatch_typed`` is its one feed, so a line a script, a
paste or a replay sends is never in it.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class TypedLine:
    """One line as typed and how it went.

    Attributes:
        text: The line as typed, prefix included.
        success: Its result's ``success``: a device line succeeds once
            sent, unless a request-mode reply matched ``request_err_pattern``.
        error: Its result's ``error``, ``""`` when it succeeded.
    """

    text: str
    success: bool = True
    error: str = ""


class TypedLines:
    """Each config's typed lines, oldest first, the newest ``limit`` of them.

    The TUI adds from a dispatch worker while another thread may read, so
    one lock covers the lists.
    """

    def __init__(self, limit: int = 100) -> None:
        self._limit = limit
        self._by_config: dict[str, deque[TypedLine]] = {}
        self._lock = threading.Lock()

    def add(self, config: str, line: TypedLine) -> None:
        """Keep ``line`` as ``config``'s newest, dropping its oldest past the limit."""
        with self._lock:
            self._by_config.setdefault(config, deque(maxlen=self._limit)).append(line)

    def lines(self, config: str) -> list[TypedLine]:
        """``config``'s typed lines, oldest first; ``[]`` before its first."""
        with self._lock:
            return list(self._by_config.get(config, ()))
