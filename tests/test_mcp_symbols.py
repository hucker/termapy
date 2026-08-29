"""The symbol sidecar through the MCP frontend: auto-load and the data record.

``run_mcp_stdio`` fires ``on_app_start`` before the startup auto-connect,
which is the moment ``ReplEngine.fire_lifecycle`` auto-loads
``<cfg>.symbols.json``.  test_sym_commands.py proves the engine; the CLI
gold proves the CLI; this proves the MCP host reaches the same wiring and
that ``/sym`` hands an agent its ``data`` record inside the envelope.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import default_cfg  # noqa: E402
from termapy.folders import SYMBOLS_SUFFIX  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402
from termapy.symbols import get_table  # noqa: E402

DEMO_SYMBOLS = (
    Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo" / f"demo{SYMBOLS_SUFFIX}"
)
DEMO_COUNT = 12


@pytest.fixture
def host(tmp_path):
    """An MCPHost over ``rig/rig.cfg`` with the demo table beside it, no port."""
    cfg = default_cfg()
    cfg["serial"]["port"] = ""
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    shutil.copyfile(DEMO_SYMBOLS, config_path.with_name(f"rig{SYMBOLS_SUFFIX}"))
    return MCPHost(cfg, str(config_path), verbose=False)


class TestMcpSymbols:

    def test_on_app_start_loads_the_sidecar(self, host):
        # Arrange -- nothing loaded until the server's boot line fires
        assert get_table(host.ctx) is None, "construction alone loads nothing"

        # Act -- what run_mcp_stdio does before auto-connect
        host.repl.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(host.ctx)
        assert table is not None, "on_app_start auto-loads the sidecar in the MCP host"
        assert len(table) == DEMO_COUNT, "the whole demo table is installed"

    def test_sym_envelope_carries_the_data_record(self, host):
        # Arrange
        host.repl.fire_lifecycle("on_app_start")

        # Act -- the agent's path
        result = asyncio.run(host.run_command_async("/sym main", "normal", 5.0))

        # Assert
        assert result["success"] is True, result["error"]
        data = result["data"]
        assert data["addr_hex"] == "0x00002000", "structured twin of the lookup"
        assert data["symbol"]["name"] == "main", "the record names what is at the address"
        assert data["symbol"]["file"] == "main.c", "provenance survives into the envelope"

    def test_sym_miss_keeps_the_record_shape(self, host):
        # Arrange
        host.repl.fire_lifecycle("on_app_start")

        # Act
        result = asyncio.run(host.run_command_async("/sym 0x7000", "normal", 5.0))

        # Assert -- a miss is success with a null symbol, not an error
        assert result["success"] is True, result["error"]
        assert result["data"]["symbol"] is None, "nothing at 0x7000 in the demo table"
        assert result["data"]["addr"] == 0x7000, "the resolved address is still reported"
