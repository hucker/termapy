"""Device memory access -- bytes in, bytes out, over an injected exchange.

Layer 1 of the memory design.  Two wire dialects, one engine:

- ``termapy`` -- the published ``MEM`` spec (see ``help/memory.md``) for
  firmware you write::

      MEM.R <addr> <len>   ->  <ADDR>: <XX XX ...>   (one or more rows)
                               OK
      MEM.W <addr> <hex>   ->  OK
      MEM.M <addr> <and> <or> -> <ADDR>: <old word bytes> then OK  (atomic)
      MEM.INFO             ->  {"max_block": 64, "address_bits": 32, "endian": "le"}
                               OK
      any failure          ->  ERR <reason>

- ``template`` -- a device with its own peek/poke grammar, described by
  the profile's ``memory`` block: a read template (``mem {addr:X} {len}``),
  a row regex, an optional write template (``mem {addr:X} ={byte:02X}``),
  optional ack / error / terminator regexes, and idle-gap framing.

Addresses go out as the template says (``0x``-prefixed hex for the
native spec); a row's address is hex with or without ``0x``; bytes are hex
pairs.  Lines that are none of row / verdict are ignored, so a monitor that
echoes or prompts is fine.

:class:`Memory` chunks a request to the device's ``max_block``, checks
that the rows come back contiguous and complete, and turns errors and
silence into :class:`DeviceMemoryError`.  It never interprets the bytes:
widths, endianness, types and bit operations are the typed-view layer
above.

The exchange is injected (``exchange(command) -> reply_text``), like
:func:`termapy.request_response.request_response`, so the engine is tested
against in-memory devices and the ``/mem.*`` plugin wires it to
``ctx.serial``.  No Textual, no pyserial, nothing from ``builtins/``.
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass, field
from typing import Any, Callable, Final, Literal, Mapping, Protocol

from termapy.symbols.format import hex_addr

# Wire dialects the engine speaks.
DIALECTS: Final[tuple[str, ...]] = ("termapy", "template")
ENDIANS: Final[tuple[str, ...]] = ("le", "be")

DEFAULT_DIALECT: Final[str] = "termapy"
DEFAULT_MAX_BLOCK: Final[int] = 64
DEFAULT_ADDRESS_BITS: Final[int] = 32
DEFAULT_ENDIAN: Final[str] = "le"

INFO_COMMAND: Final[str] = "MEM.INFO"

# One exchange: send a command line, return everything the device said.
Exchange = Callable[[str], str]


class DeviceMemoryError(Exception):
    """A memory exchange failed on the device or the wire.

    ``str(error)`` is already phrased for ``CmdResult.fail`` (``Device
    error: range``, ``No reply to MEM.R 0x00001000 16``, ...).
    """


# ── Facts about the device: profile block > MEM.INFO > defaults ─────────────


@dataclass(frozen=True)
class MemoryInfo:
    """How to talk to this device's memory, and where each fact came from.

    Attributes:
        dialect: Wire grammar name (``termapy`` or ``template``).
        max_block: Largest byte count per read or write exchange.
        address_bits: Address width; sets the printed hex width.
        endian: ``le`` / ``be``; stored for the typed-view layer.
        sources: Field name -> ``profile`` / ``device`` / ``default``.
    """

    dialect: str = DEFAULT_DIALECT
    max_block: int = DEFAULT_MAX_BLOCK
    address_bits: int = DEFAULT_ADDRESS_BITS
    endian: str = DEFAULT_ENDIAN
    atomic_modify: bool = False
    sources: Mapping[str, str] = field(default_factory=dict)

    @property
    def byte_order(self) -> Literal["little", "big"]:
        """``struct``/``int.from_bytes`` byte order for this device."""
        return "little" if self.endian == "le" else "big"


def _coerce(key: str, value: Any) -> tuple[Any, str]:
    """``(accepted value or None, warning)`` for one memory fact.

    The degrade rule for every source (profile block, MEM.INFO): a value
    that is not usable falls back to the default and says so; nothing
    ever refuses to load.
    """
    if value is None:
        return None, ""
    if key == "endian":
        if isinstance(value, str) and value in ENDIANS:
            return value, ""
        return None, f"memory/endian: expected le or be, got {value!r} (treated as le)"
    if key == "max_block":
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value, ""
        return None, (
            f"memory/max_block: expected a positive integer, got {value!r} "
            f"(default {DEFAULT_MAX_BLOCK} used)"
        )
    if key == "address_bits":
        if isinstance(value, int) and not isinstance(value, bool) and 8 <= value <= 64:
            return value, ""
        return None, (
            f"memory/address_bits: expected an integer from 8 to 64, got {value!r} "
            f"(default {DEFAULT_ADDRESS_BITS} used)"
        )
    if key == "modify":
        if isinstance(value, bool):
            return value, ""
        return None, f"memory/modify: expected true or false, got {value!r} (ignored)"
    raise KeyError(key)


def validate_block(block: Mapping[str, Any]) -> list[str]:
    """Lint a profile ``memory`` block; every problem is a degrade warning.

    Shared by the profile loader's forward-compat lint and by the runtime
    resolvers, so a warning and the behavior it describes cannot disagree.
    For the ``template`` dialect the template fields are checked the way
    :func:`parse_template_block` will: a problem there means ``/mem.*``
    refuse, which the warning says.

    Args:
        block: The decoded ``memory`` object.

    Returns:
        Warnings, each naming the field and the rule applied.
    """
    warnings: list[str] = []
    dialect = block.get("dialect")
    if dialect is not None and dialect not in DIALECTS:
        warnings.append(
            f"memory/dialect: unrecognized dialect {dialect!r} "
            f"(/mem.* commands refuse until it is recognized; known: "
            f"{', '.join(DIALECTS)})"
        )
    for key in ("max_block", "address_bits", "endian", "modify"):
        _, warning = _coerce(key, block.get(key))
        if warning:
            warnings.append(warning)
    if dialect == "template":
        try:
            parse_template_block(block)
        except ValueError as e:
            warnings.append(f"{e} (/mem.* commands refuse until it is fixed)")
    return warnings


def resolve_info(
    profile_block: Mapping[str, Any] | None,
    device_info: Mapping[str, Any] | None,
) -> MemoryInfo:
    """Merge the profile's ``memory`` block over what ``MEM.INFO`` reported.

    Precedence per field: an explicit profile value, then the device's
    answer, then the default.  Unusable values are skipped (the degrade
    rule of :func:`validate_block`).  Only the profile names a dialect.

    Args:
        profile_block: The profile's ``memory`` object, or None.
        device_info: The parsed ``MEM.INFO`` record, or None.

    Returns:
        The resolved facts with their sources.
    """
    values: dict[str, Any] = {
        "dialect": DEFAULT_DIALECT,
        "max_block": DEFAULT_MAX_BLOCK,
        "address_bits": DEFAULT_ADDRESS_BITS,
        "endian": DEFAULT_ENDIAN,
        "modify": False,
    }
    sources: dict[str, str] = {key: "default" for key in values}
    # Device first, profile last: the later write wins.
    for source, block in (("device", device_info), ("profile", profile_block)):
        if not isinstance(block, Mapping):
            continue
        for key in ("max_block", "address_bits", "endian", "modify"):
            value, _ = _coerce(key, block.get(key))
            if value is not None:
                values[key] = value
                sources[key] = source
        dialect = block.get("dialect") if source == "profile" else None
        if isinstance(dialect, str) and dialect:
            values["dialect"] = dialect  # unknown is kept: make_dialect refuses it
            sources["dialect"] = "profile"
    return MemoryInfo(
        dialect=values["dialect"],
        max_block=values["max_block"],
        address_bits=values["address_bits"],
        endian=values["endian"],
        atomic_modify=values["modify"],
        sources=sources,
    )


# ── The native wire ─────────────────────────────────────────────────────────

_ROW_RE: Final = re.compile(
    r"^\s*(?:0[xX])?(?P<addr>[0-9A-Fa-f]{1,16}):\s*(?P<hex>(?:[0-9A-Fa-f]{2}\s*)+)$"
)
_OK_RE: Final = re.compile(r"^\s*OK\s*$")
# The optional colon is for real monitors that answer ``ERR: reason``
# (the m3 bench device does): without it the colon lands in the captured
# reason and renders "Device error: : unknown command" -- a double colon
# in every unavailable-verdict message.  ``\b`` keeps ``ERROR:`` lines
# (a different grammar) out of the native verdict.
_ERR_RE: Final = re.compile(r"^\s*ERR\b\s*:?\s*(?P<reason>.*?)\s*$")


def read_command(addr: int, length: int) -> str:
    """``MEM.R 0x1000 16`` -- the native read request for one block."""
    return f"MEM.R 0x{addr:X} {length}"


def write_command(addr: int, data: bytes) -> str:
    """``MEM.W 0x1000 1B00`` -- the native write request for one block."""
    return f"MEM.W 0x{addr:X} {data.hex().upper()}"


def modify_command(addr: int, and_mask: int, or_mask: int, width: int) -> str:
    """``MEM.M 0x40000000 FFFF7FFF 00008000`` -- the atomic masked write.

    The masks' digit count carries the access width (2/4/8 hex digits =
    u8/u16/u32), so both are zero-padded to ``width`` bytes.
    """
    digits = width * 2
    return f"MEM.M 0x{addr:X} {and_mask:0{digits}X} {or_mask:0{digits}X}"


@dataclass(frozen=True)
class Reply:
    """One parsed native reply.

    Attributes:
        complete: An ``OK`` or ``ERR`` line was seen.
        error: The ``ERR`` reason (``""`` when ERR carried none); None on OK
            or when incomplete.
        rows: ``(address, bytes)`` per data row, in wire order.
        info: The first JSON object line, decoded, or None.
    """

    complete: bool
    error: str | None
    rows: tuple[tuple[int, bytes], ...]
    info: dict[str, Any] | None


def parse_reply(text: str) -> Reply:
    """Parse native reply text: rows, the JSON record, and the OK / ERR verdict.

    Stops at the first ``OK`` or ``ERR`` line.  Every other line that is
    not a row or a JSON object is ignored (echo, prompt, banner).

    Args:
        text: Everything received since the command was sent.

    Returns:
        The parsed reply; ``complete`` is False when no verdict arrived.
    """
    rows: list[tuple[int, bytes]] = []
    info: dict[str, Any] | None = None
    for line in text.splitlines():
        if _OK_RE.match(line):
            return Reply(True, None, tuple(rows), info)
        err = _ERR_RE.match(line)
        if err:
            return Reply(True, err.group("reason"), tuple(rows), info)
        row = _ROW_RE.match(line)
        if row:
            rows.append((int(row.group("addr"), 16), bytes.fromhex(row.group("hex"))))
            continue
        stripped = line.strip()
        if info is None and stripped.startswith("{"):
            try:
                decoded = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(decoded, dict):
                info = decoded
    return Reply(False, None, tuple(rows), info)


def reply_complete(text: str) -> bool:
    """True once ``text`` holds an ``OK`` or ``ERR`` line (stop reading)."""
    return parse_reply(text).complete


def assemble(
    rows: tuple[tuple[int, bytes], ...],
    addr: int,
    length: int,
    *,
    address_bits: int,
    exact: bool = True,
) -> bytes:
    """Join rows into the requested block, refusing gaps and short totals.

    Args:
        rows: Parsed data rows in wire order.
        addr: The address that was requested.
        length: The byte count that was requested.
        address_bits: Hex width for the addresses in error text.
        exact: Refuse a total longer than ``length`` (the native spec);
            False truncates instead (a template row's trailing column can
            look like bytes).

    Returns:
        Exactly ``length`` bytes starting at ``addr``.

    Raises:
        DeviceMemoryError: A row does not start where the previous one
            ended, or the total is short (or, when ``exact``, long).
    """
    expected = addr
    out = bytearray()
    for row_addr, data in rows:
        if row_addr != expected:
            raise DeviceMemoryError(
                f"Discontinuous reply: expected {hex_addr(expected, address_bits)}, "
                f"got {hex_addr(row_addr, address_bits)}"
            )
        out.extend(data)
        expected += len(data)
    if len(out) < length or (exact and len(out) > length):
        kind = "Short" if len(out) < length else "Long"
        raise DeviceMemoryError(
            f"{kind} reply: {len(out)} of {length} bytes at {hex_addr(addr, address_bits)}"
        )
    return bytes(out[:length])


# ── Dump rows: the prose renderer and its data= twin, side by side ──────────


@dataclass(frozen=True)
class DumpRow:
    """One hexdump row.

    Attributes:
        addr: Address of the first byte.
        data: The row's bytes (up to the dump width).
        label: Symbolic annotation for ``addr`` (``main+0x10``), or ``""``.
    """

    addr: int
    data: bytes
    label: str = ""

    @property
    def hex(self) -> str:
        """``1B 00 40 06`` -- space-separated upper-case pairs."""
        return " ".join(f"{byte:02X}" for byte in self.data)

    @property
    def ascii(self) -> str:
        """Printable ASCII with ``.`` for everything else."""
        return "".join(chr(byte) if 0x20 <= byte < 0x7F else "." for byte in self.data)


def dump_rows(
    addr: int,
    data: bytes,
    *,
    width: int = 16,
    label: Callable[[int], str] | None = None,
) -> list[DumpRow]:
    """Split a block into rows of ``width`` bytes.

    Args:
        addr: Address of ``data[0]``.
        data: The block.
        width: Bytes per row.
        label: Optional ``addr -> annotation`` (the plugin passes the
            symbol table's containing-symbol lookup); None = no labels.

    Returns:
        The rows, in address order.
    """
    return [
        DumpRow(addr + offset, data[offset:offset + width], label(addr + offset) if label else "")
        for offset in range(0, len(data), width)
    ]


def format_row(row: DumpRow, *, address_bits: int = DEFAULT_ADDRESS_BITS, width: int = 16) -> str:
    """``00001000  1B 00 ...  |..@.|  gTemp`` -- one display line.

    The hex column is padded to ``width`` bytes so the ASCII column lines
    up on the short last row.
    """
    hex_width = width * 3 - 1
    line = f"{hex_addr(row.addr, address_bits)}  {row.hex:<{hex_width}}  |{row.ascii}|"
    return f"{line}  {row.label}" if row.label else line


def row_record(row: DumpRow, *, address_bits: int = DEFAULT_ADDRESS_BITS) -> dict[str, Any]:
    """The agent record for one row: the same facts as :func:`format_row`."""
    return {
        "addr": row.addr,
        "addr_hex": hex_addr(row.addr, address_bits),
        "hex": row.hex,
        "ascii": row.ascii,
        "symbolic": row.label,
    }


# ── Dialects ────────────────────────────────────────────────────────────────


# One message for every write path on a read-only interface, raised BEFORE
# any device traffic (Memory.write / Memory.modify pre-check, and the
# template's own write_request as the backstop) -- an RMW must never read
# a word it can then not write back.
_READ_ONLY_MSG: Final[str] = (
    "Read-only memory interface: the profile declares no write template."
)


class Dialect(Protocol):
    """What :class:`Memory` needs from a wire grammar.

    ``write_unit`` is the bytes per write exchange: 0 = a whole block
    (``max_block``), 1 = one byte per command (a ``=val`` monitor).
    ``settle_ms`` is the idle gap that ends a reply when ``complete``
    cannot; 0 = the transport's default.  ``writable`` is False for a
    read-only grammar (a template block with no ``write``); the engine
    refuses writes and modifies up front instead of failing mid-exchange.
    """

    name: str
    supports_info: bool
    write_unit: int
    settle_ms: int

    @property
    def writable(self) -> bool:
        """False for a read-only grammar; a plain class attribute satisfies this."""
        ...

    def read_request(self, addr: int, length: int) -> str: ...
    def parse_read(self, text: str, request: str, addr: int, length: int, *, address_bits: int) -> bytes: ...
    def write_request(self, addr: int, data: bytes) -> str: ...
    def parse_write(self, text: str, request: str) -> None: ...
    def complete(self, text: str) -> bool: ...


def _verdict(text: str, request: str) -> Reply:
    """Parse a native reply, turning silence and ``ERR`` into errors.

    An incomplete reply says what DID arrive -- row count and the last
    line -- so a stall diagnoses itself from the error alone.
    """
    reply = parse_reply(text)
    if not reply.complete:
        if not text.strip():
            raise DeviceMemoryError(f"No reply to {request}")
        last = [line.strip() for line in text.splitlines() if line.strip()][-1]
        raise DeviceMemoryError(
            f"Incomplete reply to {request} "
            f"({len(reply.rows)} rows, no OK/ERR; last line {last[:40]!r})"
        )
    if reply.error is not None:
        raise DeviceMemoryError(f"Device error: {reply.error or 'ERR'}")
    return reply


class NativeDialect:
    """The published ``MEM`` spec: rows + ``OK``, block writes, ``MEM.INFO``."""

    name = "termapy"
    supports_info = True
    writable = True
    write_unit = 0
    settle_ms = 0

    def read_request(self, addr: int, length: int) -> str:
        return read_command(addr, length)

    def parse_read(
        self, text: str, request: str, addr: int, length: int, *, address_bits: int,
    ) -> bytes:
        reply = _verdict(text, request)
        return assemble(reply.rows, addr, length, address_bits=address_bits)

    def write_request(self, addr: int, data: bytes) -> str:
        return write_command(addr, data)

    def parse_write(self, text: str, request: str) -> None:
        _verdict(text, request)

    def complete(self, text: str) -> bool:
        return reply_complete(text)


# Template dialect defaults.  The row regex takes a hex address (0x
# optional), a colon, then hex pairs; it stops at the first token that is
# not a pair, so an ASCII column set off by ``|`` or extra spacing is not
# read as data.  ``row_bytes`` caps a full row so a bare ASCII column that
# happens to start with hex digits cannot lengthen it.
DEFAULT_ROW_PATTERN: Final[str] = (
    r"^\s*(?:0[xX])?(?P<addr>[0-9A-Fa-f]{2,16}):\s*(?P<hex>(?:[0-9A-Fa-f]{2}\s+)*[0-9A-Fa-f]{2})"
)
DEFAULT_ERROR_PATTERN: Final[str] = r"(?i)^\s*(err|error|fault)\b"
DEFAULT_ROW_BYTES: Final[int] = 16
DEFAULT_SETTLE_MS: Final[int] = 100


@dataclass(frozen=True)
class TemplateSpec:
    """A device's own memory grammar, from the profile ``memory`` block.

    Attributes:
        read: ``str.format`` template with ``{addr}`` and ``{len}``.
        row: Regex with ``addr`` and ``hex`` groups matching one data row.
        row_bytes: Most bytes one row carries.
        write: Template with ``{addr}`` and ``{byte}`` (one byte per
            command) or ``{hex}`` (a block of pairs); None = read-only.
        ack: Regex a successful write reply must contain; None = none.
        error: Regex flagging a failed command anywhere in the reply.
        terminator: Regex that ends a reply early (a prompt); None = the
            reply ends at the idle gap.
        settle_ms: Idle gap that ends a reply.
    """

    read: str
    row: re.Pattern[str]
    row_bytes: int = DEFAULT_ROW_BYTES
    write: str | None = None
    ack: re.Pattern[str] | None = None
    error: re.Pattern[str] = re.compile(DEFAULT_ERROR_PATTERN)
    terminator: re.Pattern[str] | None = None
    settle_ms: int = DEFAULT_SETTLE_MS


def _compile(block: Mapping[str, Any], key: str, default: str | None) -> re.Pattern[str] | None:
    value = block.get(key, default)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"memory/{key}: expected a regex string")
    try:
        return re.compile(value)
    except re.error as e:
        raise ValueError(f"memory/{key}: invalid regex: {e}") from None


def _template_fields(template: str, key: str) -> set[str]:
    """The placeholder names a template uses (``{addr:X}`` -> ``addr``)."""
    try:
        return {
            field.split(".")[0].split("[")[0]
            for _, field, _, _ in string.Formatter().parse(template)
            if field
        }
    except ValueError as e:
        raise ValueError(f"memory/{key}: bad placeholder in {template!r} ({e})") from None


def _check_template(block: Mapping[str, Any], key: str, names: tuple[str, ...]) -> str:
    template = block.get(key)
    if not isinstance(template, str) or not template.strip():
        raise ValueError(f"memory/{key}: required for the template dialect")
    fields = _template_fields(template, key)
    if any(name not in fields for name in names):
        raise ValueError(f"memory/{key}: must use {' and '.join('{' + name + '}' for name in names)}")
    try:
        template.format(addr=0, len=0, hex="00", byte=0)
    except (KeyError, ValueError, IndexError) as e:
        raise ValueError(f"memory/{key}: bad placeholder in {template!r} ({e})") from None
    return template


def _positive_int(block: Mapping[str, Any], key: str, default: int) -> int:
    value = block.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"memory/{key}: expected a positive integer")
    return value


def parse_template_block(block: Mapping[str, Any]) -> TemplateSpec:
    """Build a :class:`TemplateSpec` from a profile ``memory`` block.

    Raises:
        ValueError: A field-qualified message (``memory/read: ...``).
    """
    read = _check_template(block, "read", ("addr", "len"))
    write_template = block.get("write")
    write: str | None = None
    if write_template not in (None, ""):
        template = _check_template(block, "write", ("addr",))
        if not ({"byte", "hex"} & _template_fields(template, "write")):
            raise ValueError("memory/write: must use {byte} (one byte per command) or {hex}")
        write = template
    row = _compile(block, "row", DEFAULT_ROW_PATTERN)
    assert row is not None  # the default is never empty
    if "addr" not in row.groupindex or "hex" not in row.groupindex:
        raise ValueError("memory/row: regex needs (?P<addr>...) and (?P<hex>...) groups")
    error = _compile(block, "error", DEFAULT_ERROR_PATTERN)
    assert error is not None
    return TemplateSpec(
        read=read,
        row=row,
        row_bytes=_positive_int(block, "row_bytes", DEFAULT_ROW_BYTES),
        write=write,
        ack=_compile(block, "ack", None),
        error=error,
        terminator=_compile(block, "terminator", None),
        settle_ms=_positive_int(block, "settle_ms", DEFAULT_SETTLE_MS),
    )


class TemplateDialect:
    """A device's own grammar, rendered from :class:`TemplateSpec`."""

    name = "template"
    supports_info = False

    def __init__(self, spec: TemplateSpec) -> None:
        self.spec = spec
        self.write_unit = 0 if (spec.write and "{hex" in spec.write) else 1
        self.settle_ms = spec.settle_ms

    def read_request(self, addr: int, length: int) -> str:
        return self.spec.read.format(addr=addr, len=length)

    def _check_error(self, text: str) -> None:
        for line in text.splitlines():
            if self.spec.error.search(line):
                raise DeviceMemoryError(f"Device error: {line.strip()}")

    def parse_read(
        self, text: str, request: str, addr: int, length: int, *, address_bits: int,
    ) -> bytes:
        self._check_error(text)
        rows: list[tuple[int, bytes]] = []
        for line in text.splitlines():
            match = self.spec.row.match(line)
            if match:
                data = bytes.fromhex("".join(match.group("hex").split()))
                rows.append((int(match.group("addr"), 16), data[:self.spec.row_bytes]))
        if not rows:
            if not text.strip():
                raise DeviceMemoryError(f"No reply to {request}")
            first = next(line.strip() for line in text.splitlines() if line.strip())
            raise DeviceMemoryError(f"Unrecognized reply to {request}: {first[:60]}")
        return assemble(tuple(rows), addr, length, address_bits=address_bits, exact=False)

    @property
    def writable(self) -> bool:
        return self.spec.write is not None

    def write_request(self, addr: int, data: bytes) -> str:
        if self.spec.write is None:
            raise DeviceMemoryError(_READ_ONLY_MSG)
        return self.spec.write.format(addr=addr, byte=data[0], hex=data.hex().upper())

    def parse_write(self, text: str, request: str) -> None:
        self._check_error(text)
        if self.spec.ack is None:
            return
        if any(self.spec.ack.search(line) for line in text.splitlines()):
            return
        if not text.strip():
            raise DeviceMemoryError(f"No acknowledgement to {request}")
        first = next(line.strip() for line in text.splitlines() if line.strip())
        raise DeviceMemoryError(f"Unexpected reply to {request}: {first[:60]}")

    def complete(self, text: str) -> bool:
        if self.spec.terminator is None:
            return False
        return any(self.spec.terminator.search(line) for line in text.splitlines())


def make_dialect(info: MemoryInfo, block: Mapping[str, Any] | None) -> Dialect:
    """The dialect ``info`` names, built from the profile block when needed.

    Raises:
        ValueError: An unknown dialect, or a template block that does
            not describe a grammar (field-qualified message).
    """
    if info.dialect == "termapy":
        return NativeDialect()
    if info.dialect == "template":
        if not isinstance(block, Mapping):
            raise ValueError("memory/read: required for the template dialect")
        return TemplateDialect(parse_template_block(block))
    raise ValueError(f"Unknown memory dialect: {info.dialect} (dialects: {', '.join(DIALECTS)})")


# ── The engine ──────────────────────────────────────────────────────────────


class Memory:
    """Byte-block access to one device through an exchange callable.

    Args:
        exchange: Sends one command line and returns the reply text (what
            arrived until the dialect said the reply was complete or the
            idle gap / timeout hit; ``""`` on silence).
        info: The resolved facts; ``max_block`` drives the chunking.
        dialect: The wire grammar; None = the native spec (and ``info``
            must name it).

    Raises:
        ValueError: ``info`` names a dialect that needs a block.
    """

    def __init__(
        self,
        exchange: Exchange,
        info: MemoryInfo | None = None,
        dialect: Dialect | None = None,
    ) -> None:
        self.info = info if info is not None else MemoryInfo()
        self.dialect: Dialect = dialect if dialect is not None else make_dialect(self.info, None)
        self._exchange = exchange

    # -- validation ---------------------------------------------------------

    def _check_range(self, addr: int, length: int) -> None:
        bits = self.info.address_bits
        if addr < 0 or addr >= 1 << bits:
            raise ValueError(f"Invalid address: 0x{addr:X} (address_bits {bits})")
        if length < 1:
            raise ValueError(f"Invalid length: {length}")
        if addr + length > 1 << bits:
            raise ValueError(
                f"Invalid address range: {hex_addr(addr, bits)} + {length} exceeds "
                f"{bits}-bit space"
            )

    # -- operations ---------------------------------------------------------

    def read(self, addr: int, length: int) -> bytes:
        """Read ``length`` bytes at ``addr``, in ``max_block`` chunks.

        Raises:
            ValueError: Bad address or length.
            DeviceMemoryError: Silence, a device error, or rows that do
                not add up to the request.
        """
        self._check_range(addr, length)
        out = bytearray()
        for offset in range(0, length, self.info.max_block):
            chunk_addr = addr + offset
            chunk_len = min(self.info.max_block, length - offset)
            request = self.dialect.read_request(chunk_addr, chunk_len)
            text = self._exchange(request)
            out.extend(
                self.dialect.parse_read(
                    text, request, chunk_addr, chunk_len, address_bits=self.info.address_bits,
                )
            )
        return bytes(out)

    def write(self, addr: int, data: bytes) -> int:
        """Write ``data`` at ``addr``, in the dialect's write units.

        Returns:
            The number of bytes written (``len(data)``).

        Raises:
            ValueError: Bad address or empty data.
            DeviceMemoryError: A read-only dialect (refused before any
                traffic), silence, a missing acknowledgement, or a
                device error; earlier units stay written (no transaction).
        """
        if not self.dialect.writable:
            raise DeviceMemoryError(_READ_ONLY_MSG)
        self._check_range(addr, len(data))
        unit = self.dialect.write_unit or self.info.max_block
        for offset in range(0, len(data), unit):
            chunk = data[offset:offset + unit]
            request = self.dialect.write_request(addr + offset, chunk)
            self.dialect.parse_write(self._exchange(request), request)
        return len(data)

    def modify(self, addr: int, width: int, and_mask: int, or_mask: int) -> tuple[bytes, bytes]:
        """Masked write of one word: ``word = (word & and_mask) | or_mask``.

        Uses the device's atomic ``MEM.M`` when it advertised one (native
        dialect, ``MEM.INFO "modify": true``); otherwise a host-side read
        + write-back, which can race an ISR touching the same word --
        the documented fallback.  Either way the pre-modify word comes
        back, so the caller's audit line carries a true "before".

        Args:
            addr: Word address (aligned to ``width``).
            width: Word byte width: 1, 2 or 4.
            and_mask: AND mask over the logical word.
            or_mask: OR mask over the logical word.

        Returns:
            ``(old bytes, new bytes)`` in memory order, ``width`` long.

        Raises:
            ValueError: Bad width, masks that do not fit, or a bad address.
            DeviceMemoryError: A read-only dialect (refused before the
                read -- never read a word you cannot write back), silence,
                a device error, or a malformed ``MEM.M`` reply.
        """
        if not self.dialect.writable:
            raise DeviceMemoryError(_READ_ONLY_MSG)
        if width not in (1, 2, 4):
            raise ValueError(f"Invalid width: {width} (use 1, 2 or 4)")
        self._check_range(addr, width)
        mask_limit = (1 << (8 * width)) - 1
        for label, mask in (("and", and_mask), ("or", or_mask)):
            if not 0 <= mask <= mask_limit:
                raise ValueError(f"Invalid {label} mask: 0x{mask:X} ({width * 8} bits)")
        atomic = self.dialect.name == "termapy" and self.info.atomic_modify
        if atomic:
            request = modify_command(addr, and_mask, or_mask, width)
            reply = _verdict(self._exchange(request), request)
            old = assemble(reply.rows, addr, width, address_bits=self.info.address_bits)
        else:
            old = self.read(addr, width)
        old_word = int.from_bytes(old, self.info.byte_order)
        new_word = (old_word & and_mask) | or_mask
        new = new_word.to_bytes(width, self.info.byte_order)
        if not atomic:
            self.write(addr, new)
        return old, new

    def query_info(self) -> dict[str, Any]:
        """Ask a native device for its ``MEM.INFO`` record.

        Returns:
            The decoded JSON object as sent (values are coerced later by
            :func:`resolve_info`).

        Raises:
            DeviceMemoryError: The dialect has no such command, silence,
                ``ERR`` (a device without ``MEM.INFO``), or a reply
                carrying no JSON object.
        """
        if not self.dialect.supports_info:
            raise DeviceMemoryError(f"Dialect {self.dialect.name} has no {INFO_COMMAND}")
        reply = _verdict(self._exchange(INFO_COMMAND), INFO_COMMAND)
        if reply.info is None:
            raise DeviceMemoryError(f"No JSON record in the reply to {INFO_COMMAND}")
        return reply.info
