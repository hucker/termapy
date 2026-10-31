"""Request/response I/O pipeline for synchronous device exchanges.

Pure helper that drives the **drain -> send -> wait -> collect** cycle every
synchronous serial-command exchange shares.  Its caller is the MCP
profile executor (``mcp/server.py:_dispatch_via_profile``) for commands
declared in a loaded device profile, which then runs
:func:`response_parsers.parse_response` over the collected text per the
profile's ``response.format`` rules.

This module is **pure plumbing** -- no parsing, no host coupling.  Callers
inject the functions they need (send, drain, wait, optional async event
recorder) so the helper works in any environment that exposes those
operations.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def request_response(
    *,
    send: Callable[[str], None],
    drain_recent_lines: Callable[[], list[str]],
    wait_for_lines: Callable[..., list[str]],
    command: str,
    timeout_s: float,
    terminator: str = "",
    idle_gap_s: float = 0.05,
    on_drained_line: Callable[[str], None] | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Drain stale lines, send the command as a line, wait for response.

    Args:
        send: Sends one text line, its ending added and paced
            (``ctx.serial.send``); raises ``OSError`` /
            ``serial.SerialException`` on transport failure.
        drain_recent_lines: Snapshot-and-clear the engine's recent-lines
            ring buffer.  Called once before send so stale lines from a
            prior command don't pollute this exchange's parse.
        wait_for_lines: Line-oriented response collector.  Called with
            ``timeout=...``, ``terminator=...``, ``idle_gap=...``.
            Returns the lines that arrived during the window.
        command: Command text to send, without the line ending: ``send``
            adds the configured one.
        timeout_s: Outer wall-clock cap for the response window.  When
            this elapses, ``wait_for_lines`` returns whatever it has.
        terminator: Optional regex; first matching line ends collection.
            Used by profile commands with ``response.format == "lines"``.
        idle_gap_s: Idle window in seconds.  Collection finalizes when
            no new line arrives within this window (after at least one
            line has arrived).  ``0.05`` matches the profile executor.
        on_drained_line: Optional callback invoked once per stale line
            drained before send.  The MCP host uses this to record stale
            lines as ``async_events`` on the ``device_state`` resource.
            None = silently discard drained lines.
        wait: When False, skips ``wait_for_lines`` after the send -- the
            fire-and-forget shape used by profile commands declared with
            ``response.format == "none"``.  Drain + send still happen.

    Returns:
        Dict with these keys:

          - ``elapsed_s`` (float): wall clock from send-attempt to collection done.
          - ``text`` (str): collected lines joined with ``"\\n"``; ``""`` on no response.
          - ``lines`` (list[str]): the individual lines (empty on no response).
          - ``error`` (str): send-error message, ``""`` on success.

        ``error`` populated means the send itself failed (port closed,
        write error).  Empty ``text`` with empty ``error`` means the
        send succeeded but no response arrived within ``timeout_s``.
    """
    # Drain stale lines so they don't pollute this command's parse.
    stale = drain_recent_lines()
    if on_drained_line is not None:
        for line in stale:
            on_drained_line(line)

    # Send.  Inside the try: a character outside the configured encoding
    # is a send failure to report, not an exception for the caller's
    # worker thread.
    t0 = time.perf_counter()
    try:
        send(command)
    except (OSError, Exception) as exc:  # noqa: BLE001 -- serial boundary
        return {
            "elapsed_s": time.perf_counter() - t0,
            "text": "",
            "lines": [],
            "error": f"Send error: {exc}",
        }

    # Fire-and-forget: skip the wait window entirely.
    if not wait:
        return {
            "elapsed_s": time.perf_counter() - t0,
            "text": "",
            "lines": [],
            "error": "",
        }

    # Receive: line-oriented collection with idle-gap settling.  Multi-
    # line responses settle on the idle gap; single-line responses pay
    # the idle-gap tax (currently 50ms).  Profile callers can pass a
    # ``terminator`` regex to short-circuit when an end-marker arrives.
    lines = wait_for_lines(
        timeout=timeout_s,
        terminator=terminator,
        idle_gap=idle_gap_s,
    )
    elapsed = time.perf_counter() - t0
    return {
        "elapsed_s": elapsed,
        "text": "\n".join(lines),
        "lines": lines,
        "error": "",
    }
