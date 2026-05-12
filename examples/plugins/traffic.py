"""Example plugin: scoped serial traffic counter.

Samples TX/RX byte counts for a fixed duration and reports the
totals.  Demonstrates the ``ctx.serial.rx_observer()`` /
``ctx.serial.tx_observer()`` context managers (the only public path
for observer registration -- the bare add/remove primitives are
intentionally underscore-prefixed so leaks are structurally
impossible from plugin code).

To use: copy this file to ``termapy_cfg/plugin/`` (global) or
``termapy_cfg/<config>/plugin/`` (per-config).

Usage::

    /traffic            Sample for 5 seconds (default).
    /traffic 10s        Sample for 10 seconds.
    /traffic 500ms      Sample for half a second.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command
from termapy.scripting import parse_duration

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Sample serial traffic for a duration; report TX/RX totals."""
    duration_arg = args.strip() or "5s"
    try:
        duration_s = parse_duration(duration_arg)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))

    tx_total = [0]
    rx_total = [0]

    def on_rx(data: bytes) -> None:
        rx_total[0] += len(data)

    def on_tx(data: bytes) -> None:
        tx_total[0] += len(data)

    ctx.io.status(f"Sampling traffic for {duration_s:.1f}s...")
    with (
        ctx.serial.rx_observer(on_rx),
        ctx.serial.tx_observer(on_tx),
    ):
        time.sleep(duration_s)

    ctx.io.result(
        f"TX: {tx_total[0]} bytes  RX: {rx_total[0]} bytes  "
        f"({duration_s:.1f}s)"
    )
    return CmdResult.ok(
        value={"tx_bytes": tx_total[0], "rx_bytes": rx_total[0],
               "duration_s": duration_s},
    )


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="traffic",
    args="{duration}",
    help="Sample serial TX/RX byte counts for a duration (default 5s).",
    handler=_handler,
)
