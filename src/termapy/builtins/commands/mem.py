"""Built-in plugin: /mem.* -- device memory over the MEM spec.

The command surface only.  The engine (:mod:`termapy.memory`) speaks the
wire, chunks to the device's block limit and checks continuity; this file
wires it to ``ctx.serial``, resolves addresses through the symbol table,
renders dumps, and writes the audit line for every write.

Subcommands:

- ``/mem.dump <addr> {len}`` -- hexdump ``len`` bytes (default 64).
- ``/mem.write <addr> <hex>`` -- write hex bytes; reads them first for the audit.
- ``/mem.info`` -- how termapy talks to this device's memory, and why.

The device's facts (block limit, address width, byte order) come from the
profile's ``memory`` block, else from the device's ``MEM.INFO`` answer,
else from the defaults; the answer is cached in ``ctx.ns("memory")`` for
the connection and dropped on connect / config load.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Final

from termapy.memory import (
    DeviceMemoryError,
    Memory,
    MemoryInfo,
    dump_rows,
    format_row,
    reply_complete,
    resolve_info,
    row_record,
)
from termapy.plugins import CapabilitySet, CmdResult, Command, format_kv_lines
from termapy.plugins.params import ParamSpec
from termapy.protocol.core import parse_hex
from termapy.symbols import Address, get_table, parse_address, parse_number, symbolic_name
from termapy.symbols.format import hex_addr
from termapy.variables import launch_var

if TYPE_CHECKING:
    from termapy.plugins import PluginContext
    from termapy.symbols import SymbolTable

MEMORY_NS: Final[str] = "memory"
_DEFAULT_DUMP_LEN: Final[int] = 64
# Wall-clock cap for one block exchange.  A 64-byte row block is ~250
# bytes on the wire: ~22 ms at 115200, ~260 ms at 9600.
_EXCHANGE_TIMEOUT_MS: Final[int] = 1000

# FRONT_END launch var -> the origin word in the audit line.
_ORIGINS: Final[dict[str, str]] = {"textual": "tui", "cli": "cli", "mcp": "mcp"}


# ── lifecycle: the device-info cache lives for one connection ───────────────


def on_connect(ctx: PluginContext) -> None:
    """A new connection may be a different device: forget its MEM.INFO."""
    ctx.ns(MEMORY_NS).clear()


def on_config_load(ctx: PluginContext) -> None:
    """A config switch means a different profile and device."""
    ctx.ns(MEMORY_NS).clear()


# ── helpers ────────────────────────────────────────────────────────────────


def _exchange_factory(ctx: PluginContext) -> Callable[[str], str]:
    """The engine's ``exchange``: claim, drain, send, read until OK / ERR.

    ``read_raw`` returns at the first silence gap, so a reply that arrives
    in bursts is read again until the verdict line shows up or the
    exchange timeout expires; the engine reports what it got.
    """
    encoding = ctx.cfg.get("encoding", "utf-8")

    def exchange(command: str) -> str:
        buffer = bytearray()
        with ctx.serial.io():
            ctx.serial.drain()
            ctx.serial.send(command)
            deadline = time.monotonic() + _EXCHANGE_TIMEOUT_MS / 1000
            while True:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    break
                chunk = ctx.serial.read_raw(timeout_ms=remaining_ms)
                if not chunk:
                    break
                buffer.extend(chunk)
                if reply_complete(buffer.decode(encoding, errors="replace")):
                    break
        return buffer.decode(encoding, errors="replace")

    return exchange


def _profile_block(ctx: PluginContext) -> dict[str, Any] | None:
    profile = ctx.ns("active_profile")
    block = profile.get("memory") if isinstance(profile, dict) else None
    return block if isinstance(block, dict) else None


def _memory_info(ctx: PluginContext, *, refresh: bool = False) -> MemoryInfo:
    """Resolve the device facts, asking ``MEM.INFO`` once per connection.

    The device is only asked when the profile does not already fix the
    block limit -- a legacy device would answer the query with noise, and
    its profile block is exactly what says so.
    """
    block = _profile_block(ctx)
    cache = ctx.ns(MEMORY_NS)
    if refresh:
        cache.pop("queried", None)
    if not cache.get("queried") and (block is None or "max_block" not in block):
        cache["queried"] = True
        try:
            cache["device_info"] = Memory(_exchange_factory(ctx)).query_info()
        except DeviceMemoryError as e:
            cache["device_info"] = None
            cache["device_error"] = str(e)
    return resolve_info(block, cache.get("device_info"))


def _resolve(ctx: PluginContext, target: str) -> Address | CmdResult:
    """The address a token names, or the failure to return."""
    try:
        parsed = parse_address(target, get_table(ctx))
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    if parsed.suffix:
        # Bit / slice / field access is the typed-view step; refusing beats
        # silently dumping the whole word.
        return CmdResult.fail(msg=f"Unsupported address suffix: .{parsed.suffix}")
    return parsed


def _labeler(table: SymbolTable | None) -> Callable[[int], str] | None:
    if table is None:
        return None

    def label(addr: int) -> str:
        symbol = table.lookup(addr)
        return symbolic_name(symbol, addr) if symbol is not None else ""

    return label


def _origin() -> str:
    return _ORIGINS.get(launch_var("FRONT_END"), "unknown")


# ── handlers ───────────────────────────────────────────────────────────────


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Hexdump a block, rows annotated with the containing symbol."""
    parsed = _resolve(ctx, str(ctx.arg("addr")))
    if isinstance(parsed, CmdResult):
        return parsed
    # The address grammar's number rule (decimal, 0x hex, Nh), not a bare
    # int: on a memory tool "0x10" is how people say sixteen.
    raw_len = str(ctx.arg("len"))
    length = parse_number(raw_len)
    if length is None:
        return CmdResult.fail(msg=f"Invalid length: {raw_len}")
    info = _memory_info(ctx)
    try:
        data = Memory(_exchange_factory(ctx), info).read(parsed.addr, length)
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    rows = dump_rows(parsed.addr, data, label=_labeler(get_table(ctx)))
    bits = info.address_bits
    value = data.hex().upper()
    if ctx.wants_data:
        return CmdResult.ok(value=value, data={
            "addr": parsed.addr,
            "addr_hex": hex_addr(parsed.addr, bits),
            "len": len(data),
            "bytes_hex": value,
            "rows": [row_record(row, address_bits=bits) for row in rows],
        })
    for row in rows:
        ctx.io.output(format_row(row, address_bits=bits))
    return CmdResult.ok(value=value)


def _handler_write(ctx: PluginContext, args: str) -> CmdResult:
    """Write hex bytes; the bytes that were there go into the audit line."""
    hex_text = str(ctx.arg("hex"))
    try:
        data = parse_hex(hex_text)
    except ValueError:
        return CmdResult.fail(msg=f"Invalid hex: {hex_text}")
    if not data:
        return CmdResult.fail(msg=f"Invalid hex: {hex_text}")
    parsed = _resolve(ctx, str(ctx.arg("addr")))
    if isinstance(parsed, CmdResult):
        return parsed
    info = _memory_info(ctx)
    memory = Memory(_exchange_factory(ctx), info)
    try:
        before = memory.read(parsed.addr, len(data))
        count = memory.write(parsed.addr, data)
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    bits = info.address_bits
    addr_hex = hex_addr(parsed.addr, bits)
    before_hex = before.hex().upper()
    after_hex = data.hex().upper()
    origin = _origin()
    # The audit record: every write leaves a line in the session log with
    # what was there, what was written and who asked, whatever the output
    # level -- the record that makes "let the agent poke" defensible.
    ctx.io.log("#", f"MEM.W {addr_hex} before={before_hex} after={after_hex} origin={origin}")
    where = f"{addr_hex}  {parsed.symbol.name}" if parsed.symbol is not None and parsed.offset == 0 else addr_hex
    ctx.io.result(f"Wrote {count} bytes at {where}  (was {before_hex})")
    return CmdResult.ok(value=str(count), data={
        "addr": parsed.addr,
        "addr_hex": addr_hex,
        "count": count,
        "before_hex": before_hex,
        "after_hex": after_hex,
        "origin": origin,
    })


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show the resolved facts and where each came from."""
    info = _memory_info(ctx, refresh=True)
    cache = ctx.ns(MEMORY_NS)
    device_info = cache.get("device_info")
    if device_info is not None:
        device_state = "answered MEM.INFO"
    else:
        device_state = f"no MEM.INFO ({cache.get('device_error', 'not asked')})"
    rows = [
        ("dialect", f"{info.dialect}  ({info.sources['dialect']})"),
        ("max_block", f"{info.max_block}  ({info.sources['max_block']})"),
        ("address_bits", f"{info.address_bits}  ({info.sources['address_bits']})"),
        ("endian", f"{info.endian}  ({info.sources['endian']})"),
        ("device", device_state),
    ]
    for line in format_kv_lines(rows):
        ctx.io.output_markup(line)
    return CmdResult.ok(value=info.dialect, data={
        "dialect": info.dialect,
        "max_block": info.max_block,
        "address_bits": info.address_bits,
        "endian": info.endian,
        "sources": dict(info.sources),
        "device_info": device_info,
    })


_LONG_HELP: Final[str] = (
    "Reads and writes device memory as bytes.  The device implements the\n"
    "termapy MEM spec (see /help memory):\n"
    "\n"
    "  MEM.R <addr> <len>   -> rows \"<ADDR>: <XX XX ...>\" then OK\n"
    "  MEM.W <addr> <hex>   -> OK\n"
    "  MEM.INFO             -> {\"max_block\": 64, \"address_bits\": 32, \"endian\": \"le\"} then OK\n"
    "  any failure          -> ERR <reason>\n"
    "\n"
    "Addresses take every /sym form: 0x1000, 1000h, 1000 (decimal), gTemp,\n"
    "main+0x10, tick@adc.c.  Dump rows are annotated with the containing\n"
    "symbol when a table is loaded.  Block limit, address width and byte\n"
    "order come from the profile's \"memory\" block, else from MEM.INFO, else\n"
    "the defaults (64 / 32 / le); /mem.info shows which.\n"
    "\n"
    "Every write is audited in the session log (address, bytes before and\n"
    "after, origin).  Over MCP /mem.write is destructive: confirm=true.\n"
    "\n"
    "Commands:\n"
    "  /mem.dump <addr> {len}   - hexdump len bytes (default 64; 32, 0x20 or 20h)\n"
    "  /mem.write <addr> <hex>  - write hex bytes (1B00, 1B 00, 0x1B 0x00)\n"
    "  /mem.info                - dialect, max_block, address_bits, endian"
)

_CONNECTED: Final = CapabilitySet(serial_connected=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="mem",
    help="Device memory: dump and write bytes, resolve names via /sym.",
    long_help=_LONG_HELP,
    sub_commands={
        "dump": Command(
            params=[
                ParamSpec("addr", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec(
                    "len", "str", positional=True, default=str(_DEFAULT_DUMP_LEN),
                    help="bytes to read: decimal, 0x hex, or Nh",
                ),
            ],
            help="Hexdump bytes at an address or symbol (default 64).",
            handler=_handler_dump,
            needs=_CONNECTED,
            safety="readonly",
        ),
        "write": Command(
            params=[
                ParamSpec("addr", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec(
                    "hex", "str", positional=True, required=True, rest=True,
                    help="bytes as hex pairs",
                ),
            ],
            help="Write hex bytes at an address or symbol (audited).",
            handler=_handler_write,
            needs=_CONNECTED,
            safety="destructive",
        ),
        "info": Command(
            help="Show how memory access is configured: dialect, block limit, address width, endian.",
            handler=_handler_info,
            needs=_CONNECTED,
            safety="readonly",
        ),
    },
)
