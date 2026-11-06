"""Measure what termapy receives intact, and how fast, on the jumpered hw_loopback adapter.

Two shapes of traffic, because they fail differently:

  repl    REPL-sized replies, one at a time: each reply (1 KB by default)
          is sent only after the previous one has fully arrived, as a
          command-and-answer session runs.  Reports replies intact and
          the time from sending a reply to the layer having all of it.
  stream  one long stream at full line rate: the worst case, a device
          dumping a large buffer.  Reports lines lost and how close each
          layer finished to the wire time.

A reply never fills a buffer.  Bytes are lost only when a SUSTAINED stream
arrives faster than it is drained for longer than every buffer on the way
can hide: the bridge chip's FIFO, the driver buffer termapy requests
(``SERIAL_RX_BUFFER_BYTES``) and termapy's own queues.  So the stream runs
for seconds, and each layer is judged by when it FINISHED: the jumper
delivers at most the line rate, so a layer that keeps up finishes with the
wire, and one that falls behind finishes late by its backlog -- visible
long before any buffer overflows.  Every line is numbered and checked.

The same traffic goes through four layers, each on a fresh connection:

  raw     a bare pyserial reader thread: the adapter and driver ceiling
  engine  termapy's SerialEngine reader, decoding and splitting lines
          (what the MCP server sits on)
  cli     the CLI: the engine plus every line printed by the CLI's
          console, on the reader thread, into os.devnull, so a terminal
          emulator's own speed is not in the number
  tui     the TUI, headless: lines rendered into the output pane on the
          main thread

The cli and tui layers subclass the real frontends and record each batch
of lines where every frontend hands them on (``_feed_rx_consumers``,
``_write_batch``); nothing else is changed.

SAFETY: everything is written into the port, so this refuses to run unless
a probe sent at 115200 comes straight back -- a jumpered adapter, not a
device.  The writer runs in this process, as it must (one process holds the
port), and a loopback must SEND every byte it receives: both make the
numbers pessimistic for termapy, since a real device does its own sending
and answers a short command with a long reply.  Close any termapy holding
the adapter first.

Usage:
    uv run python scripts/throughput.py
    uv run python scripts/throughput.py --mode repl --baud 115200,921600,2000000
    uv run python scripts/throughput.py --baud 460800,921600 --kb 2048 --layers raw,engine,tui

Exits 1 when the rig is unusable (no adapter, busy, or no echo), else 0:
it reports numbers, it does not pass or fail them.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from termapy.capture import CaptureEngine
from termapy.config import SERIAL_RX_BUFFER_BYTES, open_serial
from termapy.defaults import DEFAULT_CFG
from termapy.port_control import AmbiguousSerialNumberError, resolve_port
from termapy.serial_engine import SerialEngine

LAYERS = ("raw", "engine", "cli", "tui")
MODES = ("stream", "repl")
# 8N1: a start bit, eight data bits and a stop bit per byte.
BITS_PER_BYTE = 10
WRITE_CHUNK = 4096
# No progress for this long after the last write: the layer has taken all it will.
QUIET_S = 3.0
# A reply not complete this long after its wire time counts as missed.
REPLY_GRACE_S = 2.0
_FILL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" * 16


@dataclass(frozen=True)
class Stream:
    """The numbered lines every layer receives: ``00000042 ABC...`` then CRLF."""

    line_bytes: int
    count: int

    def text(self, seq: int) -> str:
        """Line ``seq`` as a layer should receive it, without its CRLF."""
        return f"{seq:08d} " + _FILL[: self.line_bytes - 11]

    def data(self) -> bytes:
        """Every line as it goes on the wire."""
        return "".join(self.text(seq) + "\r\n" for seq in range(self.count)).encode()


@dataclass(frozen=True)
class Plan:
    """What the writer sends at one baud: a flat-out stream, or replies one at a time."""

    stream: Stream
    data: bytes
    baud: int
    reply_lines: int  # 0: one stream, flat out; else lines per reply, each waiting for the last

    def wire_s(self, size: int) -> float:
        """Seconds ``size`` bytes take on the wire at this baud."""
        return size * BITS_PER_BYTE / self.baud

    def target(self, units_are_bytes: bool) -> int:
        """What the layer has consumed once it has everything: bytes for raw, lines otherwise."""
        return len(self.data) if units_are_bytes else self.stream.count


class Sink:
    """What a layer consumed, and when it last consumed anything; filled from any thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.lines: list[str] = []
        self.units = 0  # lines, or bytes for the raw layer
        self.last = 0.0

    def add_lines(self, lines: list[str]) -> None:
        """Record a batch of lines the layer has finished with."""
        with self._lock:
            self.lines.extend(lines)
            self.units += len(lines)
            self.last = time.monotonic()

    def add_bytes(self, count: int) -> None:
        """Record bytes the raw reader took."""
        with self._lock:
            self.units += count
            self.last = time.monotonic()


class Writer:
    """Sends the plan from its own thread, as a device would: flat out, or a reply at a time."""

    def __init__(
        self, write: Callable[[bytes], object], plan: Plan, sink: Sink, units_are_bytes: bool
    ) -> None:
        self._write = write
        self._plan = plan
        self._sink = sink
        self._units_are_bytes = units_are_bytes
        self.started = 0.0
        self.finished = 0.0
        self.error = ""
        # Writes the port reported as shorter than the chunk: bytes that never
        # left, lost on the SENDING side, which only the loopback has.
        self.short_writes = 0
        self.short_bytes = 0
        # repl mode: seconds from sending each reply to the layer having all of it.
        self.reply_times: list[float] = []
        self.replies = 0

    def start(self) -> None:
        """Start sending; ``started`` is the clock every layer is timed from."""
        self.started = time.monotonic()
        threading.Thread(target=self._run, name="throughput-writer", daemon=True).start()

    def _run(self) -> None:
        """Send the plan, recording a write failure rather than raising on the thread."""
        try:
            if self._plan.reply_lines:
                self._replies()
            else:
                for offset in range(0, len(self._plan.data), WRITE_CHUNK):
                    self._send(self._plan.data[offset : offset + WRITE_CHUNK])
        except OSError as e:  # pyserial's SerialException is an OSError
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.finished = time.monotonic()

    def _send(self, chunk: bytes) -> None:
        """Write one chunk, counting it if the port reports it short."""
        written = self._write(chunk)
        if isinstance(written, int) and written < len(chunk):
            self.short_writes += 1
            self.short_bytes += len(chunk) - written

    def _replies(self) -> None:
        """Send each reply, then wait until the layer has all of it before sending the next."""
        size = self._plan.reply_lines * self._plan.stream.line_bytes
        expect = size if self._units_are_bytes else self._plan.reply_lines
        limit = self._plan.wire_s(size) + REPLY_GRACE_S
        for offset in range(0, len(self._plan.data), size):
            self.replies += 1
            before = self._sink.units
            sent = time.monotonic()
            self._send(self._plan.data[offset : offset + size])
            while self._sink.units < before + expect and time.monotonic() - sent < limit:
                time.sleep(0.0005)
            if self._sink.units >= before + expect:
                self.reply_times.append(time.monotonic() - sent)


def _consuming(sink: Sink, target: int, writer: Writer, cap_s: float) -> Iterator[None]:
    """Yield while the layer is still taking the plan; stop at the target, a quiet spell or the cap."""
    while sink.units < target:
        now = time.monotonic()
        if now - writer.started > cap_s:
            return
        if writer.finished and now - max(sink.last, writer.finished) > QUIET_S:
            return
        yield


def _writer_of(port: Any) -> Callable[[bytes], object]:
    """The underlying pyserial port's own write: no session-log TX lines, which a device never causes."""
    return port.write


def _cfg(device: str, baud: int, folder: Path) -> tuple[dict, str]:
    """A config on disk for ``device``, flow control off, written in a temporary folder."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**serial, "port": device, "baud_rate": baud, "flow_control": "none"},
        "auto_connect": False,
    }
    path = folder / f"throughput{baud}" / f"throughput{baud}.cfg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    cfg["_config_path"] = str(path)
    return cfg, str(path)


# ── Layers ───────────────────────────────────────────────────────────────────
# Each returns the lines it consumed, the writer (its start is time zero) and
# the sink's last-consumed time.


def _layer_raw(
    cfg: dict, path: str, plan: Plan, cap_s: float, folder: Path
) -> tuple[list[str], Writer, float]:
    """A bare pyserial reader thread, opened the way termapy opens a port."""
    port = open_serial(cfg)
    sink = Sink()
    received = bytearray()
    stop = threading.Event()

    def read() -> None:
        while not stop.is_set():
            chunk = port.read(port.in_waiting or 1)
            if chunk:
                received.extend(chunk)
                sink.add_bytes(len(chunk))

    reader = threading.Thread(target=read, name="throughput-raw", daemon=True)
    reader.start()
    writer = Writer(port.write, plan, sink, units_are_bytes=True)
    writer.start()
    for _ in _consuming(sink, plan.target(True), writer, cap_s):
        time.sleep(0.02)
    stop.set()
    reader.join(2.0)
    port.close()
    lines = bytes(received).decode("utf-8", errors="replace").split("\r\n")
    return lines, writer, sink.last


def _layer_engine(
    cfg: dict, path: str, plan: Plan, cap_s: float, folder: Path
) -> tuple[list[str], Writer, float]:
    """termapy's SerialEngine reader: read, queue, decode and split, with a callback per batch."""
    sink = Sink()
    engine = SerialEngine(
        cfg=cfg, capture=CaptureEngine(), open_fn=open_serial, log=lambda d, t: None
    )
    if not engine.connect():
        raise RuntimeError(f"the engine could not open the port: {engine.last_error}")
    try:
        engine.start_reader(on_lines=sink.add_lines)
        writer = Writer(_writer_of(engine.port_obj), plan, sink, units_are_bytes=False)
        writer.start()
        for _ in _consuming(sink, plan.target(False), writer, cap_s):
            time.sleep(0.02)
    finally:
        engine.stop_event.set()
        engine.disconnect()
    return sink.lines, writer, sink.last


def _layer_cli(
    cfg: dict, path: str, plan: Plan, cap_s: float, folder: Path
) -> tuple[list[str], Writer, float]:
    """The CLI: its reader prints every line through its console, here into os.devnull."""
    from termapy.cli import CLITerminal

    class CountingCli(CLITerminal):
        """The CLI, recording each batch after it has printed it."""

        sink: Sink

        def _feed_rx_consumers(self, lines: list[str]) -> None:
            """Hand the lines on as usual, then record them."""
            super()._feed_rx_consumers(lines)
            self.sink.add_lines(lines)

    sink = Sink()
    with open(os.devnull, "w", encoding="utf-8") as null, contextlib.redirect_stdout(null):
        terminal = CountingCli(cfg, path, no_color=True, term_width=120)
        terminal.sink = sink
        terminal.repl.global_root = folder  # keep the checkout's plugin/ out of it
        if not terminal._connect():
            raise RuntimeError("the CLI could not open the port")
        try:
            writer = Writer(_writer_of(terminal.engine.port_obj), plan, sink, units_are_bytes=False)
            writer.start()
            for _ in _consuming(sink, plan.target(False), writer, cap_s):
                time.sleep(0.02)
        finally:
            terminal._disconnect()
    return sink.lines, writer, sink.last


def _layer_tui(
    cfg: dict, path: str, plan: Plan, cap_s: float, folder: Path
) -> tuple[list[str], Writer, float]:
    """The TUI, headless: lines reach the main thread and are rendered into the output pane."""
    from termapy.app import SerialTerminal

    class CountingTui(SerialTerminal):
        """The TUI, recording each batch after it has rendered and logged it."""

        sink: Sink

        def _write_batch(self, lines: list[str], rows: list[str] | None = None) -> None:
            """Render and hand on the batch as usual, then record it."""
            super()._write_batch(lines, rows)
            self.sink.add_lines(lines)

    sink = Sink()

    async def scenario() -> Writer:
        app = CountingTui(cfg, path)
        app.sink = sink
        app.repl.global_root = folder
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            if not app._connect():
                raise RuntimeError("the TUI could not open the port")
            try:
                writer = Writer(_writer_of(app.engine.port_obj), plan, sink, units_are_bytes=False)
                writer.start()
                for _ in _consuming(sink, plan.target(False), writer, cap_s):
                    await asyncio.sleep(0.02)
            finally:
                app._disconnect()
            return writer

    writer = asyncio.run(scenario())
    return sink.lines, writer, sink.last


_LAYER_RUNNERS = {"raw": _layer_raw, "engine": _layer_engine, "cli": _layer_cli, "tui": _layer_tui}


# ── Rig, tally, report ───────────────────────────────────────────────────────


def _jumpered(device: str) -> bool:
    """Whether a probe written at 115200 comes straight back: the SAFETY check."""
    import serial  # after termapy's imports, which put the vendored pyserial on the path

    probe = b"TERMAPY-THROUGHPUT-PROBE\n"
    port = serial.Serial(device, 115200, timeout=0.05, exclusive=True)
    try:
        port.reset_input_buffer()
        port.write(probe)
        port.flush()
        seen = b""
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and probe.strip() not in seen:
            seen += port.read(port.in_waiting or 1)
    finally:
        port.close()
    return probe.strip() in seen


@dataclass(frozen=True)
class Result:
    """One layer's numbers at one baud."""

    baud: int
    layer: str
    lines: int
    lost: int
    corrupt: int
    gaps: int  # runs of consecutive missing lines
    gaps_at_write_end: int  # of those, the runs that end where a write chunk ends
    short_writes: int
    short_bytes: int
    finish_s: float | None
    replies: int
    reply_times: tuple[float, ...]
    error: str


def _tally(
    baud: int, layer: str, lines: list[str], plan: Plan, writer: Writer, last: float
) -> Result:
    """Check every received line against the plan, find the gaps, time the layer from the first write.

    A gap that ends exactly where one of the writer's chunks ends, with no
    damaged line beside it, is the tail of a write that never went out: lost
    on the sending side.  Bytes lost on the receiving side fall anywhere and
    leave a damaged line where the stream resumes.
    """
    stream = plan.stream
    seen: set[int] = set()
    corrupt = 0
    for line in lines:
        if not line:
            continue
        head = line[:8]
        if head.isdigit() and int(head) < stream.count and line == stream.text(int(head)):
            seen.add(int(head))
        else:
            corrupt += 1  # a damaged line, or two halves of lines joined across a loss
    gaps = gaps_at_write_end = 0
    seq = 0
    while seq < stream.count:
        if seq in seen:
            seq += 1
            continue
        while seq < stream.count and seq not in seen:
            seq += 1
        gaps += 1
        if (seq * stream.line_bytes) % WRITE_CHUNK == 0:
            gaps_at_write_end += 1
    return Result(
        baud=baud,
        layer=layer,
        lines=len(seen),
        lost=stream.count - len(seen),
        corrupt=corrupt,
        gaps=gaps,
        gaps_at_write_end=gaps_at_write_end,
        short_writes=writer.short_writes,
        short_bytes=writer.short_bytes,
        finish_s=last - writer.started if seen else None,
        replies=writer.replies,
        reply_times=tuple(writer.reply_times),
        error=writer.error,
    )


def _ms(seconds: float) -> str:
    """Seconds as whole milliseconds for the table."""
    return f"{seconds * 1000:.0f} ms"


def _report_baud(plan: Plan, mode: str, results: list[Result]) -> None:
    """One baud's block: the traffic, then one row per layer."""
    stream = plan.stream
    total = len(plan.data)
    print(f"\n{plan.baud} baud 8N1, {plan.baud // BITS_PER_BYTE} B/s line rate")
    if mode == "repl":
        size = plan.reply_lines * stream.line_bytes
        print(
            f"  {total // size} replies of {size} B ({plan.reply_lines} lines), each sent when the last"
            f" had arrived; a reply takes {_ms(plan.wire_s(size))} on the wire"
        )
        print(
            f"  {'LAYER':<7} {'REPLIES OK':>12} {'LINES LOST':>10} {'GAPS':>5} {'TYPICAL':>8}"
            f" {'99%':>8} {'WORST':>16}"
        )
        for result in results:
            times = result.reply_times
            ok = f"{len(times)}/{result.replies}"
            typical = _ms(statistics.median(times)) if times else "-"
            p99 = _ms(statistics.quantiles(times, n=100)[98]) if len(times) > 1 else "-"
            # Which reply was slowest: the first, right after the connect, is not a stall.
            worst = f"{_ms(max(times))} (#{times.index(max(times)) + 1})" if times else "-"
            print(
                f"  {result.layer:<7} {ok:>12} {result.lost:>10} {result.gaps:>5} {typical:>8}"
                f" {p99:>8} {worst:>16}"
            )
    else:
        wire = plan.wire_s(total)
        print(
            f"  {stream.count} lines x {stream.line_bytes} B = {total} B, {wire:.2f} s on the wire"
        )
        print(
            f"  {'LAYER':<7} {'LINES':>6} {'LOST':>5} {'CORRUPT':>7} {'GAPS':>5} {'AT WRITE END':>12}"
            f" {'SHORT WRITES':>12} {'FINISH':>7} {'KEPT UP':>7}"
        )
        for result in results:
            finish = f"{result.finish_s:.2f}s" if result.finish_s is not None else "-"
            kept = f"{wire / result.finish_s * 100:.1f}%" if result.finish_s else "-"
            short = (
                f"{result.short_writes} ({result.short_bytes} B)" if result.short_writes else "0"
            )
            print(
                f"  {result.layer:<7} {result.lines:>6} {result.lost:>5} {result.corrupt:>7} {result.gaps:>5}"
                f" {result.gaps_at_write_end:>12} {short:>12} {finish:>7} {kept:>7}"
            )
    for result in results:
        if result.error:
            print(f"  {result.layer}: write failed: {result.error}")


def _report_summary(results: list[Result], layers: list[str]) -> None:
    """Lines lost over lines checked, one row per baud: where losses start."""
    print("\nSUMMARY: lines lost / lines checked")
    print(f"  {'BAUD':>8} " + " ".join(f"{layer:>16}" for layer in layers))
    for baud in sorted({result.baud for result in results}):
        row = {result.layer: result for result in results if result.baud == baud}
        cells = [
            f"{row[layer].lost} / {row[layer].lines + row[layer].lost}" if layer in row else "-"
            for layer in layers
        ]
        print(f"  {baud:>8} " + " ".join(f"{cell:>16}" for cell in cells))


def _report_notes(mode: str) -> None:
    """How to read the columns."""
    print()
    if mode == "repl":
        print("  REPLIES OK counts replies the layer had in full within their wire time plus")
        print(
            f"  {REPLY_GRACE_S:.0f} s.  TYPICAL (the median), 99% and WORST are from sending a reply"
        )
        print("  to the layer having all of it; #N is which reply was the slowest.")
        return
    print(
        "  KEPT UP is the wire time over the time the layer took to finish, from the first write:"
    )
    print(
        "  near 100% kept pace with the line rate, lower fell behind by that much.  CORRUPT counts"
    )
    print("  lines damaged or joined across a loss.  A GAP that ends AT a WRITE's END with no")
    print("  damaged line is a write's tail that never went out: lost sending, not receiving.")
    print("  SHORT WRITES are writes the port itself reported as short.")


def _plan(baud: int, args: argparse.Namespace) -> Plan:
    """The traffic for one baud, from the mode and the sizes asked for."""
    rate = baud / BITS_PER_BYTE
    if args.mode == "repl":
        reply_lines = max(1, args.reply_bytes // args.line_bytes)
        count = reply_lines * args.replies
    else:
        reply_lines = 0
        wanted = args.kb * 1024 if args.kb else args.seconds * rate
        count = max(1, round(wanted / args.line_bytes))
    stream = Stream(args.line_bytes, count)
    return Plan(stream, stream.data(), baud, reply_lines)


def _rig(sn: str) -> str | None:
    """The jumpered adapter's device name, or None after saying why it cannot be used."""
    try:
        device = resolve_port(sn)
    except AmbiguousSerialNumberError as e:
        print(f"Serial number {sn} is ambiguous: {e}", file=sys.stderr)
        return None
    if device == sn:
        print(f"No serial adapter with serial number {sn} is attached.", file=sys.stderr)
        return None
    try:
        echoes = _jumpered(device)
    except OSError as e:
        print(f"{device} (SN {sn}) is attached but could not be opened, busy? {e}", file=sys.stderr)
        return None
    if not echoes:
        print(
            f"{device} (SN {sn}) did not echo: refusing to send into what may be a device.",
            file=sys.stderr,
        )
        return None
    return device


def main(argv: list[str] | None = None) -> int:
    """Run the layers asked for at each baud rate and print a block per baud and a summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mode", choices=MODES, default="stream", help="traffic shape (default stream)"
    )
    parser.add_argument(
        "--baud", default="921600", help="baud rate, or a comma-separated list (default 921600)"
    )
    parser.add_argument(
        "--seconds", type=float, default=8.0, help="stream: length at line rate (default 8)"
    )
    parser.add_argument(
        "--kb", type=int, default=0, help="stream: the same KB at every baud, instead of --seconds"
    )
    parser.add_argument(
        "--replies", type=int, default=300, help="repl: replies per layer (default 300)"
    )
    parser.add_argument(
        "--reply-bytes", type=int, default=1024, help="repl: bytes per reply (default 1024)"
    )
    parser.add_argument(
        "--line-bytes", type=int, default=64, help="bytes per line, CRLF included (default 64)"
    )
    parser.add_argument(
        "--layers", default=",".join(LAYERS), help=f"comma-separated, from {', '.join(LAYERS)}"
    )
    parser.add_argument(
        "--sn",
        default=os.environ.get("TERMAPY_HW_LOOPBACK_SN", "BG03U7VTA"),
        help="the jumpered adapter's USB serial number (default $TERMAPY_HW_LOOPBACK_SN or BG03U7VTA)",
    )
    args = parser.parse_args(argv)
    layers = [name.strip() for name in args.layers.split(",") if name.strip()]
    unknown = [name for name in layers if name not in LAYERS]
    if unknown:
        parser.error(f"unknown layer(s): {', '.join(unknown)}")
    try:
        bauds = [int(text) for text in args.baud.split(",") if text.strip()]
    except ValueError:
        parser.error(f"invalid --baud: {args.baud}")
    if not bauds or args.line_bytes < 16 or args.seconds <= 0 or args.replies < 1:
        parser.error("need a baud, --line-bytes >= 16, --seconds > 0 and --replies >= 1")

    device = _rig(args.sn)
    if device is None:
        return 1
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="termapy-throughput-") as temp:
        folder = Path(temp)
        for baud in bauds:
            plan = _plan(baud, args)
            cap_s = plan.wire_s(len(plan.data)) * 4 + 30
            cfg, path = _cfg(device, baud, folder)
            here: list[Result] = []
            for layer in layers:
                print(f"  {baud}: running {layer} ...", file=sys.stderr)
                lines, writer, last = _LAYER_RUNNERS[layer](cfg, path, plan, cap_s, folder)
                here.append(_tally(baud, layer, lines, plan, writer, last))
                time.sleep(0.5)  # let the adapter settle between connections
            if baud == bauds[0]:
                # Read after the layers ran: an extension imported without
                # free-threading support turns the GIL back on.
                gil = getattr(sys, "_is_gil_enabled", lambda: True)()
                print(f"termapy {args.mode} test on {device} (SN {args.sn}), flow control off")
                print(
                    f"  driver RX buffer requested: {SERIAL_RX_BUFFER_BYTES} B; Python"
                    f" {platform.python_version()} on {platform.system()}, GIL {'on' if gil else 'off'}"
                )
            _report_baud(plan, args.mode, here)
            results.extend(here)
    if len(bauds) > 1:
        _report_summary(results, layers)
    _report_notes(args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
