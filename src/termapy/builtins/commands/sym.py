"""Built-in plugin: /sym.* -- symbol table lookup, import, search.

The command surface only.  The table itself -- the model, the file format,
the address grammar, the converters, and the session namespace -- lives in
:mod:`termapy.symbols`, because the REPL engine auto-loads it on config
load and core must not import from ``builtins/``.  This file holds no
state: every handler reads through ``session.get_table`` (the merged view)
or ``get_build_table`` and writes through ``session.install_build``.

Subcommands:

- ``/sym <addr|name>`` -- name -> address, or address -> name+offset.
- ``/sym.import <file> {format=...}`` -- convert a build file (an ELF or
  a linker map) to ``sym/<cfg>.symbols.json`` and load it.
- ``/sym.load {path}`` -- load the sidecar (default) or an explicit file.
- ``/sym.unload`` -- clear the loaded table.
- ``/sym.search <pattern>`` -- the exact name, else a name pattern.
- ``/sym.info`` -- file, source, counts by section, address range.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from termapy import diag_log
from termapy.config import cfg_relative_path, not_found_message
from termapy.converters import SYMBOLS, read_source
from termapy.folders import SYM, SYMBOLS_SUFFIX, default_file_name, file_suggestion, is_inside
from termapy.help_dynamic import compose, state_line
from termapy.plugins import (
    BoundaryException,
    CapabilitySet,
    CmdResult,
    Command,
    UsageError,
)
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.plugins.params import ParamSpec
from termapy.symbols import (
    Layout,
    Symbol,
    SymbolTable,
    format_symbol,
    get_build_table,
    get_table,
    info_rows,
    install_build,
    lookup_record,
    make_recipe,
    make_witness,
    parse_address,
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
            if is_inside(path, cfg_folder):
                return path.resolve().relative_to(cfg_folder).as_posix()
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
    specs = getattr(internal, "converters", ()) or ()
    # The engine holds every KIND in one list; a device converter must
    # never be offered as a map format.
    return tuple(spec for spec in specs if spec.kind == SYMBOLS)


def _known_formats(ctx: PluginContext) -> list[str]:
    """Built-in format names plus any the plugin layer added, for errors."""
    extra = [spec.format for spec in _plugin_converters(ctx)]
    return list(FORMATS) + [name for name in extra if name not in FORMATS]


def _recorded_import(ctx: PluginContext) -> tuple[str, str] | None:
    """``(source, format)`` the loaded table was built from, or None.

    The format comes from the recipe when there is one; a table imported
    before recipes existed still has a ``source``, and re-importing it
    sniffs the map exactly as the first import did -- which is also how
    such a table EARNS a recipe.  A hand-written table whose source is
    prose (``"demo firmware"``) has no readable file, so the caller's
    existence check refuses it with the normal not-found error.
    """
    table = get_build_table(ctx)
    if table is None:
        return None
    source = (table.source or "").strip()
    if not source:
        return None
    recipe = table.recipe or {}
    converter = recipe.get("converter")
    return source, converter if isinstance(converter, str) else ""


def _anchor(ctx: PluginContext, raw: str, ext: str = "") -> Path:
    """A user path, relative ones resolved against the cfg folder.

    ``guard_external_path`` admits any in-sandbox relative path; this is
    what makes it mean "relative to the config", not to the process CWD
    (which under the MCP server is wherever the client started it).
    ``ext`` is an extension the typed name may leave off.
    """
    return cfg_relative_path(ctx.config_path, raw, ext)


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


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
    """Convert a build file (an ELF or a linker map), write the sidecar, install the table.

    Bare, it re-runs the loaded table's own recipe against its own
    source -- the rebuild step after every compile, without retyping a
    build-tree path.  An explicit ``<file>`` always wins.
    """
    raw = str(ctx.arg("file") or "")
    fmt = ctx.arg("format") or ""
    if not raw:
        reused = _recorded_import(ctx)
        if reused is None:
            raise UsageError("no map given, and the loaded table records no source to re-import")
        raw, recorded_fmt = reused
        # An explicit format= still wins: re-importing with a different
        # converter is how you switch a table's pipeline.
        fmt = fmt or recorded_fmt
    # Reading an arbitrary path is a parse/existence oracle under MCP;
    # contain to the sandbox unless the operator opted out.
    ctx.fs.guard_external_path(raw, "Build file path")
    path = _anchor(ctx, raw)
    if not path.is_file():
        return CmdResult.fail(msg=not_found_message("Build file", raw, ctx.config_path))
    dest = sidecar_path(ctx.config_path)
    assert dest is not None, "sym.import declares config_loaded"
    try:
        data, text = read_source(path)
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    spec = find_converter(fmt, text, extra=_plugin_converters(ctx), data=data)
    if spec is None:
        # Two different failures: a format= the registry doesn't have
        # (name the token the user typed) versus a map nothing claimed
        # (name the file).  format= is validated here rather than by an
        # enum param because plugin folders extend the registry at
        # runtime -- see the ParamSpec comment below.
        subject = fmt if fmt else path.name
        return CmdResult.fail(
            msg=f"Unknown build format: {subject} (formats: {', '.join(_known_formats(ctx))})"
        )
    source = spec.source_of(data, text)
    try:
        symbols = spec.convert(source)
        layout = spec.layout(source) if spec.layout is not None else Layout()
    # A plugin converter is third-party code on the dispatch path: a bad
    # regex or a typo must name the converter, not crash the app.  The
    # built-ins are documented as never raising on odd input; this guard
    # is for the ones a user writes.
    except BoundaryException as e:
        diag_log.exception("symbol converter %s raised on %s", spec.format, path)
        return CmdResult.fail(
            msg=f"Converter error: {spec.format}: {type(e).__name__}: {e}{diag_log.traceback_note()}"
        )
    if not symbols:
        return CmdResult.fail(msg=f"No symbols found in {path.name} ({spec.format})")
    if not isinstance(layout, Layout):
        return CmdResult.fail(msg=f"Converter error: {spec.format}: layout() must return a Layout")
    table = SymbolTable(
        symbols,
        path=dest,
        source=str(path),
        imported=datetime.now().isoformat(timespec="seconds"),
        address_bits=layout.address_bits or 32,
        endian=layout.endian or "le",
        regions=layout.regions,
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
    # install_build, not a bare namespace write: the device registers the
    # config loaded must survive a re-import (they are not the build's).
    install_build(ctx, table)
    ctx.io.result(
        f"Imported {len(table)} symbols from {path.name} ({spec.format}) -> {dest.name}",
        "green",
    )
    counts = Counter(symbol.name for symbol in table.symbols)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        ctx.io.output(f"  duplicate names need name@file: {', '.join(duplicates[:8])}")
    data = table_record(table, file=str(dest)) | {"format": spec.format}
    return CmdResult.ok(value=str(len(table)), data=data)


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Load the cfg sidecar (bare) or an explicit symbols file.

    Both forms need a config, which the command declares: until 2026-10-16
    an explicit path loaded without one and only the bare form checked,
    inferring "no config" from ``sidecar_path`` returning None.
    """
    raw = ctx.arg("path") or ""
    if raw:
        ctx.fs.guard_external_path(raw, "Symbols path")
        path = _anchor(ctx, raw, SYMBOLS_SUFFIX)
        # A bare filename means "the one in sym/", where /sym.import writes
        # and a hand-written table belongs; anything with a separator is
        # taken as given (config-relative).  The .symbols.json is optional
        # either way (registry ``file-name-default-ext``).
        if Path(raw).name == raw and not path.is_file():
            in_sym = default_file_name(Path(ctx.config_path).parent / SYM / raw, SYMBOLS_SUFFIX)
            if in_sym.is_file():
                path = in_sym
    else:
        default = sidecar_path(ctx.config_path)
        assert default is not None, "sym.load declares config_loaded"
        path = default
    if not path.is_file():
        # A bare name means the one in sym/ (above); a path, its own folder.
        searched = Path(ctx.config_path).parent / SYM if Path(raw).name == raw else path.parent
        hint = file_suggestion(searched, raw, SYMBOLS_SUFFIX) if raw else ""
        return CmdResult.fail(msg=f"Symbols not found: {raw or path}{hint}")
    try:
        table = SymbolTable.load(path)
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    except ValueError as e:
        return CmdResult.fail(msg=f"Parse error: {e}")
    install_build(ctx, table)
    ctx.io.result(f"Loaded {len(table)} symbols ({path.name})", "green")
    return CmdResult.ok(value=str(len(table)), data=table_record(table, file=str(path)))


def _handler_unload(ctx: PluginContext, args: str) -> CmdResult:
    """Clear the build's table; the file is untouched.

    Device registers are the board's, not the build's, so they stay --
    and the message says so, because "unload" leaving symbols behind
    would otherwise read as a failure.
    """
    build = get_build_table(ctx)
    if build is None:
        ctx.io.result(STATE_MESSAGES["symbols_loaded"], "yellow")
        return CmdResult.ok(value="0")
    n = len(build)
    remaining = install_build(ctx, None)
    if remaining is None:
        ctx.io.result(f"Unloaded symbols ({n}).", "green")
    else:
        ctx.io.result(
            f"Unloaded build symbols ({n}); {len(remaining)} device registers remain.",
            "green",
        )
    return CmdResult.ok(value=str(n))


def _handler_search(ctx: PluginContext, args: str) -> CmdResult:
    """Search symbol names: the exact name, else a name pattern (``SymbolTable.search``)."""
    # Hand-rolled: trivial single-arg (CLAUDE.md rates the migration
    # net-zero); a params positional would render <pattern> identically.
    pattern = args.strip()
    if not pattern:
        raise UsageError()
    # needs=symbols_loaded: the gate answered a missing table already.
    table = get_table(ctx)
    matches = table.search(pattern) if table is not None else []
    if table is None or not matches:
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
    """Show the loaded table's provenance and shape.

    A record answer (registry ``record-one-format``): ``table_record`` is
    the ``data``, ``info_rows`` its prose twin.  Its ``loaded`` key is
    ``True``; with no table the data is ``{"loaded": False}`` beside the
    one-line answer, so an agent reads the state rather than the prose.
    """
    table = get_table(ctx)
    if table is None:
        ctx.io.result(
            f"{STATE_MESSAGES['symbols_loaded']}  {ctx.prefix}sym.import <map> or {ctx.prefix}sym.load {{path}} to load one.",
            "yellow",
        )
        return CmdResult.ok(value="", data={"loaded": False})
    file = _display_path(ctx, table.path)
    return CmdResult.record(
        {"loaded": True, **table_record(table, file=file)},
        info_rows(table, file=file),
        value=str(len(table)),
    )


# ── Dynamic long_help ─────────────────────────────────────────────────────────


_GRAMMAR_HELP: Final[str] = (
    "Symbols come from your build: {prefix}sym.import <file> converts its ELF (names,\n"
    "sizes and the C types in its debug info) or its linker map (names and\n"
    "sizes) to sym/<cfg>.symbols.json in the config folder (a generated file,\n"
    "overwritten on re-import) and loads it.  The sidecar auto-loads whenever\n"
    "the config loads -- TUI, CLI and MCP alike.\n"
    "\n"
    "Address forms:\n"
    "  0x1000         hex\n"
    "  1000h          hex (assembly suffix)\n"
    "  1000           DECIMAL -- bare hex is never guessed\n"
    "  main           symbol name (exact, case-sensitive)\n"
    "  main+0x10      symbol plus offset (also main-4)\n"
    "  tick@adc.c     duplicate static, qualified by file (adc or src/adc.c too)\n"
    "  name.<bit>     a bit, slice or field (see {prefix}help mem); an exact symbol\n"
    "                 name wins, so count.12 is the symbol count.12\n"
    "\n"
    "Search forms ({prefix}sym.search): the exact name first, else a name\n"
    "pattern: plain text is contained (gpio), a wildcard globs (Mon*).\n"
    "\n"
    "Commands:\n"
    "  {prefix}sym <addr|name>        - name -> address, or address -> name+offset\n"
    "  {prefix}sym.import {file}      - convert an ELF or a linker map and load it\n"
    "                            (bare: re-import the same file, after a rebuild)\n"
    "  {prefix}sym.load {path}        - reload the sidecar, or load an explicit file\n"
    "  {prefix}sym.unload             - clear the loaded table (file untouched)\n"
    "  {prefix}sym.search <pattern>   - search names\n"
    "  {prefix}sym.info               - file, source, counts by section, range"
)


def _long_help(ctx: PluginContext) -> str:
    table = get_table(ctx)
    state = f"{len(table)} ({_display_path(ctx, table.path)})" if table is not None else "none"
    return compose(state_line("symbols", state), _GRAMMAR_HELP)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="devices",
    name="sym",
    maturity="preview",
    args="<addr|name>",
    help="Symbol table: name -> address, or address -> name+offset.",
    intro=(
        "A symbol table gives the addresses in your firmware their names, so "
        "{prefix}mem can read and write by name. It comes from your build's ELF or "
        "linker map."
    ),
    long_help=_long_help,
    handler=_handler_root,
    shows=True,
    sub_commands={
        "import": Command(
            params=[
                ParamSpec(
                    "file",
                    "path",
                    positional=True,
                    rest=True,
                    help="ELF or linker map to convert (default: re-import the loaded table's own source)",
                ),
                # A str, not an enum: plugin folders add converters at
                # runtime (a per-config format is a board's own pipeline),
                # so the valid set is not knowable at import.  The handler
                # validates against built-ins PLUS the loaded plugin
                # converters and names them all on a miss.
                ParamSpec(
                    "format",
                    "str",
                    help=f"build file format ({', '.join(FORMATS)}, or a plugin "
                    "converter); omitted = sniff the file",
                ),
            ],
            help="Convert an ELF or a linker map to sym/<cfg>.symbols.json and load it (bare: re-import the same file).",
            handler=_handler_import,
            needs=CapabilitySet.CONFIG_LOADED,
        ),
        "load": Command(
            params=[
                ParamSpec(
                    "path",
                    "path",
                    positional=True,
                    rest=True,
                    help="symbols JSON file (default: the cfg's sidecar)",
                ),
            ],
            help="Load a symbol table (default: sym/<cfg>.symbols.json, the file {prefix}sym.import writes).",
            handler=_handler_load,
            files="sym",
            needs=CapabilitySet.CONFIG_LOADED,
        ),
        "unload": Command(
            help="Clear the loaded symbol table.",
            handler=_handler_unload,
        ),
        "search": Command(
            args="<pattern>",
            help="Search symbols by name: the exact name, else contains (gpio) or a glob (Mon*).",
            handler=_handler_search,
            shows=True,
            needs=CapabilitySet(symbols_loaded=True),
        ),
        "info": Command(
            help="Show the loaded symbol table: file, source, counts by section, address range.",
            handler=_handler_info,
            shows=True,
            state=True,
        ),
    },
)
