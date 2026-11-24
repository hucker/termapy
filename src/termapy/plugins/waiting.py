"""A wait a person can watch and stop: the one loop behind ``/delay`` and ``/port.connect timeout=``.

Beside it, ``print_lines``: a file's text printed a person can stop, behind
``/cfg.file`` and the ``.dump`` family, since a big file prints for seconds.

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
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext

# Progress redraw and stop-check interval.
TICK_S = 0.25
# Shorter waits draw no progress bar: it would flash and vanish.  Their
# words still show; the frontends read this to leave the bar out.
PROGRESS_MIN_S = 1.0

Outcome = Literal["met", "elapsed", "stopped"]


def wait(
    ctx: PluginContext,
    seconds: float,
    until: Callable[[], bool] | None = None,
    *,
    interval: float = TICK_S,
    label: str = "",
) -> Outcome:
    """Wait up to ``seconds``, or until ``until()`` is True, showing progress and honoring a stop.

    Args:
        ctx: The command's context: its stop event, progress display and
            output level.
        seconds: The longest wait.
        until: Checked first and then every ``interval``; the wait ends
            when it returns True.  None waits the whole time.
        interval: How often ``until`` is checked and progress redrawn.
        label: What the wait is for, in words.  The frontend places it
            beside its progress: the TUI in the prompt's ghost text, the
            CLI as a line before its bar, MCP nowhere.  Empty for a wait
            that needs no words (``/delay``, which says its own).

    Returns:
        ``"met"`` when ``until`` returned True, ``"elapsed"`` when the time
        ran out, ``"stopped"`` when a stop or Ctrl+C ended it.
    """
    stop = ctx.internal.script_stop_event
    if stop is not None and not ctx.internal.in_script():
        stop.clear()
    shows = ctx.output_level in ("normal", "verbose") and (seconds >= PROGRESS_MIN_S or label)
    progress = ctx.internal.delay_progress if shows else None
    t0 = time.monotonic()
    try:
        while True:
            # The tick comes first, so a wait met at its first check has
            # still said what it was for.
            elapsed = time.monotonic() - t0
            if progress is not None:
                progress(min(elapsed, seconds), seconds, False, label)
            if until is not None and until():
                return "met"
            if elapsed >= seconds:
                return "elapsed"
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
            progress(seconds, seconds, True, label)


def print_lines(ctx: PluginContext, lines: Sequence[str], color: str = "dim") -> int:
    """Print ``lines`` on the result channel, ending early when a stop is asked; how many printed.

    The one loop behind a command whose answer is a file's text
    (``/cfg.file``, the ``.dump`` family, ``/show``): a big file prints for
    seconds in the TUI, and Escape, the "escape to cancel" the prompt
    shows, or the CLI's Ctrl+C ends it with a line saying how far it got.
    The command's value is the whole text still; only the screen stops.
    Typed at the prompt it clears a stop an earlier Escape left set, as
    ``wait`` does; inside a script the stop is the script's (registry
    ``plugin-never-clears-stop``).

    Args:
        ctx: The command's context: its stop event and output.
        lines: The lines to print.
        color: Their color.

    Returns:
        How many lines were printed.
    """
    stop = ctx.internal.script_stop_event
    if stop is not None and not ctx.internal.in_script():
        stop.clear()
    printed = 0
    try:
        for line in lines:
            if stop is not None and stop.is_set():
                break
            ctx.io.result(line, color)
            printed += 1
    except KeyboardInterrupt:
        # The CLI's Ctrl+C is the TUI's Escape, as in wait.
        ctx.internal.script_stop()
    if printed < len(lines):
        ctx.io.result(f"Stopped after {printed} of {len(lines)} lines.", "yellow")
    return printed
