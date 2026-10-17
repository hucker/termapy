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
from termapy.plugins.capabilities import STATE_MESSAGES

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
    for sub in ("plugin", "ss", "run", "proto", "cap", "sym"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    shutil.copyfile(DEMO_SYMBOLS, config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}")
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    terminal.repl.fire_lifecycle("on_app_start")
    logged: list[tuple[str, str]] = []
    terminal.ctx.io.log = lambda prefix, text: logged.append((prefix, text))
    terminal.audit = logged  # ty: ignore[unresolved-attribute]
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


class TestDump:

    def test_by_symbol(self, cli):
        """A symbol resolves to its address, and the value is the bytes as one hex string."""
        # Act
        result = cli.repl.dispatch("mem.dump gTemp 16")

        # Assert
        assert result.success, result.error
        assert result.value.startswith("1B00"), "gTemp is seeded to 27 (u16 le); value is the hex string"
        assert len(result.value) == 32, "16 bytes -> 32 hex digits"

    def test_rows_render_with_symbol_annotation(self, cli, capsys):
        """Each dump row is address, hex pairs, ASCII, then the symbol that contains it."""
        # Act
        cli.repl.dispatch("mem.dump gTemp 16")

        # Assert
        line = next(line for line in capsys.readouterr().out.splitlines() if "0x00001000" in line)
        assert line.lstrip().startswith("0x00001000  1B 00 "), "address, then hex pairs (CLI indents output)"
        assert line.rstrip().endswith("|  gTemp"), "ASCII column, then the containing symbol"

    def test_default_length_is_64(self, cli):
        """A bare address with no length reads 64 bytes, the explore-sized default."""
        result = cli.repl.dispatch("mem.dump 0x1000")
        assert len(result.value) == 128, "64 bytes by default"

    def test_offset_form_labels_rows_relative_to_the_symbol(self, cli):
        """A symbol+offset dump labels its data rows main+0x10, main+0x20 as it walks on."""
        # Act -- data path, so the records carry the labels
        cli.ctx.wants_data = True  # what the dispatcher sets for --json / MCP
        result = cli.repl.dispatch("mem.dump main+0x10 32")

        # Assert
        assert result.data["rows"][0]["symbolic"] == "main+0x10", "first row is inside main"
        assert result.data["rows"][1]["symbolic"] == "main+0x20", "rows advance through the symbol"

    def test_read_crosses_the_block_limit(self, cli):
        """A dump past the 64-byte block splits into MEM.R exchanges and returns every byte."""
        result = cli.repl.dispatch("mem.dump 0x1000 100")
        assert result.success, result.error
        assert len(result.value) == 200, "100 bytes came back through two MEM.R exchanges"

    def test_data_record_shape(self, cli):
        """The --json/MCP record carries address, length, hex, and every per-row key."""
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
            ("mem.dump U1MODE.ON", "Unsupported address suffix here: .ON"),
            ("mem.dump 0x9000 4", "Device error: range"),
            ("mem.dump main-0x9000", "Invalid address: main-0x9000"),
        ],
    )
    def test_errors(self, cli, line, message):
        """Lookup, suffix, offset, and device failures each surface their own message."""
        result = cli.repl.dispatch(line)
        assert not result.success, "refused"
        assert message in result.error, "the address grammar's / device's own message"

    @pytest.mark.parametrize("token, expected_len", [("16", 16), ("0x10", 16), ("10h", 16)])
    def test_length_takes_the_address_grammar_numbers(self, cli, token, expected_len):
        """Length accepts decimal, 0x hex, and the h suffix -- the same numbers as addresses."""
        result = cli.repl.dispatch(f"mem.dump gTemp {token}")
        assert result.success, result.error
        assert len(result.value) == expected_len * 2, "decimal, 0x hex and h-suffix all mean sixteen"

    @pytest.mark.parametrize("token", ["0", "zz", "-4", "1.5"])
    def test_bad_length(self, cli, token):
        """Zero, non-numeric, negative, and fractional lengths are refused, naming the token."""
        result = cli.repl.dispatch(f"mem.dump 0x1000 {token}")
        assert not result.success, "refused"
        assert f"Invalid length: {token}" in result.error, "names the token"

    def test_bare_addr_is_usage(self, cli):
        """Omitting the address yields the synthesized usage error, not a default target."""
        result = cli.repl.dispatch("mem.dump")
        assert not result.success, "addr is required"
        assert "Usage" in result.error, "synthesized usage"


class TestWrite:

    def test_write_then_read_back(self, cli):
        """Written bytes persist on the device, and the value is the byte count."""
        # Act
        written = cli.repl.dispatch("mem.write gFlags 07000000")
        read = cli.repl.dispatch("mem.dump gFlags 4")

        # Assert
        assert written.success, written.error
        assert written.value == "4", "byte count is the value"
        assert read.value == "07000000", "the device kept the bytes"

    def test_audit_line_in_the_session_log(self, cli):
        """A write logs one MEM.W line: address, before and after bytes, and origin."""
        # Act
        cli.repl.dispatch("mem.write gTemp 2C01")

        # Assert
        audits = [text for prefix, text in cli.audit if prefix == "#" and text.startswith("MEM.W")]
        assert audits == ["MEM.W 0x00001000 before=1B00 after=2C01 origin=cli"], (
            "one # line: address, old bytes, new bytes, origin"
        )

    def test_result_line_shows_what_was_there(self, cli, capsys):
        """The feedback line names the symbol and shows the bytes the write replaced."""
        cli.repl.dispatch("mem.write gTemp 2C01")
        out = capsys.readouterr().out
        assert "Wrote 2 bytes at 0x00001000  gTemp  (was 1B00)" in out, "before-bytes in the feedback"

    def test_data_record(self, cli):
        """The data record holds before and after hex; spaced hex pairs are accepted as input."""
        cli.ctx.wants_data = True  # what the dispatcher sets for --json / MCP
        result = cli.repl.dispatch("mem.write 0x1000 2C 01")
        assert result.data == {
            "addr": 0x1000, "addr_hex": "0x00001000", "count": 2,
            "before_hex": "1B00", "before_skipped": "", "after_hex": "2C01", "origin": "cli",
        }, "spaced hex pairs accepted; record carries before and after"

    def test_write_crosses_the_block_limit(self, cli):
        """A 70-byte write spans two MEM.W exchanges, and every byte lands in order."""
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
        """Non-hex, odd digit counts, unknown symbols, and device errors all refuse the write."""
        result = cli.repl.dispatch(line)
        assert not result.success, "refused"
        assert message in result.error

    def test_no_audit_when_the_write_fails(self, cli):
        """A device-refused write leaves no MEM.W line, so the audit never claims a change."""
        cli.repl.dispatch("mem.write 0x9000 00")
        assert not any(text.startswith("MEM.W") for _, text in cli.audit), "nothing was written, nothing is audited"


class TestRead:
    """One typed value: scalars, chars, register fields, bits, slices."""

    def test_typed_symbol_scalar(self, cli):
        """A symbol's own type decodes it with no type token, and the data names the type."""
        result = cli.repl.dispatch("mem.read gTemp")
        assert result.success, result.error
        assert result.value == "27", "gTemp's u16 type decodes the seed"
        assert result.data["type"] == "u16"

    def test_explicit_type_overrides(self, cli):
        """A type token overrides the symbol's type -- u8 reads just gTemp's first byte."""
        result = cli.repl.dispatch("mem.read gTemp u8")
        assert result.value == "27", "first byte as u8"

    def test_hex_twin_is_the_decoded_value_not_the_raw_bytes(self, cli, capsys):
        """On LE, the 0x twin is the decoded scalar (0x001B), not the wire bytes (0x1B00)."""
        # Arrange -- gTemp is u16 = 27 on a little-endian device, so the
        # bytes on the wire are 1B 00 while the VALUE is 0x001B.  Printing
        # the bytes with an 0x prefix reads as a byte-swapped register.

        # Act
        cli.repl.dispatch("mem.read gTemp")

        # Assert
        actual = capsys.readouterr().out
        assert "gTemp = 27  (0x001B)" in actual, (
            "the hex twin formats the decoded scalar; 0x1B00 would be the raw bytes"
        )

    def test_hex_twin_of_a_signed_value_is_twos_complement(self, cli, capsys):
        """A negative value's hex twin is two's complement at its width, never a signed -2."""
        # Arrange -- put a negative i32 in gFlags: bytes FE FF FF FF little
        # endian is -2.  A plain f"{-2:X}" would render "-2".
        cli.repl.dispatch("mem.write gFlags FEFFFFFF")
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch("mem.read gFlags i32")

        # Assert
        assert result.value == "-2", "the decoded signed value"
        assert "gFlags = -2  (0xFFFFFFFE)" in capsys.readouterr().out, (
            "a negative value renders as two's complement at the symbol's width"
        )

    def test_char_token(self, cli, capsys):
        """The char type renders the byte as a quoted character while the value stays numeric."""
        result = cli.repl.dispatch("mem.read sBanner char")
        assert result.value == "66", "the value is the byte"
        assert "sBanner = 'B' (0x42)" in capsys.readouterr().out, "rendered as a character"

    def test_register_shows_all_fields(self, cli, capsys):
        """A format-spec register renders every field, and the display agrees with the data."""
        # Act -- U1MODE has a format-spec type: apply_format renders it
        result = cli.repl.dispatch("mem.read U1MODE")

        # Assert
        assert result.success, result.error
        assert result.data["fields"] == {"ON": 1, "UEN": 0, "BRGH": 1}, "the seeded 0x8008"
        out = capsys.readouterr().out
        assert "ON       = 1" in out, "the DISPLAY agrees with the extract twin (gold caught it at 0)"
        assert "BRGH     = 1" in out, "bit 3 rendered from the combined word"

    def test_named_field(self, cli):
        """A .FIELD read returns that field's value and carries the whole word in the data."""
        result = cli.repl.dispatch("mem.read U1MODE.ON")
        assert result.value == "1", "bit 15 of the LE word is set"
        assert result.data["word"] == 0x8008, "the whole word rides along"

    def test_numeric_bit_and_slice(self, cli):
        """A .N bit number and a .lo-hi slice extract just as a named field does."""
        assert cli.repl.dispatch("mem.read U1MODE.15").value == "1", "same bit by number"
        assert cli.repl.dispatch("mem.read gFlags.0-2").value == "5", "the low three bits of 5"

    def test_bare_address_bit(self, cli):
        """A .bit suffix works on a raw address too, not only on a named register."""
        assert cli.repl.dispatch("mem.read 0xBF806000.3").value == "1", "BRGH by raw address"

    def test_float_symbol(self, cli):
        """An f32 symbol decodes to its float value, not to an integer of its bytes."""
        result = cli.repl.dispatch("mem.read gPressure")
        assert result.value == "1013.25", "the seeded f32"

    def test_bare_address_needs_a_type(self, cli):
        """A raw address has no type to decode by, so a read without one is refused."""
        result = cli.repl.dispatch("mem.read 0x1000")
        assert not result.success
        assert "Type required" in result.error

    @pytest.mark.parametrize(
        "line, message",
        [
            ("mem.read gTemp u128", "Unknown type: u128"),
            ("mem.read U1MODE.BOGUS", "Unknown field: U1MODE.BOGUS (fields: ON, UEN, BRGH)"),
            ("mem.read gFlags.32", "Invalid bit: 32 (the word is 32 bits)"),
        ],
    )
    def test_errors(self, cli, line, message):
        """Unknown types and fields and out-of-word bits refuse; a bad field lists the real ones."""
        result = cli.repl.dispatch(line)
        assert not result.success
        assert message in result.error


class TestBitWrites:
    """A .field / .bit target on /mem.write is a masked word write."""

    def test_clear_a_named_field(self, cli, capsys):
        """Clearing ON goes atomic via MEM.M, reports old and new words, and reads back 0."""
        # Act
        result = cli.repl.dispatch("mem.write U1MODE.ON 0")

        # Assert
        assert result.success, result.error
        assert result.value == "0", "the field value written"
        assert result.data["atomic"] is True, "the demo advertises MEM.M"
        assert result.data["before_word"] == 0x8008 and result.data["after_word"] == 0x0008
        assert "Set U1MODE.ON = 0  (0x00008008 -> 0x00000008)" in capsys.readouterr().out
        assert cli.repl.dispatch("mem.read U1MODE.ON").value == "0", "read-back"

    def test_audit_carries_the_word_change(self, cli):
        """An atomic modify logs a MEM.M line with whole-word before/after in memory order."""
        cli.repl.dispatch("mem.write U1MODE.ON 0")
        audits = [text for prefix, text in cli.audit if text.startswith("MEM.M")]
        assert audits == ["MEM.M 0xBF806000 before=08800000 after=08000000 origin=cli"], (
            "atomic modifies audit like writes, memory-order bytes"
        )

    def test_set_a_slice(self, cli):
        """Writing a multi-bit slice sets only those bits and leaves the neighbors alone."""
        result = cli.repl.dispatch("mem.write gFlags.4-6 5")
        assert result.success, result.error
        assert cli.repl.dispatch("mem.read gFlags.4-6").value == "5", "three bits landed"
        assert cli.repl.dispatch("mem.read gFlags.0-2").value == "5", "neighbors untouched"

    def test_value_too_big(self, cli):
        """A value wider than the field is refused rather than silently truncated."""
        result = cli.repl.dispatch("mem.write U1MODE.ON 2")
        assert not result.success
        assert "Invalid value: 2 (ON is 1 bit)" in result.error

    def test_rmw_refused_on_w1c(self, cli):
        """A bit write to a w1c (rmw: false) register is refused, writing and auditing nothing."""
        result = cli.repl.dispatch("mem.write U1STA.0 1")
        assert not result.success
        assert "RMW refused: U1STA is rmw: false" in result.error
        assert not any(text.startswith("MEM.M") for _, text in cli.audit), "nothing written, nothing audited"


class TestMaskOps:

    def test_or_width_from_symbol_type(self, cli, capsys):
        """OR takes its width from the symbol's u32 type and rides the atomic MEM.M."""
        # Act -- gFlags is u32 = 5; set bit 4
        result = cli.repl.dispatch("mem.or gFlags 0x10")

        # Assert
        assert result.success, result.error
        assert result.value == "00000015", "the new word"
        assert result.data["atomic"] is True, "or rides MEM.M"
        assert "OR 0x00000010 at 0x00001008  gFlags  (0x00000005 -> 0x00000015)" in capsys.readouterr().out

    def test_and(self, cli):
        """AND keeps only the mask bits and returns the new word as hex."""
        result = cli.repl.dispatch("mem.and gFlags 0x1")
        assert result.value == "00000001", "5 & 1"

    def test_xor_is_never_atomic(self, cli):
        """XOR cannot be expressed as (word & and) | or, so it never rides MEM.M."""
        result = cli.repl.dispatch("mem.xor gFlags 0xFF")
        assert result.value == "000000FA", "5 ^ 0xFF"
        assert result.data["atomic"] is False, "xor cannot ride (word & and) | or"

    def test_mask_width_from_digit_count_on_a_bare_address(self, cli):
        """With no symbol type, the mask's hex digit count sets the width: 0xE0 is one byte."""
        # Arrange -- 2 hex digits = a byte op at gTemp's first byte
        result = cli.repl.dispatch("mem.or 0x1000 0xE0")

        # Assert
        assert result.success, result.error
        assert result.data["width"] == 1, "0xE0 is a u8 mask"
        assert result.value == "FB", "0x1B | 0xE0"

    def test_clear_is_the_complement_footgun_removed(self, cli):
        """/mem.clear takes the bits to clear, so no mask is hand-complemented; it is atomic."""
        # Act -- gFlags is 5; clear bit 0 without hand-complementing a mask
        result = cli.repl.dispatch("mem.clear gFlags 0x1")

        # Assert
        assert result.success, result.error
        assert result.value == "00000004", "5 & ~1"
        assert result.data["atomic"] is True, "clear is (word & ~mask) | 0: MEM.M-able"

    def test_not_inverts_the_word(self, cli):
        """NOT inverts at the symbol width, is never atomic, and applied twice restores it."""
        result = cli.repl.dispatch("mem.not gFlags")
        assert result.value == "FFFFFFFA", "~5 over the u32 width"
        assert result.data["atomic"] is False, "NOT depends on the old value"
        assert cli.repl.dispatch("mem.not gFlags").value == "00000005", "involution"

    def test_not_width_from_symbol_type(self, cli):
        """NOT on a u16 symbol works on two bytes, not a default word size."""
        result = cli.repl.dispatch("mem.not gTemp")
        assert result.data["width"] == 2, "gTemp is u16"

    @pytest.mark.parametrize("line", ["mem.or U1STA 1", "mem.clear U1STA 1", "mem.not U1STA"])
    def test_rmw_refused_on_w1c(self, cli, line):
        """OR, clear, and NOT each refuse a w1c (rmw: false) register, as bit writes do."""
        result = cli.repl.dispatch(line)
        assert not result.success
        assert "RMW refused: U1STA is rmw: false" in result.error

    def test_bad_mask(self, cli):
        """A non-numeric mask is refused, naming the token."""
        result = cli.repl.dispatch("mem.or gFlags zz")
        assert "Invalid mask: zz" in result.error


class TestStr:

    def test_banner_by_symbol(self, cli, capsys):
        """The NUL-terminated string is the value, with its length and an untruncated flag."""
        # Act
        result = cli.repl.dispatch("mem.str sBanner")

        # Assert
        assert result.success, result.error
        assert result.value == "Bassomatic v77", "the NUL-terminated string is the value"
        assert result.data["length"] == 14 and result.data["truncated"] is False
        assert '0x00003100  sBanner  "Bassomatic v77"' in capsys.readouterr().out

    def test_cap_truncates(self, cli):
        """A cap reached with no NUL ends the scan there and flags the result truncated."""
        # Arrange -- code bytes at main have no NUL in the first 8
        result = cli.repl.dispatch("mem.str 0x2000 8")
        assert result.data["truncated"] is True, "no NUL within the cap"
        assert result.data["length"] == 8

    def test_bad_cap(self, cli):
        """A non-numeric cap is refused with the same Invalid length message as dump."""
        result = cli.repl.dispatch("mem.str sBanner zz")
        assert "Invalid length: zz" in result.error


class TestDumpModes:

    def test_u16_word_columns(self, cli, capsys):
        """A u16 mode renders LE hex words per column while the value stays raw bytes."""
        # Act
        result = cli.repl.dispatch("mem.dump gTemp 16 u16")

        # Assert
        assert result.success, result.error
        line = capsys.readouterr().out.splitlines()[0].lstrip()
        assert line.startswith("0x00001000  001B 5000 447D 213A"), "LE u16 hex words"
        assert result.value.startswith("1B00"), "value stays the raw bytes"

    def test_i16_decimal(self, cli):
        """An i16 mode puts signed decimal values in each data row's values list."""
        cli.ctx.wants_data = True  # what the dispatcher sets for --json / MCP
        result = cli.repl.dispatch("mem.dump gTemp 4 i16")
        assert result.data["rows"][0]["values"] == [27, 20480], "signed decimal values in the records"

    def test_bare_mode(self, cli, capsys):
        """With addr and ascii both off, each row is only hex values, with no symbol label."""
        # Act -- both columns off: nothing but values
        cli.repl.dispatch("mem.dump gTemp 4 addr=off ascii=off")

        # Assert
        line = capsys.readouterr().out.splitlines()[0].lstrip()
        assert line == "1B 00 00 50", "no address, no ascii, no label"

    def test_addr_off_keeps_ascii(self, cli, capsys):
        """Turning off only the address column keeps the ASCII column."""
        cli.repl.dispatch("mem.dump sBanner 8 addr=off")
        line = capsys.readouterr().out.splitlines()[0].lstrip()
        assert line.startswith("42 61 73 73") and "|Bassomat|" in line, "ascii column without addresses"

    def test_word_length_must_align(self, cli):
        """A length that is not a multiple of the word size is refused, naming the type."""
        result = cli.repl.dispatch("mem.dump gTemp 3 u16")
        assert "Invalid length: 3 (not a multiple of 2 for u16)" in result.error

    @pytest.mark.parametrize("line", [
        "mem.dump gTemp 4 addr=false ascii=no",
        "mem.dump gTemp 4 addr=0 ascii=OFF",
    ])
    def test_toggles_take_the_whole_parse_bool_vocabulary(self, cli, line, capsys):
        """The addr= and ascii= toggles accept any parse_bool token, such as false, no, 0, OFF."""
        # Act -- on/off is canonical, but any scripting.parse_bool token works
        result = cli.repl.dispatch(line)

        # Assert
        assert result.success, result.error
        assert capsys.readouterr().out.splitlines()[0].lstrip() == "1B 00 00 50", "bare either way"

    def test_bad_toggle_token_names_the_vocabulary(self, cli):
        """An unrecognized toggle token errors and lists the accepted boolean vocabulary."""
        result = cli.repl.dispatch("mem.dump gTemp 4 addr=maybe")
        assert not result.success
        assert "invalid addr: 'maybe' (expected a boolean: on/off/true/false/yes/no/1/0/y/n/t/f)" in result.error

    def test_type_token_in_the_len_slot(self, cli):
        """A type token in the length slot shifts over, so the symbol's own size applies."""
        # Act -- the type token shifts out of the len slot, leaving len unset
        result = cli.repl.dispatch("mem.dump gTemp u16")

        # Assert
        assert result.success, result.error
        assert result.value == "1B00", "gTemp's own 2 bytes, the u16 having shifted"

    def test_a_named_symbol_dumps_itself(self, cli):
        """No length means the symbol's size, not 64 bytes of its neighbors."""
        # Act
        result = cli.repl.dispatch("mem.dump gTemp")

        # Assert
        assert result.success, result.error
        assert result.value == "1B00", "gTemp is a u16, so a bare dump is 2 bytes"

    def test_a_bare_address_still_gets_the_flat_default(self, cli):
        """Nothing names a size, so the explore-sized default stands."""
        # Act
        result = cli.repl.dispatch("mem.dump 0x1000")

        # Assert
        assert result.success, result.error
        assert len(result.value) == 128, "64 bytes as hex pairs"

    def test_an_offset_target_gets_the_flat_default(self, cli):
        """gTemp+2 is not gTemp, so its size is not the thing to read."""
        # Act
        result = cli.repl.dispatch("mem.dump gTemp+2")

        # Assert
        assert result.success, result.error
        assert len(result.value) == 128, "an offset means exploring, not naming"

    def test_an_explicit_length_still_wins(self, cli):
        """A typed length beats the symbol-size default for a named symbol."""
        # Act
        result = cli.repl.dispatch("mem.dump gTemp 8")

        # Assert
        assert result.success, result.error
        assert len(result.value) == 16, "what the user typed beats any default"


class TestInfo:

    def test_device_answers_mem_info(self, cli):
        """With no profile, MEM.INFO supplies the facts and each is sourced to the device."""
        # Act -- no profile: the facts come from the device
        result = cli.repl.dispatch("mem.info")

        # Assert
        assert result.success, result.error
        assert result.value == "termapy", "the dialect is the value"
        assert result.data["sources"]["max_block"] == "device", "MEM.INFO supplied the block limit"
        assert result.data["device_info"] == {
            "max_block": 64, "address_bits": 32, "endian": "le", "modify": True,
        }
        assert result.data["atomic_modify"] is True, "the demo advertises MEM.M"

    def test_profile_block_wins(self, cli):
        """A profile's memory block outranks the device answer and is reported as the source."""
        # Arrange -- the demo profile pins the block
        loaded = cli.repl.dispatch(f"profile.load {DEMO_PROFILE}")
        assert loaded.success, loaded.error

        # Act
        result = cli.repl.dispatch("mem.info")

        # Assert
        assert result.data["sources"]["max_block"] == "profile", "an explicit profile value is the source"
        assert result.data["max_block"] == 64

    def test_rows_show_each_source(self, cli, capsys):
        """The prose page labels every fact with where it came from."""
        cli.repl.dispatch("mem.info")
        out = capsys.readouterr().out
        assert "max_block" in out and "(device)" in out, "every fact names where it came from"

    def test_cache_is_dropped_on_reconnect(self, cli):
        """Reconnecting clears the cached MEM.INFO, since the port may now be another device."""
        # Arrange
        cli.repl.dispatch("mem.info")
        assert cli.ctx.ns("memory").get("queried") is True, "MEM.INFO was asked"

        # Act
        cli._disconnect()
        cli._connect()

        # Assert
        assert cli.ctx.ns("memory") == {}, "a new connection forgets the old device's answer"


class TestSpaceForm:
    """``/mem dump ...`` redirects to ``/mem.dump ...`` (any interior command).

    The dotted form is the grammar; the space form is what fingers type.
    The old behavior -- print the subcommand list and ignore the
    arguments -- reported success while doing nothing.
    """

    def test_space_form_dispatches_the_subcommand(self, cli):
        """Arguments survive the redirect: the result equals /mem.dump gTemp 2."""
        result = cli.repl.dispatch("mem dump gTemp 2")
        assert result.success, result.error
        assert result.value == "1B00", "identical to /mem.dump gTemp 2"

    def test_a_failed_redirect_answers_once_with_the_subcommands_error(self, cli, capsys):
        """failure-reported-once: /mem dump on a closed port fails with Not connected., printed once.

        The redirect handed up a failure the subcommand had already printed,
        so the line showed twice (review 2026-10-15, S4).
        """
        # Arrange
        cli._disconnect()
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch("mem dump gTemp 2")
        out = capsys.readouterr().out

        # Assert
        assert (result.success, result.error) == (False, STATE_MESSAGES["serial_connected"]), "the subcommand's failure is the answer"
        assert out.count(STATE_MESSAGES["serial_connected"]) == 1, "printed once, at the redirect's exit"

    def test_unknown_subcommand_names_the_choices(self, cli):
        """An unknown word after /mem is an error that lists the real subcommands."""
        result = cli.repl.dispatch("mem bogus 1")
        assert not result.success, "an unknown word is an error, not a silent listing"
        assert "Unknown subcommand: bogus" in result.error, "names what was typed"
        assert "dump" in result.error and "write" in result.error, "and lists the real ones"

    def test_bare_mem_is_the_status_check(self, cli, capsys):
        """Bare /mem = /mem.info (bare_sub): probe + status, not a listing.

        The bare-queries convention: the natural first thing to type
        after connecting performs the availability check.
        """
        # Act
        result = cli.repl.dispatch("mem")

        # Assert
        assert result.success, result.error
        assert result.value == "termapy", "identical to /mem.info"
        out = capsys.readouterr().out
        assert "status" in out, "the availability verdict renders"
        assert "Subcommands of" not in out, "no listing -- bare queries"

    def test_bare_parent_without_bare_sub_still_lists(self, cli, capsys):
        """A parent with no bare_sub, like /app, still prints its subcommand listing."""
        # Arrange / Act -- /app declares no bare_sub, so the synthesized
        # interior handler keeps its listing behavior
        result = cli.repl.dispatch("app")

        # Assert
        assert result is None or result.success is True, "the listing is not an error"
        assert "Subcommands of /app:" in capsys.readouterr().out, "the listing renders"


class TestRequestModeEnvelope:
    """One user command = one envelope, however deep the dispatch nests.

    The bare_sub / space-form redirects re-dispatch through the engine;
    before the _handler_depth gate, request mode wrapped the inner
    command's whole envelope -- escaped -- inside the outer envelope's
    output_lines (the same facts three times).
    """

    @staticmethod
    def _envelopes(out: str) -> list[dict]:
        import json
        return [
            json.loads(line)
            for line in out.splitlines()
            if line.strip().startswith("{")
        ]

    def test_bare_mem_emits_one_envelope(self, cli, capsys):
        """The bare_sub redirect yields one envelope, the inner record as data and no prose beside it."""
        # Arrange
        assert cli.repl.dispatch("term.request on").success
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch("mem")
        envelopes = self._envelopes(capsys.readouterr().out)

        # Assert
        assert result.success, result.error
        assert len(envelopes) == 1, "one envelope for one user command"
        envelope = envelopes[0]
        assert envelope["cmd"] == "/mem", "the command as the user issued it"
        assert envelope["value"] == "termapy", "the inner result propagates"
        assert envelope["data"]["dialect"] == "termapy", "the inner record propagates as data"
        assert envelope["output_lines"] == [], "the facts ride once, as data, never as rows beside it"

    def test_space_form_emits_one_envelope(self, cli, capsys):
        """The space-form redirect adds no second envelope, and the inner value propagates."""
        # Arrange
        assert cli.repl.dispatch("term.request on").success
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch("mem dump gTemp 2")
        envelopes = self._envelopes(capsys.readouterr().out)

        # Assert
        assert result.success, result.error
        assert len(envelopes) == 1, "the redirect adds no second envelope"
        assert envelopes[0]["value"] == "1B00", "the subcommand's value propagates"


class TestTemplateDialect:
    """The same RAM through the device's own ``mem`` grammar."""

    @pytest.fixture
    def legacy(self, cli):
        shutil.copyfile(DEMO_DIR / "demo_legacy.profile.json", Path(cli.config_path).with_name("demo_legacy.profile.json"))
        loaded = cli.repl.dispatch("profile.load demo_legacy.profile.json")  # cfg-relative
        assert loaded.success, loaded.error
        return cli

    def test_info_shows_the_template(self, legacy):
        """/mem.info reports the profile's read/write templates and never sends MEM.INFO."""
        # Act
        result = legacy.repl.dispatch("mem.info")

        # Assert
        assert result.value == "template", "the profile's dialect"
        assert result.data["template"]["read"] == "mem {addr:X} {len}", "the read template"
        assert result.data["template"]["write"] == "mem {addr:X} ={byte:02X}", "the write template"
        assert result.data["device_info"] is None, "MEM.INFO is never asked of a template device"
        assert result.data["sources"]["max_block"] == "profile", "256 from the block"

    def test_dump_matches_the_native_dialect(self, cli):
        """A template-dialect dump returns byte-for-byte what the native dialect returned."""
        # Arrange -- native first
        native = cli.repl.dispatch("mem.dump gTemp 40").value
        shutil.copyfile(DEMO_DIR / "demo_legacy.profile.json", Path(cli.config_path).with_name("demo_legacy.profile.json"))
        assert cli.repl.dispatch("profile.load demo_legacy.profile.json").success

        # Act
        legacy = cli.repl.dispatch("mem.dump gTemp 40")

        # Assert
        assert legacy.success, legacy.error
        assert legacy.value == native, "one RAM, two grammars, same bytes"

    def test_write_is_one_command_per_byte_and_audited(self, legacy):
        """Template writes go one byte per command and log the same MEM.W line as native."""
        # Act
        written = legacy.repl.dispatch("mem.write gTemp 2C01")
        read = legacy.repl.dispatch("mem.dump gTemp 2")

        # Assert
        assert written.success, written.error
        assert written.value == "2"
        assert read.value == "2C01", "both bytes landed through =val writes"
        assert any(text == "MEM.W 0x00001000 before=1B00 after=2C01 origin=cli" for _, text in legacy.audit), (
            "the audit line does not care which dialect carried the write"
        )

    def test_device_error_is_reported_verbatim(self, legacy):
        """The template device's own error text is passed through unchanged."""
        result = legacy.repl.dispatch("mem.dump 0x9000 4")
        assert not result.success
        assert "Device error: err: address range 00009000..00009003 not mapped" in result.error

    def test_unload_returns_to_the_native_spec(self, legacy):
        """Unloading the profile drops back to the native termapy dialect."""
        legacy.repl.dispatch("profile.unload")
        result = legacy.repl.dispatch("mem.info")
        assert result.value == "termapy", "no profile block -> the native dialect again"

    def test_broken_template_block_refuses_with_the_field(self, cli, tmp_path):
        """A template block with no read template refuses with the lint's own message."""
        # Arrange -- a template block with no read template
        broken = Path(cli.config_path).with_name("broken.profile.json")
        broken.write_text(json.dumps({"profile_version": 2, "memory": {"dialect": "template"}, "commands": {}}))
        assert cli.repl.dispatch("profile.load broken.profile.json").success, "lint warns, load succeeds"

        # Act
        result = cli.repl.dispatch("mem.dump gTemp 2")

        # Assert
        assert not result.success
        assert "memory/read: required for the template dialect" in result.error, "the same message the lint gave"


class TestHelpSection:

    def test_required_capabilities_round_trips_with_the_code(self, cli, capsys):
        """The section is named for the feature (CapabilitySet), not a synonym.

        It was REQUIRES until 2026-09-03 -- ungreppable against the code
        vocabulary and pinned by nothing, which is how it drifted.
        """
        # Act
        result = cli.repl.dispatch("help mem.dump")

        # Assert
        assert result.success, result.error
        out = capsys.readouterr().out
        assert "REQUIRED CAPABILITIES" in out, "the heading names the feature"
        assert "serial_connected" in out, "rows are the greppable CapabilitySet field names"
        assert "an open serial port" in out, (
            "the hint is a noun phrase completing 'requires ...' "
            "(REQUIREMENT_HINTS in plugins/capabilities.py)"
        )

    def test_rows_are_live_status(self, cli, capsys):
        """A met requirement renders plain; an unmet one is marked (missing).

        The renderer reads the dispatch gate's effective set (via
        ctx.internal.effective_capabilities), so the page doubles as
        "why won't this run right now" -- the serial_connected row must
        not render as if satisfied while no port is open.
        """
        # Arrange / Act -- connected: the DEMO port is open
        result = cli.repl.dispatch("help mem.dump")
        assert result.success, result.error
        connected_out = capsys.readouterr().out

        # Act -- disconnected: same page, no port
        cli._disconnect()
        result = cli.repl.dispatch("help mem.dump")
        assert result.success, result.error
        disconnected_out = capsys.readouterr().out

        # Assert
        assert "(missing)" not in connected_out, "met requirements carry no marker"
        assert "serial_connected" in disconnected_out, "the row still renders when unmet"
        assert "(missing)" in disconnected_out, "an unmet requirement is marked in text, not just color"

    def test_available_treats_serial_as_attainable(self, cli, capsys):
        """The AVAILABLE matrix answers "could it EVER run there".

        ENVIRONMENTS unions in the dynamic capabilities (any host can
        open a port), so a serial command no longer renders the
        statically wrong "TUI: no  CLI: no  MCP: no"; right-now status
        is the REQUIRED CAPABILITIES rows' job.
        """
        # Act
        result = cli.repl.dispatch("help mem.dump")
        assert result.success, result.error
        out = capsys.readouterr().out

        # Assert
        assert "TUI: yes" in out, "every environment can attain serial_connected"
        assert "does not provide: serial_connected" not in out, (
            "a dynamic capability is never a permanent environment gap"
        )

    def test_available_marks_the_current_environment(self, cli, capsys):
        """The column for ctx.environment carries a (current) marker."""
        # Act
        result = cli.repl.dispatch("help mem.dump")
        assert result.success, result.error
        out = capsys.readouterr().out

        # Assert
        assert "CLI: yes (current)" in out, "the CLI rig is marked as where we are"
        assert "TUI: yes (current)" not in out, "only the running environment is marked"


class TestNoMemoryInterface:
    """A connected device that does not speak MEM refuses fast, not slow.

    Poking the probe cache is honest here: the verdict IS data (the
    recorded outcome of one real failed probe), and the DEMO device
    would answer a live probe, which is exactly what the retry test
    uses.
    """

    @staticmethod
    def _poison(cli):
        cache = cli.ctx.ns("memory")
        cache["queried"] = True
        cache["device_info"] = None
        cache["device_error"] = "No reply to MEM.INFO"

    def test_commands_refuse_with_the_verdict(self, cli):
        """A cached failed probe refuses at once and points to /mem.info for a retry."""
        # Arrange
        self._poison(cli)

        # Act
        result = cli.repl.dispatch("mem.dump 0x1000 4")

        # Assert
        assert not result.success
        assert "No memory interface" in result.error, (
            "the cached verdict, not a fresh per-command timeout"
        )
        assert "mem.info" in result.error, "the retry path is named"

    def test_mem_info_retries_and_clears_the_verdict(self, cli):
        """/mem.info re-probes, and a success lifts the refusal for later commands."""
        # Arrange
        self._poison(cli)
        assert not cli.repl.dispatch("mem.dump 0x1000 4").success, "refusing before the retry"

        # Act -- /mem.info re-probes; the DEMO device answers
        info = cli.repl.dispatch("mem.info")
        after = cli.repl.dispatch("mem.dump 0x1000 4")

        # Assert
        assert info.success, info.error
        assert info.data["available"] is True, "the retry probe succeeded"
        assert after.success, after.error

    def test_mem_info_reports_ok_status(self, cli, capsys):
        """A working device's info page leads with an ok availability status."""
        # Act
        result = cli.repl.dispatch("mem.info")

        # Assert
        assert result.success, result.error
        out = capsys.readouterr().out
        assert "status" in out and "ok" in out, "the availability verdict leads the page"


class TestNotConnected:

    def test_dump_needs_a_port(self, cli):
        """With the port closed, /mem.dump is refused by the serial_connected gate."""
        cli._disconnect()
        result = cli.repl.dispatch("mem.dump 0x1000")
        assert not result.success, "serial_connected is required"


class TestValueContract:

    @pytest.mark.parametrize("line", ["mem.dump gTemp 4", "mem.write gTemp 1B00", "mem.info"])
    def test_every_command_returns_a_str_value(self, cli, line):
        """Dump, write, and info each return a str value, so $(X) capture works."""
        result = cli.repl.dispatch(line)
        assert result.success, result.error
        assert isinstance(result.value, str), f"{line}: value must be a str for $(X) <- capture"


# ── Peripheral read safety ──────────────────────────────────────────────────

# Registers over the demo's SFR window (0xBF806000 + 0x20), so the reads the
# gates allow actually reach the FakeSerial backend and return real bytes.
# UDATA stands in for a FIFO port: the vendor SVDs leave readAction empty, so
# read_effect only ever arrives from a file that says it, which is this one.
_SFR_DEVICE = {
    "device_version": 1,
    "device": "demo-uart",
    "registers": [
        {"name": "UMODE", "addr": "0xBF806000", "size": 4},
        {"name": "USTAT", "addr": "0xBF806010", "size": 4},
        {"name": "UDATA", "addr": "0xBF806014", "size": 4, "read_effect": True},
        {"name": "UTXREG", "addr": "0xBF806018", "size": 4, "access": "wo"},
        # One register described twice, the way SVD models a peripheral's
        # operating modes (a SERCOM CTRLA is six such definitions).  Both
        # names are the same four bytes at 0xBF806004.
        {"name": "UMODE_SPI_CTRL", "addr": "0xBF806004", "size": 4},
        {"name": "UMODE_I2C_CTRL", "addr": "0xBF806004", "size": 4},
        # One register described at two WIDTHS, the way an RTC's COUNT is a
        # 32-bit MODE0 definition over a 16-bit MODE1 one: the same silicon
        # at 0xBF80601C, not two registers.
        {"name": "UCNT_MODE0", "addr": "0xBF80601C", "size": 4},
        {"name": "UCNT_MODE1", "addr": "0xBF80601C", "size": 2},
    ],
}


@pytest.fixture
def sfr_cli(cli, tmp_path):
    """The rig with a device file loaded over the demo's SFR window."""
    dev = tmp_path / "rig" / "dev"
    dev.mkdir(exist_ok=True)
    (dev / "demo-uart.device.json").write_text(json.dumps(_SFR_DEVICE), encoding="utf-8")
    cli.repl.fire_lifecycle("on_config_load")
    return cli


class TestPeripheralReadGates:
    """Reading silicon through a driver that assumes it is the only reader."""

    def test_the_device_registers_are_loaded(self, sfr_cli):
        """The device file's register names resolve, reading the seeded U1MODE word."""
        # Act
        result = sfr_cli.repl.dispatch("mem.read UMODE u32")

        # Assert
        assert result.success, result.error
        assert result.value == "32776", "U1MODE's seeded 0x8008, read by its device name"

    def test_dump_across_registers_is_refused(self, sfr_cli):
        """A dump sweeping peripheral space is refused, counting and naming the registers."""
        # Act -- the sweep the whole gate exists for
        result = sfr_cli.repl.dispatch("mem.dump 0xBF806000 0x20")

        # Assert
        assert not result.success, "a sweep over peripheral space must not run"
        assert "Bulk read refused" in result.error
        assert "covers 6 device registers" in result.error, "it says how much it would have touched"
        assert "UMODE" in result.error, "and names them so the user can read one"

    def test_dump_of_one_register_is_allowed(self, sfr_cli):
        """Naming a register is deliberate; only the SWEEP is refused."""
        # Act
        result = sfr_cli.repl.dispatch("mem.dump UMODE 4")

        # Assert
        assert result.success, result.error
        assert result.value == "08800000", "the seeded word, little-endian"

    def test_a_bare_dump_of_a_register_is_allowed(self, sfr_cli):
        """The spelling a user reaches for first must not hit the gate.

        A flat 64-byte default made this sweep UMODE's neighbors and get
        refused -- the gate firing on the one act it is meant to permit.
        """
        # Act
        result = sfr_cli.repl.dispatch("mem.dump UMODE")

        # Assert
        assert result.success, result.error
        assert result.value == "08800000", "the register's own 4 bytes"

    @pytest.mark.parametrize("name", ["UMODE", "USTAT", "UDATA", "UCNT_MODE0", "UCNT_MODE1"])
    def test_every_register_dumps_bare(self, sfr_cli, name):
        """Including the last ones in the window, where a 64-byte read ran on."""
        # Act
        result = sfr_cli.repl.dispatch(f"mem.dump {name}")

        # Assert
        assert result.success, f"{name}: naming one register is always allowed"

    def test_a_mode_aliased_register_is_one_register(self, sfr_cli):
        """SVD describes a peripheral once per MODE; that is not a sweep.

        Found on a real PIC32CM: a SERCOM's CTRLA is six definitions
        (I2CM / I2CS / SPIM / SPIS / USART_INT / USART_EXT) at one address,
        so counting ENTRIES refused a read of a single register on every
        aliased peripheral -- 410 such addresses on that part.
        """
        # Act
        result = sfr_cli.repl.dispatch("mem.dump UMODE_SPI_CTRL")

        # Assert
        assert result.success, result.error
        assert len(result.value) == 8, "one 4-byte register, not a two-register span"

    def test_the_other_alias_reads_the_same_bytes(self, sfr_cli):
        """Two mode-alias names for one register read identical bytes."""
        # Act
        spi = sfr_cli.repl.dispatch("mem.dump UMODE_SPI_CTRL")
        i2c = sfr_cli.repl.dispatch("mem.dump UMODE_I2C_CTRL")

        # Assert
        assert spi.value == i2c.value, "two names, one register, same silicon"

    def test_a_width_alias_is_one_register(self, sfr_cli):
        """SVD also describes one register once per WIDTH; still not a sweep.

        Measured on the real files: an RTC's MODE0_COUNT (32-bit) and
        MODE1_COUNT (16-bit) sit at one address, as does every TC COUNT /
        CC / PER -- 267 of 3527 registers on an ATSAME54P20A, 142 of 2517
        on a PIC32CM5164LE00100.  Keying the count on (addr, size) called
        them two registers and refused the bare dump of either.
        """
        # Act
        wide = sfr_cli.repl.dispatch("mem.dump UCNT_MODE0")
        narrow = sfr_cli.repl.dispatch("mem.dump UCNT_MODE1")

        # Assert
        assert wide.success, wide.error
        assert narrow.success, narrow.error
        assert (len(wide.value), len(narrow.value)) == (8, 4), "each reads its own width"
        assert wide.value.startswith(narrow.value), "the narrow alias is the wide one's low half"

    def test_a_span_over_aliases_and_a_real_neighbor_still_refuses(self, sfr_cli):
        """Collapsing aliases must not collapse genuinely distinct registers."""
        # Act -- 0xBF806000 covers UMODE and both CTRL aliases
        result = sfr_cli.repl.dispatch("mem.dump 0xBF806000 8")

        # Assert
        assert not result.success, "UMODE and the CTRL pair are two real registers"
        assert "covers 2 device registers" in result.error, "aliases counted once, not three"

    def test_the_refusal_counts_and_names_by_span(self, sfr_cli):
        """Aliases at one address count once and are named once in the refusal."""
        # Act
        result = sfr_cli.repl.dispatch("mem.dump 0xBF806000 0x20")

        # Assert
        assert "covers 6 device registers" in result.error, (
            "four singles, the mode-aliased pair and the width-aliased pair: one each"
        )
        assert result.error.count("UMODE_") == 1, "one name per span, not both aliases"

    def test_dump_of_ram_is_untouched(self, sfr_cli):
        """The same-size sweep in RAM runs, since no device file covers it."""
        # Act -- the same size sweep, in RAM
        result = sfr_cli.repl.dispatch("mem.dump 0x1000 0x20")

        # Assert
        assert result.success, "no device file covers RAM, so nothing is gated"

    def test_a_refused_dump_reads_nothing(self, sfr_cli):
        """The bulk refusal fires before the wire, so no MEM.R line is logged."""
        # Act
        sfr_cli.repl.dispatch("mem.dump 0xBF806000 0x20")

        # Assert
        assert not any(text.startswith("MEM.R") for _, text in sfr_cli.audit), \
            "the refusal is before the wire: no read happened to log"

    def test_write_only_register_refuses_a_read(self, sfr_cli):
        """Reading an access: wo register is refused, since it returns bus noise."""
        # Act
        result = sfr_cli.repl.dispatch("mem.read UTXREG u32")

        # Assert
        assert not result.success, "reading a wo register returns bus noise, not the value"
        assert "Read refused: UTXREG is access: wo" in result.error

    @pytest.mark.parametrize("line", [
        "mem.dump UTXREG 4",
        "mem.str UTXREG",
        "mem.or UTXREG 1",
        "mem.not UTXREG",
    ])
    def test_every_reading_path_honors_write_only(self, sfr_cli, line):
        """Including the read half of a read-modify-write."""
        # Act
        result = sfr_cli.repl.dispatch(line)

        # Assert
        assert not result.success, f"{line}: reads a wo register"
        assert "access: wo" in result.error

    def test_write_only_register_still_accepts_a_write(self, sfr_cli):
        """The gate is on reading; writing a wo register is its whole purpose."""
        # Act
        result = sfr_cli.repl.dispatch("mem.write UTXREG 41000000")

        # Assert
        assert result.success, result.error

    def test_str_refuses_to_scan_into_peripheral_space(self, sfr_cli):
        """A NUL scan of a register is refused; peripheral space is not a C string."""
        # Act
        result = sfr_cli.repl.dispatch("mem.str UMODE")

        # Assert
        assert not result.success, "a register is not a C string"
        assert "String read refused" in result.error

    def test_str_refuses_when_the_scan_could_reach_a_register(self, sfr_cli):
        """The span it MAY walk, not where it happens to stop."""
        # Act -- starts below the window; 256 bytes of scan reaches into it
        result = sfr_cli.repl.dispatch("mem.str 0xBF805F80")

        # Assert
        assert not result.success, "where a NUL scan stops is decided by unread bytes"
        assert "String read refused" in result.error

    def test_str_in_ram_is_untouched(self, sfr_cli):
        """With a device file loaded, /mem.str of a RAM symbol still reads normally."""
        # Act
        result = sfr_cli.repl.dispatch("mem.str sBanner")

        # Assert
        assert result.success, result.error
        assert result.value == "Bassomatic v77", "RAM strings still read"


class TestPeripheralReadLog:
    """The log line that turns an undetectable bug into a grep."""

    def _reads(self, cli) -> list[str]:
        return [text for prefix, text in cli.audit if text.startswith("MEM.R")]

    def test_a_named_register_read_is_logged(self, sfr_cli):
        """A register read logs exactly one MEM.R line naming the register and origin."""
        # Act
        result = sfr_cli.repl.dispatch("mem.read UMODE u32")

        # Assert
        assert result.success, result.error
        logged = self._reads(sfr_cli)
        assert len(logged) == 1, "one read, one line"
        assert "regs=UMODE" in logged[0], "the line names what was touched"
        assert "origin=cli" in logged[0], "and who touched it"

    def test_a_read_effect_register_is_flagged_in_the_line(self, sfr_cli):
        """Allowed -- naming a FIFO port is deliberate -- but never silent."""
        # Act
        result = sfr_cli.repl.dispatch("mem.read UDATA u32")

        # Assert
        assert result.success, "a deliberate read of a FIFO port proceeds"
        assert "read_effect=UDATA" in self._reads(sfr_cli)[0], \
            "the side effect is what a later grep needs to find"

    def test_a_field_read_is_logged(self, sfr_cli):
        """A bit read of a register logs the register just as a whole-word read does."""
        # Act
        result = sfr_cli.repl.dispatch("mem.read UMODE.15")

        # Assert
        assert result.success, result.error
        assert "regs=UMODE" in self._reads(sfr_cli)[0], "the bit path logs like any other"

    def test_an_allowed_dump_is_logged(self, sfr_cli):
        """A permitted register dump logs a MEM.R line recording the byte count."""
        # Act
        result = sfr_cli.repl.dispatch("mem.dump UMODE 4")

        # Assert
        assert result.success, result.error
        assert "len=4" in self._reads(sfr_cli)[0], "the line records how much was read"

    def test_ram_reads_are_not_logged(self, sfr_cli):
        """Only peripheral reads; logging plain memory would bury them."""
        # Act
        result = sfr_cli.repl.dispatch("mem.dump 0x1000 0x20")

        # Assert
        assert result.success, result.error
        assert self._reads(sfr_cli) == [], "RAM has no side effect to record"


class TestWritePathReadGates:
    """The write path's audit pre-read is a read: gated and logged like one.

    The plain ``/mem.write`` read its target for ``before=`` outside every
    read gate and the read log -- writing a write-only register read it,
    writing a FIFO port popped it, and a masked write ran a read-modify-
    write on an unreadable word (2026-09-18 review, M1).  The register
    cannot tell which verb the user typed; it sees a read.
    """

    def _lines(self, cli, verb: str) -> list[str]:
        return [text for _, text in cli.audit if text.startswith(verb)]

    def test_a_write_only_register_is_not_read_first(self, sfr_cli):
        """Writing a wo register skips the pre-read; MEM.W shows before=? and names it."""
        # Act
        result = sfr_cli.repl.dispatch("mem.write UTXREG 41000000")

        # Assert
        assert result.success, "writing a wo register is its whole purpose"
        assert self._lines(sfr_cli, "MEM.R") == [], "no pre-read reached the wire"
        assert self._lines(sfr_cli, "MEM.W") == [
            "MEM.W 0xBF806018 before=? after=41000000 regs=UTXREG origin=cli"
        ], "the audit line says the before value was not read, and names the register"

    def test_the_skipped_pre_read_is_explained_in_the_record(self, sfr_cli):
        """The data record has before_hex None and says why the pre-read was skipped."""
        # Arrange
        sfr_cli.ctx.wants_data = True

        # Act
        result = sfr_cli.repl.dispatch("mem.write UTXREG 41000000")

        # Assert
        assert result.data["before_hex"] is None, "unknown, not a guess"
        assert result.data["before_skipped"] == "UTXREG is access: wo", "and the record says why"

    def test_the_skipped_pre_read_is_explained_in_the_result_line(self, sfr_cli, capsys):
        """The feedback line says the target was not read first and gives the reason."""
        # Act
        sfr_cli.repl.dispatch("mem.write UTXREG 41000000")

        # Assert
        assert "Wrote 4 bytes at 0xBF806018  UTXREG  (not read first: UTXREG is access: wo)" in (
            capsys.readouterr().out
        )

    def test_a_read_effect_register_is_not_popped_by_a_write(self, sfr_cli):
        """A NAMED read of a FIFO port is the user's call; a write asked for no read."""
        # Act
        result = sfr_cli.repl.dispatch("mem.write UDATA 01000000")

        # Assert
        assert result.success, result.error
        assert self._lines(sfr_cli, "MEM.R") == [], "the FIFO was not read on the user's behalf"
        assert "before=? after=01000000 regs=UDATA" in self._lines(sfr_cli, "MEM.W")[0]

    def test_the_pre_read_of_a_plain_register_is_logged(self, sfr_cli):
        """A readable register's pre-read shows in the read log; MEM.W keeps the true before."""
        # Act
        result = sfr_cli.repl.dispatch("mem.write UMODE 09800000")

        # Assert
        assert result.success, result.error
        assert self._lines(sfr_cli, "MEM.R") == [
            "MEM.R 0xBF806000 len=4 regs=UMODE origin=cli"
        ], "the pre-read is a peripheral read and appears in the read log"
        assert self._lines(sfr_cli, "MEM.W") == [
            "MEM.W 0xBF806000 before=08800000 after=09800000 regs=UMODE origin=cli"
        ], "and the audit line still carries the true before"

    def test_ram_writes_still_read_first(self, sfr_cli):
        """RAM writes still pre-read for before=, with no MEM.R line and no regs= field."""
        # Act
        sfr_cli.repl.dispatch("mem.write gTemp 2C01")

        # Assert
        assert self._lines(sfr_cli, "MEM.R") == [], "a RAM read is not a peripheral read"
        assert self._lines(sfr_cli, "MEM.W") == [
            "MEM.W 0x00001000 before=1B00 after=2C01 origin=cli"
        ], "a variable has no side effect to skip for, and no regs= to carry"

    def test_a_masked_write_on_a_write_only_register_is_refused(self, sfr_cli):
        """A read-modify-write of an unreadable word: the other bits come from the bus."""
        # Act
        result = sfr_cli.repl.dispatch("mem.write UTXREG.3 1")

        # Assert
        assert not result.success, "every other bit of the result would be undefined"
        assert "RMW refused: UTXREG is access: wo" in result.error
        assert "write the whole word" in result.error, "the whole-word write is the way to do it"
        assert self._lines(sfr_cli, "MEM.M") == [], "nothing written, nothing audited"

    @pytest.mark.parametrize("line", [
        "mem.write UDATA.0 1",
        "mem.or UDATA 1",
        "mem.clear UDATA 1",
        "mem.xor UDATA 1",
        "mem.not UDATA",
    ])
    def test_every_masked_form_refuses_a_read_effect_register(self, sfr_cli, line):
        """The read half would pop the FIFO -- the surprise is in the mechanism."""
        # Act
        result = sfr_cli.repl.dispatch(line)

        # Assert
        assert not result.success, f"{line}: its read half reads a read_effect register"
        assert "RMW refused: UDATA is read_effect" in result.error


class TestWriteAudit:
    """The audit line names the registers it touched, as the read log does.

    The stated purpose of the log is to turn "the board went strange" into
    a grep; a grep for INTFLAG that found its reads and missed its writes
    was half a log (2026-09-18 review, M3).
    """

    def _audits(self, cli) -> list[str]:
        return [text for _, text in cli.audit if text.startswith("MEM.")]

    def test_a_masked_write_names_the_register(self, sfr_cli):
        """A MEM.M line carries regs= in the same slot as a MEM.R line does."""
        # Act
        result = sfr_cli.repl.dispatch("mem.write UMODE.15 0")

        # Assert
        assert result.success, result.error
        assert self._audits(sfr_cli) == [
            "MEM.M 0xBF806000 before=08800000 after=08000000 regs=UMODE origin=cli"
        ], "MEM.M carries regs= in the same position as MEM.R"

    def test_every_alias_is_named_and_the_typed_one_leads(self, sfr_cli):
        """A write lists every alias at the address in regs=, the typed name first."""
        # Act
        result = sfr_cli.repl.dispatch("mem.write UMODE_SPI_CTRL 00000000")

        # Assert
        assert result.success, result.error
        write = [text for text in self._audits(sfr_cli) if text.startswith("MEM.W")]
        assert "regs=UMODE_SPI_CTRL UMODE_I2C_CTRL" in write[0], (
            "a grep for either alias finds the write; the act performed reads first"
        )

    def test_a_grep_for_a_register_finds_its_reads_and_its_writes(self, sfr_cli):
        """A grep for regs=UMODE finds the read, the write's pre-read, and the write."""
        # Act
        sfr_cli.repl.dispatch("mem.read UMODE u32")
        sfr_cli.repl.dispatch("mem.write UMODE 09800000")

        # Assert
        hits = [text for text in self._audits(sfr_cli) if "regs=UMODE" in text]
        assert [text.split()[0] for text in hits] == ["MEM.R", "MEM.R", "MEM.W"], (
            "the typed read, the write's pre-read, and the write itself"
        )
