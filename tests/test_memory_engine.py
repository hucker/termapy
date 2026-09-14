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
    DEFAULT_ROW_PATTERN,
    DIALECTS,
    DeviceMemoryError,
    DumpRow,
    Memory,
    MemoryInfo,
    assemble,
    dump_rows,
    format_row,
    make_dialect,
    modify_command,
    parse_reply,
    parse_template_block,
    read_command,
    reply_complete,
    resolve_info,
    row_record,
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
        if parts[0] == "MEM.M":
            addr = int(parts[1], 16)
            and_mask, or_mask = int(parts[2], 16), int(parts[3], 16)
            width = 1 if len(parts[2]) <= 2 else 2 if len(parts[2]) <= 4 else 4
            if not (BASE <= addr and addr + width <= BASE + SIZE):
                return ["ERR range"]
            old = bytes(self.ram[addr - BASE:addr - BASE + width])
            word = (int.from_bytes(old, "little") & and_mask) | or_mask
            self.ram[addr - BASE:addr - BASE + width] = word.to_bytes(width, "little")
            return [f"{addr:08X}: " + " ".join(f"{byte:02X}" for byte in old), "OK"]
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

    def test_err_with_colon_does_not_double_the_colon(self):
        # A real monitor (the m3 bench device) answers "ERR: reason";
        # capturing the colon rendered "Device error: : unknown command"
        reply = parse_reply("ERR: unknown command: MEM.INFO\r\n")
        assert reply.error == "unknown command: MEM.INFO", (
            "the separator colon is grammar, not reason text"
        )

    def test_error_is_not_err(self):
        reply = parse_reply("ERROR: Unknown command 'BOGUS'\r\n")
        assert reply.complete is False, (
            "ERROR: is a different grammar -- never the native ERR verdict"
        )

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

    def test_incomplete_says_what_arrived(self):
        device = FakeDevice(override=lambda command: "00001000: 01 02 03 04\r\n")
        with pytest.raises(
            DeviceMemoryError,
            match="^Incomplete reply to MEM.R 0x1000 4 \\(1 rows, no OK/ERR; last line '00001000: 01 02 03 04'\\)$",
        ):
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


# ── Memory.modify: MEM.M when advertised, read + write-back otherwise ───────


class TestModify:

    def test_atomic_path_sends_mem_m(self):
        # Arrange -- the device advertised the atomic modify
        device = FakeDevice()
        memory = Memory(device.exchange, MemoryInfo(atomic_modify=True))

        # Act -- set bit 4 of the u8 at 0x1000 (pattern byte 0x5A)
        old, new = memory.modify(0x1000, 1, 0xFF, 0x10)

        # Assert
        assert device.sent == ["MEM.M 0x1000 FF 10"], "one atomic exchange, masks zero-padded"
        assert old == b"\x5a", "the reply's row is the PRE-modify word"
        assert new == b"\x5a", "0x5A already has bit 4 set"
        assert bytes(device.ram[:1]) == b"\x5a", "device state agrees"

    def test_atomic_word_width_from_masks(self):
        device = FakeDevice()
        memory = Memory(device.exchange, MemoryInfo(atomic_modify=True))
        old, new = memory.modify(0x1000, 4, 0xFFFF7FFF, 0x00008000)
        assert device.sent == ["MEM.M 0x1000 FFFF7FFF 00008000"], "8-digit masks = u32"
        assert int.from_bytes(new, "little") == (int.from_bytes(old, "little") & 0xFFFF7FFF) | 0x8000
        assert bytes(device.ram[:4]) == new, "read-back matches the computed word"

    def test_fallback_is_read_then_write(self):
        # Arrange -- no MEM.INFO "modify": the documented racy fallback
        device = FakeDevice()
        memory = Memory(device.exchange, MemoryInfo())

        # Act
        old, new = memory.modify(0x1000, 1, 0x0F, 0x80)

        # Assert
        assert device.sent == ["MEM.R 0x1000 1", "MEM.W 0x1000 8A"], "read, then the masked word"
        assert (old, new) == (b"\x5a", b"\x8a"), "(0x5A & 0x0F) | 0x80"
        assert bytes(device.ram[:1]) == b"\x8a", "the write landed"

    def test_device_error(self):
        device = FakeDevice()
        memory = Memory(device.exchange, MemoryInfo(atomic_modify=True))
        with pytest.raises(DeviceMemoryError, match="^Device error: range$"):
            memory.modify(0x9000, 1, 0xFF, 0x00)

    @pytest.mark.parametrize(
        "width, and_mask, or_mask, message",
        [
            (3, 0, 0, "Invalid width: 3 \\(use 1, 2 or 4\\)"),
            (1, 0x100, 0, "Invalid and mask: 0x100 \\(8 bits\\)"),
            (2, 0, 0x10000, "Invalid or mask: 0x10000 \\(16 bits\\)"),
        ],
    )
    def test_argument_validation(self, width, and_mask, or_mask, message):
        device = FakeDevice()
        with pytest.raises(ValueError, match=message):
            Memory(device.exchange, MemoryInfo(atomic_modify=True)).modify(0x1000, width, and_mask, or_mask)
        assert device.sent == [], "nothing goes to the wire for a bad argument"

    def test_modify_command_format(self):
        assert modify_command(0xBF806000, 0xFFFF7FFF, 0x8000, 4) == "MEM.M 0xBF806000 FFFF7FFF 00008000"
        assert modify_command(0x1000, 0xFE, 0x01, 1) == "MEM.M 0x1000 FE 01"


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
        with pytest.raises(ValueError, match="Unknown memory dialect: uboot \\(dialects: termapy, template\\)"):
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


# ── The template dialect: a monitor with its own grammar ────────────────────


LEGACY_BLOCK = {
    "dialect": "template",
    "read": "mem {addr:X} {len}",
    "write": "mem {addr:X} ={byte:02X}",
    "ack": r"^ok\b",
    "error": r"(?i)^\s*err\b",
    "max_block": 256,
}


class FakeLegacyDevice:
    """A monitor speaking ``mem <addr> [count]`` / ``mem <addr> =<hex>``.

    Rows are ``ADDR:  XX XX XX XX  XX ...  |ascii|`` with no terminator;
    writes answer ``ok [ADDR] 0xOLD -> 0xNEW (byte)``; failures ``err: ...``.
    """

    def __init__(self, *, max_count: int = 256, echo: bool = False, prompt: str = "") -> None:
        self.ram = bytearray(((i * 7) ^ 0x5A) & 0xFF for i in range(SIZE))
        self.max_count = max_count
        self.echo = echo
        self.prompt = prompt
        self.sent: list[str] = []

    def exchange(self, command: str) -> str:
        self.sent.append(command)
        lines: list[str] = []
        if self.echo:
            lines.append(f"> {command}")
        lines.extend(self._answer(command))
        if self.prompt:
            lines.append(self.prompt)
        return "\r\n".join(lines) + "\r\n"

    def _answer(self, command: str) -> list[str]:
        parts = command.split()
        addr = int(parts[1], 16)
        if len(parts) > 2 and parts[2].startswith("="):
            if not BASE <= addr < BASE + SIZE:
                return [f"err: address {addr:08X} not mapped"]
            old = self.ram[addr - BASE]
            self.ram[addr - BASE] = int(parts[2][1:], 16)
            return [f"ok [{addr:08X}] 0x{old:02X} -> 0x{self.ram[addr - BASE]:02X} (byte)"]
        count = min(int(parts[2]) if len(parts) > 2 else 16, self.max_count)
        if not (BASE <= addr and addr + count <= BASE + SIZE):
            return [f"err: address range {addr:08X}..{addr + count - 1:08X} not mapped (would HardFault)"]
        data = self.ram[addr - BASE:addr - BASE + count]
        rows = []
        for i in range(0, count, 16):
            row = data[i:i + 16]
            line = f"{addr + i:08X}:"
            for j in range(16):
                if j % 4 == 0:
                    line += " "
                line += f" {row[j]:02X}" if j < len(row) else "   "
            ascii_part = "".join(chr(byte) if 0x20 <= byte <= 0x7E else "." for byte in row)
            rows.append(f"{line}  |{ascii_part}|")
        return rows


def _legacy_memory(device: FakeLegacyDevice, block: dict | None = None) -> Memory:
    block = LEGACY_BLOCK if block is None else block
    info = resolve_info(block, None)
    return Memory(device.exchange, info, make_dialect(info, block))


class TestTemplateBlock:

    def test_parse_defaults(self):
        spec = parse_template_block({"read": "rd {addr:X} {len}"})
        assert spec.write is None, "no write template = read-only"
        assert spec.ack is None, "no ack expected by default"
        assert spec.row.pattern == DEFAULT_ROW_PATTERN, "the generic row"
        assert spec.row_bytes == 16 and spec.settle_ms == 100, "defaults"

    @pytest.mark.parametrize(
        "block, message",
        [
            ({}, "memory/read: required for the template dialect"),
            ({"read": "rd {addr}"}, "memory/read: must use {addr} and {len}"),
            ({"read": "rd {addr} {len} {nope}"}, "memory/read: bad placeholder"),
            ({"read": "rd {addr} {len}", "write": "wr {addr}"}, "memory/write: must use {byte}"),
            ({"read": "rd {addr} {len}", "row": "("}, "memory/row: invalid regex"),
            ({"read": "rd {addr} {len}", "row": "^(?P<addr>..):"}, "memory/row: regex needs"),
            ({"read": "rd {addr} {len}", "ack": 3}, "memory/ack: expected a regex string"),
            ({"read": "rd {addr} {len}", "settle_ms": 0}, "memory/settle_ms: expected a positive integer"),
        ],
    )
    def test_problems_are_field_qualified(self, block, message):
        with pytest.raises(ValueError, match=message.replace("(", r"\(").replace("{", r"\{")):
            parse_template_block(block)

    def test_validate_block_reports_template_problems_as_warnings(self):
        warnings = validate_block({"dialect": "template"})
        assert any("memory/read: required" in warning and "refuse" in warning for warning in warnings), (
            "the lint says what /mem.* will do"
        )

    def test_make_dialect_needs_the_block(self):
        with pytest.raises(ValueError, match="memory/read: required"):
            make_dialect(resolve_info({"dialect": "template"}, None), None)

    def test_hex_write_template_means_block_writes(self):
        dialect = make_dialect(
            resolve_info(LEGACY_BLOCK, None), {**LEGACY_BLOCK, "write": "wr {addr:X} {hex}"},
        )
        assert dialect.write_unit == 0, "{hex} = one command per block"
        assert dialect.write_request(0x1000, b"\x1b\x00") == "wr 1000 1B00"


class TestTemplateRead:

    def test_rows_with_ascii_column(self):
        # Arrange
        device = FakeLegacyDevice()
        memory = _legacy_memory(device)

        # Act
        data = memory.read(0x1000, 20)

        # Assert
        assert device.sent == ["mem 1000 20"], "the read template, addr in hex, len in decimal"
        assert data == bytes(device.ram[:20]), "grouped pairs parsed, ASCII column ignored"

    def test_chunks_to_max_block(self):
        device = FakeLegacyDevice()
        memory = _legacy_memory(device, {**LEGACY_BLOCK, "max_block": 32})
        assert memory.read(0x1000, 70) == bytes(device.ram[:70]), "32 + 32 + 6"
        assert device.sent == ["mem 1000 32", "mem 1020 32", "mem 1040 6"], "chunked to the device's limit"

    def test_echo_and_prompt_tolerated(self):
        device = FakeLegacyDevice(echo=True, prompt="mon> ")
        assert _legacy_memory(device).read(0x1000, 4) == bytes(device.ram[:4])

    def test_device_error_line_is_the_message(self):
        device = FakeLegacyDevice()
        with pytest.raises(DeviceMemoryError, match="^Device error: err: address range 00009000..00009003 not mapped"):
            _legacy_memory(device).read(0x9000, 4)

    def test_silence(self):
        memory = _legacy_memory(FakeLegacyDevice())
        memory._exchange = lambda command: ""
        with pytest.raises(DeviceMemoryError, match="^No reply to mem 1000 4$"):
            memory.read(0x1000, 4)

    def test_unrecognized_reply(self):
        memory = _legacy_memory(FakeLegacyDevice())
        memory._exchange = lambda command: "Unknown command: mem\r\n"
        with pytest.raises(DeviceMemoryError, match="^Unrecognized reply to mem 1000 4: Unknown command: mem$"):
            memory.read(0x1000, 4)

    def test_short_reply(self):
        memory = _legacy_memory(FakeLegacyDevice())
        memory._exchange = lambda command: "00001000:  01 02\r\n"
        with pytest.raises(DeviceMemoryError, match="Short reply: 2 of 4 bytes"):
            memory.read(0x1000, 4)

    def test_bare_ascii_column_cannot_lengthen_a_full_row(self):
        # Arrange -- a dump whose ASCII column is not delimited and starts
        # with hex-looking text: the row cap keeps it out of the data
        memory = _legacy_memory(FakeLegacyDevice())
        row = " ".join(f"{i:02X}" for i in range(16))
        memory._exchange = lambda command: f"00001000: {row}  AB..CD..\r\n00001010: 10 11 12 13  ....\r\n"

        # Act
        data = memory.read(0x1000, 20)

        # Assert
        assert data == bytes(range(16)) + b"\x10\x11\x12\x13", "16 per row, then the tail; nothing from the column"

    def test_terminator_completes_the_reply(self):
        dialect = make_dialect(resolve_info(LEGACY_BLOCK, None), {**LEGACY_BLOCK, "terminator": r"^mon> $"})
        assert dialect.complete("00001000:  01\r\n") is False, "still waiting"
        assert dialect.complete("00001000:  01\r\nmon> ") is True, "the prompt ends it"

    def test_no_terminator_never_completes_early(self):
        dialect = make_dialect(resolve_info(LEGACY_BLOCK, None), LEGACY_BLOCK)
        assert dialect.complete("00001000:  01\r\n") is False, "framed by the idle gap instead"
        assert dialect.settle_ms == 100, "the default gap"


class TestReadOnlyTemplate:
    """A template block with no ``write`` refuses BEFORE any device traffic."""

    @staticmethod
    def _read_only(device: FakeLegacyDevice) -> Memory:
        block = {key: value for key, value in LEGACY_BLOCK.items() if key != "write"}
        return _legacy_memory(device, block)

    def test_write_refused_with_no_traffic(self):
        # Arrange
        device = FakeLegacyDevice()
        memory = self._read_only(device)

        # Act / Assert
        with pytest.raises(DeviceMemoryError, match="^Read-only memory interface"):
            memory.write(0x1000, b"\x01")
        assert device.sent == [], "the refusal costs no device traffic"

    def test_modify_refused_before_the_read(self):
        # Arrange -- RMW reads first; a read-only dialect must refuse
        # before that read, never after (a word read but not written
        # back is a half-done RMW that looks like success)
        device = FakeLegacyDevice()
        memory = self._read_only(device)

        # Act / Assert
        with pytest.raises(DeviceMemoryError, match="^Read-only memory interface"):
            memory.modify(0x1000, 2, 0xFFFF, 0x0001)
        assert device.sent == [], "refused before the read half of the RMW"


class TestTemplateWrite:

    def test_one_command_per_byte_with_ack(self):
        # Arrange
        device = FakeLegacyDevice()
        memory = _legacy_memory(device)

        # Act
        count = memory.write(0x1000, b"\x2c\x01")

        # Assert
        assert count == 2
        assert device.sent == ["mem 1000 =2C", "mem 1001 =01"], "the write template, one byte each"
        assert bytes(device.ram[:2]) == b"\x2c\x01", "both landed"

    def test_missing_ack_is_an_error(self):
        memory = _legacy_memory(FakeLegacyDevice())
        memory._exchange = lambda command: ""
        with pytest.raises(DeviceMemoryError, match="^No acknowledgement to mem 1000 =2C$"):
            memory.write(0x1000, b"\x2c")

    def test_unexpected_reply_is_an_error(self):
        memory = _legacy_memory(FakeLegacyDevice())
        memory._exchange = lambda command: "huh?\r\n"
        with pytest.raises(DeviceMemoryError, match="^Unexpected reply to mem 1000 =2C: huh\\?$"):
            memory.write(0x1000, b"\x2c")

    def test_device_error_on_write(self):
        with pytest.raises(DeviceMemoryError, match="^Device error: err: address 00009000 not mapped$"):
            _legacy_memory(FakeLegacyDevice()).write(0x9000, b"\x00")

    # Read-only refusal (write AND modify, before any traffic) is
    # TestReadOnlyTemplate's job above.

    def test_no_ack_configured_accepts_silence(self):
        block = {key: value for key, value in LEGACY_BLOCK.items() if key != "ack"}
        memory = _legacy_memory(FakeLegacyDevice(), block)
        memory._exchange = lambda command: ""
        assert memory.write(0x1000, b"\x00") == 1, "fire-and-forget write is fine when no ack is declared"

    def test_query_info_refused(self):
        with pytest.raises(DeviceMemoryError, match="Dialect template has no MEM.INFO"):
            _legacy_memory(FakeLegacyDevice()).query_info()


# ── Dump rows ───────────────────────────────────────────────────────────────


class TestDumpRows:

    def test_splits_by_width_with_short_tail(self):
        rows = dump_rows(0x1000, bytes(range(20)), width=16)
        assert [(row.addr, len(row.data)) for row in rows] == [(0x1000, 16), (0x1010, 4)], "16 then the tail"

    def test_label_callback_per_row(self):
        rows = dump_rows(0x1000, bytes(32), label=lambda addr: f"sym+0x{addr - 0x1000:X}")
        assert [row.label for row in rows] == ["sym+0x0", "sym+0x10"], "the label sees each row's address"

    def test_hex_and_ascii(self):
        row = DumpRow(0x1000, b"AB\x00\x7f")
        assert row.hex == "41 42 00 7F", "upper-case pairs"
        assert row.ascii == "AB..", "non-printables become dots"

    def test_format_row_pads_the_short_row(self):
        # Arrange -- a 2-byte row on a 16-byte width
        line = format_row(DumpRow(0x1000, b"\x1b\x00", "gTemp"), address_bits=32)

        # Assert
        assert line == "0x00001000  1B 00" + " " * 42 + "  |..|  gTemp", (
            "hex column padded to 16 bytes so the ASCII column aligns; label last"
        )

    def test_format_row_without_label_has_no_trailing_spaces(self):
        line = format_row(DumpRow(0x1000, bytes(16)))
        assert line.endswith("|................|"), "no label, no trailing separator"

    def test_row_record_shape(self):
        record = row_record(DumpRow(0x1000, b"\x01", "main"), address_bits=16)
        assert record == {"addr": 0x1000, "addr_hex": "0x1000", "hex": "01", "ascii": ".", "symbolic": "main"}
