"""Example plugin: PIC compiler map file address lookup.

A complex plugin example demonstrating:

  - Multiple subcommands with nesting (map.path, map.path.clear)
  - Required and optional arguments (<address>, {path})
  - Persistent per-config storage via ctx.plugin_cfg()
  - Lifecycle hooks (on_app_start, on_config_load for auto-loading)
  - Plugin namespace for session state (ctx.ns() for caching loaded data)
  - File change detection (mtime-based auto-reload)
  - Search with glob, regex, and substring fallback

To use: copy this file to termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config).  The parser module
(termapy.pic_map) is included in the termapy package.

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

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

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
    from termapy import pic_map

    resolved = _resolve_map_path(ctx)
    if resolved is None:
        return None
    try:
        mf = pic_map.MapFile.from_file(resolved)
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
    from termapy import pic_map

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
        new_mf = pic_map.MapFile.from_file(mf.path)
    except OSError:
        return
    _set_map(ctx, new_mf)
    ctx.io.output(f"Reloaded {len(new_mf)} symbols (file changed)", "dim")


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Lookup an address in the loaded map file."""
    from termapy import pic_map

    arg = args.strip()
    if not arg:
        prefix = ctx.engine.prefix
        ctx.io.output(f"Usage: {prefix}map 0xFFFF | {prefix}map.search main")
        ctx.io.output(f"  {prefix}map.path <path>   -- set map file (remembered)")
        ctx.io.output(f"  {prefix}map.load {{path}}  -- load/reload map file")
        resolved = _resolve_map_path(ctx)
        ctx.io.output(f"  Current: {resolved or '(none)'}")
        return CmdResult.ok()

    _check_reload(ctx)
    mf = _get_map(ctx)
    if mf is None:
        mf = _auto_load(ctx)
    if mf is None:
        ctx.io.output("No map file loaded. Use /map.load <path>", "yellow")
        return CmdResult.fail(msg="No map file loaded")

    addr = pic_map.parse_address(arg)
    if addr is not None:
        sym = mf.lookup(addr)
        if sym is None:
            ctx.io.output(f"0x{addr:08X}  -- no symbol found", "yellow")
            return CmdResult.ok()
        text = pic_map.format_symbol(sym, addr)
        ctx.io.output(f"  {text}")
        return CmdResult.ok(value=sym.name)

    # Not an address -- treat as a name search.
    # Exact match returns only that symbol; otherwise substring search.
    exact = [s for s in mf.symbols if s.name == arg]
    if exact:
        for sym in exact:
            ctx.io.output(f"  {pic_map.format_symbol(sym)}")
        return CmdResult.ok(value=exact[0].name)
    matches = mf.search(arg)
    if not matches:
        ctx.io.output(f"No symbol matching '{arg}'", "yellow")
        return CmdResult.ok()
    for sym in matches:
        ctx.io.output(f"  {pic_map.format_symbol(sym)}")
    return CmdResult.ok(value=matches[0].name)


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Load a PIC map file. Uses the default path if none given."""
    from termapy import pic_map

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
        mf = pic_map.MapFile.from_file(path)
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
    from termapy import pic_map

    try:
        mf = pic_map.MapFile.from_file(resolved)
    except OSError as e:
        return CmdResult.fail(msg=f"Path saved but cannot read: {e}")
    _set_map(ctx, mf)
    ctx.io.output(f"Map path saved. Loaded {len(mf)} symbols from {resolved.name}", "green")
    return CmdResult.ok()


def _handler_path_clear(ctx: PluginContext, args: str) -> CmdResult:
    """Clear the saved map file path."""
    pcfg = ctx.plugin_cfg(_PLUGIN_NAME)
    pcfg.pop("map_path", None)
    pcfg.save()
    _set_map(ctx, None)
    ctx.io.output("Map path cleared.", "green")
    return CmdResult.ok()


def _handler_unload(ctx: PluginContext, args: str) -> CmdResult:
    """Unload the current map file."""
    if _get_map(ctx) is None:
        ctx.io.output("No map file loaded.", "yellow")
        return CmdResult.ok()
    _set_map(ctx, None)
    ctx.io.output("Map file unloaded.", "green")
    return CmdResult.ok()


def _handler_search(ctx: PluginContext, args: str) -> CmdResult:
    """Search symbols by name."""
    from termapy import pic_map

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
            ctx.io.output(f"  {pic_map.format_symbol(sym)}")
        return CmdResult.ok(value=exact[0].name)

    matches = mf.search(pattern)
    if not matches:
        ctx.io.output(f"No symbols matching '{pattern}'", "yellow")
        return CmdResult.ok()

    for sym in matches:
        ctx.io.output(f"  {pic_map.format_symbol(sym)}")
    return CmdResult.ok(value=matches[0].name)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show loaded map file stats."""
    _check_reload(ctx)
    mf = _get_map(ctx)
    if mf is None:
        mf = _auto_load(ctx)
    if mf is None:
        ctx.io.output("No map file loaded.", "yellow")
        return CmdResult.ok()

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
    return CmdResult.ok()


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
