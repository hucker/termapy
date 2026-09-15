"""Built-in plugin: /sym.* -- symbol table lookup, import, search.

The command surface only.  The table itself -- the model, the file format,
the address grammar, the converters, and the session namespace -- lives in
:mod:`termapy.symbols`, because the REPL engine auto-loads it on config
load and core must not import from ``builtins/``.  This file holds no
state: every handler reads and writes through ``session.get_table`` /
``session.set_table``.

Subcommands:

- ``/sym <addr|name>`` -- name -> address, or address -> name+offset.
- ``/sym.import <file> {format=...}`` -- convert a linker map to
  ``sym/<cfg>.symbols.json`` and load it.
- ``/sym.load {path}`` -- load the sidecar (default) or an explicit file.
- ``/sym.unload`` -- clear the loaded table.
- ``/sym.search <pattern>`` -- exact, glob, regex, or substring.
- ``/sym.info`` -- file, source, counts by section, address range.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from termapy.config import cfg_relative_path
from termapy.folders import SYM
from termapy.help_dynamic import compose, state_line
from termapy.plugins import (
    BoundaryException,
    CmdResult,
    Command,
    UsageError,
    format_kv_lines,
)
from termapy.plugins.params import ParamSpec
from termapy.symbols import (
    Symbol,
    SymbolTable,
    format_symbol,
    get_table,
    info_rows,
    lookup_record,
    make_recipe,
    make_witness,
    parse_address,
    set_table,
    sidecar_path,
    symbol_record,
    symbolic_name,
    table_record,
)
from termapy.symbols.converters import FORMATS, find_converter
from termapy.symbols.format import hex_addr

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── helpers ────────────────────────────────────────────────────────────────


def _bits(table: SymbolTable | None) -> int:
    return table.address_bits if table is not None else 32


def _display_path(ctx: PluginContext, path: Path | None) -> str:
    """Config-relative (``sym/demo.symbols.json``) inside the cfg folder, else as given.

    Keeps ``/sym.info`` deterministic in the CLI gold while still saying
    where an ad-hoc file lives.
    """
    if path is None:
        return "(in-memory)"
    if ctx.config_path:
        try:
            cfg_folder = Path(ctx.config_path).resolve().parent
            resolved = path.resolve()
            if resolved.is_relative_to(cfg_folder):
                return resolved.relative_to(cfg_folder).as_posix()
        except OSError:
            pass
    return str(path)


def _plugin_converters(ctx: PluginContext) -> tuple:
    """Converters the active config's plugin folders loaded (possibly none).

    ``getattr`` rather than a bare attribute: a hand-built test context
    may carry no internal handle at all, and a missing converter list
    means "built-ins only", not an error.
    """
    internal = getattr(ctx, "internal", None)
    return tuple(getattr(internal, "converters", ()) or ())


def _known_formats(ctx: PluginContext) -> list[str]:
    """Built-in format names plus any the plugin layer added, for errors."""
    extra = [spec.format for spec in _plugin_converters(ctx)]
    return list(FORMATS) + [name for name in extra if name not in FORMATS]


def _anchor(ctx: PluginContext, raw: str) -> Path:
    """A user path, relative ones resolved against the cfg folder.

    ``guard_external_path`` admits any in-sandbox relative path; this is
    what makes it mean "relative to the config", not to the process CWD
    (which under the MCP server is wherever the client started it).
    """
    return cfg_relative_path(ctx.config_path, raw)


# ── handlers ───────────────────────────────────────────────────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Resolve one address token and report what lives at the result."""
    # Hand-rolled (see CLAUDE.md, Declarative Command Parameters): a params
    # positional renders as <target> -- params.py ignores hint for
    # positionals -- so the two-alternative synopsis <addr|name> cannot be
    # synthesized, and a trivial single-arg migration is net-zero.
    target = args.strip()
    if not target:
        raise UsageError()
    table = get_table(ctx)
    bits = _bits(table)
    try:
        parsed = parse_address(target, table)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    if parsed.suffix:
        # Reserved for bit/slice/field access; refusing beats silently
        # dropping what the user asked for.
        return CmdResult.fail(msg=f"Unsupported address suffix: .{parsed.suffix}")
    if parsed.symbol is not None and parsed.offset == 0:
        # The user NAMED this entry: show it, not whichever alias at the
        # same address the sorted lookup happens to land on.
        at: Symbol | None = parsed.symbol
    else:
        at = table.lookup(parsed.addr) if table is not None else None
    data = lookup_record(parsed, at, address_bits=bits)
    if at is None:
        ctx.io.output(f"{hex_addr(parsed.addr, bits)}  -- no symbol", "yellow")
        # Lookup ran, nothing there: an empty value lets a script tell
        # "nothing here" from "errored".
        return CmdResult.ok(value="", data=data)
    ctx.io.result(f"  {format_symbol(at, parsed.addr, address_bits=bits)}")
    # The value is the OTHER representation of what was typed.
    if parsed.symbol is not None:
        value = hex_addr(parsed.addr, bits)
    else:
        value = symbolic_name(at, parsed.addr)
    return CmdResult.ok(value=value, data=data)


def _handler_import(ctx: PluginContext, args: str) -> CmdResult:
    """Convert a linker map, write the cfg sidecar, install the table."""
    raw = str(ctx.arg("file"))
    fmt = ctx.arg("format") or ""
    # Reading an arbitrary path is a parse/existence oracle under MCP;
    # contain to the sandbox unless the operator opted out.
    ctx.fs.guard_external_path(raw, "Map path")
    path = _anchor(ctx, raw)
    if not path.is_file():
        return CmdResult.fail(msg=f"Map file not found: {raw}")
    dest = sidecar_path(ctx.config_path)
    if dest is None:
        return CmdResult.fail(msg="No config loaded.")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    spec = find_converter(fmt, text, extra=_plugin_converters(ctx))
    if spec is None:
        # Two different failures: a format= the registry doesn't have
        # (name the token the user typed) versus a map nothing claimed
        # (name the file).  format= is validated here rather than by an
        # enum param because plugin folders extend the registry at
        # runtime -- see the ParamSpec comment below.
        subject = fmt if fmt else path.name
        return CmdResult.fail(
            msg=f"Unknown map format: {subject} "
            f"(formats: {', '.join(_known_formats(ctx))})"
        )
    try:
        symbols = spec.convert(text)
    # A plugin converter is third-party code on the dispatch path: a bad
    # regex or a typo must name the converter, not crash the app.  The
    # built-ins are documented as never raising on odd input; this guard
    # is for the ones a user writes.
    except BoundaryException as e:
        return CmdResult.fail(msg=f"Converter error: {spec.format}: {e}")
    if not symbols:
        return CmdResult.fail(msg=f"No symbols found in {path.name} ({spec.format})")
    table = SymbolTable(
        symbols,
        path=dest,
        source=str(path),
        imported=datetime.now().isoformat(timespec="seconds"),
        # Provenance: what to re-run, and what the map looked like now.
        # The witness is taken AFTER the read, so a map rewritten during
        # the import reads as stale next time rather than in sync.
        recipe=make_recipe(spec.format),
        witness=make_witness(path),
    )
    # The sidecar is a generated artifact: re-importing after a rebuild is
    # the normal flow, so an existing file is overwritten without asking.
    try:
        table.save(dest)
    except OSError as e:
        return CmdResult.fail(msg=f"Write error: {e}")
    set_table(ctx, table)
    ctx.io.result(
        f"Imported {len(table)} symbols from {path.name} ({spec.format}) -> {dest.name}",
        "green",
    )
    counts = Counter(symbol.name for symbol in table.symbols)
    dups = sorted(name for name, count in counts.items() if count > 1)
    if dups:
        ctx.io.output(f"  duplicate names need name@file: {', '.join(dups[:8])}")
    data = table_record(table, file=str(dest)) | {"format": spec.format}
    return CmdResult.ok(value=str(len(table)), data=data)


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Load the cfg sidecar (bare) or an explicit symbols file."""
    raw = ctx.arg("path") or ""
    if raw:
        ctx.fs.guard_external_path(raw, "Symbols path")
        path = _anchor(ctx, raw)
        # A bare filename means "the one in sym/", where /sym.import writes
        # and a hand-written table belongs; anything with a separator is
        # taken as given (config-relative).
        if ctx.config_path and Path(raw).name == raw and not path.is_file():
            in_sym = Path(ctx.config_path).parent / SYM / raw
            if in_sym.is_file():
                path = in_sym
    else:
        default = sidecar_path(ctx.config_path)
        if default is None:
            return CmdResult.fail(msg="No config loaded.")
        path = default
    if not path.is_file():
        return CmdResult.fail(msg=f"Symbols not found: {raw or path}")
    try:
        table = SymbolTable.load(path)
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    except ValueError as e:
        return CmdResult.fail(msg=f"Parse error: {e}")
    set_table(ctx, table)
    ctx.io.result(f"Loaded {len(table)} symbols ({path.name})", "green")
    return CmdResult.ok(value=str(len(table)), data=table_record(table, file=str(path)))


def _handler_unload(ctx: PluginContext, args: str) -> CmdResult:
    """Clear the loaded table; the file is untouched."""
    table = get_table(ctx)
    if table is None:
        ctx.io.result("No symbols loaded.", "yellow")
        return CmdResult.ok(value="0")
    n = len(table)
    set_table(ctx, None)
    ctx.io.result(f"Unloaded symbols ({n}).", "green")
    return CmdResult.ok(value=str(n))


def _handler_search(ctx: PluginContext, args: str) -> CmdResult:
    """Search symbol names: exact, glob, regex, or substring."""
    # Hand-rolled: trivial single-arg (CLAUDE.md rates the migration
    # net-zero); a params positional would render <pattern> identically.
    pattern = args.strip()
    if not pattern:
        raise UsageError()
    table = get_table(ctx)
    if table is None:
        return CmdResult.fail(msg="No symbols loaded.")
    matches = table.search(pattern)
    if not matches:
        ctx.io.output(f"No symbols matching '{pattern}'", "yellow")
        return CmdResult.ok(value="", data=[])
    bits = table.address_bits
    if ctx.wants_data:
        return CmdResult.ok(
            value=matches[0].name,
            data=[symbol_record(symbol, address_bits=bits) for symbol in matches],
        )
    for symbol in matches:
        ctx.io.output(f"  {format_symbol(symbol, address_bits=bits)}")
    return CmdResult.ok(value=matches[0].name)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show the loaded table's provenance and shape."""
    table = get_table(ctx)
    if table is None:
        ctx.io.result(
            "No symbols loaded.  /sym.import <map> or /sym.load {path} to load one.",
            "yellow",
        )
        return CmdResult.ok(value="")
    file = _display_path(ctx, table.path)
    for line in format_kv_lines(info_rows(table, file=file)):
        ctx.io.output_markup(line)
    return CmdResult.ok(value=str(len(table)), data=table_record(table, file=file))


# ── Dynamic long_help ─────────────────────────────────────────────────────────


_GRAMMAR_HELP: Final[str] = (
    "Symbols come from your linker map: /sym.import <map> converts it to\n"
    "sym/<cfg>.symbols.json in the config folder (a generated file, overwritten on\n"
    "re-import) and loads it.  The sidecar auto-loads whenever the config\n"
    "loads -- TUI, CLI and MCP alike.\n"
    "\n"
    "Address forms:\n"
    "  0x1000         hex\n"
    "  1000h          hex (assembly suffix)\n"
    "  1000           DECIMAL -- bare hex is never guessed\n"
    "  main           symbol name (exact, case-sensitive)\n"
    "  main+0x10      symbol plus offset (also main-4)\n"
    "  tick@adc.c     duplicate static, qualified by file (adc or src/adc.c too)\n"
    "  name.<bit>     reserved for bit/slice/field access; an exact symbol\n"
    "                 name wins, so count.12 is the symbol count.12\n"
    "\n"
    "Search forms (/sym.search): exact name, glob (*main*, Mon*), regex\n"
    "(^Mon), or a case-insensitive substring.\n"
    "\n"
    "Commands:\n"
    "  /sym <addr|name>        - name -> address, or address -> name+offset\n"
    "  /sym.import <map>       - convert a linker map and load it\n"
    "  /sym.load {path}        - reload the sidecar, or load an explicit file\n"
    "  /sym.unload             - clear the loaded table (file untouched)\n"
    "  /sym.search <pattern>   - search names\n"
    "  /sym.info               - file, source, counts by section, range"
)


def _long_help(ctx: PluginContext) -> str:
    table = get_table(ctx)
    state = (
        f"{len(table)} ({_display_path(ctx, table.path)})" if table is not None else "none"
    )
    return compose(state_line("symbols", state), _GRAMMAR_HELP)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="sym",
    args="<addr|name>",
    help="Symbol table: name -> address, or address -> name+offset.",
    long_help=_long_help,
    handler=_handler_root,
    sub_commands={
        "import": Command(
            params=[
                ParamSpec(
                    "file", "path", positional=True, required=True, rest=True,
                    help="linker map to convert",
                ),
                # A str, not an enum: plugin folders add converters at
                # runtime (a per-config format is a board's own pipeline),
                # so the valid set is not knowable at import.  The handler
                # validates against built-ins PLUS the loaded plugin
                # converters and names them all on a miss.
                ParamSpec(
                    "format", "str",
                    help=f"map format ({', '.join(FORMATS)}, or a plugin "
                         "converter); omitted = sniff the file",
                ),
            ],
            help="Convert a linker map to sym/<cfg>.symbols.json and load it.",
            handler=_handler_import,
        ),
        "load": Command(
            params=[
                ParamSpec(
                    "path", "path", positional=True, rest=True,
                    help="symbols JSON file (default: the cfg's sidecar)",
                ),
            ],
            help="Load a symbol table (default: sym/<cfg>.symbols.json, the file /sym.import writes).",
            handler=_handler_load,
        ),
        "unload": Command(
            help="Clear the loaded symbol table.",
            handler=_handler_unload,
        ),
        "search": Command(
            args="<pattern>",
            help="Search symbols by name: exact, glob (*main*), regex (^Mon), or substring.",
            handler=_handler_search,
        ),
        "info": Command(
            help="Show the loaded symbol table: file, source, counts by section, address range.",
            handler=_handler_info,
        ),
    },
)
