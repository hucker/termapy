"""Built-in plugin: measure serial response time."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str, *, quiet: bool = False) -> None:
    kw = parse_keywords(args, {"cmd", "count", "timeout"}, rest_keyword="cmd")
    cmd = kw.get("cmd", "")
    if not cmd:
        ctx.write("Usage: /ping {count=<N>} {timeout=<dur>} cmd=<command>", "red")
        return
    try:
        count = int(kw.get("count", "1"))
    except ValueError:
        ctx.write("Ping: count must be an integer", "red")
        return
    if count < 1:
        ctx.write("Ping: count must be >= 1", "red")
        return
    try:
        timeout_ms = int(parse_duration(kw.get("timeout", "250ms")) * 1000)
    except ValueError as e:
        ctx.write(f"Ping: {e}", "red")
        return
    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return
    times: list[float] = []
    for i in range(count):
        with ctx.serial_io():
            ctx.serial_drain()
            start = time.perf_counter()
            ctx.serial_send(cmd)
            response = ctx.serial_read_raw(timeout_ms=timeout_ms)
            ms = (time.perf_counter() - start) * 1000
        times.append(ms)
        if response:
            ctx.write(f"{cmd} -- {ms:.0f}ms", "green")
            if not quiet:
                text = response.decode(ctx.cfg.get("encoding", "utf-8"), errors="replace").strip()
                if text:
                    ctx.write(f"  {text}", "dim")
        else:
            ctx.write(f"{cmd} -- timeout ({timeout_ms}ms)", "red")
    if count > 1:
        avg = sum(times) / len(times)
        lo = min(times)
        hi = max(times)
        ctx.write(f"{count} pings: avg={avg:.0f}ms min={lo:.0f}ms max={hi:.0f}ms", "dim")


def _handler_quiet(ctx: PluginContext, args: str) -> None:
    _handler(ctx, args, quiet=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Send a command and measure response time.",
    name="ping",
    args="{count=<N>} {timeout=<dur>} cmd=<command>",
    handler=_handler,
    sub_commands={
        "quiet": Command(
            "Ping without showing device response.",
            handler=_handler_quiet,
            args="{count=<N>} {timeout=<dur>} cmd=<command>",
        ),
    },
)
