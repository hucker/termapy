"""Microchip XC32 (GNU ld) linker-map converter -- the only home of XC32 knowledge.

Three passes over the map text, first sighting of an address wins:

1. The detailed ``Linker script and memory map`` section: ``.text.Name%123``
   blocks carry full, untruncated names, with the address and size on the
   same line or the next.  The input-section line that follows each block
   names the object file the symbol came from (same-line form
   `` .bss.sState    0x200062f2  0x1 build/src/SMBlink.c.o`` or next-line
   form after `` .text.decfloat``), which becomes ``Symbol.file`` so
   ``name@file`` can tell duplicate statics apart.
2. The ``Memory-Usage Report`` summary section: truncated names, used only
   for addresses pass 1 did not see.
3. Linker globals (``0x20005a58  sCal``): size 0, section ``global``.

GCC merged string-literal pools (``.rodata.str1.1``) are skipped in passes
1 and 2: they are not symbols, and a C program cannot name one ``str1.1``.
Dotted names (``count.12``, ``sPacket.1``) are kept verbatim.

Known limits (converter-only changes if they ever matter): address-level
dedup drops aliases at the same address; ``file`` stays empty when a map
layout puts no object line after a block.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PureWindowsPath
from typing import Final

from termapy.symbols.table import Symbol

FORMAT: Final[str] = "xc32"
DESCRIPTION: Final[str] = "Microchip XC32 (GNU ld) linker map"

# The summary-report header (line ~312 of a real map, ~49 KB in) OR the
# toolchain path in the archive/LOAD lines (byte 85 of the same map).  The
# generic GNU ld line "Linker script and memory map" is deliberately NOT a
# marker -- it would collide with a future gcc-map converter.
DETECT: Final[tuple[str, ...]] = ("Microchip PIC32 Memory-Usage Report", "/xc32/")

# Summary section (truncated names):
#   .text.FunctionName      0x12345   0x100   256
_SUMMARY_RE: Final = re.compile(
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
_DETAIL_RE: Final = re.compile(
    r"^\.(?P<section>text|bss|data|rodata)\."
    r"(?P<name>[^%\s]+)"
    r"%\d+"
    r"(?:\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+))?"
)

# Continuation line with address + size (follows a name-only detail line):
#                 0x00016c1e       0xd4
_DETAIL_ADDR_RE: Final = re.compile(
    r"^\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+)\s*$"
)

# Global symbols from the linker map section:
#                 0x20005a58                sCal
_GLOBAL_RE: Final = re.compile(
    r"^\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<name>[a-zA-Z_]\w+)\s*$"
)

# Input-section line after a %NNN block (leading single space).  The object
# path is on the same line in one form and on the next line in the other.
_INPUT_SECTION_RE: Final = re.compile(
    r"^ \.(?:text|bss|data|rodata)\.\S+"
    r"(?:\s+0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+\s+(?P<obj>\S.*))?$"
)

# Continuation line carrying the object path (next-line form):
#                 0x000055e4      0x820 .../libg-musl.a(floatscan.o)
_OBJ_LINE_RE: Final = re.compile(
    r"^\s+0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+\s+(?P<obj>\S.*)$"
)

# GCC merged string-literal pools: .rodata.str1.1, .rodata.str1.4 ...
_LITERAL_POOL_RE: Final = re.compile(r"^str\d+\.\d+$")

_ARCHIVE_MEMBER_RE: Final = re.compile(r"^.*\((?P<member>[^()]+)\)$")


def object_file_stem(obj: str) -> str:
    """Reduce an object path from the map to a short file name.

    ``CMakeFiles/x.dir/src/SMBlink.c.o`` -> ``SMBlink.c``; ``Monitor.o`` ->
    ``Monitor``; ``libc.a(memcmp.o)`` -> ``memcmp``.  This is map TEXT, not
    an OS path: a map written on either OS may use either separator, and
    ``PureWindowsPath`` treats both as separators.

    Args:
        obj: The object path as printed in the map.

    Returns:
        The member/file name with one trailing ``.o`` removed.
    """
    name = PureWindowsPath(obj.strip()).name
    member = _ARCHIVE_MEMBER_RE.match(name)
    if member:
        name = member.group("member")
    if name.endswith(".o"):
        name = name[:-2]
    return name


def convert(text: str) -> list[Symbol]:
    """Parse XC32 map text into symbols (unsorted; ``SymbolTable`` sorts).

    Args:
        text: The whole map file.

    Returns:
        The symbols found; empty when nothing matched.  Never raises on
        odd input.
    """
    symbols: list[Symbol] = []
    seen_addrs: set[int] = set()
    lines = text.splitlines()

    # Pass 1: detailed linker section (full, untruncated names), each block
    # followed by the input-section line that names its object file.
    pending_name: str | None = None
    pending_section: str | None = None
    # Index of the symbol just appended, while its object line may still
    # follow; None once a blank line or an unrelated line ends the block.
    awaiting_obj: int | None = None
    for line in lines:
        m = _DETAIL_RE.match(line)
        if m:
            awaiting_obj = None
            name = m.group("name")
            section = m.group("section")
            if _LITERAL_POOL_RE.match(name):
                pending_name = None
                continue
            if m.group("addr"):
                # Name + addr on same line
                addr = int(m.group("addr"), 16)
                size = int(m.group("size"), 16)
                if addr not in seen_addrs:
                    symbols.append(Symbol(name, addr, size, section))
                    seen_addrs.add(addr)
                    awaiting_obj = len(symbols) - 1
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
                    awaiting_obj = len(symbols) - 1
            pending_name = None
            pending_section = None
            continue
        if awaiting_obj is not None:
            if not line.strip():
                awaiting_obj = None
                continue
            m = _INPUT_SECTION_RE.match(line)
            if m:
                if m.group("obj"):
                    symbols[awaiting_obj] = replace(
                        symbols[awaiting_obj], file=object_file_stem(m.group("obj")),
                    )
                    awaiting_obj = None
                # else: next-line form -- the object path follows.
                continue
            m = _OBJ_LINE_RE.match(line)
            if m:
                symbols[awaiting_obj] = replace(
                    symbols[awaiting_obj], file=object_file_stem(m.group("obj")),
                )
            awaiting_obj = None

    # Pass 2: summary section (fallback for any addresses not yet seen).
    for line in lines:
        m = _SUMMARY_RE.match(line)
        if m:
            if _LITERAL_POOL_RE.match(m.group("name")):
                continue
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

    return symbols
