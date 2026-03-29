"""Demo plugin: sample temperature and draw an ASCII sparkline.

This plugin was designed with an LLM (Claude) using the PluginContext API.
It demonstrates a common embedded workflow:

    1. Send a command to the device repeatedly
    2. Parse numeric values from the responses
    3. Visualize the data as an ASCII plot in the terminal

The prompt used to generate this plugin:

    "Write a termapy plugin that sends AT+TEMP to the device N times,
    parses the temperature from each response, and draws an ASCII
    sparkline in the terminal. Use serial_io() for exclusive access,
    serial_drain/write/read_raw for the I/O cycle."

This was run from the root folder of termapy using claude code so it
could see the full context of the app.  This code was the first shot
with no edits. (other than this comment)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.scripting import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Sample temperature N times and display an ASCII sparkline.

    Sends AT+TEMP to the device, parses the numeric response,
    collects samples, and renders a sparkline with min/max/avg stats.

    Args:
        ctx: Plugin context for serial I/O and output.
        args: Number of samples (default 20).
    """
    if not ctx.is_connected():
        return CmdResult.fail(msg="Not connected.")

    try:
        count = int(args.strip()) if args.strip() else 20
        if count < 1 or count > 200:
            return CmdResult.fail(msg="Sample count must be 1-200.")
    except ValueError:
        return CmdResult.fail(msg="Usage: /temp_plot {count}")

    encoding = ctx.cfg.get("encoding", "utf-8")
    line_ending = ctx.cfg.get("line_ending", "\r")
    readings: list[float] = []

    ctx.write(f"Sampling {count} temperatures...", "dim")

    with ctx.serial_io():
        for _ in range(count):
            ctx.serial_drain()
            ctx.serial_write(f"AT+TEMP{line_ending}".encode(encoding))
            raw = ctx.serial_read_raw()
            text = raw.decode(encoding, errors="replace").strip()

            # Parse "+TEMP: 23.4C" -> 23.4
            for line in text.splitlines():
                if "+TEMP:" in line:
                    try:
                        val = float(line.split(":")[1].strip().rstrip("C"))
                        readings.append(val)
                    except (ValueError, IndexError):
                        pass
                    break
            time.sleep(0.025)

    if not readings:
        return CmdResult.fail(msg="No temperature readings captured.")

    # Stats
    lo = min(readings)
    hi = max(readings)
    avg = sum(readings) / len(readings)

    # ASCII sparkline - map values to block characters
    bars = "▁▂▃▄▅▆▇█"
    span = hi - lo or 1
    spark = ""
    for v in readings:
        idx = int((v - lo) / span * (len(bars) - 1))
        spark += bars[idx]

    ctx.write_markup(f"  [cyan]{spark}[/]")
    ctx.write(
        f"  {len(readings)} samples: "
        f"min={lo:.1f}°C  max={hi:.1f}°C  avg={avg:.1f}°C"
    )
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="temp_plot",
    args="{count}",
    help="Sample temperature and show ASCII sparkline.",
    long_help="""\
Send AT+TEMP to the device repeatedly and visualize the results.

  /temp_plot        - 20 samples (default)
  /temp_plot 50     - 50 samples
  /temp_plot 5      - quick 5-sample check

This plugin was generated with an LLM using the PluginContext API.
See the source code for the prompt and pattern.""",
    handler=_handler,
)
