"""The symbol model and termapy's one symbol-file format.

``Symbol`` is one named address; ``SymbolTable`` is the sorted collection
with address lookup, name lookup, search, and the JSON load/save pair.
Validation is by construction: ``SymbolTable.from_dict`` raises a
``ValueError`` naming the first offending field (``symbols[3].addr: ...``),
so a handler renders it and nothing else needs an error list.

File shape (``<cfg_dir>/<cfg_stem>.symbols.json``, see :func:`sidecar_path`)::

    {
      "symbols_version": 1,                 # the only hard gate on load
      "source": "build/mem.map",            # free text: where it came from
      "imported": "2026-08-29T10:12:00",    # free text: when
      "address_bits": 32,                   # hex width of printed addresses
      "endian": "le",                       # le | be (stored; step 3 reads it)
      "regions": [],                        # opaque until the regions step
      "symbols": [
        {"name": "gTemp", "addr": "0x00001000", "size": 2,
         "section": "bss", "file": "sensor.c", "type": "u16"},
        {"name": "U1STA", "addr": "0xBF806010", "size": 4,
         "section": "sfr", "type": "u32", "rmw": false}
      ]
    }

Unknown keys are ignored on load and dropped on save.  Two symbols may
share a name (statics in different files) or an address (aliases).

``termapy.protocol.core`` is imported lazily inside :func:`_check_type`
because its module import pulls the CRC registry; this module stays on
the engine's import path and must not pay for that.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Final, Iterable

from termapy import folders

SYMBOLS_VERSION: Final[int] = 1

# Scalar type tokens a symbol's ``type`` may carry; anything else must be a
# format spec (``protocol.core.parse_format_spec``).
SCALAR_TYPES: Final[frozenset[str]] = frozenset({
    "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "f32", "f64",
})

_ENDIANS: Final[tuple[str, ...]] = ("le", "be")

# Linker section -> display label.  Converters emit the linker's own names
# (text/bss/data/rodata, plus ``global`` for sizeless linker globals); a
# hand-written table may say ``sfr``.
_SECTION_LABELS: Final[dict[str, str]] = {
    "text": "code",
    "bss": "bss",
    "data": "data",
    "rodata": "const",
    "global": "global",
    "sfr": "sfr",
}

_HEX_ADDR_RE: Final = re.compile(r"^0[xX][0-9a-fA-F]+$")


def hex_digits(address_bits: int) -> int:
    """Hex digits needed to print an ``address_bits``-wide address."""
    return -(-address_bits // 4)


def section_label(section: str) -> str:
    """Display label for a linker section (``text`` -> ``code``)."""
    return _SECTION_LABELS.get(section, section)


@dataclass(frozen=True, slots=True)
class Symbol:
    """One named address.

    Positional order (``name, addr, size, section``) is the historical
    constructor shape; the remaining fields are the optional JSON keys.

    Attributes:
        name: Symbol name, dots kept verbatim (``count.12``).
        addr: Start address.
        size: Byte size; 0 = unknown (linker globals).
        section: Linker section (``text``/``bss``/``data``/``rodata``/
            ``global``/``sfr``), free text.
        file: Defining object or source file; disambiguates duplicate
            statics (``name@file`` at the prompt).
        type: A scalar in ``SCALAR_TYPES`` or a format spec.  Empty =
            untyped.
        space: Reserved for Harvard parts; stored, never interpreted here.
        rmw: False forbids slice/mask writes (W1C, self-clearing).
            Reserved for the typed-write step.
    """

    name: str
    addr: int
    size: int = 0
    section: str = ""
    file: str = ""
    type: str = ""
    space: str = ""
    rmw: bool = True

    @property
    def end(self) -> int:
        """One past the last byte (``addr + size``)."""
        return self.addr + self.size

    @property
    def section_label(self) -> str:
        """Display label for the section (``text`` -> ``code``)."""
        return section_label(self.section)

    def contains(self, addr: int) -> bool:
        """True when ``addr`` is in ``[addr, end)``; always False for size 0."""
        return self.addr <= addr < self.end

    def to_dict(self, *, address_bits: int = 32) -> dict[str, Any]:
        """The FILE shape: required keys always, optionals only when set."""
        out: dict[str, Any] = {
            "name": self.name,
            "addr": f"0x{self.addr:0{hex_digits(address_bits)}X}",
            "size": self.size,
        }
        for key in ("section", "file", "type", "space"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if not self.rmw:
            out["rmw"] = False
        return out

    @classmethod
    def from_dict(cls, raw: Any, where: str) -> Symbol:
        """Build a Symbol from one ``symbols[i]`` object.

        Args:
            raw: The decoded JSON value.
            where: Field path for error messages (``symbols[3]``).

        Returns:
            The validated Symbol.

        Raises:
            ValueError: ``"<where>.<field>: <problem>"`` for the first
                problem found.  Unknown keys are ignored.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: expected an object")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{where}.name: expected a non-empty string")
        addr = _parse_addr_field(raw.get("addr"), where)
        size = raw.get("size", 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"{where}.size: expected a non-negative integer")
        texts: dict[str, str] = {}
        for key in ("section", "file", "type", "space"):
            value = raw.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{where}.{key}: expected a string")
            texts[key] = value
        _check_type(texts["type"], where)
        rmw = raw.get("rmw", True)
        if not isinstance(rmw, bool):
            raise ValueError(f"{where}.rmw: expected a boolean")
        return cls(
            name, addr, size, texts["section"],
            file=texts["file"], type=texts["type"], space=texts["space"], rmw=rmw,
        )


def _parse_addr_field(value: Any, where: str) -> int:
    """``addr`` is a ``0x`` hex string or a non-negative int -- nothing guessed."""
    if isinstance(value, bool):
        pass
    elif isinstance(value, int) and value >= 0:
        return value
    elif isinstance(value, str) and _HEX_ADDR_RE.match(value):
        return int(value[2:], 16)
    raise ValueError(f"{where}.addr: expected 0x-hex string or int, got {value!r}")


def _check_type(spec: str, where: str) -> None:
    """Accept ``""``, a scalar token, or a format spec with >= 1 column.

    The scalar check comes first: ``parse_format_spec("u16")`` itself
    raises, since a scalar is not ``Name:Type`` shaped.
    """
    if not spec or spec in SCALAR_TYPES:
        return
    # Lazy: protocol.core pulls the CRC registry at import.
    from termapy.protocol.core import parse_format_spec

    try:
        columns = parse_format_spec(spec)
    except (ValueError, IndexError, KeyError):
        columns = []
    if not columns:
        raise ValueError(f"{where}.type: invalid type spec {spec!r}")


class SymbolTable:
    """A sorted symbol collection with lookup, search, and the file format.

    Symbols are kept sorted by ``(addr, size, name)`` so a sized symbol
    sorts AFTER a size-0 global at the same address and ``bisect`` lands
    on the sized one.
    """

    def __init__(
        self,
        symbols: Iterable[Symbol],
        *,
        path: Path | None = None,
        source: str = "",
        imported: str = "",
        address_bits: int = 32,
        endian: str = "le",
        regions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Build a table.

        Args:
            symbols: Any iterable of Symbol; sorted here.
            path: File the table was loaded from or saved to, if any.
            source: Free text: where the symbols were imported from.
            imported: Free text: when (ISO-8601 by convention).
            address_bits: Hex width of every printed address.
            endian: ``"le"`` or ``"be"``; stored for the typed-view step.
            regions: Linker MEMORY regions, carried opaquely for now.
        """
        self.symbols: list[Symbol] = sorted(
            symbols, key=lambda symbol: (symbol.addr, symbol.size, symbol.name),
        )
        self._addrs: list[int] = [symbol.addr for symbol in self.symbols]
        self.path = path
        self.source = source
        self.imported = imported
        self.address_bits = address_bits
        self.endian = endian
        self.regions: list[dict[str, Any]] = list(regions or [])

    def __len__(self) -> int:
        return len(self.symbols)

    def lookup(self, addr: int) -> Symbol | None:
        """Find the symbol at or containing ``addr``.

        Bisects to the last symbol starting at or below ``addr``, then walks
        backward: a size-0 global sitting inside a sized symbol's range
        must not hide it.  The walk stops at the first sized symbol that
        does not contain ``addr``, so a miss costs O(run of size-0
        entries) at worst -- fine for a few thousand symbols; a 100k-symbol
        ELF import would want an interval index.

        Args:
            addr: The address to resolve.

        Returns:
            The Symbol whose start equals ``addr`` or whose range contains
            it, or None.
        """
        i = bisect_right(self._addrs, addr) - 1
        for j in range(i, -1, -1):
            symbol = self.symbols[j]
            if symbol.addr == addr or symbol.contains(addr):
                return symbol
            if symbol.size > 0:
                return None
        return None

    def by_name(self, name: str, file: str = "") -> list[Symbol]:
        """Exact, case-sensitive name matches, optionally narrowed by file.

        ``file`` matches ``Symbol.file`` exactly, by basename, or by stem,
        so ``tick@adc``, ``tick@adc.c`` and ``tick@src/adc.c`` all reach a
        symbol whose file is ``src/adc.c``.  ``Symbol.file`` is map text,
        not an OS path, so both separators count (``PureWindowsPath``).

        Args:
            name: Symbol name as typed.
            file: Optional file qualifier.

        Returns:
            Matching symbols in address order.
        """
        hits = [symbol for symbol in self.symbols if symbol.name == name]
        if not file:
            return hits
        return [
            symbol for symbol in hits
            if symbol.file and (
                symbol.file == file
                or PureWindowsPath(symbol.file).name == file
                or PureWindowsPath(symbol.file).stem == file
            )
        ]

    def search(self, pattern: str) -> list[Symbol]:
        """Search symbols by name: exact, then glob/regex, then substring.

        Supports glob wildcards (``*main*``, ``Mon*``), regex patterns
        (``^Mon``, ``SERCOM[0-4]``), and plain substring matching.

        Args:
            pattern: Exact name, glob/regex pattern, or substring.

        Returns:
            List of matching symbols, sorted by address.
        """
        # 1. Exact match
        exact = [symbol for symbol in self.symbols if symbol.name == pattern]
        if exact:
            return exact
        # 2. Convert glob-style wildcards to regex, then try as regex
        rx_str = pattern
        if "*" in pattern or "?" in pattern:
            # Glob -> regex: escape everything except * and ?
            rx_str = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
            rx_str = f"^{rx_str}$"
        try:
            rx = re.compile(rx_str, re.IGNORECASE)
            matches = [symbol for symbol in self.symbols if rx.search(symbol.name)]
            if matches:
                return matches
        except re.error:
            pass
        # 3. Plain substring fallback
        lowered = pattern.lower()
        return [symbol for symbol in self.symbols if lowered in symbol.name.lower()]

    def stats(self) -> dict[str, int]:
        """Return symbol counts by section."""
        counts: dict[str, int] = {}
        for symbol in self.symbols:
            counts[symbol.section] = counts.get(symbol.section, 0) + 1
        return counts

    def span(self) -> tuple[int, int] | None:
        """``(lowest addr, highest end)`` or None when empty."""
        if not self.symbols:
            return None
        return self.symbols[0].addr, max(symbol.end for symbol in self.symbols)

    def to_dict(self) -> dict[str, Any]:
        """The file shape (see the module docstring)."""
        return {
            "symbols_version": SYMBOLS_VERSION,
            "source": self.source,
            "imported": self.imported,
            "address_bits": self.address_bits,
            "endian": self.endian,
            "regions": self.regions,
            "symbols": [
                symbol.to_dict(address_bits=self.address_bits) for symbol in self.symbols
            ],
        }

    @classmethod
    def from_dict(cls, data: Any, *, path: Path | None = None) -> SymbolTable:
        """Validate a decoded JSON document and build the table.

        Args:
            data: The decoded JSON value.
            path: Recorded as ``table.path``.

        Returns:
            The table.

        Raises:
            ValueError: The first problem, field-qualified.
        """
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        version = data.get("symbols_version", "missing")
        if isinstance(version, bool) or version != SYMBOLS_VERSION:
            raise ValueError(
                f"symbols_version: expected {SYMBOLS_VERSION}, got {version}"
            )
        raw_symbols = data.get("symbols")
        if not isinstance(raw_symbols, list):
            raise ValueError("symbols: expected a list")
        texts: dict[str, str] = {}
        for key in ("source", "imported"):
            value = data.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{key}: expected a string")
            texts[key] = value
        address_bits = data.get("address_bits", 32)
        if (
            isinstance(address_bits, bool)
            or not isinstance(address_bits, int)
            or address_bits <= 0
        ):
            raise ValueError("address_bits: expected a positive integer")
        endian = data.get("endian", "le")
        if endian not in _ENDIANS:
            raise ValueError(f"endian: expected le or be, got {endian!r}")
        regions = data.get("regions", [])
        if not isinstance(regions, list) or not all(
            isinstance(region, dict) for region in regions
        ):
            raise ValueError("regions: expected a list of objects")
        symbols = [
            Symbol.from_dict(raw, f"symbols[{i}]") for i, raw in enumerate(raw_symbols)
        ]
        return cls(
            symbols,
            path=path,
            source=texts["source"],
            imported=texts["imported"],
            address_bits=address_bits,
            endian=endian,
            regions=regions,
        )

    @classmethod
    def load(cls, path: str | Path) -> SymbolTable:
        """Read and validate a symbols file.

        Raises:
            OSError: The file could not be read.
            ValueError: Invalid JSON (``json.JSONDecodeError`` is one) or
                an invalid shape.
        """
        file = Path(path)
        return cls.from_dict(json.loads(file.read_text(encoding="utf-8")), path=file)

    def save(self, path: str | Path) -> None:
        """Write the table as indented JSON with a trailing newline.

        Raises:
            OSError: The file could not be written.
        """
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8",
        )


def sidecar_path(config_path: str) -> Path | None:
    """``<cfg_dir>/<cfg_stem><SYMBOLS_SUFFIX>`` beside the cfg, or None.

    Args:
        config_path: The active config file; ``""`` in the zero-config CLI.

    Returns:
        The sidecar path, or None when there is no config.
    """
    if not config_path:
        return None
    cfg_file = Path(config_path)
    return cfg_file.parent / f"{cfg_file.stem}{folders.SYMBOLS_SUFFIX}"
