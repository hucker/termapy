"""Built-in plugin: repeat a command multiple times."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _is_stopped(ctx: PluginContext) -> bool:
    """Check if /stop has been requested."""
    ev = getattr(ctx.engine, "script_stop_event", None)
    return ev is not None and ev.is_set()


def _interruptible_sleep(ctx: PluginContext, seconds: float) -> bool:
    """Sleep in 100ms chunks, returning True if stopped early."""
    ev = getattr(ctx.engine, "script_stop_event", None)
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
        args: Keyword arguments: count, delay, var, cmd.
    """
    kw = parse_keywords(args, {"count", "delay", "var", "cmd"}, rest_keyword="cmd")

    cmd = kw.get("cmd", "")
    if not cmd:
        return CmdResult.fail(
            msg="Usage: /repeat count=<N> {delay=<dur>} {var=<name>} cmd=<command>"
        )

    count_str = kw.get("count", "")
    if not count_str:
        return CmdResult.fail(msg="Count is required")
    try:
        count = int(count_str)
    except ValueError:
        return CmdResult.fail(msg=f"Count must be an integer: {count_str}")
    if count < 1:
        return CmdResult.fail(msg=f"Count must be > 0: {count}")

    delay_s = 0.0
    if "delay" in kw:
        try:
            delay_s = parse_duration(kw["delay"])
        except ValueError as e:
            return CmdResult.fail(msg=str(e))

    var_name = kw.get("var", "REPEAT_N")

    from termapy.builtins.plugins.var import _VARS

    ev = getattr(ctx.engine, "script_stop_event", None)
    if ev is not None:
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
        if ev is not None:
            ev.clear()
        ctx.result(f"Repeat stopped after {ran}/{count} iterations.")
        return CmdResult.ok()

    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="repeat",
    args="count=<N> {delay=<dur>} {var=<name>} cmd=<command>",
    help="Repeat a command N times with optional delay (escape to cancel).",
    long_help=(
        "Runs a command count times in sequence, optionally with a delay\n"
        "between iterations.  Press Escape to cancel.  If var= is given,\n"
        "the current iteration index (1..N) is available as $(var) in\n"
        "the command.\n"
        "\n"
        "Parameters:\n"
        "  cmd=<command>     REQUIRED command to repeat (must be last)\n"
        "  count=<N>         REQUIRED number of repetitions\n"
        "  delay=<dur>       pause between iterations, e.g. 100ms (default: no delay)\n"
        "  var=<name>        variable name for the iteration index"
    ),
    handler=_handler,
)
