"""Built-in plugin: measure serial response time."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.plugins.params import ParamSpec

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# Declarative parameters -- the dispatcher parses/coerces/validates these and
# fails with a synthesized usage message before the handler runs.  Shared by
# /ping and /ping.quiet.  ``timeout`` default is 0.25 == 250ms (post-coercion
# float seconds); the old hand-written help claimed "1s", which was drift.
_PING_PARAMS = [
    ParamSpec("count", "int", default=1, min=1, help="number of pings"),
    ParamSpec(
        "timeout", "duration", default=0.25, help="response timeout per ping"
    ),
    ParamSpec("cmd", "command", required=True, rest=True, help="command to send"),
]


def _handler(ctx: PluginContext, args: str, *, quiet: bool = False) -> CmdResult:
    cmd = ctx.arg("cmd")
    count = ctx.arg("count")
    timeout_ms = int(ctx.arg("timeout") * 1000)  # param is float seconds
    times: list[float] = []
    for i in range(count):
        with ctx.serial.io():
            ctx.serial.drain()
            start = time.perf_counter()
            ctx.serial.send(cmd)
            response = ctx.serial.read_raw(timeout_ms=timeout_ms)
            ms = (time.perf_counter() - start) * 1000
        times.append(ms)
        if response:
            ctx.io.output(f"{cmd} -- {ms:.0f}ms", "green")
            if not quiet:
                text = response.decode(ctx.cfg.get("encoding", "utf-8"), errors="replace").strip()
                if text:
                    ctx.io.output(f"  {text}")
        else:
            ctx.io.output(f"{cmd} -- timeout ({timeout_ms}ms)", "red")
    if count > 1:
        avg = sum(times) / len(times)
        lo = min(times)
        hi = max(times)
        result_text = f"{count} pings: avg={avg:.0f}ms min={lo:.0f}ms max={hi:.0f}ms"
        ctx.io.result(result_text)
        return CmdResult.ok(value=result_text)
    if count == 1 and times:
        ms = times[0]
        result_text = f"{ms:.0f}ms"
        return CmdResult.ok(value=result_text)
    # Ping ran but every response timed out -- empty value rather
    # than missing, so scripts can distinguish "tried but nothing"
    # from "didn't run."
    return CmdResult.ok(value="")


def _handler_quiet(ctx: PluginContext, args: str) -> CmdResult:
    return _handler(ctx, args, quiet=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Send a command and measure response time.",
    name="ping",
    long_help=(
        "Sends a command to the device and measures round-trip time,\n"
        "repeating for count iterations.  Reports min/max/mean timing."
    ),
    handler=_handler,
    needs=CapabilitySet(serial_connected=True),
    params=_PING_PARAMS,
    sub_commands={
        "quiet": Command(
            "Ping without showing device response.",
            long_help=(
                "Same as {prefix}ping but suppresses the device response text;\n"
                "only the timing summary is printed."
            ),
            handler=_handler_quiet,
            needs=CapabilitySet(serial_connected=True),
            params=_PING_PARAMS,
        ),
    },
)
