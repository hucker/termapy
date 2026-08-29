"""Behavior tests for the built-in ``/mem.*`` commands against the demo device.

A ``CLITerminal`` on the DEMO port (the ``tests/test_cli.py`` rig), with
the demo symbol table beside its cfg so names resolve.  Every case runs
through the real dispatcher and the real ``FakeSerial``: the engine and
the wire are covered by test_memory_engine.py / test_demo_memory.py, so
these pin the command surface -- argument handling, rendering, the
value/data contracts, the audit line and the device-info cache.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.folders import SYMBOLS_SUFFIX

DEMO_DIR = Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo"
DEMO_SYMBOLS = DEMO_DIR / f"demo{SYMBOLS_SUFFIX}"
DEMO_PROFILE = DEMO_DIR / "demo.profile.json"


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port with the demo symbols auto-loaded.

    The session-log callback is wired to a list (the CLI itself keeps no
    log file) so the audit line is observable.
    """
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200},
        "eol": "\r",
    }
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "proto", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    shutil.copyfile(DEMO_SYMBOLS, config_path.with_name(f"rig{SYMBOLS_SUFFIX}"))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    terminal.repl.fire_lifecycle("on_app_start")
    logged: list[tuple[str, str]] = []
    terminal.ctx.io.log = lambda prefix, text: logged.append((prefix, text))
    terminal.audit = logged  # type: ignore[attr-defined]
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


class TestDump:

    def test_by_symbol(self, cli):
        # Act
        result = cli.repl.dispatch("mem.dump gTemp 16")

        # Assert
        assert result.success, result.error
        assert result.value.startswith("1B00"), "gTemp is seeded to 27 (u16 le); value is the hex string"
        assert len(result.value) == 32, "16 bytes -> 32 hex digits"

    def test_rows_render_with_symbol_annotation(self, cli, capsys):
        # Act
        cli.repl.dispatch("mem.dump gTemp 16")

        # Assert
        line = next(line for line in capsys.readouterr().out.splitlines() if "0x00001000" in line)
        assert line.lstrip().startswith("0x00001000  1B 00 "), "address, then hex pairs (CLI indents output)"
        assert line.rstrip().endswith("|  gTemp"), "ASCII column, then the containing symbol"

    def test_default_length_is_64(self, cli):
        result = cli.repl.dispatch("mem.dump 0x1000")
        assert len(result.value) == 128, "64 bytes by default"

    def test_offset_form_labels_rows_relative_to_the_symbol(self, cli):
        # Act -- data path, so the records carry the labels
        cli.ctx.wants_data = True  # what the dispatcher sets for --json / MCP
        result = cli.repl.dispatch("mem.dump main+0x10 32")

        # Assert
        assert result.data["rows"][0]["symbolic"] == "main+0x10", "first row is inside main"
        assert result.data["rows"][1]["symbolic"] == "main+0x20", "rows advance through the symbol"

    def test_read_crosses_the_block_limit(self, cli):
        result = cli.repl.dispatch("mem.dump 0x1000 100")
        assert result.success, result.error
        assert len(result.value) == 200, "100 bytes came back through two MEM.R exchanges"

    def test_data_record_shape(self, cli):
        # Arrange
        cli.ctx.wants_data = True  # what the dispatcher sets for --json / MCP

        # Act
        result = cli.repl.dispatch("mem.dump gTemp 2")

        # Assert
        assert result.data["addr_hex"] == "0x00001000", "resolved address"
        assert result.data["len"] == 2, "bytes read"
        assert result.data["bytes_hex"] == "1B00", "the block as hex"
        assert result.data["rows"][0] == {
            "addr": 0x1000, "addr_hex": "0x00001000", "hex": "1B 00", "ascii": "..", "symbolic": "gTemp",
        }, "one row with every key"

    @pytest.mark.parametrize(
        "line, message",
        [
            ("mem.dump nosuch", "Unknown symbol: nosuch"),
            ("mem.dump U1MODE.ON", "Unsupported address suffix: .ON"),
            ("mem.dump 0x9000 4", "Device error: range"),
            ("mem.dump main-0x9000", "Invalid address: main-0x9000"),
        ],
    )
    def test_errors(self, cli, line, message):
        result = cli.repl.dispatch(line)
        assert not result.success, "refused"
        assert message in result.error, "the address grammar's / device's own message"

    def test_length_below_one_is_a_usage_error(self, cli):
        result = cli.repl.dispatch("mem.dump 0x1000 0")
        assert not result.success, "min=1 enforced by the dispatcher"
        assert "len" in result.error, "names the parameter"

    def test_bare_addr_is_usage(self, cli):
        result = cli.repl.dispatch("mem.dump")
        assert not result.success, "addr is required"
        assert "Usage" in result.error, "synthesized usage"


class TestWrite:

    def test_write_then_read_back(self, cli):
        # Act
        written = cli.repl.dispatch("mem.write gFlags 07000000")
        read = cli.repl.dispatch("mem.dump gFlags 4")

        # Assert
        assert written.success, written.error
        assert written.value == "4", "byte count is the value"
        assert read.value == "07000000", "the device kept the bytes"

    def test_audit_line_in_the_session_log(self, cli):
        # Act
        cli.repl.dispatch("mem.write gTemp 2C01")

        # Assert
        audits = [text for prefix, text in cli.audit if prefix == "#" and text.startswith("MEM.W")]
        assert audits == ["MEM.W 0x00001000 before=1B00 after=2C01 origin=cli"], (
            "one # line: address, old bytes, new bytes, origin"
        )

    def test_result_line_shows_what_was_there(self, cli, capsys):
        cli.repl.dispatch("mem.write gTemp 2C01")
        out = capsys.readouterr().out
        assert "Wrote 2 bytes at 0x00001000  gTemp  (was 1B00)" in out, "before-bytes in the feedback"

    def test_data_record(self, cli):
        cli.ctx.wants_data = True  # what the dispatcher sets for --json / MCP
        result = cli.repl.dispatch("mem.write 0x1000 2C 01")
        assert result.data == {
            "addr": 0x1000, "addr_hex": "0x00001000", "count": 2,
            "before_hex": "1B00", "after_hex": "2C01", "origin": "cli",
        }, "spaced hex pairs accepted; record carries before and after"

    def test_write_crosses_the_block_limit(self, cli):
        # Arrange -- 70 bytes: two MEM.W exchanges
        payload = "".join(f"{i:02X}" for i in range(70))

        # Act
        written = cli.repl.dispatch(f"mem.write 0x1010 {payload}")
        read = cli.repl.dispatch("mem.dump 0x1010 70")

        # Assert
        assert written.value == "70", "all bytes counted"
        assert read.value == payload, "all bytes landed in order"

    @pytest.mark.parametrize(
        "line, message",
        [
            ("mem.write gTemp ZZ", "Invalid hex: ZZ"),
            ("mem.write gTemp ABC", "Invalid hex: ABC"),
            ("mem.write nosuch 00", "Unknown symbol: nosuch"),
            ("mem.write 0x9000 00", "Device error: range"),
        ],
    )
    def test_errors(self, cli, line, message):
        result = cli.repl.dispatch(line)
        assert not result.success, "refused"
        assert message in result.error

    def test_no_audit_when_the_write_fails(self, cli):
        cli.repl.dispatch("mem.write 0x9000 00")
        assert not any(text.startswith("MEM.W") for _, text in cli.audit), "nothing was written, nothing is audited"


class TestInfo:

    def test_device_answers_mem_info(self, cli):
        # Act -- no profile: the facts come from the device
        result = cli.repl.dispatch("mem.info")

        # Assert
        assert result.success, result.error
        assert result.value == "termapy", "the dialect is the value"
        assert result.data["sources"]["max_block"] == "device", "MEM.INFO supplied the block limit"
        assert result.data["device_info"] == {"max_block": 64, "address_bits": 32, "endian": "le"}

    def test_profile_block_wins(self, cli):
        # Arrange -- the demo profile pins the block
        loaded = cli.repl.dispatch(f"profile.load {DEMO_PROFILE}")
        assert loaded.success, loaded.error

        # Act
        result = cli.repl.dispatch("mem.info")

        # Assert
        assert result.data["sources"]["max_block"] == "profile", "an explicit profile value is the source"
        assert result.data["max_block"] == 64

    def test_rows_show_each_source(self, cli, capsys):
        cli.repl.dispatch("mem.info")
        out = capsys.readouterr().out
        assert "max_block" in out and "(device)" in out, "every fact names where it came from"

    def test_cache_is_dropped_on_reconnect(self, cli):
        # Arrange
        cli.repl.dispatch("mem.info")
        assert cli.ctx.ns("memory").get("queried") is True, "MEM.INFO was asked"

        # Act
        cli._disconnect()
        cli._connect()

        # Assert
        assert cli.ctx.ns("memory") == {}, "a new connection forgets the old device's answer"


class TestNotConnected:

    def test_dump_needs_a_port(self, cli):
        cli._disconnect()
        result = cli.repl.dispatch("mem.dump 0x1000")
        assert not result.success, "serial_connected is required"


class TestValueContract:

    @pytest.mark.parametrize("line", ["mem.dump gTemp 4", "mem.write gTemp 1B00", "mem.info"])
    def test_every_command_returns_a_str_value(self, cli, line):
        result = cli.repl.dispatch(line)
        assert result.success, result.error
        assert isinstance(result.value, str), f"{line}: value must be a str for $(X) <- capture"
