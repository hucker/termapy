"""Unit tests for ``termapy.memory`` -- the bytes-only engine over the MEM spec.

Everything runs against ``FakeDevice``, an in-memory device that speaks the
wire grammar (rows + ``OK``, ``ERR <reason>``, the ``MEM.INFO`` JSON line)
through the same ``exchange(command) -> text`` seam the ``/mem.*`` plugin
wires to a serial port.  No port, no threads, no mocking: the fake IS the
other end of the spec, so these cases pin chunking, continuity, the reply
parser and the profile / MEM.INFO precedence.
"""

from __future__ import annotations

import json
from typing import Callable

import pytest

from termapy.memory import (
    DEFAULT_ADDRESS_BITS,
    DEFAULT_MAX_BLOCK,
    DIALECTS,
    DeviceMemoryError,
    Memory,
    MemoryInfo,
    assemble,
    parse_reply,
    read_command,
    reply_complete,
    resolve_info,
    validate_block,
    write_command,
)

BASE = 0x1000
SIZE = 0x400


class FakeDevice:
    """A device speaking the termapy MEM dialect over ``exchange``.

    Args:
        max_block: The device's block limit (``ERR length`` above it).
        echo: Echo the command line first, like a monitor with local echo.
        prompt: A trailing prompt line after every reply (``"> "``).
        override: ``callable(command) -> reply text or None`` to inject a
            broken reply for one command.
    """

    def __init__(
        self,
        *,
        max_block: int = DEFAULT_MAX_BLOCK,
        echo: bool = False,
        prompt: str = "",
        override: Callable[[str], str | None] | None = None,
    ) -> None:
        self.ram = bytearray(((i * 7) ^ 0x5A) & 0xFF for i in range(SIZE))
        self.max_block = max_block
        self.echo = echo
        self.prompt = prompt
        self.override = override
        self.sent: list[str] = []

    def exchange(self, command: str) -> str:
        self.sent.append(command)
        if self.override is not None:
            forced = self.override(command)
            if forced is not None:
                return forced
        lines: list[str] = []
        if self.echo:
            lines.append(command)
        lines.extend(self._answer(command.split()))
        if self.prompt:
            lines.append(self.prompt)
        return "\r\n".join(lines) + "\r\n"

    def _answer(self, parts: list[str]) -> list[str]:
        if parts[0] == "MEM.INFO":
            return [json.dumps({"max_block": self.max_block, "address_bits": 32, "endian": "be"}), "OK"]
        if parts[0] == "MEM.R":
            addr, length = int(parts[1], 16), int(parts[2])
            if length > self.max_block:
                return ["ERR length"]
            if not (BASE <= addr and addr + length <= BASE + SIZE):
                return ["ERR range"]
            data = self.ram[addr - BASE:addr - BASE + length]
            rows = [
                f"{addr + i:08X}: " + " ".join(f"{byte:02X}" for byte in data[i:i + 16])
                for i in range(0, length, 16)
            ]
            return [*rows, "OK"]
        if parts[0] == "MEM.W":
            addr, data = int(parts[1], 16), bytes.fromhex(parts[2])
            if len(data) > self.max_block:
                return ["ERR length"]
            if not (BASE <= addr and addr + len(data) <= BASE + SIZE):
                return ["ERR range"]
            self.ram[addr - BASE:addr - BASE + len(data)] = data
            return ["OK"]
        return ["ERR usage"]


# ── The wire: request formatting and reply parsing ──────────────────────────


class TestCommands:

    def test_read_command(self):
        assert read_command(0x1000, 16) == "MEM.R 0x1000 16"

    def test_write_command_upper_hex_no_spaces(self):
        assert write_command(0xBF806000, b"\x1b\x00") == "MEM.W 0xBF806000 1B00"


class TestParseReply:

    def test_rows_then_ok(self):
        # Arrange
        text = "00001000: 1B 00 FF\r\n00001003: 01\r\nOK\r\n"

        # Act
        reply = parse_reply(text)

        # Assert
        assert reply.complete is True, "OK ends the reply"
        assert reply.error is None, "OK is not an error"
        assert reply.rows == ((0x1000, b"\x1b\x00\xff"), (0x1003, b"\x01")), "rows in wire order"

    def test_err_carries_reason(self):
        reply = parse_reply("ERR range\r\n")
        assert reply.complete is True, "ERR ends the reply"
        assert reply.error == "range", "the reason text after ERR"

    def test_bare_err_is_empty_reason(self):
        assert parse_reply("ERR\r\n").error == "", "ERR with no reason is still an error"

    def test_incomplete_without_verdict(self):
        reply = parse_reply("00001000: 1B 00\r\n")
        assert reply.complete is False, "rows without OK/ERR are not a finished reply"
        assert reply.rows == ((0x1000, b"\x1b\x00"),), "rows so far are kept"

    def test_echo_prompt_and_banner_are_ignored(self):
        # Arrange -- a monitor that echoes, prints a banner line, and prompts
        text = "MEM.R 0x1000 2\r\n[mon] ok to read\r\n0x1000: 1B 00\r\nOK\r\n> "

        # Act
        reply = parse_reply(text)

        # Assert
        assert reply.rows == ((0x1000, b"\x1b\x00"),), "only the row line is data"
        assert reply.complete is True, "OK found despite the noise"

    def test_row_hex_may_be_lowercase_and_contiguous(self):
        reply = parse_reply("1000: 1b00ff\r\nOK\r\n")
        assert reply.rows == ((0x1000, b"\x1b\x00\xff"),), "case and spacing are the device's choice"

    def test_json_line_is_decoded(self):
        reply = parse_reply('{"max_block": 32, "endian": "le"}\r\nOK\r\n')
        assert reply.info == {"max_block": 32, "endian": "le"}, "the MEM.INFO record"

    def test_stops_at_first_verdict(self):
        reply = parse_reply("OK\r\n00001000: 1B\r\n")
        assert reply.rows == (), "nothing after OK belongs to this reply"

    def test_reply_complete(self):
        assert reply_complete("00001000: 1B\r\n") is False, "still waiting"
        assert reply_complete("00001000: 1B\r\nOK\r\n") is True, "done"


class TestAssemble:

    def test_joins_contiguous_rows(self):
        rows = ((0x1000, b"\x01\x02"), (0x1002, b"\x03"))
        assert assemble(rows, 0x1000, 3, address_bits=32) == b"\x01\x02\x03"

    def test_gap_is_discontinuous(self):
        rows = ((0x1000, b"\x01\x02"), (0x1010, b"\x03"))
        with pytest.raises(DeviceMemoryError, match="Discontinuous reply: expected 0x00001002, got 0x00001010"):
            assemble(rows, 0x1000, 3, address_bits=32)

    def test_wrong_start_is_discontinuous(self):
        with pytest.raises(DeviceMemoryError, match="expected 0x00001000, got 0x00000FF0"):
            assemble(((0xFF0, b"\x01"),), 0x1000, 1, address_bits=32)

    def test_short(self):
        with pytest.raises(DeviceMemoryError, match="Short reply: 2 of 4 bytes at 0x00001000"):
            assemble(((0x1000, b"\x01\x02"),), 0x1000, 4, address_bits=32)

    def test_long(self):
        with pytest.raises(DeviceMemoryError, match="Long reply: 3 of 2 bytes"):
            assemble(((0x1000, b"\x01\x02\x03"),), 0x1000, 2, address_bits=32)

    def test_address_width_follows_address_bits(self):
        with pytest.raises(DeviceMemoryError, match="expected 0x1000, got 0x2000"):
            assemble(((0x2000, b"\x01"),), 0x1000, 1, address_bits=16)


# ── Memory.read ─────────────────────────────────────────────────────────────


class TestRead:

    def test_block_within_max_is_one_exchange(self):
        # Arrange
        device = FakeDevice()
        memory = Memory(device.exchange)

        # Act
        data = memory.read(0x1000, 16)

        # Assert
        assert data == bytes(device.ram[:16]), "the device's bytes, verbatim"
        assert device.sent == ["MEM.R 0x1000 16"], "one exchange for one block"

    def test_chunks_to_max_block(self):
        # Arrange
        device = FakeDevice()
        memory = Memory(device.exchange, MemoryInfo(max_block=64))

        # Act
        data = memory.read(0x1000, 100)

        # Assert
        assert device.sent == ["MEM.R 0x1000 64", "MEM.R 0x1040 36"], "64 then the 36-byte remainder"
        assert data == bytes(device.ram[:100]), "chunks are joined in order"

    def test_device_max_block_from_info_drives_chunking(self):
        device = FakeDevice(max_block=16)
        memory = Memory(device.exchange, resolve_info(None, {"max_block": 16}))
        assert memory.read(0x1000, 40) == bytes(device.ram[:40]), "three 16/16/8 chunks"
        assert len(device.sent) == 3, "chunked to the device's limit"

    def test_echo_and_prompt_tolerated(self):
        device = FakeDevice(echo=True, prompt="> ")
        assert Memory(device.exchange).read(0x1000, 4) == bytes(device.ram[:4]), "noise around rows is ignored"

    def test_device_error(self):
        device = FakeDevice()
        with pytest.raises(DeviceMemoryError, match="^Device error: range$"):
            Memory(device.exchange).read(0x9000, 4)

    def test_silence(self):
        device = FakeDevice(override=lambda command: "")
        with pytest.raises(DeviceMemoryError, match="^No reply to MEM.R 0x1000 4$"):
            Memory(device.exchange).read(0x1000, 4)

    def test_incomplete(self):
        device = FakeDevice(override=lambda command: "00001000: 01 02 03 04\r\n")
        with pytest.raises(DeviceMemoryError, match="^Incomplete reply to MEM.R 0x1000 4$"):
            Memory(device.exchange).read(0x1000, 4)

    def test_discontinuous_rows_refused(self):
        device = FakeDevice(override=lambda command: "00001000: 01 02\r\n00001010: 03 04\r\nOK\r\n")
        with pytest.raises(DeviceMemoryError, match="Discontinuous reply"):
            Memory(device.exchange).read(0x1000, 4)

    def test_short_reply_refused(self):
        device = FakeDevice(override=lambda command: "00001000: 01 02\r\nOK\r\n")
        with pytest.raises(DeviceMemoryError, match="Short reply: 2 of 4 bytes"):
            Memory(device.exchange).read(0x1000, 4)

    def test_failure_in_second_chunk_names_that_chunk(self):
        # Arrange -- first chunk fine, second silent
        device = FakeDevice(override=lambda command: "" if command == "MEM.R 0x1040 4" else None)
        memory = Memory(device.exchange, MemoryInfo(max_block=64))

        # Act / Assert
        with pytest.raises(DeviceMemoryError, match="No reply to MEM.R 0x1040 4"):
            memory.read(0x1000, 68)

    @pytest.mark.parametrize(
        "addr, length, message",
        [
            (0x1000, 0, "Invalid length: 0"),
            (0x1000, -1, "Invalid length: -1"),
            (-1, 1, "Invalid address: "),
            (1 << 32, 1, "Invalid address: 0x100000000"),
            (0xFFFFFFFF, 2, "Invalid address range: 0xFFFFFFFF \\+ 2 exceeds 32-bit space"),
        ],
    )
    def test_argument_validation(self, addr, length, message):
        device = FakeDevice()
        with pytest.raises(ValueError, match=message):
            Memory(device.exchange).read(addr, length)
        assert device.sent == [], "nothing goes to the wire for a bad argument"


# ── Memory.write ────────────────────────────────────────────────────────────


class TestWrite:

    def test_write_then_read_back(self):
        # Arrange
        device = FakeDevice()
        memory = Memory(device.exchange)

        # Act
        count = memory.write(0x1000, b"\x1b\x00")

        # Assert
        assert count == 2, "bytes written"
        assert device.sent == ["MEM.W 0x1000 1B00"], "one exchange, hex pairs with no spaces"
        assert memory.read(0x1000, 2) == b"\x1b\x00", "the device kept the bytes"

    def test_chunks_to_max_block(self):
        device = FakeDevice()
        memory = Memory(device.exchange, MemoryInfo(max_block=4))
        memory.write(0x1000, bytes(range(10)))
        assert device.sent == [
            "MEM.W 0x1000 00010203", "MEM.W 0x1004 04050607", "MEM.W 0x1008 0809",
        ], "4/4/2 chunks at advancing addresses"
        assert bytes(device.ram[:10]) == bytes(range(10)), "all ten landed"

    def test_device_error(self):
        device = FakeDevice()
        with pytest.raises(DeviceMemoryError, match="^Device error: range$"):
            Memory(device.exchange).write(0x9000, b"\x00")

    def test_empty_payload_refused_before_the_wire(self):
        device = FakeDevice()
        with pytest.raises(ValueError, match="Invalid length: 0"):
            Memory(device.exchange).write(0x1000, b"")
        assert device.sent == [], "nothing sent"


# ── MEM.INFO and the precedence resolver ────────────────────────────────────


class TestQueryInfo:

    def test_record_returned_as_sent(self):
        device = FakeDevice(max_block=32)
        assert Memory(device.exchange).query_info() == {
            "max_block": 32, "address_bits": 32, "endian": "be",
        }, "the JSON object, uncoerced"

    def test_device_without_info_answers_err(self):
        device = FakeDevice(override=lambda command: "ERR unknown command\r\n")
        with pytest.raises(DeviceMemoryError, match="^Device error: unknown command$"):
            Memory(device.exchange).query_info()

    def test_ok_without_record(self):
        device = FakeDevice(override=lambda command: "OK\r\n")
        with pytest.raises(DeviceMemoryError, match="No JSON record in the reply to MEM.INFO"):
            Memory(device.exchange).query_info()


class TestResolveInfo:

    def test_defaults(self):
        info = resolve_info(None, None)
        assert (info.dialect, info.max_block, info.address_bits, info.endian) == (
            "termapy", DEFAULT_MAX_BLOCK, DEFAULT_ADDRESS_BITS, "le",
        )
        assert set(info.sources.values()) == {"default"}, "every fact is a default"

    def test_device_fills_what_the_profile_leaves_open(self):
        info = resolve_info({"dialect": "termapy"}, {"max_block": 16, "endian": "be"})
        assert info.max_block == 16, "device value used"
        assert info.sources["max_block"] == "device", "and attributed to the device"
        assert info.endian == "be", "device value used"
        assert info.address_bits == 32, "default where neither spoke"
        assert info.sources["dialect"] == "profile", "the profile named the dialect"

    def test_profile_beats_device(self):
        info = resolve_info({"max_block": 8}, {"max_block": 64})
        assert info.max_block == 8, "an explicit profile value wins"
        assert info.sources["max_block"] == "profile", "attributed to the profile"

    def test_invalid_values_degrade_to_default(self):
        info = resolve_info({"max_block": 0, "endian": "middle", "address_bits": "32"}, None)
        assert (info.max_block, info.endian, info.address_bits) == (DEFAULT_MAX_BLOCK, "le", 32), (
            "each unusable value falls back on its own"
        )
        assert info.sources["max_block"] == "default", "and is reported as a default"

    def test_bool_is_not_an_int(self):
        info = resolve_info({"max_block": True}, None)
        assert info.max_block == DEFAULT_MAX_BLOCK, "True is not a block size"

    def test_unknown_dialect_is_kept_and_refused_by_the_engine(self):
        info = resolve_info({"dialect": "uboot"}, None)
        assert info.dialect == "uboot", "the resolver records what the profile said"
        with pytest.raises(ValueError, match="Unknown memory dialect: uboot \\(dialects: termapy\\)"):
            Memory(lambda command: "", info)


class TestValidateBlock:

    def test_clean_block_has_no_warnings(self):
        assert validate_block({"dialect": "termapy", "max_block": 64, "address_bits": 32, "endian": "le"}) == []

    def test_empty_block_is_fine(self):
        assert validate_block({}) == [], "every field is optional"

    @pytest.mark.parametrize(
        "block, fragment",
        [
            ({"dialect": "uboot"}, "memory/dialect: unrecognized dialect 'uboot'"),
            ({"max_block": 0}, "memory/max_block: expected a positive integer, got 0 (default 64 used)"),
            ({"address_bits": 7}, "memory/address_bits: expected an integer from 8 to 64"),
            ({"endian": "middle"}, "memory/endian: expected le or be, got 'middle' (treated as le)"),
        ],
    )
    def test_each_field_warns_with_its_degrade_rule(self, block, fragment):
        warnings = validate_block(block)
        assert len(warnings) == 1, "one warning per bad field"
        assert fragment in warnings[0], "names the field and the rule applied"

    def test_dialects_table_is_the_vocabulary(self):
        assert "termapy" in DIALECTS, "the published dialect is registered"
