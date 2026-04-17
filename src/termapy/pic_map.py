"""PIC compiler map file parser -- address-to-symbol lookup.

Parses GCC/XC32 linker map files (the "Microchip PIC32 Memory-Usage
Report" section) and builds a sorted address table for fast lookups.

Each entry captures: section type (text/bss/data/rodata), symbol name,
start address, and size.  Lookups accept hex (0xFFFF) or decimal
addresses and return the symbol that contains that address.

No Textual or pyserial imports -- pure functions + one dataclass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Summary section (truncated names):
#   .text.FunctionName      0x12345   0x100   256
_SUMMARY_RE = re.compile(
    r"^\.(?P<section>text|bss|data|rodata)\."
    r"(?P<name>\S+)"
    r"\s+(?P<addr>0x[0-9a-fA-F]+)"
    r"\s+(?P<size>0x[0-9a-fA-F]+)"
    r"\s+(?P<dec>\d+)"
)

# Detailed linker section -- full names with %NNN suffix.
# Two formats:
#   .text.FullName%123                         (name only, addr on next line)
#   .bss.FullName%10    0x20002aa4   0x702     (name + addr on same line)
_DETAIL_RE = re.compile(
    r"^\.(?P<section>text|bss|data|rodata)\."
    r"(?P<name>[^%\s]+)"
    r"%\d+"
    r"(?:\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+))?"
)

# Continuation line with address + size (follows a name-only detail line):
#                 0x00016c1e       0xd4
_DETAIL_ADDR_RE = re.compile(
    r"^\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+)\s*$"
)

# Global symbols from the linker map section:
#                 0x20005a58                sCal
_GLOBAL_RE = re.compile(
    r"^\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<name>[a-zA-Z_]\w+)\s*$"
)

_SECTION_LABELS = {
    "text": "code",
    "bss": "bss",
    "data": "data",
    "rodata": "const",
    "global": "global",
}


@dataclass(frozen=True, slots=True)
class Symbol:
    """One symbol from the map file."""

    name: str
    addr: int
    size: int
    section: str  # text, bss, data, rodata

    @property
    def end(self) -> int:
        return self.addr + self.size

    @property
    def section_label(self) -> str:
        return _SECTION_LABELS.get(self.section, self.section)

    def contains(self, addr: int) -> bool:
        return self.addr <= addr < self.end


class MapFile:
    """Parsed map file with fast address lookup.

    Symbols are stored sorted by address for binary search.
    """

    def __init__(self, symbols: list[Symbol], path: str | None = None) -> None:
        self.symbols = sorted(symbols, key=lambda s: s.addr)
        self.path = path

    @classmethod
    def from_file(cls, path: str | Path) -> MapFile:
        """Parse a map file from disk.

        Args:
            path: Path to the .map file.

        Returns:
            MapFile with all parsed symbols.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        p = Path(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        return cls.from_text(text, str(p))

    @classmethod
    def from_text(cls, text: str, path: str | None = None) -> MapFile:
        """Parse map content from a string.

        Args:
            text: Full contents of the map file.
            path: Optional path for display.

        Returns:
            MapFile with all parsed symbols.
        """
        symbols: list[Symbol] = []
        seen_addrs: set[int] = set()
        lines = text.splitlines()

        # Pass 1: detailed linker section (full, untruncated names).
        pending_name: str | None = None
        pending_section: str | None = None
        for line in lines:
            m = _DETAIL_RE.match(line)
            if m:
                name = m.group("name")
                section = m.group("section")
                if m.group("addr"):
                    # Name + addr on same line
                    addr = int(m.group("addr"), 16)
                    size = int(m.group("size"), 16)
                    if addr not in seen_addrs:
                        symbols.append(Symbol(name, addr, size, section))
                        seen_addrs.add(addr)
                    pending_name = None
                else:
                    # Name only - addr on next line
                    pending_name = name
                    pending_section = section
                continue
            if pending_name is not None:
                m = _DETAIL_ADDR_RE.match(line)
                if m:
                    addr = int(m.group("addr"), 16)
                    size = int(m.group("size"), 16)
                    if addr not in seen_addrs:
                        symbols.append(Symbol(
                            pending_name, addr, size, pending_section or "",
                        ))
                        seen_addrs.add(addr)
                pending_name = None
                pending_section = None
                continue

        # Pass 2: summary section (fallback for any addresses not yet seen).
        for line in lines:
            m = _SUMMARY_RE.match(line)
            if m:
                addr = int(m.group("addr"), 16)
                if addr not in seen_addrs:
                    symbols.append(Symbol(
                        name=m.group("name"),
                        addr=addr,
                        size=int(m.group("size"), 16),
                        section=m.group("section"),
                    ))
                    seen_addrs.add(addr)
                continue

        # Pass 3: global symbols (address + name, no size).
        for line in lines:
            m = _GLOBAL_RE.match(line)
            if m:
                addr = int(m.group("addr"), 16)
                if addr not in seen_addrs:
                    symbols.append(Symbol(
                        name=m.group("name"),
                        addr=addr,
                        size=0,
                        section="global",
                    ))
                    seen_addrs.add(addr)

        return cls(symbols, path)

    def __len__(self) -> int:
        return len(self.symbols)

    def lookup(self, addr: int) -> Symbol | None:
        """Find the symbol containing an address (binary search).

        Args:
            addr: Integer address to look up.

        Returns:
            The Symbol whose range contains addr, or None.
        """
        lo, hi = 0, len(self.symbols) - 1
        result: Symbol | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            sym = self.symbols[mid]
            if sym.addr <= addr:
                result = sym
                lo = mid + 1
            else:
                hi = mid - 1
        if result is not None and (result.contains(addr) or result.addr == addr):
            return result
        return None

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
        exact = [s for s in self.symbols if s.name == pattern]
        if exact:
            return exact
        # 2. Convert glob-style wildcards to regex, then try as regex
        rx_str = pattern
        if "*" in pattern or "?" in pattern:
            # Glob → regex: escape everything except * and ?
            rx_str = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
            rx_str = f"^{rx_str}$"
        try:
            rx = re.compile(rx_str, re.IGNORECASE)
            matches = [s for s in self.symbols if rx.search(s.name)]
            if matches:
                return matches
        except re.error:
            pass
        # 3. Plain substring fallback
        pat = pattern.lower()
        return [s for s in self.symbols if pat in s.name.lower()]

    def stats(self) -> dict[str, int]:
        """Return symbol counts by section type."""
        counts: dict[str, int] = {}
        for s in self.symbols:
            counts[s.section] = counts.get(s.section, 0) + 1
        return counts


def parse_address(text: str) -> int | None:
    """Parse a user-provided address string (hex or decimal).

    Accepts: 0xFFFF, 0XFFFF, FFFFh, FFFF (if all hex digits), or
    plain decimal like 12345.

    Args:
        text: User input string.

    Returns:
        Integer address, or None if unparseable.
    """
    s = text.strip()
    if not s:
        return None
    # 0x prefix
    if s.lower().startswith("0x"):
        try:
            return int(s, 16)
        except ValueError:
            return None
    # Trailing 'h' suffix (assembly convention)
    if s.lower().endswith("h") and len(s) > 1:
        try:
            return int(s[:-1], 16)
        except ValueError:
            pass
    # All hex digits (4+ chars to avoid treating small decimals as hex)
    if len(s) >= 4 and all(c in "0123456789abcdefABCDEF" for c in s):
        try:
            return int(s, 16)
        except ValueError:
            pass
    # Plain decimal
    try:
        return int(s)
    except ValueError:
        return None


def format_symbol(sym: Symbol, query_addr: int | None = None) -> str:
    """Format a symbol for display.

    Args:
        sym: Symbol to format.
        query_addr: If provided, shows offset from symbol start.

    Returns:
        Formatted string like "0x1234  main +0x10  [code 442 bytes]"
    """
    offset = ""
    if query_addr is not None and query_addr != sym.addr:
        off = query_addr - sym.addr
        offset = f" +0x{off:X}"
    size_str = f" {sym.size} bytes" if sym.size else ""
    return (
        f"0x{sym.addr:08X}  {sym.name}{offset}"
        f"  [{sym.section_label}{size_str}]"
    )
