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

In JSON mode a device that takes JSON lines (``protocol: "ndjson"``) gets
an id on every request (registry ``json-mode-ids``): ``id_field`` says
whether, ``json_request`` builds the line, and ``pick_reply`` finds the one
line that answers it.  In REPL mode nothing carries an id.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from termapy import diag_log
from termapy.defaults import cfg_value


def id_field(cfg: Mapping[str, Any]) -> str:
    """The field a JSON-mode request's id goes in, or ``""`` when the device takes plain lines.

    Only a device whose config says it takes JSON lines
    (``protocol: "ndjson"``) gets ids: a text device is sent the line as
    typed, in JSON mode too.  The name is ``ndjson_field_routing.response_id``.
    """
    if cfg_value(cfg, "protocol") != "ndjson":
        return ""
    return str(cfg_value(cfg, "ndjson_field_routing.response_id"))


def json_request(command: str, request_id: int, field: str) -> str:
    """``command`` as one JSON request line carrying ``request_id``: ``{"cmd":"led","args":"on","id":7}``.

    The first word is the command and the rest, spacing kept, its ``args``,
    the shape termapy's device monitors and the NDJSON demo read.
    """
    name, _, args = command.strip().partition(" ")
    request: dict[str, Any] = {"cmd": name}
    if args.strip():
        request["args"] = args.strip()
    request[field] = request_id
    return json.dumps(request, separators=(",", ":"))


def pick_reply(lines: Sequence[str], request_id: int, field: str) -> tuple[str, list[str]]:
    """The line answering ``request_id``, ``""`` when none does, and every other line, in order.

    A line answers when it is a JSON object whose ``field`` is the id; a
    late reply to an earlier request, an event or a print from the
    device's own code is one of the others.
    """
    reply = ""
    others: list[str] = []
    for line in lines:
        if not reply and _carries_id(line, request_id, field):
            reply = line
        else:
            others.append(line)
    return reply, others


def _carries_id(line: str, request_id: int, field: str) -> bool:
    """Whether ``line`` is a JSON object whose ``field`` is ``request_id``."""
    text = line.strip()
    if not text.startswith("{"):
        return False
    try:
        message = json.loads(text)
    except ValueError:
        return False
    if not isinstance(message, dict):
        return False
    value = message.get(field)
    return isinstance(value, int) and not isinstance(value, bool) and value == request_id


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
    reply_id: int | None = None,
    field: str = "",
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
        reply_id: The id ``command`` carries, with ``field`` its field
            (``json_request``): collection goes on past an idle gap until
            the line carrying it arrives or ``timeout_s`` runs out, so a
            stray line before the reply never ends the wait.

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
    except Exception as exc:  # serial boundary: a failed send is the answer
        # A lost port (pyserial's SerialException is an OSError) or a
        # character outside the encoding (a ValueError) is expected; anything
        # else is a bug, and its traceback is kept.
        if isinstance(exc, (OSError, ValueError)):
            error = f"Send error: {exc}"
        else:
            diag_log.exception("request send raised: %r", command)
            error = f"Send error: {type(exc).__name__}: {exc}{diag_log.traceback_note()}"
        return {
            "elapsed_s": time.perf_counter() - t0,
            "text": "",
            "lines": [],
            "error": error,
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
    if reply_id is not None and field:
        # A stray line ends an idle-gap window early; keep waiting for the
        # one line that answers, as long as the timeout allows.
        deadline = t0 + timeout_s
        while not pick_reply(lines, reply_id, field)[0]:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            more = wait_for_lines(timeout=remaining, terminator=terminator, idle_gap=idle_gap_s)
            if not more:
                break
            lines = lines + more
    elapsed = time.perf_counter() - t0
    return {
        "elapsed_s": elapsed,
        "text": "\n".join(lines),
        "lines": lines,
        "error": "",
    }
