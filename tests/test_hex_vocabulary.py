"""Hex has one vocabulary: one reader for input, in any case; one case for output.

Until 2026-10-13 the byte-payload commands each read hex their own way:
``/proto.send`` took every spelling, ``/proto.crc.verify`` and ``.find`` took
``0x`` tokens but refused a run-together frame, ``.reverse`` refused ``0x``, and
``/proto.crc.calc`` guessed -- anything that was not all two-character pairs
became text, so ``0x01 0x03 ...`` silently got the CRC of its characters.  Now
every byte payload is read by ``protocol.parse_data`` (hex in any spelling and
either case, quoted text for ASCII), and every hex value a command prints or
returns is uppercase.  These tests drive the commands the way a user does, on
a CLI with no port open (none of them needs a device).
"""

from __future__ import annotations

import json

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.protocol import parse_data, parse_hex, parse_pattern
from termapy.symbols import parse_number

# One Modbus request, 01 03 00 00 00 0A, whose crc16-modbus is 0xCDC5.
_FRAME = bytes.fromhex("01030000000A")
_FRAME_CRC = "0xCDC5"
_SPELLINGS = {
    "spaced pairs": "01 03 00 00 00 0A",
    "lowercase": "01 03 00 00 00 0a",
    "0x tokens": "0x01 0x03 0x00 0x00 0x00 0x0A",
    "0X tokens": "0X01 0X03 0X00 0X00 0X00 0X0a",
    "run together": "01030000000A",
    "commas": "01,03,00,00,00,0A",
}


@pytest.fixture
def cli(tmp_path):
    """A CLI with no port open, its cfg folder under tmp_path."""
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(DEFAULT_CFG))
    terminal = CLITerminal(dict(DEFAULT_CFG), str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    return terminal


# ── the readers ─────────────────────────────────────────────────────────────


class TestInputIsCaseInsensitive:

    @pytest.mark.parametrize("spelling", list(_SPELLINGS.values()), ids=list(_SPELLINGS))
    def test_parse_hex_and_parse_data_read_every_spelling(self, spelling):
        """hex-one-reader: both byte readers read every spelling, in either case, to the same bytes."""
        assert parse_hex(spelling) == _FRAME, "parse_hex"
        assert parse_data(spelling) == _FRAME, "parse_data"

    def test_a_pattern_reads_either_case(self):
        """An expect pattern's hex and its wildcards read the same in either case."""
        assert parse_pattern("4f ** 0D") == parse_pattern("4F ** 0d"), "case never changes a pattern"

    @pytest.mark.parametrize("text", ["0x1f", "0X1F", "1fh", "1FH"])
    def test_a_hex_number_reads_either_case(self, text):
        """The number reader takes 0x and h forms in either case."""
        assert parse_number(text) == 0x1F


# ── the commands that take a byte payload ───────────────────────────────────


class TestOnePayloadGrammar:

    @pytest.mark.parametrize("spelling", list(_SPELLINGS.values()), ids=list(_SPELLINGS))
    def test_calc_reads_every_spelling_as_bytes(self, cli, spelling):
        """/proto.crc.calc reads hex in every spelling as bytes; it once returned the CRC of the characters."""
        result = cli.repl.dispatch_full(f"/proto.crc.calc crc16-modbus {spelling}")
        assert result.value == _FRAME_CRC, "the CRC of the six bytes, whatever the spelling"

    @pytest.mark.parametrize("spelling", list(_SPELLINGS.values()), ids=list(_SPELLINGS))
    def test_send_reads_every_spelling_as_the_same_frame(self, cli, spelling):
        """/proto.send --dry-run returns the same frame for every spelling."""
        result = cli.repl.dispatch_full(f"/proto.send --dry-run {spelling}")
        assert result.value == _FRAME.hex().upper(), "the same six bytes, as uppercase hex"

    @pytest.mark.parametrize("spelling", list(_SPELLINGS.values()), ids=list(_SPELLINGS))
    def test_verify_reads_every_spelling(self, cli, spelling):
        """/proto.crc.verify reads the frame and its trailer in every spelling."""
        result = cli.repl.dispatch_full(f"/proto.crc.verify crc16-modbus endian=le {spelling} C5 CD")
        assert result.success is True, result.error

    @pytest.mark.parametrize("spelling", list(_SPELLINGS.values()), ids=list(_SPELLINGS))
    def test_find_reads_every_spelling(self, cli, spelling):
        """/proto.crc.find bin= reads the packet in every spelling and identifies its CRC."""
        result = cli.repl.dispatch_full(f"/proto.crc.find bin={spelling} C5 CD")
        assert result.success is True, result.error
        assert "crc16-modbus" in str(result.value), "the Modbus CRC is identified"

    def test_reverse_reads_0x_packets(self, cli):
        """/proto.crc.reverse reads each packet token with the shared reader, so 0x is accepted there too."""
        # Arrange -- a malformed token, then the same packet spelled with 0x
        bad = cli.repl.dispatch_full("/proto.crc.reverse crc_bytes=2 ZZ 0102AABB")
        prefixed = cli.repl.dispatch_full("/proto.crc.reverse crc_bytes=2 0x01020304AABB 0x0102AABB")

        # Assert -- the reader refuses ZZ and accepts 0x; what follows is the reversal's own verdict
        assert bad.error.startswith("Parse error: "), bad.error
        assert not prefixed.error.startswith("Parse error"), "0x is a spelling, not an error"


class TestCalcText:

    def test_quoted_text_is_text(self, cli):
        """Text goes in quotes, as for /proto.send; the catalog check string gives the catalog check value."""
        result = cli.repl.dispatch_full('/proto.crc.calc crc32 "123456789"')
        assert result.value == "0xCBF43926", "crc32's catalog check value"

    def test_unquoted_text_is_refused_with_a_hint(self, cli):
        """Unquoted text is a Parse error that says text goes in quotes, never a silent CRC of something else."""
        result = cli.repl.dispatch_full("/proto.crc.calc crc32 hello")
        assert result.success is False
        assert result.error.startswith("Parse error: "), result.error
        assert 'text goes in quotes: "hello"' in result.error, "the hint names the fix"

    def test_bare_calc_checks_the_catalog_string(self, cli, capsys):
        """With no data, calc computes over the catalog's check string and reports the check."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/proto.crc.calc crc32")
        assert result.value == "0xCBF43926"
        assert "Check:     PASS" in capsys.readouterr().out


# ── output case ─────────────────────────────────────────────────────────────


class TestOutputIsUppercase:

    def test_send_returns_uppercase_hex(self, cli):
        """hex-output-uppercase: /proto.send's scriptable value is uppercase, matching what it prints."""
        result = cli.repl.dispatch_full("/proto.send --dry-run 0a 0b")
        assert result.value == "0A0B"

    def test_a_crc_value_is_uppercase(self, cli):
        """/proto.crc.calc returns its CRC as 0x-prefixed uppercase, whatever case the bytes were typed in."""
        result = cli.repl.dispatch_full("/proto.crc.calc crc16-modbus 01 03 00 00 00 0a")
        assert result.value == "0xCDC5"
