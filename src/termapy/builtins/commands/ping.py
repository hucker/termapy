"""Built-in plugin: measure serial response time."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from termapy.defaults import cfg_value
from termapy.markup import received_json_markup
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.plugins.params import ParamSpec

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# A duration arrives as float seconds (0.25 = 250 ms).
_PING_PARAMS = [
    ParamSpec("count", "int", default=1, min=1, help="number of pings"),
    ParamSpec("timeout", "duration", default=0.25, help="response timeout per ping"),
    ParamSpec("cmd", "command", required=True, rest=True, help="command to send"),
]


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Send ``cmd=`` ``count`` times, printing each round trip, then the avg/min/max summary.

    The output levels shape it: each round trip's time is output (normal),
    the reply's text under it is detail (verbose), and the summary is the
    result, so ``.quiet`` shows the summary alone.  The value is the mean
    reply time (``61ms``), ``""`` when every ping timed out; the data is
    every round trip (``_ping_record``).  A timeout is not a reply time, so
    it is counted, never averaged.
    """
    cmd = ctx.arg("cmd")
    count = ctx.arg("count")
    timeout_ms = int(ctx.arg("timeout") * 1000)  # param is float seconds
    # One entry per ping: its reply time in ms, or None when it timed out.
    times: list[float | None] = []
    for i in range(count):
        with ctx.serial.io():
            ctx.serial.drain()
            start = time.perf_counter()
            ctx.serial.send(cmd)
            response = ctx.serial.read_raw(timeout_ms=timeout_ms)
            ms = (time.perf_counter() - start) * 1000
        if response:
            times.append(ms)
            ctx.io.output(f"{cmd} -- {ms:.0f}ms", "green")
            text = response.decode(cfg_value(ctx.cfg, "encoding"), errors="replace").strip()
            _show_reply(ctx, text)
        else:
            times.append(None)
            ctx.io.output(f"{cmd} -- timeout ({timeout_ms}ms)", "red")
    record = _ping_record(cmd, times)
    replies = record["replies"]
    if count > 1 and replies:
        timeouts = f" ({record['timeouts']} timed out)" if record["timeouts"] else ""
        ctx.io.result(
            f"{count} pings: avg={record['avg_ms']:.0f}ms min={record['min_ms']:.0f}ms "
            f"max={record['max_ms']:.0f}ms{timeouts}"
        )
    # The mean reply time in ms, a number (registry value-typed; the unit is
    # the key's, as in data).  Every ping timed out: an empty value rather
    # than a missing one, so a script tells "tried but nothing" from "didn't run".
    value = round(record["avg_ms"], 1) if replies else ""
    return CmdResult.ok(value=value, data=record)


def _show_reply(ctx: PluginContext, text: str) -> None:
    """The reply's text under its time, a detail line each (verbose), JSON laid out as the screen lays it out.

    A JSON line reads like one arriving from the device (``json_fmt`` on a
    screen, one line in a pipe), so a JSON-speaking board's reply looks the
    same under ``/ping.verbose`` as at the prompt.  An answer for a program
    (``--json``, MCP) keeps each line as it came; ``json_layout`` knows.
    """
    indent, highlight = ctx.internal.json_layout()
    for line in text.splitlines():
        shown = received_json_markup(line, indent, highlight)
        if shown is None:
            ctx.io.status(f"  {line}")
        else:
            ctx.io.status_markup("\n".join(f"  {row}" for row in shown.split("\n")))


def _ping_record(cmd: str, times: list[float | None]) -> dict[str, Any]:
    """Every round trip as data: each reply time in ms (``None`` a timeout) and their stats.

    ``avg_ms``, ``min_ms`` and ``max_ms`` are over the replies only, and
    ``None`` when there were none.
    """
    replied = [ms for ms in times if ms is not None]
    return {
        "cmd": cmd,
        "count": len(times),
        "replies": len(replied),
        "timeouts": len(times) - len(replied),
        "times_ms": [None if ms is None else round(ms, 1) for ms in times],
        "avg_ms": round(sum(replied) / len(replied), 1) if replied else None,
        "min_ms": round(min(replied), 1) if replied else None,
        "max_ms": round(max(replied), 1) if replied else None,
    }


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Send a command and measure response time.",
    long_help=(
        "Sends a command to the device and measures the round trip, count times,\n"
        "then reports min/max/mean.  Each round trip prints its time; add .verbose\n"
        "for each reply's text under it, or .quiet for the summary alone."
    ),
    group="protocol",
    name="ping",
    handler=_handler,
    cfg={"encoding": "decodes each reply (shown at verbose)"},
    needs=CapabilitySet.SERIAL_CONNECTED,
    params=_PING_PARAMS,
)
