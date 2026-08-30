"""The demo device's native memory: ``MEM.R`` / ``MEM.W`` / ``MEM.INFO``.

Drives ``FakeSerial`` directly (bytes in, bytes out) so these are
conformance tests for the wire spec the demo device implements, and the
reference for what a firmware author's monitor must answer.  The legacy
``mem <addr> [len]`` hash dump is asserted untouched: it is the worked
example of a device with its own grammar.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from termapy.demo import FakeSerial
from termapy.folders import SYMBOLS_SUFFIX
from termapy.memory import Memory, parse_reply

DEMO_SYMBOLS = (
    Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo" / f"demo{SYMBOLS_SUFFIX}"
)


@pytest.fixture
def dev() -> FakeSerial:
    return FakeSerial()


def _exchange(dev: FakeSerial, command: str) -> str:
    """Send one line, return everything the device answered."""
    dev.write(command.encode() + b"\r")
    time.sleep(0.01)
    chunks: list[bytes] = []
    while True:
        chunk = dev.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode()


class TestMemInfo:

    def test_one_json_line_then_ok(self, dev):
        # Act
        text = _exchange(dev, "MEM.INFO")

        # Assert
        lines = text.splitlines()
        assert lines[-1] == "OK", "every MEM reply ends with OK"
        record = json.loads(lines[0])
        assert record == {"max_block": 64, "address_bits": 32, "endian": "le"}, (
            "the facts a native device publishes instead of a profile block"
        )


class TestMemRead:

    def test_seeded_gtemp_is_readable(self, dev):
        # Arrange -- gTemp (u16 le) is seeded to 27 at 0x1000
        # Act
        text = _exchange(dev, "MEM.R 0x1000 2")

        # Assert
        assert text.splitlines() == ["00001000: 1B 00", "OK"], "row + OK, 8-digit address, hex pairs"

    def test_rows_are_sixteen_bytes_and_contiguous(self, dev):
        # Act
        reply = parse_reply(_exchange(dev, "MEM.R 0x1000 40"))

        # Assert
        assert reply.complete is True, "OK terminated"
        assert [addr for addr, _ in reply.rows] == [0x1000, 0x1010, 0x1020], "one row per 16 bytes"
        assert [len(data) for _, data in reply.rows] == [16, 16, 8], "the tail row is short"

    def test_address_without_0x_prefix(self, dev):
        assert _exchange(dev, "MEM.R 1000 2").splitlines()[0] == "00001000: 1B 00", "hex either way"

    def test_sfr_window(self, dev):
        # Arrange -- U1MODE seeded to ON | BRGH = 0x8008 (u32 le)
        text = _exchange(dev, "MEM.R 0xBF806000 4")
        assert text.splitlines() == ["BF806000: 08 80 00 00", "OK"], "the SFR window is backed like RAM"

    @pytest.mark.parametrize(
        "command, reason",
        [
            ("MEM.R", "usage"),
            ("MEM.R 0x1000", "usage"),
            ("MEM.R zz 4", "usage"),
            ("MEM.R 0x1000 x", "usage"),
            ("MEM.R 0x1000 0", "length"),
            ("MEM.R 0x1000 65", "length"),
            ("MEM.R 0x4000 1", "range"),
            ("MEM.R 0x3FFF 2", "range"),
            ("MEM.R 0x9000 1", "range"),
            ("MEM.R 0xBF806020 1", "range"),
        ],
    )
    def test_errors(self, dev, command, reason):
        assert _exchange(dev, command).splitlines() == [f"ERR {reason}"], "one ERR line, no OK"

    def test_every_demo_symbol_address_is_readable(self, dev):
        # Arrange -- the symbol table names addresses; the device must serve them
        table = json.loads(DEMO_SYMBOLS.read_text(encoding="utf-8"))

        # Act / Assert
        for symbol in table["symbols"]:
            addr = int(symbol["addr"], 16)
            length = max(1, min(symbol.get("size", 0), 64))
            reply = parse_reply(_exchange(dev, f"MEM.R {symbol['addr']} {length}"))
            assert reply.error is None, f"{symbol['name']} at 0x{addr:X} must be in a window"
            assert sum(len(data) for _, data in reply.rows) == length, f"{symbol['name']} read in full"


class TestMemWrite:

    def test_write_persists_and_reads_back(self, dev):
        # Act
        ack = _exchange(dev, "MEM.W 0x1000 2C01")
        text = _exchange(dev, "MEM.R 0x1000 4")

        # Assert
        assert ack.splitlines() == ["OK"], "a write answers OK alone"
        assert text.splitlines()[0].startswith("00001000: 2C 01"), "the new bytes are there"

    def test_write_tolerates_spaced_hex(self, dev):
        assert _exchange(dev, "MEM.W 0x1000 2C 01").splitlines() == ["OK"], "space-separated pairs accepted"
        assert _exchange(dev, "MEM.R 0x1000 2").splitlines()[0] == "00001000: 2C 01", "both bytes landed"

    @pytest.mark.parametrize(
        "command, reason",
        [
            ("MEM.W 0x1000", "usage"),
            ("MEM.W 0x1000 ABC", "usage"),
            ("MEM.W 0x1000 ZZ", "usage"),
            ("MEM.W 0x1000 " + "00" * 65, "length"),
            ("MEM.W 0x3FFF 0000", "range"),
        ],
    )
    def test_errors(self, dev, command, reason):
        assert _exchange(dev, command).splitlines() == [f"ERR {reason}"]

    def test_reset_restores_the_seed(self, dev):
        # Arrange
        _exchange(dev, "MEM.W 0x1000 FFFF")

        # Act
        _exchange(dev, "AT+RESET")

        # Assert
        assert _exchange(dev, "MEM.R 0x1000 2").splitlines()[0] == "00001000: 1B 00", "RAM re-seeded on reset"

    def test_unknown_mem_verb(self, dev):
        assert _exchange(dev, "MEM.X 0x1000").splitlines() == ["ERR usage"], "unknown MEM.* verb"


class TestLegacyGrammar:
    """The device's own ``mem`` syntax -- the template dialect's worked example.

    Mirrors a real monitor: grouped hex rows with an ASCII column and no
    terminator, ``=val`` writes sized by digit count, ``err:`` failures.
    """

    def test_dump_row_shape(self, dev):
        # Act
        text = _exchange(dev, "mem 0x1000 16")

        # Assert -- 4-byte groups, ASCII column, no OK line
        line = text.splitlines()[0]
        assert line.startswith("00001000:  1B 00 00 50  7D 44 3A 21  "), "grouped pairs after the address"
        assert line.endswith("|"), "ASCII column closed with |"
        assert "OK" not in text, "a monitor dump has no terminator"

    def test_same_bytes_as_the_native_spec(self, dev):
        legacy = _exchange(dev, "mem 0x1000 8").splitlines()[0]
        native = _exchange(dev, "MEM.R 0x1000 8").splitlines()[0]
        assert legacy.split("|")[0].replace(" ", "") == native.replace(" ", ""), "one RAM behind both grammars"

    def test_short_last_row_is_space_padded(self, dev):
        line = _exchange(dev, "mem 0x1000 4").splitlines()[0]
        assert line == "00001000:  1B 00 00 50" + " " * 39 + "  |...P|", "missing slots keep the column layout"

    def test_count_default_and_clamp(self, dev):
        assert len(_exchange(dev, "mem 0x1000").splitlines()) == 1, "default count 16 = one row"
        assert len(_exchange(dev, "mem 0x1000 999").splitlines()) == 16, "count clamped to 256"

    @pytest.mark.parametrize(
        "command, expected",
        [
            ("mem 0x1000 =2C", "ok [00001000] 0x1B -> 0x2C (byte)"),
            ("mem 0x1000 =012C", "ok [00001000] 0x001B -> 0x012C (half)"),
            ("mem 0x1008 =00000007", "ok [00001008] 0x00000005 -> 0x00000007 (word)"),
        ],
    )
    def test_write_width_by_digit_count(self, dev, command, expected):
        assert _exchange(dev, command).splitlines() == [expected], "old -> new, sized by the digits typed"

    def test_write_persists(self, dev):
        _exchange(dev, "mem 0x1000 =2C")
        assert _exchange(dev, "MEM.R 0x1000 1").splitlines()[0] == "00001000: 2C", "visible through the spec too"

    @pytest.mark.parametrize(
        "command, reason",
        [
            ("mem", "err: expected hex address, pin name, or peripheral name"),
            ("mem zz", "err: expected hex address, pin name, or peripheral name"),
            ("mem 0x1000 =", "err: expected hex value after '='"),
            ("mem 0x1001 =0102", "err: half-word write requires 2-byte alignment"),
            ("mem 0x1002 =01020304", "err: word write requires 4-byte alignment"),
            ("mem 0x9000 =01", "err: address 00009000 not mapped"),
            ("mem 0x9000 4", "err: address range 00009000..00009003 not mapped (would HardFault)"),
        ],
    )
    def test_errors(self, dev, command, reason):
        assert _exchange(dev, command).splitlines() == [reason]


class TestEngineOverFakeSerial:
    """The engine end-to-end against the demo device's actual bytes."""

    def test_read_and_write_through_memory(self, dev):
        # Arrange
        memory = Memory(lambda command: _exchange(dev, command))

        # Act
        before = memory.read(0x1000, 100)
        memory.write(0x1010, bytes(range(70)))
        after = memory.read(0x1000, 100)

        # Assert
        assert before[:2] == b"\x1b\x00", "seed visible through the engine"
        assert after[16:86] == bytes(range(70)), "a 70-byte write crossed the 64-byte block limit"
        assert after[:16] == before[:16], "bytes outside the write are untouched"
