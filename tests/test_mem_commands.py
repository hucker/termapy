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
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
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
            ("mem.dump U1MODE.ON", "Unsupported address suffix here: .ON"),
            ("mem.dump 0x9000 4", "Device error: range"),
            ("mem.dump main-0x9000", "Invalid address: main-0x9000"),
        ],
    )
    def test_errors(self, cli, line, message):
        result = cli.repl.dispatch(line)
        assert not result.success, "refused"
        assert message in result.error, "the address grammar's / device's own message"

    @pytest.mark.parametrize("token, expected_len", [("16", 16), ("0x10", 16), ("10h", 16)])
    def test_length_takes_the_address_grammar_numbers(self, cli, token, expected_len):
        result = cli.repl.dispatch(f"mem.dump gTemp {token}")
        assert result.success, result.error
        assert len(result.value) == expected_len * 2, "decimal, 0x hex and h-suffix all mean sixteen"

    @pytest.mark.parametrize("token", ["0", "zz", "-4", "1.5"])
    def test_bad_length(self, cli, token):
        result = cli.repl.dispatch(f"mem.dump 0x1000 {token}")
        assert not result.success, "refused"
        assert f"Invalid length: {token}" in result.error, "names the token"

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


class TestRead:
    """One typed value: scalars, chars, register fields, bits, slices."""

    def test_typed_symbol_scalar(self, cli):
        result = cli.repl.dispatch("mem.read gTemp")
        assert result.success, result.error
        assert result.value == "27", "gTemp's u16 type decodes the seed"
        assert result.data["type"] == "u16"

    def test_explicit_type_overrides(self, cli):
        result = cli.repl.dispatch("mem.read gTemp u8")
        assert result.value == "27", "first byte as u8"

    def test_hex_twin_is_the_decoded_value_not_the_raw_bytes(self, cli, capsys):
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
        result = cli.repl.dispatch("mem.read sBanner char")
        assert result.value == "66", "the value is the byte"
        assert "sBanner = 'B' (0x42)" in capsys.readouterr().out, "rendered as a character"

    def test_register_shows_all_fields(self, cli, capsys):
        # Act -- U1MODE has a format-spec type: apply_format renders it
        result = cli.repl.dispatch("mem.read U1MODE")

        # Assert
        assert result.success, result.error
        assert result.data["fields"] == {"ON": 1, "UEN": 0, "BRGH": 1}, "the seeded 0x8008"
        out = capsys.readouterr().out
        assert "ON       = 1" in out, "the DISPLAY agrees with the extract twin (gold caught it at 0)"
        assert "BRGH     = 1" in out, "bit 3 rendered from the combined word"

    def test_named_field(self, cli):
        result = cli.repl.dispatch("mem.read U1MODE.ON")
        assert result.value == "1", "bit 15 of the LE word is set"
        assert result.data["word"] == 0x8008, "the whole word rides along"

    def test_numeric_bit_and_slice(self, cli):
        assert cli.repl.dispatch("mem.read U1MODE.15").value == "1", "same bit by number"
        assert cli.repl.dispatch("mem.read gFlags.0-2").value == "5", "the low three bits of 5"

    def test_bare_address_bit(self, cli):
        assert cli.repl.dispatch("mem.read 0xBF806000.3").value == "1", "BRGH by raw address"

    def test_float_symbol(self, cli):
        result = cli.repl.dispatch("mem.read gPressure")
        assert result.value == "1013.25", "the seeded f32"

    def test_bare_address_needs_a_type(self, cli):
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
        result = cli.repl.dispatch(line)
        assert not result.success
        assert message in result.error


class TestBitWrites:
    """A .field / .bit target on /mem.write is a masked word write."""

    def test_clear_a_named_field(self, cli, capsys):
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
        cli.repl.dispatch("mem.write U1MODE.ON 0")
        audits = [text for prefix, text in cli.audit if text.startswith("MEM.M")]
        assert audits == ["MEM.M 0xBF806000 before=08800000 after=08000000 origin=cli"], (
            "atomic modifies audit like writes, memory-order bytes"
        )

    def test_set_a_slice(self, cli):
        result = cli.repl.dispatch("mem.write gFlags.4-6 5")
        assert result.success, result.error
        assert cli.repl.dispatch("mem.read gFlags.4-6").value == "5", "three bits landed"
        assert cli.repl.dispatch("mem.read gFlags.0-2").value == "5", "neighbors untouched"

    def test_value_too_big(self, cli):
        result = cli.repl.dispatch("mem.write U1MODE.ON 2")
        assert not result.success
        assert "Invalid value: 2 (ON is 1 bit)" in result.error

    def test_rmw_refused_on_w1c(self, cli):
        result = cli.repl.dispatch("mem.write U1STA.0 1")
        assert not result.success
        assert "RMW refused: U1STA is rmw: false" in result.error
        assert not any(text.startswith("MEM.M") for _, text in cli.audit), "nothing written, nothing audited"


class TestMaskOps:

    def test_or_width_from_symbol_type(self, cli, capsys):
        # Act -- gFlags is u32 = 5; set bit 4
        result = cli.repl.dispatch("mem.or gFlags 0x10")

        # Assert
        assert result.success, result.error
        assert result.value == "00000015", "the new word"
        assert result.data["atomic"] is True, "or rides MEM.M"
        assert "OR 0x00000010 at 0x00001008  gFlags  (0x00000005 -> 0x00000015)" in capsys.readouterr().out

    def test_and(self, cli):
        result = cli.repl.dispatch("mem.and gFlags 0x1")
        assert result.value == "00000001", "5 & 1"

    def test_xor_is_never_atomic(self, cli):
        result = cli.repl.dispatch("mem.xor gFlags 0xFF")
        assert result.value == "000000FA", "5 ^ 0xFF"
        assert result.data["atomic"] is False, "xor cannot ride (word & and) | or"

    def test_mask_width_from_digit_count_on_a_bare_address(self, cli):
        # Arrange -- 2 hex digits = a byte op at gTemp's first byte
        result = cli.repl.dispatch("mem.or 0x1000 0xE0")

        # Assert
        assert result.success, result.error
        assert result.data["width"] == 1, "0xE0 is a u8 mask"
        assert result.value == "FB", "0x1B | 0xE0"

    def test_clear_is_the_complement_footgun_removed(self, cli):
        # Act -- gFlags is 5; clear bit 0 without hand-complementing a mask
        result = cli.repl.dispatch("mem.clear gFlags 0x1")

        # Assert
        assert result.success, result.error
        assert result.value == "00000004", "5 & ~1"
        assert result.data["atomic"] is True, "clear is (word & ~mask) | 0: MEM.M-able"

    def test_not_inverts_the_word(self, cli):
        result = cli.repl.dispatch("mem.not gFlags")
        assert result.value == "FFFFFFFA", "~5 over the u32 width"
        assert result.data["atomic"] is False, "NOT depends on the old value"
        assert cli.repl.dispatch("mem.not gFlags").value == "00000005", "involution"

    def test_not_width_from_symbol_type(self, cli):
        result = cli.repl.dispatch("mem.not gTemp")
        assert result.data["width"] == 2, "gTemp is u16"

    @pytest.mark.parametrize("line", ["mem.or U1STA 1", "mem.clear U1STA 1", "mem.not U1STA"])
    def test_rmw_refused_on_w1c(self, cli, line):
        result = cli.repl.dispatch(line)
        assert not result.success
        assert "RMW refused: U1STA is rmw: false" in result.error

    def test_bad_mask(self, cli):
        result = cli.repl.dispatch("mem.or gFlags zz")
        assert "Invalid mask: zz" in result.error


class TestStr:

    def test_banner_by_symbol(self, cli, capsys):
        # Act
        result = cli.repl.dispatch("mem.str sBanner")

        # Assert
        assert result.success, result.error
        assert result.value == "Bassomatic v77", "the NUL-terminated string is the value"
        assert result.data["length"] == 14 and result.data["truncated"] is False
        assert '0x00003100  sBanner  "Bassomatic v77"' in capsys.readouterr().out

    def test_cap_truncates(self, cli):
        # Arrange -- code bytes at main have no NUL in the first 8
        result = cli.repl.dispatch("mem.str 0x2000 8")
        assert result.data["truncated"] is True, "no NUL within the cap"
        assert result.data["length"] == 8

    def test_bad_cap(self, cli):
        result = cli.repl.dispatch("mem.str sBanner zz")
        assert "Invalid length: zz" in result.error


class TestDumpModes:

    def test_u16_word_columns(self, cli, capsys):
        # Act
        result = cli.repl.dispatch("mem.dump gTemp 16 u16")

        # Assert
        assert result.success, result.error
        line = capsys.readouterr().out.splitlines()[0].lstrip()
        assert line.startswith("0x00001000  001B 5000 447D 213A"), "LE u16 hex words"
        assert result.value.startswith("1B00"), "value stays the raw bytes"

    def test_i16_decimal(self, cli):
        cli.ctx.wants_data = True  # what the dispatcher sets for --json / MCP
        result = cli.repl.dispatch("mem.dump gTemp 4 i16")
        assert result.data["rows"][0]["values"] == [27, 20480], "signed decimal values in the records"

    def test_bare_mode(self, cli, capsys):
        # Act -- both columns off: nothing but values
        cli.repl.dispatch("mem.dump gTemp 4 addr=off ascii=off")

        # Assert
        line = capsys.readouterr().out.splitlines()[0].lstrip()
        assert line == "1B 00 00 50", "no address, no ascii, no label"

    def test_addr_off_keeps_ascii(self, cli, capsys):
        cli.repl.dispatch("mem.dump sBanner 8 addr=off")
        line = capsys.readouterr().out.splitlines()[0].lstrip()
        assert line.startswith("42 61 73 73") and "|Bassomat|" in line, "ascii column without addresses"

    def test_word_length_must_align(self, cli):
        result = cli.repl.dispatch("mem.dump gTemp 3 u16")
        assert "Invalid length: 3 (not a multiple of 2 for u16)" in result.error

    @pytest.mark.parametrize("line", [
        "mem.dump gTemp 4 addr=false ascii=no",
        "mem.dump gTemp 4 addr=0 ascii=OFF",
    ])
    def test_toggles_take_the_whole_parse_bool_vocabulary(self, cli, line, capsys):
        # Act -- on/off is canonical, but any scripting.parse_bool token works
        result = cli.repl.dispatch(line)

        # Assert
        assert result.success, result.error
        assert capsys.readouterr().out.splitlines()[0].lstrip() == "1B 00 00 50", "bare either way"

    def test_bad_toggle_token_names_the_vocabulary(self, cli):
        result = cli.repl.dispatch("mem.dump gTemp 4 addr=maybe")
        assert not result.success
        assert "invalid addr: 'maybe' (expected a boolean: on/off/true/false/yes/no/1/0)" in result.error

    def test_type_token_in_the_len_slot(self, cli):
        result = cli.repl.dispatch("mem.dump gTemp u16")
        assert result.success, result.error
        assert len(result.value) == 128, "len defaulted to 64 with the type shifted"


class TestInfo:

    def test_device_answers_mem_info(self, cli):
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


class TestSpaceForm:
    """``/mem dump ...`` redirects to ``/mem.dump ...`` (any interior command).

    The dotted form is the grammar; the space form is what fingers type.
    The old behavior -- print the subcommand list and ignore the
    arguments -- reported success while doing nothing.
    """

    def test_space_form_dispatches_the_subcommand(self, cli):
        result = cli.repl.dispatch("mem dump gTemp 2")
        assert result.success, result.error
        assert result.value == "1B00", "identical to /mem.dump gTemp 2"

    def test_unknown_subcommand_names_the_choices(self, cli):
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
        assert any("status" in line for line in envelope["output_lines"]), (
            "the inner command's prose is captured as plain lines"
        )
        assert all('"output_lines"' not in line for line in envelope["output_lines"]), (
            "never an escaped envelope inside output_lines"
        )

    def test_space_form_emits_one_envelope(self, cli, capsys):
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
        # Act
        result = legacy.repl.dispatch("mem.info")

        # Assert
        assert result.value == "template", "the profile's dialect"
        assert result.data["template"]["read"] == "mem {addr:X} {len}", "the read template"
        assert result.data["template"]["write"] == "mem {addr:X} ={byte:02X}", "the write template"
        assert result.data["device_info"] is None, "MEM.INFO is never asked of a template device"
        assert result.data["sources"]["max_block"] == "profile", "256 from the block"

    def test_dump_matches_the_native_dialect(self, cli):
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
        result = legacy.repl.dispatch("mem.dump 0x9000 4")
        assert not result.success
        assert "Device error: err: address range 00009000..00009003 not mapped" in result.error

    def test_unload_returns_to_the_native_spec(self, legacy):
        legacy.repl.dispatch("profile.unload")
        result = legacy.repl.dispatch("mem.info")
        assert result.value == "termapy", "no profile block -> the native dialect again"

    def test_broken_template_block_refuses_with_the_field(self, cli, tmp_path):
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
        # Act
        result = cli.repl.dispatch("mem.info")

        # Assert
        assert result.success, result.error
        out = capsys.readouterr().out
        assert "status" in out and "ok" in out, "the availability verdict leads the page"


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
