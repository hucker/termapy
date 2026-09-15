"""Symbol tables -- named addresses for a firmware build.

termapy reads exactly ONE symbol format: its own JSON sidecar,
``<cfg_dir>/<cfg_stem>.symbols.json`` (see :mod:`termapy.symbols.table`).
Converters (:mod:`termapy.symbols.converters`) turn a toolchain's linker
map into that file; the runtime never parses a vendor map.  Everything
above this layer -- the address grammar, ``/sym.*``, and later the memory
commands -- works from the table without knowing where the names came from.

Layout:

- ``table.py``      -- ``Symbol``, ``SymbolTable`` (model + load/save/validate),
                       ``sidecar_path``.
- ``address.py``    -- the address grammar (``0x1000``, ``1000h``, decimal,
                       ``name``, ``name+off``, ``name@file``; ``.suffix`` reserved).
- ``format.py``     -- prose renderers and their structured ``data=`` twins.
- ``provenance.py`` -- is the table still what the map would produce?
                       (witness + recipe -> in sync / stale / unknown).
- ``session.py``    -- the ``ctx.ns("symbols")`` owner and the auto-load rule.
- ``converters/``   -- the registry (``CONVERTERS``, ``find_converter``) and
                       one module per toolchain (``xc32``).

This package is library-shaped: no Textual, no pyserial, and nothing from
``termapy.builtins`` (``builtins/commands/sym.py`` is the command surface
built on top of it, the ``variables.py`` <- ``var.py`` relationship).
The converter registry is NOT re-exported here: ``/sym.import`` imports
``termapy.symbols.converters`` itself, so this package's import path
stays the model, grammar and session only.
"""

from __future__ import annotations

from termapy.symbols.address import Address, parse_address, parse_number
from termapy.symbols.format import (
    format_symbol,
    info_rows,
    lookup_record,
    symbol_record,
    symbolic_name,
    table_record,
)
from termapy.symbols.provenance import (
    IN_SYNC,
    STALE,
    UNKNOWN,
    Staleness,
    check_staleness,
    make_recipe,
    make_witness,
)
from termapy.symbols.session import SYMBOLS_NS, autoload, get_table, set_table
from termapy.symbols.table import (
    SCALAR_TYPES,
    SYMBOLS_VERSION,
    Symbol,
    SymbolTable,
    sidecar_path,
)

__all__ = [
    "SYMBOLS_VERSION",
    "SCALAR_TYPES",
    "Symbol",
    "SymbolTable",
    "sidecar_path",
    "Address",
    "parse_number",
    "parse_address",
    "format_symbol",
    "symbolic_name",
    "symbol_record",
    "lookup_record",
    "table_record",
    "info_rows",
    "SYMBOLS_NS",
    "get_table",
    "set_table",
    "autoload",
    "IN_SYNC",
    "STALE",
    "UNKNOWN",
    "Staleness",
    "check_staleness",
    "make_recipe",
    "make_witness",
]
