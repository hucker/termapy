"""Built-in plugin: wait for a duration, the one /delay in every frontend.

Until 2026-10-16 /delay had four implementations -- a TUI hook, a CLI hook,
an MCP hook and the script runner's own -- and the TUI's ``/delay.silent``
returned without waiting (review 2026-10-15, D2).  This handler is the only
one: it waits on the shared stop event, so Escape (TUI) and Ctrl+C (CLI)
cancel it, and a frontend contributes only how progress is drawn
(``ctx.internal.delay_progress``).  ``.silent`` and ``.quiet`` are the
universal level suffixes.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable

from termapy.plugins import CmdResult, Command
from termapy.plugins.params import ParamSpec

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# Progress redraw and stop-check interval.
_TICK_S = 0.25
# Shorter waits draw no progress: the bar would flash and vanish.
_PROGRESS_MIN_S = 1.0


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    seconds = ctx.arg("duration")
    label = args.strip()
    stop = ctx.internal.script_stop_event
    # Typed, /delay is itself the outermost cancellable operation, so a stop
    # left set by an earlier Escape must not end it at once; inside a script
    # the stop belongs to the script (registry plugin-never-clears-stop).
    if stop is not None and not ctx.internal.in_script():
        stop.clear()
    shows_progress = seconds >= _PROGRESS_MIN_S and ctx.output_level in ("normal", "verbose")
    progress = ctx.internal.delay_progress if shows_progress else None
    try:
        canceled = _wait(seconds, stop, progress)
    except KeyboardInterrupt:
        # The CLI's Ctrl+C is the TUI's Escape: it ends the wait and any
        # script the wait is in, instead of leaving the CLI's loop.
        ctx.internal.script_stop()
        canceled = True
    if canceled:
        # Inside a script the stop belongs to the script, as /expect says too.
        if ctx.internal.in_script():
            return CmdResult.fail(msg="Script stopped.")
        return CmdResult.fail(msg=f"Delay {label} canceled.")
    ctx.io.output(f"Delay {label} done.")
    return CmdResult.ok(value=str(seconds))


def _wait(
    seconds: float,
    stop: threading.Event | None,
    progress: Callable[[float, float, bool], None] | None,
) -> bool:
    """Wait ``seconds``, ticking ``progress``; True when ``stop`` ended it early."""
    t0 = time.monotonic()
    try:
        while (elapsed := time.monotonic() - t0) < seconds:
            if progress is not None:
                progress(elapsed, seconds, False)
            step = min(_TICK_S, seconds - elapsed)
            if stop is None:
                time.sleep(step)
            elif stop.wait(step):
                return True
        return False
    finally:
        if progress is not None:
            progress(seconds, seconds, True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="delay",
    help="Wait for a duration (e.g. 500ms, 1.5s); Escape or Ctrl+C cancels.",
    long_help=(
        "Waits for <duration>, showing progress for a second or more where\n"
        "the frontend draws it: the bottom bar or the script overlay in the\n"
        "TUI, a bar in the CLI, nothing over MCP.  Escape (TUI) or Ctrl+C\n"
        "(CLI) cancels the wait and the script it is in, and the command\n"
        "fails.  {prefix}delay.silent waits with no progress and no output.\n"
        "\n"
        "Example:\n"
        "  AT+RESET\n"
        "  {prefix}delay 2s"
    ),
    handler=_handler,
    params=[
        ParamSpec(
            "duration", "duration", positional=True, required=True,
            help="how long to wait, e.g. 500ms, 1.5s, 2m",
        ),
    ],
)
