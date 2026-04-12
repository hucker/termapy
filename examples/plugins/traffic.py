"""Example plugin: serial traffic monitor.

Displays a running TX/RX byte count in the status bar whenever data
flows.  Demonstrates the RX/TX observer API and the status bar API.

To use: copy this file to termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config).

Usage:
    /traffic            Show current counts
    /traffic on         Start monitoring
    /traffic off        Stop monitoring
    /traffic.reset      Reset counters to zero
"""

from __future__ import annotations

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
        ns["active"] = False
    return ns


def _format_count(n: int) -> str:
    """Format a byte count with commas."""
    if n < 1024:
        return f"{n}"
    if n < 1024 * 1024:
        return f"{n:,} ({n / 1024:.1f} KB)"
    return f"{n:,} ({n / (1024 * 1024):.1f} MB)"


def _update_status(ctx: PluginContext) -> None:
    """Push current counts to the status bar."""
    ns = _state(ctx)
    tx = _format_count(ns["tx_bytes"])
    rx = _format_count(ns["rx_bytes"])
    ctx.status_bar(f"TX:{tx}  RX:{rx}", timeout=10.0)


def _on_rx(ctx: PluginContext, data: bytes) -> None:
    """RX observer callback."""
    ns = _state(ctx)
    ns["rx_bytes"] += len(data)
    _update_status(ctx)


def _on_tx(ctx: PluginContext, data: bytes) -> None:
    """TX observer callback."""
    ns = _state(ctx)
    ns["tx_bytes"] += len(data)
    _update_status(ctx)


def _start(ctx: PluginContext) -> None:
    """Register observers and start monitoring."""
    ns = _state(ctx)
    if ns["active"]:
        return
    # Create bound callbacks and store references for removal
    ns["_rx_cb"] = lambda data: _on_rx(ctx, data)
    ns["_tx_cb"] = lambda data: _on_tx(ctx, data)
    ctx.add_rx_observer(ns["_rx_cb"])
    ctx.add_tx_observer(ns["_tx_cb"])
    ns["active"] = True


def _stop(ctx: PluginContext) -> None:
    """Unregister observers and stop monitoring."""
    ns = _state(ctx)
    if not ns["active"]:
        return
    ctx.remove_rx_observer(ns["_rx_cb"])
    ctx.remove_tx_observer(ns["_tx_cb"])
    ns["active"] = False
    ctx.status_bar("")


# ── Handlers ────────────────────────────────────────────────────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Show counts, or turn monitoring on/off."""
    arg = args.strip().lower()
    ns = _state(ctx)

    if arg == "on":
        _start(ctx)
        ctx.write("Traffic monitor enabled.", "green")
        return CmdResult.ok()

    if arg == "off":
        _stop(ctx)
        ctx.write("Traffic monitor disabled.", "green")
        return CmdResult.ok()

    if not arg:
        tx = _format_count(ns["tx_bytes"])
        rx = _format_count(ns["rx_bytes"])
        status = "on" if ns["active"] else "off"
        ctx.write(f"  TX: {tx}  RX: {rx}  ({status})")
        return CmdResult.ok()

    ctx.write("Usage: /traffic [on|off]", "yellow")
    return CmdResult.ok()


def _handler_reset(ctx: PluginContext, args: str) -> CmdResult:
    """Reset byte counters."""
    ns = _state(ctx)
    ns["tx_bytes"] = 0
    ns["rx_bytes"] = 0
    ctx.write("Traffic counters reset.", "green")
    if ns["active"]:
        _update_status(ctx)
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="traffic",
    args="{on|off}",
    help="Serial traffic monitor with live byte counts in status bar.",
    handler=_handler_root,
    sub_commands={
        "reset": Command(
            help="Reset TX/RX byte counters to zero.",
            handler=_handler_reset,
        ),
    },
)
