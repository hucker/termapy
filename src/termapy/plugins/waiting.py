"""A wait a person can watch and stop: the one loop behind ``/delay`` and ``/port.connect timeout=``.

It ticks the frontend's progress display (``ctx.internal.delay_progress``:
the TUI's bar or script overlay, the CLI's bar, nothing over MCP) and ends
early when the script-stop event is set, which Escape (TUI), Ctrl+C (CLI)
and a script's Stop button do.  Given ``until``, it ends as soon as that
returns True, which is how a command waits for something rather than for
a time.

A wait typed at the prompt is the outermost cancellable operation, so it
clears a stop an earlier Escape left set; inside a script the stop belongs
to the script and is honored (registry ``plugin-never-clears-stop``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext

# Progress redraw and stop-check interval.
TICK_S = 0.25
# Shorter waits draw no progress: the bar would flash and vanish.
PROGRESS_MIN_S = 1.0

Outcome = Literal["met", "elapsed", "stopped"]


def wait(
    ctx: PluginContext,
    seconds: float,
    until: Callable[[], bool] | None = None,
    *,
    interval: float = TICK_S,
) -> Outcome:
    """Wait up to ``seconds``, or until ``until()`` is True, showing progress and honoring a stop.

    Args:
        ctx: The command's context: its stop event, progress display and
            output level.
        seconds: The longest wait.
        until: Checked first and then every ``interval``; the wait ends
            when it returns True.  None waits the whole time.
        interval: How often ``until`` is checked and progress redrawn.

    Returns:
        ``"met"`` when ``until`` returned True, ``"elapsed"`` when the time
        ran out, ``"stopped"`` when a stop or Ctrl+C ended it.
    """
    stop = ctx.internal.script_stop_event
    if stop is not None and not ctx.internal.in_script():
        stop.clear()
    shows_progress = seconds >= PROGRESS_MIN_S and ctx.output_level in ("normal", "verbose")
    progress = ctx.internal.delay_progress if shows_progress else None
    t0 = time.monotonic()
    try:
        while True:
            if until is not None and until():
                return "met"
            elapsed = time.monotonic() - t0
            if elapsed >= seconds:
                return "elapsed"
            if progress is not None:
                progress(elapsed, seconds, False)
            step = min(interval, seconds - elapsed)
            if stop is None:
                time.sleep(step)
            elif stop.wait(step):
                return "stopped"
    except KeyboardInterrupt:
        # The CLI's Ctrl+C is the TUI's Escape: it ends the wait and any
        # script the wait is in, instead of leaving the CLI's loop.
        ctx.internal.script_stop()
        return "stopped"
    finally:
        if progress is not None:
            progress(seconds, seconds, True)
