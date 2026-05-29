"""Example plugin: PIC compiler map file address lookup.

A complex plugin example demonstrating:

  - Multiple subcommands with nesting (map.path, map.path.clear)
  - Required and optional arguments (<address>, {path})
  - Persistent per-config storage via ctx.plugin_cfg()
  - Lifecycle hooks (on_app_start, on_config_load for auto-loading)
  - Plugin namespace for session state (ctx.ns() for caching loaded data)
  - File change detection (mtime-based auto-reload)
  - Search with glob, regex, and substring fallback

Self-contained: the GCC/XC32 linker-map parser (``MapFile``, ``Symbol``,
``parse_address``, ``format_symbol``) is included inline -- earlier
versions imported it from ``termapy.pic_map`` but that module was a
device-specific helper that didn't belong in the package root.

To use: copy this file to termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config).

Usage:
    /map <address>          Lookup 0xFFFF, FFFFh, or decimal
    /map.path <path>        Set the map file path (remembered across sessions)
    /map.path               Show the configured path
    /map.path.clear         Clear the saved path
    /map.load {path}        Load (or reload) a map file
    /map.search <pattern>   Search symbols by name (glob/regex/substring)
    /map.info               Show loaded map stats
    /map.unload             Unload the current map file
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Parser (formerly termapy.pic_map; inlined 2026-05-15) ─────────────────


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


# ── Plugin state ────────────────────────────────────────────────────────────


# Map file is stored in the plugin namespace so it persists across calls.
_NS_KEY = "pic_map"

_MAP_FILENAME = "mem.map"
_PLUGIN_NAME = "pic_map"


# ── Map file resolution ────────────────────────────────────────────────────


def _resolve_map_path(ctx: PluginContext) -> Path | None:
    """Find the map file: saved config path > plugin/mem.map > builtin.

    Returns the first path that exists, or None.
    """
    # 1. Configured path from plugin config
    saved = ""
    if ctx.config_path:
        saved = ctx.plugin_cfg(_PLUGIN_NAME).get("map_path", "")
    if saved:
        p = Path(saved)
        if p.exists():
            return p

    # 2. mem.map in the config's plugin folder
    if ctx.config_path:
        cfg_plugin = Path(ctx.config_path).parent / "plugin" / _MAP_FILENAME
        if cfg_plugin.exists():
            return cfg_plugin

    # 3. Bundled mem.map next to this plugin file
    bundled = Path(__file__).parent / _MAP_FILENAME
    if bundled.exists():
        return bundled

    return None


def _get_map(ctx: PluginContext):
    """Get the loaded MapFile from namespace, or None."""
    return ctx.ns(_NS_KEY).get("map_file")


def _auto_load(ctx: PluginContext):
    """Try to auto-load the map file. Returns MapFile or None."""
    resolved = _resolve_map_path(ctx)
    if resolved is None:
        return None
    try:
        mf = MapFile.from_file(resolved)
    except OSError:
        return None
    _set_map(ctx, mf)
    ctx.io.output(f"Auto-loaded {len(mf)} symbols from {resolved.name}", "dim")
    return mf


def _set_map(ctx: PluginContext, mf):
    """Store a MapFile in the namespace, along with the file's mtime."""
    ns = ctx.ns(_NS_KEY)
    ns["map_file"] = mf
    if mf is not None and mf.path:
        try:
            ns["map_mtime"] = Path(mf.path).stat().st_mtime
        except OSError:
            ns["map_mtime"] = None
    else:
        ns["map_mtime"] = None


def _check_reload(ctx: PluginContext):
    """Reload the map file if it changed on disk since last load."""
    ns = ctx.ns(_NS_KEY)
    mf = ns.get("map_file")
    if mf is None or mf.path is None:
        return
    prev_mtime = ns.get("map_mtime")
    try:
        cur_mtime = Path(mf.path).stat().st_mtime
    except OSError:
        return
    if prev_mtime is not None and cur_mtime == prev_mtime:
        return
    try:
        new_mf = MapFile.from_file(mf.path)
    except OSError:
        return
    _set_map(ctx, new_mf)
    ctx.io.output(f"Reloaded {len(new_mf)} symbols (file changed)", "dim")


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Lookup an address in the loaded map file."""
    arg = args.strip()
    if not arg:
        prefix = ctx.prefix
        ctx.io.output(f"Usage: {prefix}map 0xFFFF | {prefix}map.search main")
        ctx.io.output(f"  {prefix}map.path <path>   -- set map file (remembered)")
        ctx.io.output(f"  {prefix}map.load {{path}}  -- load/reload map file")
        resolved = _resolve_map_path(ctx)
        ctx.io.output(f"  Current: {resolved or '(none)'}")
        return CmdResult.ok(value=str(resolved) if resolved else "")

    _check_reload(ctx)
    mf = _get_map(ctx)
    if mf is None:
        mf = _auto_load(ctx)
    if mf is None:
        ctx.io.output("No map file loaded. Use /map.load <path>", "yellow")
        return CmdResult.fail(msg="No map file loaded")

    addr = parse_address(arg)
    if addr is not None:
        sym = mf.lookup(addr)
        if sym is None:
            ctx.io.output(f"0x{addr:08X}  -- no symbol found", "yellow")
            # No symbol at this address; empty value lets scripts
            # distinguish "lookup ran, nothing here" from "errored."
            return CmdResult.ok(value="")
        text = format_symbol(sym, addr)
        ctx.io.output(f"  {text}")
        return CmdResult.ok(value=sym.name)

    # Not an address -- treat as a name search.
    # Exact match returns only that symbol; otherwise substring search.
    exact = [s for s in mf.symbols if s.name == arg]
    if exact:
        for sym in exact:
            ctx.io.output(f"  {format_symbol(sym)}")
        return CmdResult.ok(value=exact[0].name)
    matches = mf.search(arg)
    if not matches:
        ctx.io.output(f"No symbol matching '{arg}'", "yellow")
        return CmdResult.ok(value="")
    for sym in matches:
        ctx.io.output(f"  {format_symbol(sym)}")
    return CmdResult.ok(value=matches[0].name)


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Load a PIC map file. Uses the default path if none given."""
    raw = args.strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute() and ctx.config_path:
            cfg_candidate = Path(ctx.config_path).parent / "plugin" / raw
            if cfg_candidate.exists():
                p = cfg_candidate
        if not p.exists():
            return CmdResult.fail(msg=f"File not found: {p}")
        path = str(p)
    else:
        resolved = _resolve_map_path(ctx)
        if resolved is None:
            return CmdResult.fail(
                msg="No map file found. Use /map.path <path> to configure."
            )
        path = str(resolved)

    try:
        mf = MapFile.from_file(path)
    except OSError as e:
        return CmdResult.fail(msg=f"Cannot read: {e}")

    _set_map(ctx, mf)
    stats = mf.stats()
    total = len(mf)
    parts = [f"{v} {k}" for k, v in sorted(stats.items())]
    ctx.io.output(f"Loaded {total} symbols ({', '.join(parts)})", "green")
    return CmdResult.ok(value=str(total))


def _handler_path(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the saved map file path."""
    arg = args.strip()

    if not arg:
        # Show current
        pcfg = ctx.plugin_cfg(_PLUGIN_NAME)
        saved = pcfg.get("map_path", "")
        if saved:
            exists = Path(saved).exists()
            status = "" if exists else " (file not found)"
            ctx.io.output(f"  {saved}{status}")
        else:
            ctx.io.output("  No map path configured. Use /map.path <path>")
        return CmdResult.ok(value=saved)

    # Set path
    p = Path(arg)
    if not p.exists():
        return CmdResult.fail(msg=f"File not found: {p}")

    resolved = p.resolve()
    pcfg = ctx.plugin_cfg(_PLUGIN_NAME)
    pcfg["map_path"] = str(resolved)
    pcfg.save()

    # Load immediately so the user can start using it
    try:
        mf = MapFile.from_file(resolved)
    except OSError as e:
        return CmdResult.fail(msg=f"Path saved but cannot read: {e}")
    _set_map(ctx, mf)
    ctx.io.output(f"Map path saved. Loaded {len(mf)} symbols from {resolved.name}", "green")
    return CmdResult.ok(value=resolved)


def _handler_path_clear(ctx: PluginContext, args: str) -> CmdResult:
    """Clear the saved map file path."""
    pcfg = ctx.plugin_cfg(_PLUGIN_NAME)
    pcfg.pop("map_path", None)
    pcfg.save()
    _set_map(ctx, None)
    ctx.io.output("Map path cleared.", "green")
    return CmdResult.ok(value="")


def _handler_unload(ctx: PluginContext, args: str) -> CmdResult:
    """Unload the current map file."""
    if _get_map(ctx) is None:
        ctx.io.output("No map file loaded.", "yellow")
        return CmdResult.ok(value="")
    _set_map(ctx, None)
    ctx.io.output("Map file unloaded.", "green")
    return CmdResult.ok(value="")


def _handler_search(ctx: PluginContext, args: str) -> CmdResult:
    """Search symbols by name."""
    pattern = args.strip()
    if not pattern:
        ctx.io.output("Usage: /map.search <pattern>", "yellow")
        return CmdResult.fail(msg="No pattern given")

    _check_reload(ctx)
    mf = _get_map(ctx)
    if mf is None:
        mf = _auto_load(ctx)
    if mf is None:
        ctx.io.output("No map file loaded. Use /map.load <path>", "yellow")
        return CmdResult.fail(msg="No map file loaded")

    # Exact match takes priority over substring hits
    exact = [s for s in mf.symbols if s.name == pattern]
    if exact:
        for sym in exact:
            ctx.io.output(f"  {format_symbol(sym)}")
        return CmdResult.ok(value=exact[0].name)

    matches = mf.search(pattern)
    if not matches:
        ctx.io.output(f"No symbols matching '{pattern}'", "yellow")
        return CmdResult.ok(value="")

    for sym in matches:
        ctx.io.output(f"  {format_symbol(sym)}")
    return CmdResult.ok(value=matches[0].name)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show loaded map file stats."""
    _check_reload(ctx)
    mf = _get_map(ctx)
    if mf is None:
        mf = _auto_load(ctx)
    if mf is None:
        ctx.io.output("No map file loaded.", "yellow")
        return CmdResult.ok(value="")

    w = 10
    ctx.io.output(f"  {'File':>{w}}: {mf.path or '(unknown)'}")
    ctx.io.output(f"  {'Symbols':>{w}}: {len(mf)}")
    stats = mf.stats()
    label = {
        "text": "code",
        "rodata": "const",
        "data": "data",
        "bss": "bss",
        "global": "global",
    }
    for section in ("text", "rodata", "data", "bss", "global"):
        count = stats.get(section, 0)
        if count:
            ctx.io.output(f"  {label.get(section, section):>{w}}: {count}")
    if mf.symbols:
        first = mf.symbols[0]
        last = mf.symbols[-1]
        ctx.io.output(f"  {'Range':>{w}}: 0x{first.addr:08X} - 0x{last.end:08X}")
    # Return total symbol count so scripts can verify map load size.
    return CmdResult.ok(value=str(len(mf)))


# ── Lifecycle ───────────────────────────────────────────────────────────────


def _try_auto_load(ctx: PluginContext) -> None:
    """Auto-load the map file if one is configured or present.

    Only loads when there is a saved path in pic_map.cfg or a
    mem.map in the config's plugin folder.  The bundled fallback is
    not loaded automatically -- it is only used on-demand.
    """
    if not ctx.config_path:
        return
    # Check saved config path
    saved = ctx.plugin_cfg(_PLUGIN_NAME).get("map_path", "")
    if saved and Path(saved).exists():
        _auto_load(ctx)
        return
    # Check config plugin folder
    cfg_plugin = Path(ctx.config_path).parent / "plugin" / _MAP_FILENAME
    if cfg_plugin.exists():
        _auto_load(ctx)


def on_app_start(ctx: PluginContext) -> None:
    """Auto-load the map file at startup."""
    _try_auto_load(ctx)


def on_config_load(ctx: PluginContext) -> None:
    """Re-load the map file when the config changes."""
    _set_map(ctx, None)
    _try_auto_load(ctx)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="map",
    args="<address>",
    help="PIC map file address lookup (e.g. /map 0x1234, /map.search main).",
    handler=_handler_root,
    sub_commands={
        "path": Command(
            args="{path}",
            help="Show or set the saved map file path.",
            handler=_handler_path,
            sub_commands={
                "clear": Command(
                    help="Clear the saved map file path.",
                    handler=_handler_path_clear,
                ),
            },
        ),
        "load": Command(
            args="{path}",
            help="Load (or reload) a map file.",
            handler=_handler_load,
        ),
        "unload": Command(
            help="Unload the current map file.",
            handler=_handler_unload,
        ),
        "search": Command(
            args="<pattern>",
            help="Search symbols by name (case-insensitive substring).",
            handler=_handler_search,
        ),
        "info": Command(
            help="Show loaded map file statistics.",
            handler=_handler_info,
        ),
    },
)
