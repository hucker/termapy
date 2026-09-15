"""Session state for the loaded symbol table, and the auto-load rule.

The table lives in ``ctx.ns("symbols")`` -- the ``ctx.ns("active_profile")``
idiom -- under exactly one key, ``"table"``.  :func:`set_table` is the
single writer, so ``/sym.import``, ``/sym.load``, ``/sym.unload`` and
:func:`autoload` cannot disagree about the namespace shape.  This lives in
core rather than in the ``/sym`` plugin because ``ReplEngine`` fires the
auto-load (CLAUDE.md: infrastructure never lives under ``builtins/``).

There is no mtime watcher: ``/sym.import`` installs what it wrote, and a
hand edit is picked up by an explicit bare ``/sym.load``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from termapy.symbols.table import SymbolTable, sidecar_path

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

SYMBOLS_NS: Final[str] = "symbols"


def get_table(ctx: PluginContext) -> SymbolTable | None:
    """The loaded table, or None."""
    return ctx.ns(SYMBOLS_NS).get("table")


def set_table(ctx: PluginContext, table: SymbolTable | None) -> None:
    """Install ``table`` (or clear the namespace when None)."""
    ns = ctx.ns(SYMBOLS_NS)
    ns.clear()
    if table is not None:
        ns["table"] = table


def autoload(ctx: PluginContext, config_path: str) -> SymbolTable | None:
    """Load ``sym/<cfg>.symbols.json`` if present; never raises.

    Always clears the previous table first: a config switch drops the
    previous build's names even when the new cfg has no sidecar.  A
    missing file is silent; a broken one is reported on ``ctx.io.output``
    (an error is never silent) and leaves nothing loaded.  The success
    line is suppressed for ``--run`` / ``--exec`` so captured stdout
    contains only the user's output.

    Args:
        ctx: The plugin context whose namespace receives the table.
        config_path: The active config file (``""`` = no config).

    Returns:
        The loaded table, or None.
    """
    set_table(ctx, None)
    path = sidecar_path(config_path)
    if path is None or not path.is_file():
        return None
    try:
        table = SymbolTable.load(path)
    except (OSError, ValueError) as e:
        ctx.io.output(f"Symbols: {path.name}: {e}", "yellow")
        return None
    set_table(ctx, table)
    if not ctx.is_oneshot():
        ctx.io.output(f"Loaded {len(table)} symbols ({path.name})", "dim")
    return table
