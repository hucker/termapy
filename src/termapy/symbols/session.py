"""Session state for the loaded symbol table, and the auto-load rule.

The namespace ``ctx.ns("symbols")`` holds three keys, and one function
writes them:

- ``"build"``   -- the sidecar table (what ``/sym.import`` produced), or None
- ``"devices"`` -- the loaded device files (:mod:`termapy.devices`)
- ``"table"``   -- the MERGED view every lookup uses: build symbols plus
  device registers, the build winning a name clash

:func:`install_build` and :func:`install_devices` each replace one input
and rebuild the merged view, so ``/sym.import``, ``/sym.load``,
``/sym.unload`` and :func:`autoload` cannot disagree about the shape --
and, the bug that motivated the split, an import can no longer drop the
device registers until the next config load.  This lives in core rather
than in the ``/sym`` plugin because ``ReplEngine`` fires the auto-load
(CLAUDE.md: infrastructure never lives under ``builtins/``).

There is no mtime watcher on the SIDECAR: ``/sym.import`` installs what it
wrote, and a hand edit is picked up by an explicit bare ``/sym.load``.
The table's SOURCE map is a different question -- :func:`autoload` checks
its witness once at load and warns when it moved
(:mod:`termapy.symbols.provenance`), but never reloads or regenerates.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from termapy import devices as devices_mod, folders
from termapy.load_report import DEVICE, SYMBOLS, Record, load_record
from termapy.symbols.provenance import Staleness, check_staleness
from termapy.symbols.table import SymbolTable, sidecar_path

if TYPE_CHECKING:
    from termapy.devices import Device
    from termapy.plugins import PluginContext

SYMBOLS_NS: Final[str] = "symbols"


def get_table(ctx: PluginContext) -> SymbolTable | None:
    """The merged table every lookup uses, or None when nothing is loaded."""
    return ctx.ns(SYMBOLS_NS).get("table")


def get_build_table(ctx: PluginContext) -> SymbolTable | None:
    """The build's own table -- the sidecar as imported -- or None."""
    return ctx.ns(SYMBOLS_NS).get("build")


def get_devices(ctx: PluginContext) -> list[Device]:
    """The loaded device files, in load order (possibly empty)."""
    return list(ctx.ns(SYMBOLS_NS).get("devices") or [])


def install_build(ctx: PluginContext, table: SymbolTable | None) -> SymbolTable | None:
    """Replace the build table (None = unload it) and rebuild the merged view.

    Returns:
        The merged table now installed, or None when nothing remains.
    """
    return _install(ctx, table, get_devices(ctx))


def install_devices(ctx: PluginContext, devices: list[Device]) -> SymbolTable | None:
    """Replace the device set and rebuild the merged view.

    Returns:
        The merged table now installed, or None when nothing remains.
    """
    return _install(ctx, get_build_table(ctx), devices)


def _install(
    ctx: PluginContext,
    build: SymbolTable | None,
    devices: list[Device],
) -> SymbolTable | None:
    """The single writer.  Merges, installs, reports shadowed registers."""
    merged, shadowed = _merge(build, devices)
    ns = ctx.ns(SYMBOLS_NS)
    ns.clear()
    ns["build"] = build
    ns["devices"] = list(devices)
    if merged is not None:
        ns["table"] = merged
    for name in shadowed:
        ctx.io.output(
            f"Symbols: build symbol {name} shadows the device register of the same name",
            "yellow",
        )
    return merged


def _merge(
    build: SymbolTable | None,
    devices: list[Device],
) -> tuple[SymbolTable | None, list[str]]:
    """Build plus devices -> the merged table, and the shadowed names.

    With no devices the build table is returned AS IS -- same object, so
    nothing that was true of it (path, provenance) changes.  A table made
    from devices alone carries no source, recipe or witness: it derives
    from no build, so there is nothing to be stale against.
    """
    if not devices:
        return build, []
    base = list(build.symbols) if build is not None else []
    symbols, shadowed = devices_mod.merge_into(base, devices)
    if build is None:
        merged = SymbolTable(symbols)
    else:
        merged = SymbolTable(
            symbols,
            path=build.path,
            source=build.source,
            imported=build.imported,
            address_bits=build.address_bits,
            endian=build.endian,
            regions=build.regions,
            recipe=build.recipe,
            witness=build.witness,
        )
    merged.devices = list(devices)
    return merged, shadowed


def autoload(
    ctx: PluginContext,
    config_path: str,
    global_root: Path | None = None,
) -> list[Record]:
    """Load the sidecar and the config's device files; never raises.

    Always clears the previous state first: a config switch drops the
    previous build's names and the previous board's registers even when
    the new cfg has neither.  A missing sidecar is silent; a broken file
    of either kind is reported on ``ctx.io.output`` (an error is never
    silent) and skipped.  What loaded is not printed here: it is the
    caller's load report (``termapy.load_report``).

    Devices load even when there is NO sidecar -- a board whose firmware
    map you do not have still has registers at addresses the silicon
    fixed -- so a config with device files and no build gets a table of
    only those.

    Args:
        ctx: The plugin context whose namespace receives the state.
        config_path: The active config file (``""`` = no config).
        global_root: Folder holding ``termapy_cfg``, for the global
            device layer; None resolves the real one.

    Returns:
        The load-report rows: the sidecar's, then one per device file
        and one per device error.
    """
    ctx.ns(SYMBOLS_NS).clear()
    build, records = _load_sidecar(ctx, config_path)
    devices, errors = _resolve_and_report(ctx, config_path, global_root)
    _install(ctx, build, devices)
    return [
        *records,
        *(_device_record(device) for device in devices),
        *(load_record(DEVICE, error=error) for error in errors),
    ]


def reload_devices(
    ctx: PluginContext,
    config_path: str,
    global_root: Path | None = None,
) -> list[str]:
    """Re-scan the ``dev/`` folders and reinstall; the build table is kept.

    What ``/dev.import`` and ``/dev.use`` run after writing a file.  Quiet
    on success -- the command prints its own line -- but a broken file is
    still reported, since an error is never silent.

    Returns:
        The load errors (``"<file>: <problem>"``), already reported, so the
        command that just wrote a file can tell whether its file is among
        them and say why instead of guessing.
    """
    devices, errors = _resolve_and_report(ctx, config_path, global_root)
    install_devices(ctx, devices)
    return errors


def _resolve_and_report(
    ctx: PluginContext,
    config_path: str,
    global_root: Path | None,
) -> tuple[list[Device], list[str]]:
    """Resolve the layers and report every error."""
    devices, errors = devices_mod.resolve_devices(config_path, global_root)
    for error in errors:
        ctx.io.output(f"Device: {error}", "yellow")
    return devices, errors


def _device_record(device: Device) -> Record:
    """A device file's load-report row; ``ref`` names the library part it placed."""
    return load_record(
        DEVICE,
        name=device.name,
        layer=device.layer,
        file=Path(folders.DEV, device.path.name).as_posix() if device.path else "",
        path=str(device.path.resolve()) if device.path else "",
        count=len(device),
        detail={"ref": device.ref},
    )


def _load_sidecar(ctx: PluginContext, config_path: str) -> tuple[SymbolTable | None, list[Record]]:
    """The build's own table, staleness-checked, and its load-report row.

    Returns:
        ``(table, rows)``: no sidecar is ``(None, [])``, a broken one is
        ``(None, [its error row])``.
    """
    path = sidecar_path(config_path)
    if path is None or not path.is_file():
        return None, []
    layer = Path(config_path).stem
    file = Path(folders.SYM, path.name).as_posix()
    full = str(path.resolve())
    try:
        table = SymbolTable.load(path)
    except (OSError, ValueError) as e:
        ctx.io.output(f"Symbols: {path.name}: {e}", "yellow")
        return None, [load_record(SYMBOLS, layer=layer, file=file, path=full, error=str(e))]
    verdict = check_staleness(table, prefix=ctx.prefix)
    _report_staleness(ctx, verdict)
    recipe = table.recipe or {}
    record = load_record(
        SYMBOLS,
        name=Path(table.source).name if table.source else path.stem,
        layer=layer,
        file=file,
        path=full,
        count=len(table),
        detail={
            "source": table.source,
            "converter": recipe.get("converter", ""),
            "typed": sum(1 for symbol in table.symbols if symbol.type),
            "status": verdict.status,
        },
    )
    return table, [record]


def _report_staleness(ctx: PluginContext, verdict: Staleness) -> None:
    """Warn when the map moved under a loaded table.  Never regenerates.

    A rebuild is the COMMON case, so this reports and stops: a terminal
    that rewrote the symbol table at load would surprise the user far
    worse than a yellow line, and the line names the exact command that
    fixes it.  Only a positive ``stale`` verdict speaks -- ``unknown``
    (hand-written tables, pre-witness sidecars) stays silent, since it is
    the normal state of a file that is not wrong.

    Shown even for ``--run`` / ``--exec``: a script reading stale symbol
    addresses is exactly the case worth interrupting, and this goes to
    the output channel, not the captured value.
    """
    if not verdict.is_stale:
        return
    ctx.io.output(f"Symbols may be out of date: {verdict.reason}", "yellow")
    if verdict.command:
        ctx.io.output(f"  rebuild: {verdict.command}", "yellow")
