"""Built-in plugin: repeat a command multiple times."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command
from termapy.plugins.params import ParamSpec

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _is_stopped(ctx: PluginContext) -> bool:
    """Check if /stop has been requested."""
    ev = getattr(ctx.internal, "script_stop_event", None)
    return ev is not None and ev.is_set()


def _interruptible_sleep(ctx: PluginContext, seconds: float) -> bool:
    """Sleep in 100ms chunks, returning True if stopped early."""
    ev = getattr(ctx.internal, "script_stop_event", None)
    if ev is not None:
        # wait() returns True if the event is set (stopped)
        return ev.wait(timeout=seconds)
    time.sleep(seconds)
    return False


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Repeat a command N times with optional delay between runs.

    Sets a user variable (default ``REPEAT_N``) to the current
    iteration (1-based). The variable is removed when done.
    Stoppable via ``/stop`` -- checks between iterations and
    wakes immediately from delays.

    Args:
        ctx: Plugin context.
        args: Unused -- parameters arrive via ``ctx.arg()`` (see ``params``).
    """
    cmd = ctx.arg("cmd")
    count = ctx.arg("count")
    delay_s = ctx.arg("delay")  # float seconds (0.0 == no delay)
    var_name = ctx.arg("var")

    from termapy.builtins.commands.var import _VARS

    ev = getattr(ctx.internal, "script_stop_event", None)
    # The stop flag is the SCRIPT's, not ours.  Clearing it is only correct
    # when /repeat is itself the outermost cancellable operation -- typed at
    # the prompt, where a stale set() from an earlier Escape would otherwise
    # abort this run before it starts.  Inside a script, clearing erases a
    # stop the user just asked for: the UI acknowledges it, /repeat swallows
    # it, and the script runs to completion anyway.
    in_script = bool(ctx.internal.in_script())
    if ev is not None and not in_script:
        ev.clear()

    stopped = False
    ran = 0
    try:
        for i in range(count):
            if _is_stopped(ctx):
                stopped = True
                break
            _VARS[var_name] = str(i + 1)
            ctx.dispatch(cmd)
            ran += 1
            if i < count - 1 and delay_s > 0:
                if _interruptible_sleep(ctx, delay_s):
                    stopped = True
                    break
    finally:
        _VARS.pop(var_name, None)

    if stopped:
        # Same rule on the way out: inside a script the stop must survive so
        # the enclosing run aborts too (repl.run_script checks it before each
        # line).  Interactively there is no enclosing run, so consume it.
        if ev is not None and not in_script:
            ev.clear()
        ctx.io.result(f"Repeat stopped after {ran}/{count} iterations.")
        # Return how many iterations actually ran so scripts can detect
        # cancellation (ran < count) vs full completion.
        return CmdResult.ok(value=str(ran))

    return CmdResult.ok(value=str(ran))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="repeat",
    help="Repeat a command N times with optional delay (escape to cancel).",
    long_help=(
        "Runs a command count times in sequence, optionally with a delay\n"
        "between iterations.  Press Escape to cancel.  If var= is given,\n"
        "the current iteration index (1..N) is available as $(var) in\n"
        "the command."
    ),
    handler=_handler,
    params=[
        ParamSpec("count", "int", required=True, min=1, help="number of repetitions"),
        ParamSpec(
            "delay", "duration", default=0.0,
            help="pause between iterations, e.g. 100ms (default: no delay)",
        ),
        ParamSpec(
            "var", "str", default="REPEAT_N", hint="<name>",
            help="variable name for the iteration index",
        ),
        ParamSpec("cmd", "command", required=True, rest=True, help="command to repeat"),
    ],
)
