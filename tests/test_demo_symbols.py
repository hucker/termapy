"""Tests for the bundled demo symbol table (mirrors ``test_demo_profiles.py``).

``src/termapy/builtins/demo/demo.symbols.json`` gives the demo config a
symbol table so every frontend auto-loads names for the addresses the demo
device's ``mem`` command answers for, and the CLI gold can pin ``/sym``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.config import setup_demo_config
from termapy.devices import alias_blocks, parse_device
from termapy.folders import SYMBOLS_SUFFIX
from termapy.protocol.core import parse_format_spec
from termapy.symbols import SymbolTable

DEMO_DIR = Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo"
DEMO_SYMBOLS = DEMO_DIR / f"demo{SYMBOLS_SUFFIX}"


class TestBundledSymbols:
    def test_file_exists(self):
        """The demo symbol table ships inside the package, where setup_demo_config copies from."""
        # Assert
        assert DEMO_SYMBOLS.is_file(), f"bundled symbol table missing: {DEMO_SYMBOLS}"

    def test_loads_with_seventeen_symbols(self):
        """The count and fixed import stamp stay put, so /sym in the CLI gold is stable."""
        # Act
        table = SymbolTable.load(DEMO_SYMBOLS)

        # Assert
        assert len(table) == 17, "the gold pins this count"
        assert table.imported == "2026-08-29T00:00:00", "fixed timestamp keeps /sym.info gold-safe"

    def test_grammar_examples_are_present(self):
        """One example per lookup rule: dotted name, name@file, offset, gold address."""
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
        """Typed rows load intact: a format-spec type, rmw:false, a size-0 global."""
        # Arrange
        table = SymbolTable.load(DEMO_SYMBOLS)

        # Act
        u1mode = table.by_name("U1MODE")[0]
        u1sta = table.by_name("U1STA")[0]
        scal = table.by_name("sCal")[0]

        # Assert
        assert len(parse_format_spec(u1mode.type, word=True)) == 3, (
            "U1MODE carries a 3-field format spec"
        )
        assert u1sta.rmw is False, "U1STA is the rmw:false example"
        assert scal.size == 0, "sCal is the size-0 linker global"


class TestSetupDemoConfig:
    def test_materializes_sidecar_byte_identical(self, tmp_path):
        """A fresh demo config gets the bundled table under sym/ as an exact byte copy."""
        # Act
        config_path = setup_demo_config(tmp_path)

        # Assert
        sidecar = config_path.parent / "sym" / f"demo{SYMBOLS_SUFFIX}"
        assert sidecar.read_bytes() == DEMO_SYMBOLS.read_bytes(), "bundled file copied verbatim"

    def test_keeps_a_modified_copy_without_force(self, tmp_path):
        """Re-running setup without force leaves a user-edited sidecar alone."""
        # Arrange
        config_path = setup_demo_config(tmp_path)
        sidecar = config_path.parent / "sym" / f"demo{SYMBOLS_SUFFIX}"
        sidecar.write_text("{}", encoding="utf-8")

        # Act
        setup_demo_config(tmp_path)

        # Assert
        assert sidecar.read_text(encoding="utf-8") == "{}", "an existing copy is left alone"

    def test_force_refreshes(self, tmp_path):
        """force=True overwrites an edited sidecar with the bundled table again."""
        # Arrange
        config_path = setup_demo_config(tmp_path)
        sidecar = config_path.parent / "sym" / f"demo{SYMBOLS_SUFFIX}"
        sidecar.write_text("{}", encoding="utf-8")

        # Act
        setup_demo_config(tmp_path, force=True)

        # Assert
        assert sidecar.read_bytes() == DEMO_SYMBOLS.read_bytes(), "force rewrites the sidecar"


DEMO_DEVICE = DEMO_SYMBOLS.parent / "dev" / "console.device.json"


class TestBundledDeviceFile:
    """The demo ships one device file: the UART block the CONSOLE constant points at."""

    @pytest.mark.rule("alias-from-pointer-constant")
    def test_the_console_pointer_names_the_uart_block(self):
        """CONSOLE holds the bundled block's base, so the demo has an alias out of the box."""
        # Arrange
        table = SymbolTable.load(DEMO_SYMBOLS)
        device = parse_device(json.loads(DEMO_DEVICE.read_text(encoding="utf-8")))
        (console,) = table.by_name("CONSOLE")
        merged = [*table.symbols, *(register.symbol for register in device.registers())]

        # Act
        aliases, blocks, notes = alias_blocks(merged, [device])

        # Assert
        assert [alias.name for alias in aliases] == ["CONSOLE_MODE", "CONSOLE_STA"]
        assert (blocks[0].base, console.value, notes) == (0xBF806000, 0xBF806000, []), (
            "the constant and the block agree on the address, and nothing is skipped"
        )

    def test_the_block_matches_the_demo_devices_emulation(self):
        """The device file describes what demo.py answers: U1MODE at 0xBF806000, U1STA at 0xBF806010."""
        # Act
        device = parse_device(json.loads(DEMO_DEVICE.read_text(encoding="utf-8")))

        # Assert
        actual = [(register.symbol.name, register.symbol.addr) for register in device.registers()]
        assert actual == [("U1_MODE", 0xBF806000), ("U1_STA", 0xBF806010)]

    def test_setup_materializes_the_device_file_byte_identical(self, tmp_path):
        """A fresh demo config gets the bundled device file under dev/ as an exact byte copy."""
        # Act
        config_path = setup_demo_config(tmp_path)

        # Assert
        copy = config_path.parent / "dev" / "console.device.json"
        assert copy.read_bytes() == DEMO_DEVICE.read_bytes(), "bundled file copied verbatim"
