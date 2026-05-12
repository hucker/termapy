"""Built-in plugin: measure serial response time."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str, *, quiet: bool = False) -> CmdResult:
    kw = parse_keywords(args, {"cmd", "count", "timeout"}, rest_keyword="cmd")
    cmd = kw.get("cmd", "")
    if not cmd:
        return CmdResult.fail(msg="Usage: /ping {count=<N>} {timeout=<dur>} cmd=<command>")
    try:
        count = int(kw.get("count", "1"))
    except ValueError:
        return CmdResult.fail(msg="Ping: count must be an integer")
    if count < 1:
        return CmdResult.fail(msg="Ping: count must be >= 1")
    try:
        timeout_ms = int(parse_duration(kw.get("timeout", "250ms")) * 1000)
    except ValueError as e:
        return CmdResult.fail(msg=f"Ping: {e}")
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
            ctx.io._write(f"{cmd} -- {ms:.0f}ms", "green")
            if not quiet:
                text = response.decode(ctx.cfg.get("encoding", "utf-8"), errors="replace").strip()
                if text:
                    ctx.io.output(f"  {text}")
        else:
            ctx.io._write(f"{cmd} -- timeout ({timeout_ms}ms)", "red")
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
    return CmdResult.ok()


def _handler_quiet(ctx: PluginContext, args: str) -> CmdResult:
    return _handler(ctx, args, quiet=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Send a command and measure response time.",
    name="ping",
    args="{count=<N>} {timeout=<dur>} cmd=<command>",
    long_help=(
        "Sends a command to the device and measures round-trip time,\n"
        "repeating for count iterations.  Reports min/max/mean timing.\n"
        "\n"
        "Parameters:\n"
        "  cmd=<command>     REQUIRED command to send (must be last)\n"
        "  count=<N>         number of pings (default: 1)\n"
        "  timeout=<dur>     response timeout per ping (default: 1s)"
    ),
    handler=_handler,
    needs=CapabilitySet(serial_connected=True),
    sub_commands={
        "quiet": Command(
            "Ping without showing device response.",
            long_help=(
                "Same as {prefix}ping but suppresses the device response text;\n"
                "only the timing summary is printed.\n"
                "\n"
                "Parameters:\n"
                "  cmd=<command>     REQUIRED command to send (must be last)\n"
                "  count=<N>         number of pings (default: 1)\n"
                "  timeout=<dur>     response timeout per ping (default: 1s)"
            ),
            handler=_handler_quiet,
            args="{count=<N>} {timeout=<dur>} cmd=<command>",
            needs=CapabilitySet(serial_connected=True),
        ),
    },
)
