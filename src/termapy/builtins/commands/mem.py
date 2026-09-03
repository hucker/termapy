"""Built-in plugin: /mem.* -- device memory over the MEM spec or a template.

The command surface only.  The engine (:mod:`termapy.memory`) speaks the
wire, chunks to the device's block limit and checks continuity; the typed
layer (:mod:`termapy.memory_views`) turns bytes into scalars, characters
and named register bits; this file wires both to ``ctx.serial``, resolves
addresses through the symbol table, renders, and writes the audit line
for everything that mutates.

Subcommands:

- ``/mem.dump <target> {len} {type}`` -- hexdump (u8) or word/decimal/float
  columns; ``addr=``/``ascii=`` toggle the columns (both off = bare values).
- ``/mem.read <target> {type}`` -- ONE typed value: a scalar, a char, a
  named register field (``U1MODE.ON``), a bit (``.15``) or slice (``.4-6``).
- ``/mem.write <target> <hex>`` -- plain target: hex bytes; a target with
  a bit/field suffix: an atomic masked write (``MEM.M``) or RMW fallback.
- ``/mem.or|and|xor|clear <target> <mask>``, ``/mem.not <target>`` -- the
  boolean set on one word (``clear`` = ``word &= ~mask``).
- ``/mem.str <target> {max}`` -- a NUL-terminated string, first class.
- ``/mem.info`` -- how termapy talks to this device's memory, and why.

The device's facts (dialect, block limit, address width, byte order,
atomic modify) come from the profile's ``memory`` block, else -- for the
native dialect -- from the device's ``MEM.INFO`` answer, else from the
defaults; the answer is cached in ``ctx.ns("memory")`` for the connection
and dropped on connect / config load.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Final, Literal

from termapy.memory import (
    DeviceMemoryError,
    Dialect,
    Memory,
    MemoryInfo,
    TemplateDialect,
    dump_rows,
    make_dialect,
    resolve_info,
    row_record,
)
from termapy.memory_views import (
    SCALARS,
    TYPE_TOKENS,
    BitTarget,
    decode_scalar,
    decode_words,
    escape_text,
    extract_bits,
    mask_pair,
    render_char,
    render_value,
    resolve_bit_target,
    scalar_size,
    symbol_columns,
)
from termapy.plugins import CapabilitySet, CmdResult, Command, format_kv_lines
from termapy.plugins.params import ParamSpec
from termapy.protocol.core import apply_format, extract_column_value, parse_hex
from termapy.scripting import strip_ansi
from termapy.symbols import Address, get_table, parse_address, parse_number, symbolic_name
from termapy.symbols.format import hex_addr
from termapy.variables import launch_var

if TYPE_CHECKING:
    from termapy.plugins import PluginContext
    from termapy.symbols import SymbolTable

MEMORY_NS: Final[str] = "memory"
_DEFAULT_DUMP_LEN: Final[int] = 64
_DEFAULT_STR_MAX: Final[int] = 256
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


def _exchange_factory(ctx: PluginContext, dialect: Dialect) -> Callable[[str], str]:
    """The engine's ``exchange``: claim, drain, send, read until the reply ends.

    ``read_raw`` returns at the first silence gap (the dialect's
    ``settle_ms``, else the transport's default), so a reply that arrives
    in bursts is read again until the dialect says it is complete or the
    exchange timeout expires; the engine reports what it got.  ANSI color
    is stripped so a monitor with color on still parses.
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
                chunk = ctx.serial.read_raw(
                    timeout_ms=remaining_ms, frame_gap_ms=dialect.settle_ms,
                )
                if not chunk:
                    break
                buffer.extend(chunk)
                if dialect.complete(strip_ansi(buffer.decode(encoding, errors="replace"))):
                    break
        return strip_ansi(buffer.decode(encoding, errors="replace"))

    return exchange


def _profile_block(ctx: PluginContext) -> dict[str, Any] | None:
    profile = ctx.ns("active_profile")
    block = profile.get("memory") if isinstance(profile, dict) else None
    return block if isinstance(block, dict) else None


def _memory_info(ctx: PluginContext, *, refresh: bool = False) -> MemoryInfo:
    """Resolve the device facts, asking ``MEM.INFO`` once per connection.

    The device is only asked when the profile names the native dialect
    (or none) and does not already fix the block limit -- a device with
    its own grammar would answer the query with noise, and its profile
    block is exactly what says so.
    """
    block = _profile_block(ctx)
    cache = ctx.ns(MEMORY_NS)
    if refresh:
        cache.pop("queried", None)
    native = block is None or block.get("dialect") in (None, "termapy")
    if native and not cache.get("queried") and (block is None or "max_block" not in block):
        cache["queried"] = True
        try:
            probe = Memory(_exchange_factory(ctx, Memory(lambda _: "").dialect))
            cache["device_info"] = probe.query_info()
        except DeviceMemoryError as e:
            cache["device_info"] = None
            cache["device_error"] = str(e)
    return resolve_info(block, cache.get("device_info") if native else None)


def _engine(ctx: PluginContext) -> Memory | CmdResult:
    """A :class:`Memory` for this connection, or the failure to return."""
    info = _memory_info(ctx)
    try:
        dialect = make_dialect(info, _profile_block(ctx))
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    return Memory(_exchange_factory(ctx, dialect), info, dialect)


def _resolve(ctx: PluginContext, target: str, *, suffix_ok: bool = False) -> Address | CmdResult:
    """The address a token names, or the failure to return."""
    try:
        parsed = parse_address(target, get_table(ctx))
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    if parsed.suffix and not suffix_ok:
        return CmdResult.fail(
            msg=f"Unsupported address suffix here: .{parsed.suffix} (use /mem.read or /mem.write)"
        )
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


def _where(parsed: Address, bits: int) -> str:
    """``0x00001000  gTemp`` when the user named a symbol, else the address."""
    addr_hex = hex_addr(parsed.addr, bits)
    if parsed.symbol is not None and parsed.offset == 0:
        return f"{addr_hex}  {parsed.symbol.name}"
    return addr_hex


def _audit(ctx: PluginContext, verb: str, addr_hex: str, before: bytes, after: bytes) -> None:
    """The one line that makes "let the agent poke" defensible."""
    ctx.io.log(
        "#",
        f"{verb} {addr_hex} before={before.hex().upper()} after={after.hex().upper()} "
        f"origin={_origin()}",
    )


def _do_modify(
    ctx: PluginContext,
    memory: Memory,
    parsed: Address,
    target: BitTarget,
    and_mask: int,
    or_mask: int,
) -> tuple[bytes, bytes] | CmdResult:
    """One masked word write with the rmw gate and the audit line."""
    if parsed.symbol is not None and not parsed.symbol.rmw:
        return CmdResult.fail(
            msg=(
                f"RMW refused: {parsed.symbol.name} is rmw: false "
                f"(W1C/self-clearing); write the whole word"
            )
        )
    word_addr = parsed.addr + target.offset
    try:
        old, new = memory.modify(word_addr, target.width, and_mask, or_mask)
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    _audit(ctx, "MEM.M", hex_addr(word_addr, memory.info.address_bits), old, new)
    return old, new


def _modify_record(
    memory: Memory, word_addr: int, target: BitTarget, old: bytes, new: bytes,
) -> dict[str, Any]:
    info = memory.info
    return {
        "addr": word_addr,
        "addr_hex": hex_addr(word_addr, info.address_bits),
        "width": target.width,
        "field": target.field,
        "low": target.low,
        "bits": target.bit_width,
        "before_word": int.from_bytes(old, info.byte_order),
        "after_word": int.from_bytes(new, info.byte_order),
        "before_hex": old.hex().upper(),
        "after_hex": new.hex().upper(),
        "atomic": memory.dialect.name == "termapy" and info.atomic_modify,
        "origin": _origin(),
    }


def _word_change(old: bytes, new: bytes, width: int, byte_order: Literal["little", "big"]) -> str:
    digits = width * 2
    old_word = int.from_bytes(old, byte_order)
    new_word = int.from_bytes(new, byte_order)
    return f"0x{old_word:0{digits}X} -> 0x{new_word:0{digits}X}"


# ── handlers ───────────────────────────────────────────────────────────────


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Hexdump or word columns, rows annotated with the containing symbol."""
    parsed = _resolve(ctx, str(ctx.arg("target")))
    if isinstance(parsed, CmdResult):
        return parsed
    # Positional juggling: "/mem.dump gTemp u16" gives len the type token.
    raw_len = str(ctx.arg("len"))
    type_name = str(ctx.arg("type") or "")
    if not type_name and raw_len in TYPE_TOKENS:
        raw_len, type_name = str(_DEFAULT_DUMP_LEN), raw_len
    # The address grammar's number rule (decimal, 0x hex, Nh), not a bare
    # int: on a memory tool "0x10" is how people say sixteen.
    length = parse_number(raw_len)
    if length is None:
        return CmdResult.fail(msg=f"Invalid length: {raw_len}")
    if type_name and type_name not in TYPE_TOKENS:
        return CmdResult.fail(
            msg=f"Unknown type: {type_name} (types: {', '.join(sorted(TYPE_TOKENS))})"
        )
    show_addr = bool(ctx.arg("addr"))
    show_ascii = bool(ctx.arg("ascii"))
    memory = _engine(ctx)
    if isinstance(memory, CmdResult):
        return memory
    try:
        data = memory.read(parsed.addr, length)
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    info = memory.info
    bits = info.address_bits
    value = data.hex().upper()
    rows = dump_rows(parsed.addr, data, label=_labeler(get_table(ctx)))
    size = scalar_size(type_name) if type_name else 1
    if type_name and len(data) % size:
        return CmdResult.fail(
            msg=f"Invalid length: {len(data)} (not a multiple of {size} for {type_name})"
        )
    if ctx.wants_data:
        records = []
        for row in rows:
            record = row_record(row, address_bits=bits)
            if type_name:
                record["values"] = decode_words(row.data, type_name, info.endian)
            records.append(record)
        return CmdResult.ok(value=value, data={
            "addr": parsed.addr,
            "addr_hex": hex_addr(parsed.addr, bits),
            "len": len(data),
            "type": type_name or "u8",
            "bytes_hex": value,
            "rows": records,
        })
    # Cell width keeps columns aligned across rows (a short last row too).
    for row in rows:
        parts: list[str] = []
        if show_addr:
            parts.append(hex_addr(row.addr, bits))
        if type_name:
            cells = [
                render_value(word, type_name)
                for word in decode_words(row.data, type_name, info.endian)
            ]
            per_row = 16 // size
            cell_width = max((len(cell) for cell in cells), default=1)
            body = " ".join(f"{cell:>{cell_width}}" for cell in cells)
            pad = (cell_width + 1) * per_row - 1
            parts.append(f"{body:<{pad}}" if show_ascii or row.label else body)
        else:
            parts.append(f"{row.hex:<47}" if show_ascii or row.label else row.hex)
        if show_ascii:
            parts.append(f"|{row.ascii}|")
        if row.label and (show_addr or show_ascii):
            parts.append(row.label)
        ctx.io.output("  ".join(parts).rstrip())
    return CmdResult.ok(value=value)


def _handler_read(ctx: PluginContext, args: str) -> CmdResult:
    """One typed value: scalar, char, named field, bit or slice."""
    raw_target = str(ctx.arg("target"))
    type_name = str(ctx.arg("type") or "")
    parsed = _resolve(ctx, raw_target, suffix_ok=True)
    if isinstance(parsed, CmdResult):
        return parsed
    if type_name and type_name not in TYPE_TOKENS:
        return CmdResult.fail(
            msg=f"Unknown type: {type_name} (types: {', '.join(sorted(TYPE_TOKENS))})"
        )
    memory = _engine(ctx)
    if isinstance(memory, CmdResult):
        return memory
    info = memory.info
    bits = info.address_bits
    symbol = parsed.symbol

    if parsed.suffix:
        # A bit, slice or named field of the word at the target.
        try:
            target = resolve_bit_target(symbol, parsed.suffix)
            word_addr = parsed.addr + target.offset
            data = memory.read(word_addr, target.width)
        except (ValueError, DeviceMemoryError) as e:
            return CmdResult.fail(msg=str(e))
        word = int.from_bytes(data, info.byte_order)
        field_value = extract_bits(word, target)
        label = target.field or parsed.suffix
        digits = target.width * 2
        ctx.io.result(
            f"{raw_target} = {field_value}  (word 0x{word:0{digits}X} "
            f"at {hex_addr(word_addr, bits)})"
        )
        return CmdResult.ok(value=str(field_value), data={
            "target": raw_target,
            "addr": word_addr,
            "addr_hex": hex_addr(word_addr, bits),
            "field": label,
            "low": target.low,
            "bits": target.bit_width,
            "value": field_value,
            "word": word,
        })

    if not type_name and symbol is not None and parsed.offset == 0:
        if symbol.type in SCALARS or symbol.type == "char":
            type_name = symbol.type
        else:
            columns = symbol_columns(symbol)
            if columns:
                # A register with named fields: show them all.
                span = max(max(column.byte_indices) for column in columns) + 1
                length = symbol.size if symbol.size >= span else span
                try:
                    data = memory.read(symbol.addr, length)
                except (ValueError, DeviceMemoryError) as e:
                    return CmdResult.fail(msg=str(e))
                headers, values = apply_format(data, columns)
                for header, rendered in zip(headers, values):
                    ctx.io.output(f"  {header:<8} = {rendered}")
                fields = {
                    column.name: extract_column_value(data, column)
                    for column in columns
                    if column.type_code in ("B", "b")
                }
                return CmdResult.ok(value=data.hex().upper(), data={
                    "target": raw_target,
                    "addr": symbol.addr,
                    "addr_hex": hex_addr(symbol.addr, bits),
                    "bytes_hex": data.hex().upper(),
                    "fields": fields,
                })
    if not type_name:
        return CmdResult.fail(
            msg="Type required: /mem.read <target> <type> "
            "(u8..u64, i8..i64, f32, f64, char) -- or target a typed symbol"
        )
    size = scalar_size(type_name)
    try:
        data = memory.read(parsed.addr, size)
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    if type_name == "char":
        byte = data[0]
        ctx.io.result(f"{raw_target} = {render_char(byte)}")
        return CmdResult.ok(value=str(byte), data={
            "target": raw_target, "addr": parsed.addr,
            "addr_hex": hex_addr(parsed.addr, bits), "type": "char",
            "value": byte, "char": chr(byte) if 0x20 <= byte < 0x7F else "",
        })
    scalar = decode_scalar(data, type_name, info.endian)
    shown = f"{scalar:.6g}" if isinstance(scalar, float) else str(scalar)
    tail = "" if isinstance(scalar, float) else f"  (0x{data.hex().upper()})"
    ctx.io.result(f"{raw_target} = {shown}{tail}")
    return CmdResult.ok(value=shown, data={
        "target": raw_target, "addr": parsed.addr,
        "addr_hex": hex_addr(parsed.addr, bits), "type": type_name,
        "value": scalar, "bytes_hex": data.hex().upper(),
    })


def _handler_write(ctx: PluginContext, args: str) -> CmdResult:
    """Hex bytes at a plain target; a masked word write at a bit/field target."""
    raw_target = str(ctx.arg("target"))
    payload = str(ctx.arg("hex"))
    parsed = _resolve(ctx, raw_target, suffix_ok=True)
    if isinstance(parsed, CmdResult):
        return parsed
    memory = _engine(ctx)
    if isinstance(memory, CmdResult):
        return memory
    info = memory.info
    bits = info.address_bits

    if parsed.suffix:
        # /mem.write U1MODE.ON 1 -- the value is a number for the field.
        value = parse_number(payload)
        if value is None:
            return CmdResult.fail(msg=f"Invalid value: {payload}")
        try:
            target = resolve_bit_target(parsed.symbol, parsed.suffix)
            and_mask, or_mask = mask_pair(target, value)
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        outcome = _do_modify(ctx, memory, parsed, target, and_mask, or_mask)
        if isinstance(outcome, CmdResult):
            return outcome
        old, new = outcome
        word_addr = parsed.addr + target.offset
        ctx.io.result(
            f"Set {raw_target} = {value}  "
            f"({_word_change(old, new, target.width, info.byte_order)})"
        )
        record = _modify_record(memory, word_addr, target, old, new)
        record["value"] = value
        record["target"] = raw_target
        return CmdResult.ok(value=str(value), data=record)

    try:
        data = parse_hex(payload)
    except ValueError:
        return CmdResult.fail(msg=f"Invalid hex: {payload}")
    if not data:
        return CmdResult.fail(msg=f"Invalid hex: {payload}")
    try:
        before = memory.read(parsed.addr, len(data))
        count = memory.write(parsed.addr, data)
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    addr_hex = hex_addr(parsed.addr, bits)
    _audit(ctx, "MEM.W", addr_hex, before, data)
    ctx.io.result(
        f"Wrote {count} bytes at {_where(parsed, bits)}  (was {before.hex().upper()})"
    )
    return CmdResult.ok(value=str(count), data={
        "addr": parsed.addr,
        "addr_hex": addr_hex,
        "count": count,
        "before_hex": before.hex().upper(),
        "after_hex": data.hex().upper(),
        "origin": _origin(),
    })


def _mask_width(parsed: Address, mask_text: str, default: int = 4) -> int:
    """Word width for a mask op: symbol type > mask digit count > 32-bit."""
    symbol = parsed.symbol
    if symbol is not None and symbol.type in SCALARS:
        return SCALARS[symbol.type][1]
    if symbol is not None and symbol.size in (1, 2, 4):
        return symbol.size
    text = mask_text[2:] if mask_text[:2].lower() == "0x" else (
        mask_text[:-1] if mask_text[-1:].lower() == "h" else ""
    )
    if text:
        digits = len(text)
        return 1 if digits <= 2 else 2 if digits <= 4 else 4 if digits <= 8 else default
    return default


def _handler_mask(ctx: PluginContext, args: str, *, op: str) -> CmdResult:
    """Shared /mem.or, /mem.and, /mem.xor implementation."""
    raw_target = str(ctx.arg("target"))
    mask_text = str(ctx.arg("mask"))
    mask = parse_number(mask_text)
    if mask is None:
        return CmdResult.fail(msg=f"Invalid mask: {mask_text}")
    parsed = _resolve(ctx, raw_target)
    if isinstance(parsed, CmdResult):
        return parsed
    memory = _engine(ctx)
    if isinstance(memory, CmdResult):
        return memory
    info = memory.info
    width = _mask_width(parsed, mask_text)
    word_mask = (1 << (8 * width)) - 1
    if mask > word_mask:
        return CmdResult.fail(msg=f"Invalid mask: {mask_text} ({width * 8} bits)")
    target = BitTarget(offset=0, width=width, low=0, bit_width=width * 8)
    if op == "xor":
        # XOR is not expressible as (word & and) | or, so it is always a
        # host-side read + write-back -- racy against ISRs even when the
        # device has MEM.M.  Documented in help/memory.md.
        if parsed.symbol is not None and not parsed.symbol.rmw:
            return CmdResult.fail(
                msg=(
                    f"RMW refused: {parsed.symbol.name} is rmw: false "
                    f"(W1C/self-clearing); write the whole word"
                )
            )
        try:
            old = memory.read(parsed.addr, width)
            new_word = int.from_bytes(old, info.byte_order) ^ mask
            new = new_word.to_bytes(width, info.byte_order)
            memory.write(parsed.addr, new)
        except (ValueError, DeviceMemoryError) as e:
            return CmdResult.fail(msg=str(e))
        _audit(ctx, "MEM.W", hex_addr(parsed.addr, info.address_bits), old, new)
    else:
        if op == "or":
            and_mask, or_mask = word_mask, mask
        elif op == "and":
            and_mask, or_mask = mask, 0
        else:  # clear: keep every bit EXCEPT the mask's
            and_mask, or_mask = word_mask & ~mask, 0
        outcome = _do_modify(ctx, memory, parsed, target, and_mask, or_mask)
        if isinstance(outcome, CmdResult):
            return outcome
        old, new = outcome
    digits = width * 2
    ctx.io.result(
        f"{op.upper()} 0x{mask:0{digits}X} at {_where(parsed, info.address_bits)}  "
        f"({_word_change(old, new, width, info.byte_order)})"
    )
    record = _modify_record(memory, parsed.addr, target, old, new)
    record["op"] = op
    record["mask"] = mask
    if op == "xor":
        record["atomic"] = False
    new_word = int.from_bytes(new, info.byte_order)
    return CmdResult.ok(value=f"{new_word:0{digits}X}", data=record)


def _handler_or(ctx: PluginContext, args: str) -> CmdResult:
    return _handler_mask(ctx, args, op="or")


def _handler_and(ctx: PluginContext, args: str) -> CmdResult:
    return _handler_mask(ctx, args, op="and")


def _handler_xor(ctx: PluginContext, args: str) -> CmdResult:
    return _handler_mask(ctx, args, op="xor")


def _handler_clear(ctx: PluginContext, args: str) -> CmdResult:
    return _handler_mask(ctx, args, op="clear")


def _handler_not(ctx: PluginContext, args: str) -> CmdResult:
    """Invert one word.  NOT depends on the old value, so like xor it is
    always a read + write-back, never MEM.M."""
    raw_target = str(ctx.arg("target"))
    parsed = _resolve(ctx, raw_target)
    if isinstance(parsed, CmdResult):
        return parsed
    memory = _engine(ctx)
    if isinstance(memory, CmdResult):
        return memory
    info = memory.info
    width = _mask_width(parsed, "")
    word_mask = (1 << (8 * width)) - 1
    if parsed.symbol is not None and not parsed.symbol.rmw:
        return CmdResult.fail(
            msg=(
                f"RMW refused: {parsed.symbol.name} is rmw: false "
                f"(W1C/self-clearing); write the whole word"
            )
        )
    try:
        old = memory.read(parsed.addr, width)
        new_word = int.from_bytes(old, info.byte_order) ^ word_mask
        new = new_word.to_bytes(width, info.byte_order)
        memory.write(parsed.addr, new)
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    _audit(ctx, "MEM.W", hex_addr(parsed.addr, info.address_bits), old, new)
    digits = width * 2
    ctx.io.result(
        f"NOT at {_where(parsed, info.address_bits)}  "
        f"({_word_change(old, new, width, info.byte_order)})"
    )
    record = _modify_record(
        memory, parsed.addr, BitTarget(offset=0, width=width, low=0, bit_width=width * 8),
        old, new,
    )
    record["op"] = "not"
    record["atomic"] = False
    return CmdResult.ok(value=f"{new_word:0{digits}X}", data=record)


def _handler_str(ctx: PluginContext, args: str) -> CmdResult:
    """A NUL-terminated string as a first-class value."""
    raw_target = str(ctx.arg("target"))
    raw_max = str(ctx.arg("max"))
    cap = parse_number(raw_max)
    if cap is None or cap < 1:
        return CmdResult.fail(msg=f"Invalid length: {raw_max}")
    parsed = _resolve(ctx, raw_target)
    if isinstance(parsed, CmdResult):
        return parsed
    memory = _engine(ctx)
    if isinstance(memory, CmdResult):
        return memory
    collected = bytearray()
    truncated = False
    try:
        while len(collected) < cap:
            chunk = memory.read(
                parsed.addr + len(collected),
                min(memory.info.max_block, cap - len(collected)),
            )
            nul = chunk.find(0)
            if nul >= 0:
                collected.extend(chunk[:nul])
                break
            collected.extend(chunk)
        else:
            truncated = True
    except (ValueError, DeviceMemoryError) as e:
        return CmdResult.fail(msg=str(e))
    text = collected.decode(ctx.cfg.get("encoding", "utf-8"), errors="replace")
    bits = memory.info.address_bits
    suffix = "  ... (truncated)" if truncated else ""
    ctx.io.result(f'{_where(parsed, bits)}  "{escape_text(text)}"{suffix}')
    return CmdResult.ok(value=text, data={
        "addr": parsed.addr,
        "addr_hex": hex_addr(parsed.addr, bits),
        "length": len(collected),
        "text": text,
        "truncated": truncated,
    })


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show the resolved facts and where each came from."""
    info = _memory_info(ctx, refresh=True)
    block = _profile_block(ctx)
    cache = ctx.ns(MEMORY_NS)
    device_info = cache.get("device_info")
    rows = [
        ("dialect", f"{info.dialect}  ({info.sources['dialect']})"),
        ("max_block", f"{info.max_block}  ({info.sources['max_block']})"),
        ("address_bits", f"{info.address_bits}  ({info.sources['address_bits']})"),
        ("endian", f"{info.endian}  ({info.sources['endian']})"),
        ("atomic modify", f"{'yes' if info.atomic_modify else 'no'}  ({info.sources['modify']})"),
    ]
    template: dict[str, Any] | None = None
    try:
        dialect = make_dialect(info, block)
    except ValueError as e:
        rows.append(("problem", str(e)))
    else:
        if isinstance(dialect, TemplateDialect):
            spec = dialect.spec
            template = {
                "read": spec.read,
                "write": spec.write,
                "ack": spec.ack.pattern if spec.ack else None,
                "error": spec.error.pattern,
                "terminator": spec.terminator.pattern if spec.terminator else None,
                "row_bytes": spec.row_bytes,
                "settle_ms": spec.settle_ms,
            }
            rows.append(("read", spec.read))
            rows.append(("write", spec.write or "(none: read-only)"))
            rows.append(("ack", spec.ack.pattern if spec.ack else "(none)"))
            rows.append(("settle_ms", str(spec.settle_ms)))
    if info.dialect == "termapy":
        if device_info is not None:
            rows.append(("device", "answered MEM.INFO"))
        else:
            rows.append(("device", f"no MEM.INFO ({cache.get('device_error', 'not asked')})"))
    for line in format_kv_lines(rows):
        ctx.io.output_markup(line)
    return CmdResult.ok(value=info.dialect, data={
        "dialect": info.dialect,
        "max_block": info.max_block,
        "address_bits": info.address_bits,
        "endian": info.endian,
        "atomic_modify": info.atomic_modify,
        "sources": dict(info.sources),
        "device_info": device_info if info.dialect == "termapy" else None,
        "template": template,
    })


_LONG_HELP: Final[str] = (
    "Reads and writes device memory.  Native firmware implements the\n"
    "termapy MEM spec (see /help memory):\n"
    "\n"
    "  MEM.R <addr> <len>      -> rows \"<ADDR>: <XX XX ...>\" then OK\n"
    "  MEM.W <addr> <hex>      -> OK\n"
    "  MEM.M <addr> <and> <or> -> the old word, then OK   (atomic masked write)\n"
    "  MEM.INFO                -> one JSON line then OK\n"
    "  any failure             -> ERR <reason>\n"
    "\n"
    "A device with its own peek/poke grammar is described by the profile's\n"
    "\"memory\" block (dialect template).  Targets take every /sym form plus\n"
    "bits: gTemp, main+0x10, U1MODE.ON, gFlags.4-6, 0x1000.15.  Bit and\n"
    "mask writes use the device's atomic MEM.M when advertised, else a\n"
    "read + write-back that can race an ISR (xor always does).\n"
    "\n"
    "Every mutation is audited in the session log (address, word before\n"
    "and after, origin).  Over MCP they are destructive: confirm=true.\n"
    "\n"
    "Commands:\n"
    "  /mem.dump <target> {len} {type}  - hexdump; u16/u32 word columns,\n"
    "                                     i*/f* decimal/float; addr= ascii=\n"
    "                                     toggle columns (both off = bare)\n"
    "  /mem.read <target> {type}        - one typed value, field, bit or slice\n"
    "  /mem.write <target> <hex|value>  - hex bytes; or a field/bit value\n"
    "  /mem.or|and|xor|clear <target> <mask> - mask ops (clear: word &= ~mask)\n"
    "  /mem.not <target>                - invert one word\n"
    "  /mem.str <target> {max}          - NUL-terminated string (default cap 256)\n"
    "  /mem.info                        - dialect, limits, endian, atomic modify"
)

_CONNECTED: Final = CapabilitySet(serial_connected=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="mem",
    help="Device memory: dump, typed reads, writes and bit ops via /sym names.",
    long_help=_LONG_HELP,
    sub_commands={
        "dump": Command(
            params=[
                ParamSpec("target", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec(
                    "len", "str", positional=True, default=str(_DEFAULT_DUMP_LEN),
                    help="bytes to read: decimal, 0x hex, or Nh",
                ),
                ParamSpec(
                    "type", "str", positional=True, default="",
                    help="u8 (default) | u16/u32/u64 hex words | i8..i64 | f32/f64",
                ),
                ParamSpec("addr", "bool", default=True, help="address column"),
                ParamSpec("ascii", "bool", default=True, help="ASCII column"),
            ],
            help="Hexdump or word columns at an address or symbol (default 64 bytes).",
            handler=_handler_dump,
            needs=_CONNECTED,
            safety="readonly",
        ),
        "read": Command(
            params=[
                ParamSpec(
                    "target", "str", positional=True, required=True,
                    help="address, symbol, field (U1MODE.ON), bit (.15) or slice (.4-6)",
                ),
                ParamSpec(
                    "type", "str", positional=True, default="",
                    help="u8..u64, i8..i64, f32, f64, char (default: the symbol's type)",
                ),
            ],
            help="Read one typed value: scalar, char, register field, bit or slice.",
            handler=_handler_read,
            needs=_CONNECTED,
            safety="readonly",
        ),
        "write": Command(
            params=[
                ParamSpec(
                    "target", "str", positional=True, required=True,
                    help="address or symbol; with .field/.bit the value is masked in",
                ),
                ParamSpec(
                    "hex", "str", positional=True, required=True, rest=True,
                    help="hex bytes, or the field value for a .suffix target",
                ),
            ],
            help="Write hex bytes, or set a register field/bit (audited).",
            handler=_handler_write,
            needs=_CONNECTED,
            safety="destructive",
        ),
        "or": Command(
            params=[
                ParamSpec("target", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec("mask", "str", positional=True, required=True, help="bits to set"),
            ],
            help="OR a mask into one word (atomic via MEM.M when available).",
            handler=_handler_or,
            needs=_CONNECTED,
            safety="destructive",
        ),
        "and": Command(
            params=[
                ParamSpec("target", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec("mask", "str", positional=True, required=True, help="bits to keep"),
            ],
            help="AND a mask into one word (atomic via MEM.M when available).",
            handler=_handler_and,
            needs=_CONNECTED,
            safety="destructive",
        ),
        "xor": Command(
            params=[
                ParamSpec("target", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec("mask", "str", positional=True, required=True, help="bits to flip"),
            ],
            help="XOR a mask into one word (always read + write-back).",
            handler=_handler_xor,
            needs=_CONNECTED,
            safety="destructive",
        ),
        "clear": Command(
            params=[
                ParamSpec("target", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec("mask", "str", positional=True, required=True, help="bits to clear"),
            ],
            help="Clear mask bits in one word: word &= ~mask (atomic via MEM.M when available).",
            handler=_handler_clear,
            needs=_CONNECTED,
            safety="destructive",
        ),
        "not": Command(
            params=[
                ParamSpec("target", "str", positional=True, required=True, help="address or symbol"),
            ],
            help="Invert one word (always read + write-back).",
            handler=_handler_not,
            needs=_CONNECTED,
            safety="destructive",
        ),
        "str": Command(
            params=[
                ParamSpec("target", "str", positional=True, required=True, help="address or symbol"),
                ParamSpec(
                    "max", "str", positional=True, default=str(_DEFAULT_STR_MAX),
                    help="byte cap when no NUL is found",
                ),
            ],
            help="Read a NUL-terminated string at an address or symbol.",
            handler=_handler_str,
            needs=_CONNECTED,
            safety="readonly",
        ),
        "info": Command(
            help="Show how memory access is configured: dialect, block limit, address width, endian, atomic modify.",
            handler=_handler_info,
            needs=_CONNECTED,
            safety="readonly",
        ),
    },
)
