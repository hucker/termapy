"""Tests for the bundled demo symbol table (mirrors ``test_demo_profiles.py``).

``src/termapy/builtins/demo/demo.symbols.json`` gives the demo config a
symbol table so every frontend auto-loads names for the addresses the demo
device's ``mem`` command answers for, and the CLI gold can pin ``/sym``.
"""

from __future__ import annotations

from pathlib import Path

from termapy.config import setup_demo_config
from termapy.folders import SYMBOLS_SUFFIX
from termapy.protocol.core import parse_format_spec
from termapy.symbols import SymbolTable

DEMO_DIR = Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo"
DEMO_SYMBOLS = DEMO_DIR / f"demo{SYMBOLS_SUFFIX}"


class TestBundledSymbols:

    def test_file_exists(self):
        # Assert
        assert DEMO_SYMBOLS.is_file(), f"bundled symbol table missing: {DEMO_SYMBOLS}"

    def test_loads_with_thirteen_symbols(self):
        # Act
        table = SymbolTable.load(DEMO_SYMBOLS)

        # Assert
        assert len(table) == 13, "the gold pins this count"
        assert table.imported == "2026-08-29T00:00:00", "fixed timestamp keeps /sym.info gold-safe"

    def test_grammar_examples_are_present(self):
        # Arrange
        table = SymbolTable.load(DEMO_SYMBOLS)

        # Act
        ticks = table.by_name("tick")
        main = table.lookup(0x2010)
        gtemp = table.by_name("gTemp")

        # Assert
        assert len(table.by_name("count.12")) == 1, "a dotted name for the exact-match rule"
        assert sorted(symbol.file for symbol in ticks) == ["adc.c", "mon.c"], (
            "a duplicate static with distinct files for name@file"
        )
        assert main is not None and main.name == "main", "0x2010 is inside main (main+0x10)"
        assert gtemp and gtemp[0].addr == 0x1000, "gTemp at the address the gold dumps"

    def test_typed_symbols(self):
        # Arrange
        table = SymbolTable.load(DEMO_SYMBOLS)

        # Act
        u1mode = table.by_name("U1MODE")[0]
        u1sta = table.by_name("U1STA")[0]
        scal = table.by_name("sCal")[0]

        # Assert
        assert len(parse_format_spec(u1mode.type)) == 3, "U1MODE carries a 3-field format spec"
        assert u1sta.rmw is False, "U1STA is the rmw:false example"
        assert scal.size == 0, "sCal is the size-0 linker global"


class TestSetupDemoConfig:

    def test_materializes_sidecar_byte_identical(self, tmp_path):
        # Act
        config_path = setup_demo_config(tmp_path)

        # Assert
        sidecar = config_path.parent / f"demo{SYMBOLS_SUFFIX}"
        assert sidecar.read_bytes() == DEMO_SYMBOLS.read_bytes(), "bundled file copied verbatim"

    def test_keeps_a_modified_copy_without_force(self, tmp_path):
        # Arrange
        config_path = setup_demo_config(tmp_path)
        sidecar = config_path.parent / f"demo{SYMBOLS_SUFFIX}"
        sidecar.write_text("{}", encoding="utf-8")

        # Act
        setup_demo_config(tmp_path)

        # Assert
        assert sidecar.read_text(encoding="utf-8") == "{}", "an existing copy is left alone"

    def test_force_refreshes(self, tmp_path):
        # Arrange
        config_path = setup_demo_config(tmp_path)
        sidecar = config_path.parent / f"demo{SYMBOLS_SUFFIX}"
        sidecar.write_text("{}", encoding="utf-8")

        # Act
        setup_demo_config(tmp_path, force=True)

        # Assert
        assert sidecar.read_bytes() == DEMO_SYMBOLS.read_bytes(), "force rewrites the sidecar"
