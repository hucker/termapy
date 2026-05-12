"""Example plugin: live serial traffic monitor.

Displays running TX/RX byte counts in the status bar at the bottom
of the screen while monitoring is on.  Demonstrates:

  - The ``ctx.serial.rx_observer()`` / ``ctx.serial.tx_observer()``
    context managers (the only public observer API).
  - Holding the observer context open from a background thread so
    the persistent on/off pattern works without leaking observers
    (the with-block guarantees release on /traffic off).
  - ``ctx.io.status_bar()`` for non-scrollback status display.

To use: copy this file to ``termapy_cfg/plugin/`` (global) or
``termapy_cfg/<config>/plugin/`` (per-config).

Usage::

    /traffic            Show current counts (and on/off status).
    /traffic on         Start monitoring -- status bar updates live.
    /traffic off        Stop monitoring and clear the status bar.
    /traffic.reset      Reset the counters to zero.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_NS_KEY = "traffic"


def _state(ctx: PluginContext) -> dict:
    """Get or initialize the traffic monitor state."""
    ns = ctx.ns(_NS_KEY)
    if "tx_bytes" not in ns:
        ns["tx_bytes"] = 0
        ns["rx_bytes"] = 0
        ns["thread"] = None
        ns["stop_event"] = None
    return ns


def _format(n: int) -> str:
    if n < 1024:
        return f"{n}"
    if n < 1024 * 1024:
        return f"{n:,} ({n / 1024:.1f} KB)"
    return f"{n:,} ({n / (1024 * 1024):.1f} MB)"


def _watcher(ctx: PluginContext, ns: dict, stop_event: threading.Event) -> None:
    """Background thread: hold the observer context open and update the bar."""

    def on_rx(data: bytes) -> None:
        ns["rx_bytes"] += len(data)

    def on_tx(data: bytes) -> None:
        ns["tx_bytes"] += len(data)

    with (
        ctx.serial.rx_observer(on_rx),
        ctx.serial.tx_observer(on_tx),
    ):
        while not stop_event.is_set():
            stop_event.wait(timeout=0.5)
            # status_bar refresh is fast and idempotent; called from
            # the worker so the main thread isn't pulled in on every
            # byte.
            try:
                ctx.io.status_bar(
                    f"TX:{_format(ns['tx_bytes'])}  "
                    f"RX:{_format(ns['rx_bytes'])}",
                    timeout=2.0,
                )
            except Exception:  # noqa: BLE001 -- never crash a background thread
                pass


def _is_active(ns: dict) -> bool:
    t = ns.get("thread")
    return t is not None and t.is_alive()


def _start(ctx: PluginContext) -> None:
    ns = _state(ctx)
    if _is_active(ns):
        return
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_watcher, args=(ctx, ns, stop_event), daemon=True,
    )
    ns["stop_event"] = stop_event
    ns["thread"] = thread
    thread.start()


def _stop(ctx: PluginContext) -> None:
    ns = _state(ctx)
    if not _is_active(ns):
        return
    ns["stop_event"].set()
    # Give the thread a moment to exit the with-block and release observers.
    ns["thread"].join(timeout=1.0)
    ns["thread"] = None
    ns["stop_event"] = None
    ctx.io.status_bar("")  # clear the bar


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Show counts, or start / stop monitoring."""
    ns = _state(ctx)
    arg = args.strip().lower()

    if arg == "on":
        _start(ctx)
        return CmdResult.ok(value="on")
    if arg == "off":
        _stop(ctx)
        return CmdResult.ok(value="off")
    if not arg:
        state = "on" if _is_active(ns) else "off"
        ctx.io.result(
            f"TX: {_format(ns['tx_bytes'])}  "
            f"RX: {_format(ns['rx_bytes'])}  ({state})"
        )
        return CmdResult.ok(
            value={"tx_bytes": ns["tx_bytes"], "rx_bytes": ns["rx_bytes"],
                   "active": _is_active(ns)},
        )
    return CmdResult.fail(msg="Usage: /traffic [on|off]")


def _handler_reset(ctx: PluginContext, args: str) -> CmdResult:
    """Reset byte counters to zero."""
    ns = _state(ctx)
    ns["tx_bytes"] = 0
    ns["rx_bytes"] = 0
    return CmdResult.ok(value="reset")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="traffic",
    args="{on|off}",
    help="Live serial traffic monitor (status bar shows TX/RX byte counts).",
    handler=_handler_root,
    sub_commands={
        "reset": Command(
            help="Reset TX/RX byte counters to zero.",
            handler=_handler_reset,
        ),
    },
)
