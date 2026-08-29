"""Device memory access -- bytes in, bytes out, over an injected exchange.

Layer 1 of the memory design.  The device speaks the published ``MEM``
spec (see ``help/memory.md``), and this module is everything termapy needs
to talk it without knowing what the bytes mean::

    MEM.R <addr> <len>   ->  <ADDR>: <XX XX ...>   (one or more rows)
                             OK
    MEM.W <addr> <hex>   ->  OK
    MEM.INFO             ->  {"max_block": 64, "address_bits": 32, "endian": "le"}
                             OK
    any failure          ->  ERR <reason>

Addresses go out as ``0x``-prefixed hex, lengths in decimal; a row's
address is hex with or without ``0x``; bytes are hex pairs.  Lines that are
none of row / ``OK`` / ``ERR`` (an echoed command, a prompt, a banner) are
ignored, so the parser survives a monitor that echoes.

:class:`Memory` chunks a request to the device's ``max_block``, checks that
the rows come back contiguous and complete, and turns ``ERR`` / silence
into :class:`DeviceMemoryError`.  It never interprets the bytes: widths,
endianness, types and bit operations are the typed-view layer above.

The exchange is injected (``exchange(command) -> reply_text``), like
:func:`termapy.request_response.request_response`, so the engine is tested
against an in-memory device and the ``/mem.*`` plugin wires it to
``ctx.serial``.  No Textual, no pyserial, nothing from ``builtins/``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Final, Mapping

from termapy.symbols.format import hex_addr

# Wire dialects the engine can speak.  ``termapy`` is the spec above; the
# legacy template dialect (profile-rendered read/write templates) is a
# later step and will register here.
DIALECTS: Final[tuple[str, ...]] = ("termapy",)
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
        dialect: Wire grammar name (``termapy``).
        max_block: Largest byte count per read or write exchange.
        address_bits: Address width; sets the printed hex width.
        endian: ``le`` / ``be``; stored for the typed-view layer.
        sources: Field name -> ``profile`` / ``device`` / ``default``.
    """

    dialect: str = DEFAULT_DIALECT
    max_block: int = DEFAULT_MAX_BLOCK
    address_bits: int = DEFAULT_ADDRESS_BITS
    endian: str = DEFAULT_ENDIAN
    sources: Mapping[str, str] = field(default_factory=dict)


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
    raise KeyError(key)


def validate_block(block: Mapping[str, Any]) -> list[str]:
    """Lint a profile ``memory`` block; every problem is a degrade warning.

    Shared by the profile loader's forward-compat lint and by
    :func:`resolve_info`, so a warning and the runtime behavior it
    describes cannot disagree.

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
    for key in ("max_block", "address_bits", "endian"):
        _, warning = _coerce(key, block.get(key))
        if warning:
            warnings.append(warning)
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
    }
    sources: dict[str, str] = {key: "default" for key in values}
    # Device first, profile last: the later write wins.
    for source, block in (("device", device_info), ("profile", profile_block)):
        if not isinstance(block, Mapping):
            continue
        for key in ("max_block", "address_bits", "endian"):
            value, _ = _coerce(key, block.get(key))
            if value is not None:
                values[key] = value
                sources[key] = source
        dialect = block.get("dialect") if source == "profile" else None
        if isinstance(dialect, str) and dialect:
            values["dialect"] = dialect  # unknown is kept: Memory() refuses it
            sources["dialect"] = "profile"
    return MemoryInfo(
        dialect=values["dialect"],
        max_block=values["max_block"],
        address_bits=values["address_bits"],
        endian=values["endian"],
        sources=sources,
    )


# ── The wire ────────────────────────────────────────────────────────────────

_ROW_RE: Final = re.compile(
    r"^\s*(?:0[xX])?(?P<addr>[0-9A-Fa-f]{1,16}):\s*(?P<hex>(?:[0-9A-Fa-f]{2}\s*)+)$"
)
_OK_RE: Final = re.compile(r"^\s*OK\s*$")
_ERR_RE: Final = re.compile(r"^\s*ERR\b\s*(?P<reason>.*?)\s*$")


def read_command(addr: int, length: int) -> str:
    """``MEM.R 0x1000 16`` -- the read request for one block."""
    return f"MEM.R 0x{addr:X} {length}"


def write_command(addr: int, data: bytes) -> str:
    """``MEM.W 0x1000 1B00`` -- the write request for one block."""
    return f"MEM.W 0x{addr:X} {data.hex().upper()}"


@dataclass(frozen=True)
class Reply:
    """One parsed device reply.

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
    """Parse reply text: rows, the JSON record, and the OK / ERR verdict.

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
    rows: tuple[tuple[int, bytes], ...], addr: int, length: int, *, address_bits: int,
) -> bytes:
    """Join rows into the requested block, refusing gaps and wrong sizes.

    Args:
        rows: Parsed data rows in wire order.
        addr: The address that was requested.
        length: The byte count that was requested.
        address_bits: Hex width for the addresses in error text.

    Returns:
        Exactly ``length`` bytes starting at ``addr``.

    Raises:
        DeviceMemoryError: A row does not start where the previous one
            ended, or the total is not ``length``.
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
    if len(out) != length:
        kind = "Short" if len(out) < length else "Long"
        raise DeviceMemoryError(
            f"{kind} reply: {len(out)} of {length} bytes at {hex_addr(addr, address_bits)}"
        )
    return bytes(out)


# ── The engine ──────────────────────────────────────────────────────────────


class Memory:
    """Byte-block access to one device through an exchange callable.

    Args:
        exchange: Sends one command line and returns the reply text (what
            arrived until an ``OK`` / ``ERR`` line or a timeout; ``""`` on
            silence).
        info: The resolved facts; ``max_block`` drives the chunking.

    Raises:
        ValueError: ``info.dialect`` is not one this engine speaks.
    """

    def __init__(self, exchange: Exchange, info: MemoryInfo | None = None) -> None:
        self.info = info if info is not None else MemoryInfo()
        if self.info.dialect not in DIALECTS:
            raise ValueError(
                f"Unknown memory dialect: {self.info.dialect} "
                f"(dialects: {', '.join(DIALECTS)})"
            )
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

    def _talk(self, command: str) -> Reply:
        """One exchange, with silence and ``ERR`` turned into errors."""
        text = self._exchange(command)
        reply = parse_reply(text)
        if not reply.complete:
            if not text.strip():
                raise DeviceMemoryError(f"No reply to {command}")
            raise DeviceMemoryError(f"Incomplete reply to {command}")
        if reply.error is not None:
            raise DeviceMemoryError(f"Device error: {reply.error or 'ERR'}")
        return reply

    # -- operations ---------------------------------------------------------

    def read(self, addr: int, length: int) -> bytes:
        """Read ``length`` bytes at ``addr``, in ``max_block`` chunks.

        Raises:
            ValueError: Bad address or length.
            DeviceMemoryError: Silence, ``ERR``, or rows that do not add
                up to the request.
        """
        self._check_range(addr, length)
        out = bytearray()
        for offset in range(0, length, self.info.max_block):
            chunk_addr = addr + offset
            chunk_len = min(self.info.max_block, length - offset)
            reply = self._talk(read_command(chunk_addr, chunk_len))
            out.extend(
                assemble(reply.rows, chunk_addr, chunk_len, address_bits=self.info.address_bits)
            )
        return bytes(out)

    def write(self, addr: int, data: bytes) -> int:
        """Write ``data`` at ``addr``, in ``max_block`` chunks.

        Returns:
            The number of bytes written (``len(data)``).

        Raises:
            ValueError: Bad address or empty data.
            DeviceMemoryError: Silence or ``ERR``; earlier chunks stay
                written (the device has no transaction).
        """
        self._check_range(addr, len(data))
        for offset in range(0, len(data), self.info.max_block):
            chunk = data[offset:offset + self.info.max_block]
            self._talk(write_command(addr + offset, chunk))
        return len(data)

    def query_info(self) -> dict[str, Any]:
        """Ask the device for its ``MEM.INFO`` record.

        Returns:
            The decoded JSON object as sent (values are coerced later by
            :func:`resolve_info`).

        Raises:
            DeviceMemoryError: Silence, ``ERR`` (a device without
                ``MEM.INFO``), or a reply carrying no JSON object.
        """
        reply = self._talk(INFO_COMMAND)
        if reply.info is None:
            raise DeviceMemoryError(f"No JSON record in the reply to {INFO_COMMAND}")
        return reply.info
