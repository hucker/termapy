"""Demo plugin: a /traffic.* family for inspecting bytes on the wire.

A working example of the ``ctx.serial.rx_observer()`` and
``ctx.serial.tx_observer()`` context managers.  Demonstrates four
real debugging tools any plugin author would find useful when working
with an unfamiliar serial device:

  /traffic.count <cmd>            - run a command, count bytes both ways
  /traffic.hexdump <file> [dur]   - tee timestamped hex of all I/O
                                    to a file for a duration
  /traffic.rate [duration]        - bytes/sec sample over a window
  /traffic.snoop <hex> [timeout]  - wait until a byte pattern appears

Every handler uses the context-manager form so observers are removed
on every exit path including exceptions.  Because the bare register/
unregister methods are unreachable from plugin code, the safe pattern
is the only pattern -- you couldn't write the unsafe version even if
you tried.

Copy this file as a starting point for your own traffic-inspection
plugins (or strip out everything and keep just the snoop handler if
that's all you need).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── /traffic.count ──────────────────────────────────────────────────────────


def _handler_count(ctx: PluginContext, args: str) -> CmdResult:
    """Run the wrapped command; report TX/RX bytes that flowed during it.

    The simplest use of the observer context managers: declare two
    counter callbacks, register them for the duration of the dispatch,
    let the safe-by-construction context exit unregister them.
    """
    if not args.strip():
        return CmdResult.fail(msg="Usage: /traffic.count <cmd>")

    rx_count = [0]
    tx_count = [0]

    def count_rx(data: bytes) -> None:
        rx_count[0] += len(data)

    def count_tx(data: bytes) -> None:
        tx_count[0] += len(data)

    with ctx.serial.rx_observer(count_rx), ctx.serial.tx_observer(count_tx):
        ctx.dispatch(args)

    ctx.io.write(
        f"  TX: [yellow]{tx_count[0]}[/]  "
        f"RX: [cyan]{rx_count[0]}[/]  bytes",
    )
    return CmdResult.ok(value={"tx": tx_count[0], "rx": rx_count[0]})


# ── /traffic.hexdump ────────────────────────────────────────────────────────


def _handler_hexdump(ctx: PluginContext, args: str) -> CmdResult:
    """Tee timestamped hex of all serial I/O to a file for a duration.

    Useful for debugging line-ending issues, framing bugs, or just
    "what is actually on the wire?" -- runs alongside the normal
    pipeline so device responses still render in scrollback.

    Output format is one line per chunk::

        [+0.012s] TX  41 54 2b 56 45 52 0d
        [+0.034s] RX  56 45 52 3d 31 2e 32 2e 33 0d 0a
    """
    parts = args.split()
    if not parts:
        return CmdResult.fail(msg="Usage: /traffic.hexdump <file> [duration=<dur>]")
    out_path = Path(parts[0])
    try:
        duration_s = parse_duration(parts[1]) if len(parts) > 1 else 5.0
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid duration: {e}")

    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as f:
        def tee(direction: str):
            def cb(data: bytes) -> None:
                ts = time.monotonic() - t0
                f.write(f"[+{ts:6.3f}s] {direction}  {data.hex(' ')}\n")
                f.flush()
            return cb

        with ctx.serial.rx_observer(tee("RX")), ctx.serial.tx_observer(tee("TX")):
            ctx.io.write(f"  Hex dump -> {out_path} for {duration_s:.1f}s")
            time.sleep(duration_s)

    return CmdResult.ok(value={"path": str(out_path), "duration_s": duration_s})


# ── /traffic.rate ───────────────────────────────────────────────────────────


def _handler_rate(ctx: PluginContext, args: str) -> CmdResult:
    """Measure throughput: bytes/sec over a window.

    Counts bytes for the requested duration, returns the rate.  Useful
    for "is this device actually responsive at the configured baud?"
    sanity checks.
    """
    try:
        duration_s = parse_duration(args.strip()) if args.strip() else 5.0
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid duration: {e}")

    rx_count = [0]
    tx_count = [0]

    with (
        ctx.serial.rx_observer(lambda d: rx_count.__setitem__(0, rx_count[0] + len(d))),
        ctx.serial.tx_observer(lambda d: tx_count.__setitem__(0, tx_count[0] + len(d))),
    ):
        ctx.io.write(f"  Sampling for {duration_s:.1f}s...")
        time.sleep(duration_s)

    rx_rate = rx_count[0] / duration_s
    tx_rate = tx_count[0] / duration_s
    ctx.io.write(
        f"  TX: [yellow]{tx_count[0]}[/] bytes "
        f"([yellow]{tx_rate:.1f}[/] B/s)",
    )
    ctx.io.write(
        f"  RX: [cyan]{rx_count[0]}[/] bytes "
        f"([cyan]{rx_rate:.1f}[/] B/s)",
    )
    return CmdResult.ok(value={
        "duration_s": duration_s,
        "tx_bytes": tx_count[0],
        "rx_bytes": rx_count[0],
        "tx_rate_bps": tx_rate,
        "rx_rate_bps": rx_rate,
    })


# ── /traffic.snoop ──────────────────────────────────────────────────────────


def _handler_snoop(ctx: PluginContext, args: str) -> CmdResult:
    """Block until a hex byte pattern appears in RX, or timeout.

    Unlike ``/expect`` (which works on decoded text lines) this watches
    the raw byte stream -- useful for binary-protocol devices that
    don't always send newlines.  Crucially, the device's normal output
    keeps flowing through the regular pipeline; this is a passive watch.
    """
    kw = parse_keywords(args, {"timeout"}, rest_keyword="pattern")
    pattern_hex = kw.get("pattern", "").replace(" ", "")
    if not pattern_hex:
        return CmdResult.fail(msg="Usage: /traffic.snoop <hex> [timeout=<dur>]")
    try:
        pattern = bytes.fromhex(pattern_hex)
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid hex: {e}")
    try:
        timeout_s = parse_duration(kw.get("timeout", "5s"))
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid timeout: {e}")

    found = threading.Event()
    buf = bytearray()

    def watch(data: bytes) -> None:
        buf.extend(data)
        if pattern in buf:
            found.set()

    with ctx.serial.rx_observer(watch):
        if not found.wait(timeout=timeout_s):
            return CmdResult.fail(
                msg=f"Timeout ({timeout_s:.1f}s) waiting for {pattern_hex}",
                value={"matched": False, "pattern": pattern_hex},
            )

    # Find the offset of the match within the captured buffer so the LLM
    # can see how much "noise" preceded it.
    offset = bytes(buf).find(pattern)
    ctx.io.write(
        f"  Matched [green]{pattern_hex}[/] at offset "
        f"[bold]{offset}[/] (after {len(buf) - len(pattern) - offset} "
        f"trailing bytes captured)",
    )
    return CmdResult.ok(value={
        "matched": True,
        "pattern": pattern_hex,
        "offset": offset,
        "bytes_captured": len(buf),
    })


# ── Help summary that grows with file count ─────────────────────────────────


_LONG_HELP = """\
Inspect serial-port traffic without disrupting normal operation.  Every
subcommand uses RX/TX observers (passive byte taps) so the device's
output continues to render in scrollback while traffic is also being
measured / logged / pattern-matched in the background.

  {prefix}traffic.count <cmd>            run cmd; report TX/RX bytes
  {prefix}traffic.hexdump <file> [dur]   tee hex to file for a duration
  {prefix}traffic.rate [duration]        bytes/sec rate over a window
  {prefix}traffic.snoop <hex> [timeout]  wait for byte pattern in RX

Examples:

  {prefix}traffic.count AT+VER             - bytes used by one command
  {prefix}traffic.hexdump bug.log 30s      - capture 30s of wire-trace
  {prefix}traffic.rate 10s                 - 10s bytes/sec sample
  {prefix}traffic.snoop FF55 timeout=2s    - wait for sync sequence

This is a demo plugin -- read the source for the canonical pattern
(``with ctx.rx_observer(cb): ...`` / ``with ctx.tx_observer(cb): ...``)
that any new RX/TX-watching plugin should follow."""


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="traffic",
    help="Inspect serial-port byte traffic (count, hexdump, rate, snoop).",
    long_help=_LONG_HELP,
    handler=None,
    sub_commands={
        "count": Command(
            args="<cmd>",
            help="Run a command and report TX/RX bytes during it.",
            handler=_handler_count,
            needs=CapabilitySet(serial_connected=True),
        ),
        "hexdump": Command(
            args="<file> [duration]",
            help="Tee timestamped hex of TX/RX traffic to a file.",
            handler=_handler_hexdump,
            needs=CapabilitySet(serial_connected=True),
        ),
        "rate": Command(
            args="[duration]",
            help="Measure TX/RX bytes/sec over a window (default 5s).",
            handler=_handler_rate,
            needs=CapabilitySet(serial_connected=True),
        ),
        "snoop": Command(
            args="<hex> [timeout=<dur>]",
            help="Block until a hex byte pattern appears in the RX stream.",
            handler=_handler_snoop,
            needs=CapabilitySet(serial_connected=True, block_until=True),
        ),
    },
)
