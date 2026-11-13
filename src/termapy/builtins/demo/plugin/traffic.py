"""Demo plugin: a /traffic.* family for inspecting bytes on the wire.

A working example of the ``ctx.serial.rx_observer()`` and
``ctx.serial.tx_observer()`` context managers.  Demonstrates four
real debugging tools any plugin author would find useful when working
with an unfamiliar serial device:

  /traffic.count <cmd>            - run a command, count bytes both ways,
                                    the reply included (wait_gap=, 50ms)
  /traffic.hexdump <file> [dur]   - tee timestamped hex of all I/O
                                    to a file for a duration
  /traffic.rate [duration]        - bytes/sec sample over a window
  /traffic.snoop <bytes> {timeout} - wait until a byte pattern appears

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
from typing import TYPE_CHECKING

from termapy.folders import ensure_folder
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError, escape_markup
from termapy.plugins.params import ParamSpec
from termapy.protocol import parse_data
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from collections.abc import Callable

    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


# ── /traffic.count ──────────────────────────────────────────────────────────


def _handler_count(ctx: PluginContext, args: str) -> CmdResult:
    """Run the wrapped command, wait for the reply to go quiet, report the TX/RX bytes.

    The simplest use of the observer context managers: declare two
    counter callbacks, register them for the duration of the dispatch,
    let the safe-by-construction context exit unregister them.

    A device line returns as soon as it is written, so the reply arrives
    after the dispatch does.  The observers stay registered through
    ``ctx.serial.wait_idle``: one ``wait_gap`` of silence after the
    command, longer while bytes keep coming, 5 s at most.  A device that
    takes longer than ``wait_gap`` to START answering needs a bigger gap
    (``wait_gap=200ms``); ``wait_gap=0ms`` counts only what the dispatch
    itself saw.
    """
    rx_count = [0]
    tx_count = [0]

    def count_rx(data: bytes) -> None:
        rx_count[0] += len(data)

    def count_tx(data: bytes) -> None:
        tx_count[0] += len(data)

    with ctx.serial.rx_observer(count_rx), ctx.serial.tx_observer(count_tx):
        ctx.dispatch(ctx.arg("cmd"))
        ctx.serial.wait_idle(round(ctx.arg("wait_gap") * 1000), 5.0)

    ctx.io.output_markup(
        f"  TX: [yellow]{tx_count[0]:d}[/]  RX: [cyan]{rx_count[0]:d}[/]  bytes",
    )
    # value is the scriptable scalar, the reply's size; the counts both ways
    # are the data, keyed as /cap.wire keys them.
    return CmdResult.ok(
        value=rx_count[0],
        data={"tx_bytes": tx_count[0], "rx_bytes": rx_count[0]},
    )


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
        raise UsageError()
    # What any plugin writing a user-named file calls: a relative name lands in
    # cap/, and an MCP client stays in the config's folder, away from its code
    # folders, and replaces a file only inside it.
    out_path = ctx.fs.resolve(parts[0], "cap")
    ctx.fs.guard_code_folder(out_path.parent)
    ctx.fs.guard_replace(out_path)
    try:
        duration_s = parse_duration(parts[1]) if len(parts) > 1 else 5.0
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid duration: {e}")

    t0 = time.monotonic()
    ensure_folder(out_path.parent)
    with out_path.open("w", encoding="utf-8") as f:

        def tee(direction: str) -> Callable[[bytes], None]:
            def cb(data: bytes) -> None:
                ts = time.monotonic() - t0
                f.write(f"[+{ts:6.3f}s] {direction}  {data.hex(' ').upper()}\n")
                f.flush()

            return cb

        with ctx.serial.rx_observer(tee("RX")), ctx.serial.tx_observer(tee("TX")):
            ctx.io.output(f"  Hex dump -> {out_path} for {duration_s:.1f}s")
            time.sleep(duration_s)

    return CmdResult.ok(value=str(out_path), data={"path": str(out_path), "duration_s": duration_s})


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
        ctx.io.output(f"  Sampling for {duration_s:.1f}s...")
        time.sleep(duration_s)

    rx_rate = rx_count[0] / duration_s
    tx_rate = tx_count[0] / duration_s
    ctx.io.output_markup(
        f"  TX: [yellow]{tx_count[0]:d}[/] bytes ([yellow]{tx_rate:.1f}[/] B/s)",
    )
    ctx.io.output_markup(
        f"  RX: [cyan]{rx_count[0]:d}[/] bytes ([cyan]{rx_rate:.1f}[/] B/s)",
    )
    return CmdResult.ok(
        value=round(rx_rate, 1),
        data={
            "duration_s": duration_s,
            "tx_bytes": tx_count[0],
            "rx_bytes": rx_count[0],
            "tx_rate_bps": tx_rate,
            "rx_rate_bps": rx_rate,
        },
    )


# ── /traffic.snoop ──────────────────────────────────────────────────────────


def _handler_snoop(ctx: PluginContext, args: str) -> CmdResult:
    """Block until a hex byte pattern appears in RX, or timeout.

    Unlike ``/expect`` (which works on decoded text lines) this watches
    the raw byte stream -- useful for binary-protocol devices that
    don't always send newlines.  Crucially, the device's normal output
    keeps flowing through the regular pipeline; this is a passive watch.
    """
    # The pattern is positional, as the synopsis says.  It used to be read as
    # a ``pattern=`` rest keyword, so the documented ``/traffic.snoop 4F 4B
    # timeout=2s`` always arrived empty (found by its first test, 2026-10-13).
    kw = parse_keywords(args, {"timeout"})
    pattern_text = kw.get("_positional", "").strip()
    if not pattern_text:
        raise UsageError()
    try:
        # The shared byte reader: hex in any spelling or case, "quoted text".
        pattern = parse_data(pattern_text)
    except ValueError as e:
        return CmdResult.fail(msg=f"Parse error: {e}")
    pattern_hex = pattern.hex().upper()
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
                data={"matched": False, "pattern": pattern_hex},
            )

    # Find the offset of the match within the captured buffer so the LLM
    # can see how much "noise" preceded it.
    offset = bytes(buf).find(pattern)
    ctx.io.output_markup(
        f"  Matched [green]{escape_markup(pattern_hex)}[/] at offset "
        f"[bold]{offset:d}[/] (after {len(buf) - len(pattern) - offset:d} "
        f"trailing bytes captured)",
    )
    return CmdResult.ok(
        value=offset,
        data={
            "matched": True,
            "pattern": pattern_hex,
            "offset": offset,
            "bytes_captured": len(buf),
        },
    )


# ── Help summary that grows with file count ─────────────────────────────────


_LONG_HELP = """\
Inspect serial-port traffic without disrupting normal operation.  Every
subcommand uses RX/TX observers (passive byte taps) so the device's
output continues to render in scrollback while traffic is also being
measured / logged / pattern-matched in the background.

  {prefix}traffic.count <cmd>            run cmd; report TX/RX bytes, reply included
  {prefix}traffic.hexdump <file> [dur]   tee hex to file for a duration
  {prefix}traffic.rate [duration]        bytes/sec rate over a window
  {prefix}traffic.snoop <hex> [timeout]  wait for byte pattern in RX

Examples:

  {prefix}traffic.count AT+INFO            - bytes used by one command
  {prefix}traffic.count wait_gap=200ms AT+INFO - for a device slow to answer
  {prefix}traffic.hexdump bug.log 30s      - capture 30s of wire-trace
  {prefix}traffic.rate 10s                 - 10s bytes/sec sample
  {prefix}traffic.snoop FF55 timeout=2s    - wait for sync sequence

This is a demo plugin -- read the source for the canonical pattern
(``with ctx.serial.rx_observer(cb): ...`` / ``with ctx.serial.tx_observer(cb): ...``)
that any new RX/TX-watching plugin should follow."""


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="traffic",
    help="Inspect serial-port byte traffic (count, hexdump, rate, snoop).",
    long_help=_LONG_HELP,
    handler=None,
    sub_commands={
        "count": Command(
            help="Run a command and report the TX/RX bytes, its reply included.",
            handler=_handler_count,
            params=[
                ParamSpec(
                    "wait_gap",
                    "duration",
                    default=0.05,
                    help="quiet gap that ends the wait for the reply, e.g. 200ms",
                ),
                ParamSpec(
                    "cmd",
                    "str",
                    positional=True,
                    required=True,
                    rest=True,
                    hint="<cmd>",
                    help="the command to run and count",
                ),
            ],
            needs=CapabilitySet.SERIAL_CONNECTED,
        ),
        "hexdump": Command(
            args="<file> {duration=<dur>}",
            help="Tee timestamped hex of TX/RX traffic to a file.",
            handler=_handler_hexdump,
            needs=CapabilitySet.SERIAL_CONNECTED,
        ),
        "rate": Command(
            args="{duration}",
            help="Measure TX/RX bytes/sec over a window (default 5s).",
            handler=_handler_rate,
            needs=CapabilitySet.SERIAL_CONNECTED,
        ),
        "snoop": Command(
            args="<bytes> {timeout=<dur>}",
            help='Block until a byte pattern (hex or "text") appears in the RX stream.',
            handler=_handler_snoop,
            needs=CapabilitySet(serial_connected=True, block_until=True),
        ),
    },
)
