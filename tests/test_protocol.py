"""Tests for termapy.protocol — hex utilities, framing, matching, script parsing."""

import struct

import pytest

from termapy.protocol import (
    ColumnSpec,
    FrameCollector,
    Step,
    apply_format,
    diff_bytes,
    diff_columns,
    format_diff_markup,
    format_hex,
    format_hex_dump,
    load_proto_script,
    match_response,
    overflow_count,
    parse_data,
    extract_fmt_title,
    parse_format_spec,
    parse_hex,
    parse_pattern,
    parse_proto_script,
    parse_toml_script,
)
from termapy.protocol_crc import (
    CRC_CATALOGUE,
    CrcAlgorithm,
    _generic_crc,
    builtins_crc_dir,
    get_crc_registry,
    load_crc_plugins,
    reset_crc_registry,
)
from termapy.protocol_viz import (
    VisualizerInfo,
    builtins_viz_dir,
    load_visualizers_from_dir,
)


# ── parse_hex ──────────────────────────────────────────────────────────────


class TestParseHex:
    def test_simple_hex(self):
        actual = parse_hex("01 03 00 0A")
        expected = b"\x01\x03\x00\x0a"
        assert actual == expected

    def test_0x_prefix(self):
        actual = parse_hex("0x01 0x03 0xFF")
        expected = b"\x01\x03\xff"
        assert actual == expected

    def test_comma_separated(self):
        actual = parse_hex("0x01, 0x03, 0x0A")
        expected = b"\x01\x03\x0a"
        assert actual == expected

    def test_no_spaces(self):
        actual = parse_hex("0103000A")
        expected = b"\x01\x03\x00\x0a"
        assert actual == expected

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No valid hex"):
            parse_hex("")

    def test_no_valid_hex_raises(self):
        with pytest.raises(ValueError, match="No valid hex"):
            parse_hex("xyz")


# ── parse_data ─────────────────────────────────────────────────────────────


class TestParseData:
    def test_hex_only(self):
        actual = parse_data("01 02 03")
        expected = b"\x01\x02\x03"
        assert actual == expected

    def test_quoted_string(self):
        actual = parse_data('"HELLO"')
        expected = b"HELLO"
        assert actual == expected

    def test_mixed_hex_and_text(self):
        actual = parse_data('02 "HELLO" 03')
        expected = b"\x02HELLO\x03"
        assert actual == expected

    def test_escape_sequences(self):
        actual = parse_data('"OK\\r\\n"')
        expected = b"OK\r\n"
        assert actual == expected

    def test_backslash_escape(self):
        actual = parse_data('"a\\\\b"')
        expected = b"a\\b"
        assert actual == expected

    def test_null_escape(self):
        actual = parse_data('"\\0"')
        expected = b"\x00"
        assert actual == expected

    def test_unterminated_string_raises(self):
        with pytest.raises(ValueError, match="Unterminated"):
            parse_data('"hello')

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No valid data"):
            parse_data("")

    def test_unexpected_char_raises(self):
        with pytest.raises(ValueError, match="Unexpected character"):
            parse_data("ZZ")


# ── format_hex ─────────────────────────────────────────────────────────────


class TestFormatHex:
    def test_basic(self):
        actual = format_hex(b"\x01\x03\x00\x0a")
        expected = "01 03 00 0A"
        assert actual == expected

    def test_empty(self):
        assert format_hex(b"") == ""

    def test_single_byte(self):
        assert format_hex(b"\xff") == "FF"

    def test_roundtrip(self):
        """parse_hex(format_hex(data)) should return the original data."""
        original = b"\x01\x02\x03\xff\x00"
        assert parse_hex(format_hex(original)) == original


# ── format_hex_dump ────────────────────────────────────────────────────────


class TestFormatHexDump:
    def test_short_data(self):
        lines = format_hex_dump(b"\x01\x02\x03")
        assert len(lines) == 1
        assert lines[0].startswith("0000 |")
        assert "01 02 03" in lines[0]

    def test_includes_ascii(self):
        lines = format_hex_dump(b"Hello\x00")
        assert "Hello." in lines[0]  # \x00 shown as '.'

    def test_multiple_lines(self):
        data = bytes(range(32))
        lines = format_hex_dump(data, width=16)
        assert len(lines) == 2
        assert lines[0].startswith("0000")
        assert lines[1].startswith("0010")

    def test_empty(self):
        assert format_hex_dump(b"") == []


# ── parse_pattern + match_response ─────────────────────────────────────────


class TestPatternMatching:
    def test_exact_match(self):
        data, mask = parse_pattern("01 03 05")
        actual = b"\x01\x03\x05"
        assert match_response(data, actual, mask) is True

    def test_exact_mismatch(self):
        data, mask = parse_pattern("01 03 05")
        actual = b"\x01\x03\x06"
        assert match_response(data, actual, mask) is False

    def test_wildcard_match(self):
        data, mask = parse_pattern("01 03 ** **")
        actual = b"\x01\x03\xff\x00"
        assert match_response(data, actual, mask) is True

    def test_wildcard_any_value(self):
        """Wildcard bytes should accept any value."""
        data, mask = parse_pattern("01 **")
        # Assign
        actual_zero = b"\x01\x00"
        actual_ff = b"\x01\xff"
        actual_mid = b"\x01\x7f"
        # Assert
        assert match_response(data, actual_zero, mask) is True
        assert match_response(data, actual_ff, mask) is True
        assert match_response(data, actual_mid, mask) is True

    def test_length_mismatch_too_short(self):
        data, mask = parse_pattern("01 03 05")
        actual = b"\x01\x03"
        assert match_response(data, actual, mask) is False

    def test_overflow_is_fail(self):
        data, mask = parse_pattern("01 03")
        actual = b"\x01\x03\x05"
        assert match_response(data, actual, mask) is False  # extra bytes = fail

    def test_quoted_text_pattern(self):
        data, mask = parse_pattern('"OK\\r"')
        actual = b"OK\r"
        assert match_response(data, actual, mask) is True

    def test_mixed_hex_text_wildcard(self):
        """Pattern with hex, text, and wildcards."""
        data, mask = parse_pattern('02 "OK" ** 03')
        # Assign
        actual_match = b"\x02OK\x99\x03"
        actual_fail = b"\x02NO\x99\x03"
        # Assert
        assert match_response(data, actual_match, mask) is True
        assert match_response(data, actual_fail, mask) is False

    def test_all_wildcards(self):
        data, mask = parse_pattern("** ** **")
        actual = b"\xAA\xBB\xCC"
        assert match_response(data, actual, mask) is True

    def test_mask_values(self):
        """Verify wildcard produces 0x00 mask, concrete produces 0xFF."""
        data, mask = parse_pattern("01 ** 03")
        assert mask == b"\xff\x00\xff"
        assert data == b"\x01\x00\x03"


# ── FrameCollector ─────────────────────────────────────────────────────────


class TestFrameCollector:
    def test_single_frame_via_flush(self):
        """Data fed, then flushed after timeout."""
        fc = FrameCollector(timeout_ms=50)
        # Assign
        result_feed = fc.feed(b"\x01\x02\x03", now=0.0)
        result_flush_early = fc.flush(now=0.030)
        result_flush_after = fc.flush(now=0.060)
        # Assert
        assert result_feed is None  # no frame yet on first feed
        assert result_flush_early is None  # not enough silence
        assert result_flush_after == b"\x01\x02\x03"  # frame complete

    def test_accumulated_frame(self):
        """Multiple feeds within timeout combine into one frame."""
        fc = FrameCollector(timeout_ms=50)
        fc.feed(b"\x01\x02", now=0.0)
        fc.feed(b"\x03\x04", now=0.020)  # within timeout
        result = fc.flush(now=0.080)
        assert result == b"\x01\x02\x03\x04"

    def test_two_frames(self):
        """Gap between feeds produces two separate frames."""
        fc = FrameCollector(timeout_ms=50)
        # Assign
        fc.feed(b"\x01\x02", now=0.0)
        # Second feed after timeout gap — triggers emit of first frame
        frame1 = fc.feed(b"\x03\x04", now=0.100)
        frame2 = fc.flush(now=0.200)
        # Assert
        assert frame1 == b"\x01\x02"  # first frame emitted on gap detection
        assert frame2 == b"\x03\x04"  # second frame emitted on flush

    def test_reset_clears_buffer(self):
        fc = FrameCollector(timeout_ms=50)
        fc.feed(b"\x01\x02", now=0.0)
        assert fc.pending == 2
        fc.reset()
        assert fc.pending == 0
        assert fc.flush(now=1.0) is None

    def test_flush_no_data(self):
        fc = FrameCollector(timeout_ms=50)
        assert fc.flush(now=1.0) is None

    def test_pending_property(self):
        fc = FrameCollector(timeout_ms=50)
        assert fc.pending == 0
        fc.feed(b"\x01\x02\x03", now=0.0)
        assert fc.pending == 3


# ── parse_proto_script ─────────────────────────────────────────────────────


class TestParseProtoScript:
    def test_basic_send_expect(self):
        script = """
send: 01 03 00 00 00 0A
expect: 01 03 ** **
"""
        settings, steps = parse_proto_script(script)
        assert len(steps) == 2
        # Assert send step
        assert steps[0].action == "send"
        assert steps[0].data == b"\x01\x03\x00\x00\x00\x0a"
        # Assert expect step
        assert steps[1].action == "expect"
        assert steps[1].mask[2:] == b"\x00\x00"  # wildcards

    def test_labels(self):
        script = """
label: My test step
send: AA BB
"""
        _, steps = parse_proto_script(script)
        assert steps[0].label == "My test step"

    def test_label_resets_after_use(self):
        script = """
label: Step 1
send: 01 02
send: 03 04
"""
        _, steps = parse_proto_script(script)
        assert steps[0].label == "Step 1"
        assert steps[1].label == ""  # label consumed by first send

    def test_delay(self):
        script = "delay: 500ms"
        _, steps = parse_proto_script(script)
        assert steps[0].action == "delay"
        assert steps[0].timeout_ms == 500

    def test_delay_seconds(self):
        script = "delay: 1.5s"
        _, steps = parse_proto_script(script)
        assert steps[0].timeout_ms == 1500

    def test_comments_and_blanks_skipped(self):
        script = """
# This is a comment

send: 01 02

# Another comment
expect: 01 02
"""
        _, steps = parse_proto_script(script)
        assert len(steps) == 2

    def test_at_timeout_directive(self):
        script = """
@timeout 2000ms
send: 01
expect: 02
"""
        settings, steps = parse_proto_script(script)
        assert settings["timeout_ms"] == 2000
        assert steps[1].timeout_ms == 2000  # inherits default

    def test_at_frame_gap_directive(self):
        script = "@frame_gap 100ms"
        settings, _ = parse_proto_script(script)
        assert settings["frame_gap_ms"] == 100

    def test_per_step_timeout_override(self):
        script = """
@timeout 1000ms
timeout: 500ms
expect: 01 02
"""
        _, steps = parse_proto_script(script)
        assert steps[0].timeout_ms == 500  # override, not default

    def test_timeout_resets_after_expect(self):
        """Per-step timeout only applies to the next expect."""
        script = """
@timeout 1000ms
timeout: 500ms
expect: 01
expect: 02
"""
        _, steps = parse_proto_script(script)
        assert steps[0].timeout_ms == 500  # overridden
        assert steps[1].timeout_ms == 1000  # back to default

    def test_text_in_send(self):
        script = 'send: "HELLO\\r"'
        _, steps = parse_proto_script(script)
        assert steps[0].data == b"HELLO\r"

    def test_text_in_expect(self):
        script = 'expect: "OK\\r"'
        _, steps = parse_proto_script(script)
        assert steps[0].data == b"OK\r"
        assert steps[0].mask == b"\xff\xff\xff"

    def test_mixed_hex_text_in_send(self):
        script = 'send: 02 "HELLO" 03'
        _, steps = parse_proto_script(script)
        assert steps[0].data == b"\x02HELLO\x03"

    def test_unknown_directive_raises(self):
        with pytest.raises(ValueError, match="unknown directive"):
            parse_proto_script("@bogus value")

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="unknown key"):
            parse_proto_script("bogus: value")

    def test_invalid_directive_raises(self):
        with pytest.raises(ValueError, match="invalid directive"):
            parse_proto_script("@")

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError, match="expected"):
            parse_proto_script("no colon here")

    def test_full_script(self):
        """Parse a realistic multi-step script."""
        script = """
# Modbus RTU test
@timeout 1000ms
@frame_gap 50ms

label: Read registers
send: 01 03 00 00 00 0A C5 CD
expect: 01 03 14 ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** **

delay: 100ms

label: Write register
send: 01 06 00 01 00 03 98 0B
timeout: 500ms
expect: 01 06 00 01 00 03 98 0B
"""
        settings, steps = parse_proto_script(script)
        # Assert settings
        assert settings["timeout_ms"] == 1000
        assert settings["frame_gap_ms"] == 50
        # Assert step count
        assert len(steps) == 5  # send, expect, delay, send, expect
        # Assert step types
        actual_actions = [s.action for s in steps]
        expected_actions = ["send", "expect", "delay", "send", "expect"]
        assert actual_actions == expected_actions
        # Assert labels
        assert steps[0].label == "Read registers"
        assert steps[3].label == "Write register"
        # Assert per-step timeout
        assert steps[4].timeout_ms == 500

    def test_receive_then_respond(self):
        """Script can start with expect (responder pattern)."""
        script = """
label: Wait for handshake
expect: AA 55
send: 55 AA
"""
        _, steps = parse_proto_script(script)
        assert steps[0].action == "expect"
        assert steps[1].action == "send"

    def test_cmd_directive(self):
        """cmd: parses as a cmd step with UTF-8 encoded text."""
        script = "cmd: color off"
        _, steps = parse_proto_script(script)
        assert len(steps) == 1
        assert steps[0].action == "cmd"
        assert steps[0].data == b"color off"

    def test_cmd_with_label(self):
        script = """
label: Disable color
cmd: color off
"""
        _, steps = parse_proto_script(script)
        assert steps[0].action == "cmd"
        assert steps[0].label == "Disable color"
        assert steps[0].data == b"color off"

    def test_cmd_in_full_script(self):
        """cmd: works alongside send/expect/delay/flush."""
        script = """
cmd: color off
flush: 200ms
label: Test
send: "fw\\r"
expect: "FB8d\\r"
"""
        _, steps = parse_proto_script(script)
        actual_actions = [s.action for s in steps]
        expected_actions = ["cmd", "flush", "send", "expect"]
        assert actual_actions == expected_actions

    def test_flush_directive(self):
        """flush: parses as a flush step with timeout."""
        script = "flush: 100ms"
        _, steps = parse_proto_script(script)
        assert steps[0].action == "flush"
        assert steps[0].timeout_ms == 100


# ── TOML script parsing ─────────────────────────────────────────────────


class TestParseTomlScript:
    def test_basic_two_tests(self):
        """TOML script with two simple tests parses correctly."""
        toml = '''\
[[test]]
name = "Test A"
send = "01 02"
expect = "03 04"

[[test]]
name = "Test B"
send = '"hello\\r"'
expect = '"world\\r"'
'''
        script = parse_toml_script(toml)

        assert len(script.tests) == 2
        # Test A
        assert script.tests[0].name == "Test A"
        assert script.tests[0].send_data == b"\x01\x02"
        assert script.tests[0].binary is True
        # Test B
        assert script.tests[1].name == "Test B"
        assert script.tests[1].send_data == b"hello\r"
        assert script.tests[1].binary is False

    def test_settings(self):
        """TOML settings are parsed correctly."""
        toml = '''\
name = "My Test"
frame_gap = "100ms"
timeout = "2s"
strip_ansi = true
quiet = true

[[test]]
name = "T1"
send = "01"
expect = "02"
'''
        script = parse_toml_script(toml)

        assert script.name == "My Test"
        assert script.frame_gap_ms == 100
        assert script.timeout_ms == 2000
        assert script.strip_ansi is True
        assert script.quiet is True

    def test_setup_teardown(self):
        """Setup and teardown arrays are parsed."""
        toml = '''\
setup = ["color off", "echo on"]
teardown = ["color on"]

[[test]]
name = "T1"
send = "01"
expect = "02"
'''
        script = parse_toml_script(toml)

        assert script.setup == ["color off", "echo on"]
        assert script.teardown == ["color on"]

    def test_per_test_setup_teardown(self):
        """Per-test setup/teardown lists are parsed."""
        toml = '''\
[[test]]
name = "With setup"
setup = ["echo off", "color off"]
teardown = ["echo on"]
send = '"fw\\r"'
expect = '"FB8d\\r\\n"'
'''
        script = parse_toml_script(toml)

        assert script.tests[0].setup == ["echo off", "color off"]
        assert script.tests[0].teardown == ["echo on"]

    def test_per_test_legacy_cmd(self):
        """Legacy cmd field is converted to single-item setup list."""
        toml = '''\
[[test]]
name = "With cmd"
cmd = "echo off"
send = '"fw\\r"'
expect = '"FB8d\\r\\n"'
'''
        script = parse_toml_script(toml)

        assert script.tests[0].setup == ["echo off"]  # cmd → setup
        assert script.tests[0].teardown == []

    def test_per_test_timeout(self):
        """Per-test timeout overrides default."""
        toml = '''\
timeout = "1s"

[[test]]
name = "Fast"
send = "01"
expect = "02"
timeout = "200ms"

[[test]]
name = "Default"
send = "03"
expect = "04"
'''
        script = parse_toml_script(toml)

        assert script.tests[0].timeout_ms == 200
        assert script.tests[1].timeout_ms == 1000  # default

    def test_binary_detection(self):
        """binary flag is True for hex, False for quoted text."""
        toml = '''\
[[test]]
name = "hex"
send = "01 02 03"
expect = "04 05"

[[test]]
name = "text"
send = '"hello"'
expect = '"world"'
'''
        script = parse_toml_script(toml)

        assert script.tests[0].binary is True
        assert script.tests[1].binary is False

    def test_missing_test_key_raises(self):
        """TOML without [[test]] raises ValueError."""
        toml = 'name = "No tests"'
        with pytest.raises(ValueError, match="must contain"):
            parse_toml_script(toml)

    def test_missing_send_raises(self):
        """Test without send field raises ValueError."""
        toml = '''\
[[test]]
name = "No send"
expect = "01"
'''
        with pytest.raises(ValueError, match="missing 'send'"):
            parse_toml_script(toml)

    def test_missing_expect_raises(self):
        """Test without expect field raises ValueError."""
        toml = '''\
[[test]]
name = "No expect"
send = "01"
'''
        with pytest.raises(ValueError, match="missing 'expect'"):
            parse_toml_script(toml)

    def test_wildcard_expect(self):
        """Expect with ** wildcards sets mask correctly."""
        toml = '''\
[[test]]
name = "Wild"
send = "01"
expect = "01 ** 03"
'''
        script = parse_toml_script(toml)

        assert script.tests[0].expect_data == b"\x01\x00\x03"
        assert script.tests[0].expect_mask == b"\xff\x00\xff"

    def test_default_name(self):
        """Test without name gets auto-generated name."""
        toml = '''\
[[test]]
send = "01"
expect = "02"
'''
        script = parse_toml_script(toml)

        assert script.tests[0].name == "Test 1"

    def test_raw_strings_preserved(self):
        """send_raw and expect_raw store original strings."""
        toml = '''\
[[test]]
name = "T"
send = '"fw\\r"'
expect = '"FB8d\\r\\n"'
'''
        script = parse_toml_script(toml)

        assert script.tests[0].send_raw == '"fw\\r"'
        assert script.tests[0].expect_raw == '"FB8d\\r\\n"'


# ── inline format spec fields ───────────────────────────────────────────


class TestInlineFmtFields:
    def test_script_level_fmt(self):
        """Script-level send_fmt/expect_fmt are parsed and inherited by tests."""
        # Arrange
        toml = '''\
send_fmt = "Slave:H1 Func:H2"
expect_fmt = "Slave:H1 Data:H2-*"

[[test]]
name = "T1"
send = "01 03"
expect = "01 AA BB"
'''
        # Act
        script = parse_toml_script(toml)

        # Assert
        assert script.send_fmt == "Slave:H1 Func:H2"  # script-level parsed
        assert script.expect_fmt == "Slave:H1 Data:H2-*"  # script-level parsed
        assert script.tests[0].send_fmt == "Slave:H1 Func:H2"  # inherited
        assert script.tests[0].expect_fmt == "Slave:H1 Data:H2-*"  # inherited

    def test_per_test_override(self):
        """Per-test send_fmt/expect_fmt override script-level defaults."""
        # Arrange
        toml = '''\
send_fmt = "Default:H1-*"
expect_fmt = "Default:H1-*"

[[test]]
name = "T1"
send = "01 03"
expect = "01 AA"
send_fmt = "Custom:H1 Func:H2"
expect_fmt = "Custom:H1 Data:H2"
'''
        # Act
        script = parse_toml_script(toml)

        # Assert
        assert script.tests[0].send_fmt == "Custom:H1 Func:H2"  # overridden
        assert script.tests[0].expect_fmt == "Custom:H1 Data:H2"  # overridden

    def test_default_empty(self):
        """Missing send_fmt/expect_fmt default to empty string."""
        # Arrange
        toml = '''\
[[test]]
name = "T1"
send = "01"
expect = "02"
'''
        # Act
        script = parse_toml_script(toml)

        # Assert
        assert script.send_fmt == ""  # no script-level fmt
        assert script.expect_fmt == ""  # no script-level fmt
        assert script.tests[0].send_fmt == ""  # no per-test fmt
        assert script.tests[0].expect_fmt == ""  # no per-test fmt

    def test_send_fmt_only(self):
        """Only send_fmt set, expect_fmt defaults to empty."""
        # Arrange
        toml = '''\
send_fmt = "Addr:H1 Cmd:H2"

[[test]]
name = "T1"
send = "01 03"
expect = "01 AA"
'''
        # Act
        script = parse_toml_script(toml)

        # Assert
        assert script.tests[0].send_fmt == "Addr:H1 Cmd:H2"  # inherited
        assert script.tests[0].expect_fmt == ""  # not set


# ── viz field parsing ────────────────────────────────────────────────────


class TestVizFields:
    def test_script_viz_list(self):
        """Script-level viz list is parsed from header."""
        toml = '''\
name = "Test"
viz = ["Modbus", "Custom"]

[[test]]
send = "01"
expect = "02"
'''
        script = parse_toml_script(toml)

        assert script.viz == ["Modbus", "Custom"]

    def test_script_viz_default_empty(self):
        """Script-level viz defaults to empty list when omitted."""
        toml = '''\
[[test]]
send = "01"
expect = "02"
'''
        script = parse_toml_script(toml)

        assert script.viz == []

    def test_test_viz_string(self):
        """Per-test viz field is parsed as a string."""
        toml = '''\
[[test]]
name = "Read regs"
viz = "Modbus"
send = "01 03 00 00 00 01 84 0A"
expect = "01 03 02 00 07 F9 86"
'''
        script = parse_toml_script(toml)

        assert script.tests[0].viz == "Modbus"

    def test_test_viz_default_empty(self):
        """Per-test viz defaults to empty string when omitted."""
        toml = '''\
[[test]]
send = "01"
expect = "02"
'''
        script = parse_toml_script(toml)

        assert script.tests[0].viz == ""

    def test_mixed_viz_and_no_viz(self):
        """Some tests have viz, others don't."""
        toml = '''\
viz = ["Modbus"]

[[test]]
name = "With viz"
viz = "Modbus"
send = "01"
expect = "02"

[[test]]
name = "Without viz"
send = "03"
expect = "04"
'''
        script = parse_toml_script(toml)

        assert script.viz == ["Modbus"]
        assert script.tests[0].viz == "Modbus"
        assert script.tests[1].viz == ""


# ── load_proto_script (format detection) ─────────────────────────────────


class TestLoadProtoScript:
    def test_toml_detected(self):
        """TOML format is detected when [[test]] present."""
        toml = '''\
[[test]]
name = "T1"
send = "01"
expect = "02"
'''
        fmt, parsed = load_proto_script(toml)

        assert fmt == "toml"
        assert hasattr(parsed, "tests")  # assert ProtoScript

    def test_flat_fallback(self):
        """Flat format used when TOML parse fails."""
        flat = '''\
label: Test
send: "fw\\r"
expect: "FB8d\\r"
'''
        fmt, parsed = load_proto_script(flat)

        assert fmt == "flat"
        actual_type = type(parsed)
        assert actual_type == tuple  # assert (settings, steps)


# ── diff_bytes ───────────────────────────────────────────────────────────


class TestDiffBytes:
    def test_exact_match(self):
        """All bytes match → all 'match'."""
        expected = b"\x01\x02\x03"
        actual = b"\x01\x02\x03"
        mask = b"\xff\xff\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match", "match", "match"]

    def test_mismatch(self):
        """Differing byte → 'mismatch'."""
        expected = b"\x01\x02\x03"
        actual = b"\x01\xFF\x03"
        mask = b"\xff\xff\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match", "mismatch", "match"]

    def test_wildcard(self):
        """Wildcard mask byte → 'wildcard'."""
        expected = b"\x01\x00\x03"
        actual = b"\x01\xFF\x03"
        mask = b"\xff\x00\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match", "wildcard", "match"]

    def test_actual_shorter(self):
        """Missing bytes in actual → 'missing'."""
        expected = b"\x01\x02\x03"
        actual = b"\x01"
        mask = b"\xff\xff\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match", "missing", "missing"]

    def test_actual_longer(self):
        """Extra bytes in actual → only expected bytes diffed, overflow ignored."""
        expected = b"\x01"
        actual = b"\x01\x02\x03"
        mask = b"\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match"]  # extra bytes not in diff list

    def test_empty_both(self):
        """Empty expected and actual → empty result."""
        result = diff_bytes(b"", b"", b"")

        assert result == []

    def test_mixed(self):
        """Mix of match, mismatch, wildcard with overflow."""
        expected = b"\x01\x00\x03"
        actual = b"\x01\xFF\x04\x05"
        mask = b"\xff\x00\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match", "wildcard", "mismatch"]  # extra byte not in diff


# ── overflow_count ────────────────────────────────────────────────────


class TestOverflowCount:
    def test_no_overflow(self):
        actual = overflow_count(b"\x01\x02\x03", b"\x01\x02\x03")
        assert actual == 0  # same length, no overflow

    def test_actual_shorter(self):
        actual = overflow_count(b"\x01\x02\x03", b"\x01")
        assert actual == 0  # shorter is not overflow

    def test_overflow(self):
        actual = overflow_count(b"\x01", b"\x01\x02\x03")
        assert actual == 2  # 2 extra bytes

    def test_empty_expected(self):
        actual = overflow_count(b"", b"\x01\x02")
        assert actual == 2  # all bytes are overflow


class TestFormatDiffMarkupOverflow:
    def test_no_overflow_no_ovr_tag(self):
        result = format_diff_markup(
            actual=b"\x01\x02",
            expected=b"\x01\x02",
            mask=b"\xff\xff",
            token_fn=lambda b: f"{b:02X} ",
            missing_token="?? ",
        )
        assert "OVR" not in result  # no overflow indicator

    def test_overflow_appends_ovr_tag(self):
        result = format_diff_markup(
            actual=b"\x01\x02\x03\x04",
            expected=b"\x01\x02",
            mask=b"\xff\xff",
            token_fn=lambda b: f"{b:02X} ",
            missing_token="?? ",
        )
        assert "OVR+2" in result  # overflow indicator present


# ── Packet Visualizer Tests ──────────────────────────────────────────


# ── CRC Plugins ──────────────────────────────────────────────────────────


class TestCrcPlugins:
    """Tests for CRC plugin loading, catalogue, and computation."""

    def test_plugins_load_sum_only(self):
        """Built-in CRC plugin directory loads only sum8 and sum16."""
        algos = load_crc_plugins(builtins_crc_dir())

        expected_names = {"sum8", "sum16"}
        assert set(algos.keys()) == expected_names

    def test_sum8_compute(self):
        """Sum8 checksum computes correctly."""
        algos = load_crc_plugins(builtins_crc_dir())
        actual = algos["sum8"].compute(b"\x01\x02\x03")
        expected = 6
        assert actual == expected

    def test_registry_has_catalogue_and_plugins(self):
        """Registry includes catalogue entries + plugin entries."""
        reset_crc_registry()
        registry = get_crc_registry()

        # Catalogue entries
        assert "crc16-modbus" in registry
        assert "crc16-xmodem" in registry
        assert "crc32" in registry
        assert "crc8" in registry
        # Backward-compat aliases
        assert "crc16m" in registry
        assert "crc16x" in registry
        # Plugin entries
        assert "sum8" in registry
        assert "sum16" in registry
        assert isinstance(registry["crc16-modbus"], CrcAlgorithm)

    def test_crc16_modbus_width(self):
        """CRC-16/Modbus catalogue entry has width=2 bytes."""
        reset_crc_registry()
        registry = get_crc_registry()
        assert registry["crc16-modbus"].width == 2

    def test_crc16_modbus_compute(self):
        """CRC-16/Modbus via catalogue matches known Modbus frame CRC."""
        reset_crc_registry()
        registry = get_crc_registry()
        # Modbus read request: slave=1, func=3, addr=0, count=10
        data = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x0A])
        actual = registry["crc16-modbus"].compute(data)
        expected = 0xCDC5  # known CRC for this frame
        assert actual == expected

    def test_alias_matches_canonical(self):
        """Backward-compat alias crc16m produces same result as crc16-modbus."""
        reset_crc_registry()
        registry = get_crc_registry()
        data = b"123456789"
        actual_alias = registry["crc16m"].compute(data)
        actual_canonical = registry["crc16-modbus"].compute(data)
        assert actual_alias == actual_canonical

    def test_empty_dir(self, tmp_path):
        """Empty directory returns no plugin algorithms."""
        algos = load_crc_plugins(tmp_path)
        assert algos == {}


# ── Generic CRC engine — reveng catalogue check values ─────────────────


# Canonical names only (exclude aliases which are duplicate entries)
_CANONICAL_NAMES = [n for n in CRC_CATALOGUE if n not in ("crc16m", "crc16x")]
_CHECK_DATA = b"123456789"


class TestGenericCrcEngine:
    """Verify generic CRC engine against all 61 reveng catalogue check values."""

    @pytest.mark.parametrize("name", _CANONICAL_NAMES)
    def test_catalogue_check_value(self, name):
        """Generic engine matches reveng catalogue check value."""
        entry = CRC_CATALOGUE[name]
        actual = _generic_crc(
            _CHECK_DATA,
            entry["width"],
            entry["poly"],
            entry["init"],
            entry["refin"],
            entry["refout"],
            entry["xorout"],
        )
        expected = entry["check"]
        assert actual == expected  # {name}: {actual:#x} != {expected:#x}


class TestCrcCatalogue:
    """Verify CRC catalogue metadata."""

    @pytest.mark.parametrize("name", _CANONICAL_NAMES)
    def test_every_entry_has_desc(self, name):
        """Every catalogue entry has a non-empty desc string."""
        entry = CRC_CATALOGUE[name]
        assert "desc" in entry  # missing desc field
        assert isinstance(entry["desc"], str)  # desc must be a string
        assert len(entry["desc"]) > 0  # desc must not be empty

    def test_aliases_inherit_desc(self):
        """Backward-compatible aliases point to entries with desc."""
        for alias in ("crc16m", "crc16x"):
            entry = CRC_CATALOGUE[alias]
            assert "desc" in entry  # alias entry has desc

    def test_catalogue_count(self):
        """Catalogue has expected number of canonical algorithms (20+30+12)."""
        actual = len(_CANONICAL_NAMES)
        expected = 62
        assert actual == expected  # catalogue size changed

    def test_desc_is_short(self):
        """Description strings should be concise one-liners."""
        for name in _CANONICAL_NAMES:
            desc = CRC_CATALOGUE[name]["desc"]
            assert len(desc) <= 80  # desc too long: {name}


# ── extract_fmt_title ────────────────────────────────────────────────────


class TestExtractFmtTitle:
    def test_title_extracted(self):
        # Act
        actual_title, actual_spec = extract_fmt_title(
            "Title:Modbus_RTU Slave:H1 Func:H2")

        # Assert
        assert actual_title == "Modbus RTU"  # underscores replaced with spaces
        assert actual_spec == "Slave:H1 Func:H2"  # title stripped from spec

    def test_no_title(self):
        # Act
        actual_title, actual_spec = extract_fmt_title("Slave:H1 Func:H2")

        # Assert
        assert actual_title == ""  # no title found
        assert actual_spec == "Slave:H1 Func:H2"  # spec unchanged

    def test_title_only(self):
        # Act
        actual_title, actual_spec = extract_fmt_title("Title:My_Custom_View")

        # Assert
        assert actual_title == "My Custom View"  # title extracted
        assert actual_spec == ""  # no remaining spec

    def test_single_word_title(self):
        # Act
        actual_title, actual_spec = extract_fmt_title("Title:Modbus Slave:H1")

        # Assert
        assert actual_title == "Modbus"  # no underscores to replace
        assert actual_spec == "Slave:H1"  # remaining spec


# ── Format Spec Parsing ──────────────────────────────────────────────────


class TestParseFormatSpec:
    """Tests for format spec language parsing."""

    def test_single_hex_byte(self):
        """Single hex byte: H1."""
        cols = parse_format_spec("Slave:H1")
        assert len(cols) == 1
        assert cols[0].name == "Slave"
        assert cols[0].type_code == "H"
        assert cols[0].byte_indices == [0]  # 1-based → 0-based

    def test_hex_range(self):
        """Hex range: H3-4 (ascending = big-endian)."""
        cols = parse_format_spec("Addr:H3-4")
        assert cols[0].byte_indices == [2, 3]

    def test_hex_descending_range(self):
        """Hex descending range: H4-1 (little-endian)."""
        cols = parse_format_spec("Val:H4-1")
        assert cols[0].byte_indices == [3, 2, 1, 0]

    def test_unsigned_decimal(self):
        """Unsigned decimal type: U3-4."""
        cols = parse_format_spec("Count:U3-4")
        assert cols[0].type_code == "U"
        assert cols[0].byte_indices == [2, 3]

    def test_signed_decimal(self):
        """Signed decimal: I1-4."""
        cols = parse_format_spec("Temp:I1-4")
        assert cols[0].type_code == "I"
        assert cols[0].byte_indices == [0, 1, 2, 3]

    def test_string_type(self):
        """String type: S5-12."""
        cols = parse_format_spec("Name:S5-12")
        assert cols[0].type_code == "S"
        assert cols[0].byte_indices == list(range(4, 12))

    def test_float_type(self):
        """Float type: F1-4."""
        cols = parse_format_spec("Temp:F1-4")
        assert cols[0].type_code == "F"
        assert cols[0].byte_indices == [0, 1, 2, 3]

    def test_bit_field(self):
        """Bit field: B1.3."""
        cols = parse_format_spec("Enable:B1.3")
        assert cols[0].type_code == "B"
        assert cols[0].byte_indices == [0]
        assert cols[0].bit == 3

    def test_bit_field_multi_byte(self):
        """Multi-byte bit field: B1-2.7-9."""
        cols = parse_format_spec("Mode:B1-2.7-9")
        assert cols[0].type_code == "B"
        assert cols[0].byte_indices == [0, 1]
        assert cols[0].bit == (7, 9)  # bit range tuple

    def test_bit_field_multi_byte_descending(self):
        """Multi-byte bit field descending bytes: B2-1.4-6."""
        cols = parse_format_spec("Flags:B2-1.4-6")
        assert cols[0].byte_indices == [1, 0]  # descending = LE
        assert cols[0].bit == (4, 6)

    def test_padding_type(self):
        """Padding type: _3-4."""
        cols = parse_format_spec("A:H1 _:_2-3 B:H4")
        assert cols[1].type_code == "_"
        assert cols[1].byte_indices == [1, 2]

    def test_wildcard(self):
        """Wildcard: H7-*."""
        cols = parse_format_spec("Data:H7-*")
        assert cols[0].wildcard is True
        assert cols[0].byte_indices == [6]  # start index

    def test_crc_le(self):
        """CRC with little-endian: crc16m_le."""
        cols = parse_format_spec("CRC:crc16m_le")
        assert cols[0].type_code == "crc"
        assert cols[0].crc_algo == "crc16m"
        assert cols[0].crc_little_endian is True

    def test_crc_be(self):
        """CRC with big-endian: crc16x_be."""
        cols = parse_format_spec("CRC:crc16x_be")
        assert cols[0].crc_algo == "crc16x"
        assert cols[0].crc_little_endian is False

    def test_crc_with_range(self):
        """CRC with explicit data range: crc16m_le(1-6)."""
        cols = parse_format_spec("CRC:crc16m_le(1-6)")
        assert cols[0].crc_algo == "crc16m"
        assert cols[0].crc_data_range == (0, 5)  # 1-based → 0-based

    def test_crc_hyphenated_name_le(self):
        """CRC with hyphenated catalogue name: crc16-modbus_le."""
        cols = parse_format_spec("CRC:crc16-modbus_le")
        assert cols[0].type_code == "crc"
        assert cols[0].crc_algo == "crc16-modbus"
        assert cols[0].crc_little_endian is True

    def test_crc_hyphenated_multi_dash_be(self):
        """CRC with multi-hyphen name: crc16-ccitt-false_be."""
        cols = parse_format_spec("CRC:crc16-ccitt-false_be")
        assert cols[0].crc_algo == "crc16-ccitt-false"
        assert cols[0].crc_little_endian is False

    def test_crc_hyphenated_with_range(self):
        """CRC with hyphenated name and data range."""
        cols = parse_format_spec("CRC:crc16-modbus_le(1-6)")
        assert cols[0].crc_algo == "crc16-modbus"
        assert cols[0].crc_data_range == (0, 5)

    def test_multi_column(self):
        """Full Modbus spec parses multiple columns."""
        spec = "Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16m_le"
        cols = parse_format_spec(spec)
        assert len(cols) == 5
        actual_names = [c.name for c in cols]
        expected_names = ["Slave", "Func", "Addr", "Count", "CRC"]
        assert actual_names == expected_names


# ── apply_format ─────────────────────────────────────────────────────────


def _modbus_crc(data: bytes) -> int:
    """Helper: compute Modbus CRC-16 for test frame construction."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _modbus_frame(payload: bytes) -> bytes:
    """Helper: build a complete Modbus frame with CRC."""
    crc = _modbus_crc(payload)
    return payload + struct.pack("<H", crc)


class TestApplyFormat:
    """Tests for applying format specs to raw data."""

    def test_hex_single_byte(self):
        """Single byte formats as 2-char hex."""
        cols = parse_format_spec("Slave:H1")
        headers, values = apply_format(b"\x01", cols)
        assert headers == ["Slave"]
        assert values == ["01"]

    def test_unsigned_two_bytes(self):
        """Two-byte unsigned decimal big-endian."""
        cols = parse_format_spec("Addr:U1-2")
        headers, values = apply_format(b"\x00\x0A", cols)
        assert values == ["10"]  # 0x000A = 10

    def test_signed_negative(self):
        """Signed decimal shows sign."""
        cols = parse_format_spec("Temp:I1-2")
        headers, values = apply_format(b"\xFF\xFE", cols)
        assert values == ["-2"]

    def test_signed_positive(self):
        """Signed decimal shows + for positive."""
        cols = parse_format_spec("Temp:I1")
        headers, values = apply_format(b"\x7F", cols)
        assert values == ["+127"]

    def test_string_printable(self):
        """String type shows printable chars, dots for unprintable."""
        cols = parse_format_spec("Name:S1-5")
        headers, values = apply_format(b"Hi\x00AB", cols)
        assert values == ["Hi.AB"]

    def test_float_value(self):
        """Float type decodes IEEE 754."""
        val_bytes = struct.pack(">f", 3.14)
        cols = parse_format_spec("Temp:F1-4")
        _, values = apply_format(val_bytes, cols)
        assert float(values[0]) == pytest.approx(3.14, abs=0.01)

    def test_bit_field(self):
        """Bit field extracts single bit."""
        cols = parse_format_spec("Enable:B1.0 Error:B1.7")
        # byte 0 = 0x81 = 10000001
        _, values = apply_format(b"\x81", cols)
        assert values == ["1", "1"]  # bit 0 = 1, bit 7 = 1

    def test_bit_field_multi_byte_value(self):
        """Multi-byte bit field extracts value from bit range."""
        # Arrange — bytes 1-2 = 0x0180 (big-endian), bits 7-9
        # 0x0180 = 0000_0001_1000_0000, bits 7-9 = 011 = 3
        cols = parse_format_spec("Mode:B1-2.7-9")
        data = b"\x01\x80"

        # Act
        _, values = apply_format(data, cols)

        # Assert
        actual = int(values[0])
        expected = 3
        assert actual == expected  # bits 7-9 of 0x0180 = 3

    def test_bit_field_multi_byte_all_ones(self):
        """Multi-byte bit field with all bits set in range."""
        # Arrange — 0xFFFF, bits 4-7 = 1111 = 15
        cols = parse_format_spec("Nibble:B1-2.4-7")
        data = b"\xFF\xFF"

        # Act
        _, values = apply_format(data, cols)

        # Assert
        actual = int(values[0])
        expected = 15
        assert actual == expected  # bits 4-7 all set = 15

    def test_padding_skipped_in_output(self):
        """Padding columns are not included in headers or values."""
        # Arrange
        cols = parse_format_spec("A:H1 Pad:_2-3 B:H4")
        data = b"\x01\x02\x03\x04"

        # Act
        headers, values = apply_format(data, cols)

        # Assert
        assert headers == ["A", "B"]  # padding skipped
        assert values == ["01", "04"]  # only non-padding values

    def test_wildcard_expands(self):
        """Wildcard h1-* expands to cover all bytes (spaced per-byte hex)."""
        cols = parse_format_spec("Data:h1-*")
        _, values = apply_format(b"\x01\x02\x03", cols)
        assert values == ["01 02 03"]

    def test_hex_combined_multi_byte(self):
        """H with multi-byte range produces combined hex (no spaces)."""
        cols = parse_format_spec("Val:H1-3")
        _, values = apply_format(b"\x01\x02\x03", cols)
        assert values == ["010203"]

    def test_modbus_read_request(self):
        """Full Modbus read request decodes correctly."""
        # Slave=1, Func=3, Addr=0, Count=10
        payload = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x0A])
        frame = _modbus_frame(payload)
        spec = "Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16m_le"
        cols = parse_format_spec(spec)
        reset_crc_registry()
        headers, values = apply_format(frame, cols)
        assert headers == ["Slave", "Func", "Addr", "Count", "CRC"]
        assert values[0] == "01"  # Slave
        assert values[1] == "03"  # Func
        assert values[2] == "0"   # Addr
        assert values[3] == "10"  # Count
        # CRC should be a 4-char hex string
        assert len(values[4]) == 4

    def test_wildcard_with_crc(self):
        """Wildcard excludes CRC bytes at end."""
        # 6 data bytes + 2 CRC bytes = 8 total
        payload = b"\x01\x02ABCD"
        frame = _modbus_frame(payload)
        spec = "Data:h1-* CRC:crc16m_le"
        cols = parse_format_spec(spec)
        reset_crc_registry()
        headers, values = apply_format(frame, cols)
        # Data should be first 6 bytes, not including CRC (h = spaced)
        assert "01 02 41 42 43 44" == values[0]


# ── diff_columns ──────────────────────────────────────────────────────────


class TestDiffColumns:
    """Tests for column-based diff comparison."""

    def test_all_match(self):
        """All bytes match → all column statuses are 'match'."""
        data = b"\x01\x03\x00\x0A"
        mask = b"\xff\xff\xff\xff"
        cols = parse_format_spec("Slave:H1 Func:H2 Addr:U3-4")
        headers, exp_vals, act_vals, statuses = diff_columns(
            data, data, mask, cols)
        assert all(s == "match" for s in statuses)

    def test_mismatch_column(self):
        """Byte mismatch in a column → that column is 'mismatch'."""
        expected = b"\x01\x03\x00\x0A"
        actual = b"\x01\x04\x00\x0A"
        mask = b"\xff\xff\xff\xff"
        cols = parse_format_spec("Slave:H1 Func:H2 Addr:U3-4")
        _, _, _, statuses = diff_columns(actual, expected, mask, cols)
        assert statuses[0] == "match"      # Slave matches
        assert statuses[1] == "mismatch"   # Func differs
        assert statuses[2] == "match"      # Addr matches

    def test_wildcard_column(self):
        """Wildcard mask bytes → column status is 'wildcard' (dimmed)."""
        expected = b"\x01\x00\x03"
        actual = b"\x01\xFF\x03"
        mask = b"\xff\x00\xff"
        cols = parse_format_spec("A:H1 B:H2 C:H3")
        _, _, _, statuses = diff_columns(actual, expected, mask, cols)
        assert statuses[1] == "wildcard"  # wildcard byte, dimmed display

    def test_crc_verify_pass(self):
        """CRC column shows 'match' when CRC is valid."""
        payload = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x0A])
        frame = _modbus_frame(payload)
        mask = b"\xff" * len(frame)
        spec = "Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16m_le"
        cols = parse_format_spec(spec)
        reset_crc_registry()
        _, _, _, statuses = diff_columns(frame, frame, mask, cols)
        assert statuses[-1] == "match"  # CRC valid

    def test_crc_verify_fail(self):
        """CRC column shows 'mismatch' when CRC is corrupt."""
        payload = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x0A])
        frame = _modbus_frame(payload)
        # Corrupt the CRC
        corrupted = bytearray(frame)
        corrupted[-1] ^= 0xFF
        corrupted = bytes(corrupted)
        mask = b"\xff" * len(frame)
        spec = "Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16m_le"
        cols = parse_format_spec(spec)
        reset_crc_registry()
        _, _, _, statuses = diff_columns(corrupted, frame, mask, cols)
        assert statuses[-1] == "mismatch"  # CRC invalid

    def test_padding_skipped_in_diff(self):
        """Padding columns are excluded from diff output."""
        # Arrange
        data = b"\x01\x02\x03\x04"
        mask = b"\xff\xff\xff\xff"
        cols = parse_format_spec("A:H1 Pad:_2-3 B:H4")

        # Act
        headers, _, _, statuses = diff_columns(data, data, mask, cols)

        # Assert
        assert headers == ["A", "B"]  # padding not in output
        assert len(statuses) == 2  # only 2 columns


# ── Hex View Column API ──────────────────────────────────────────────────


class TestHexViewColumns:
    """Tests for hex_view column-based API."""

    def test_empty(self):
        """Empty bytes produce empty value."""
        from termapy.builtins.viz.hex_view import format_columns

        headers, values = format_columns(b"")
        assert headers == ["Hex"]
        assert values == [""]

    def test_multiple_bytes(self):
        """Multiple bytes as spaced hex."""
        from termapy.builtins.viz.hex_view import format_columns

        reset_crc_registry()
        headers, values = format_columns(b"\x01\x02\x0A")
        assert headers == ["Hex"]
        assert values == ["01 02 0A"]

    def test_diff_match(self):
        """All matching bytes → match status."""
        from termapy.builtins.viz.hex_view import diff_columns

        reset_crc_registry()
        _, _, _, statuses = diff_columns(
            b"\x01\x02", b"\x01\x02", b"\xff\xff")
        assert statuses == ["match"]

    def test_diff_mismatch(self):
        """Mismatched bytes → mixed status (per-byte coloring)."""
        from termapy.builtins.viz.hex_view import diff_columns

        reset_crc_registry()
        _, _, act_values, statuses = diff_columns(
            b"\x01\xFF", b"\x01\x02", b"\xff\xff")
        assert statuses == ["mixed"]  # per-byte markup
        assert "01" in act_values[0]  # matching byte present
        assert "FF" in act_values[0]  # mismatched byte present


# ── Text View Column API ─────────────────────────────────────────────────


class TestTextViewColumns:
    """Tests for text_view column-based API."""

    def test_empty(self):
        """Empty bytes produce empty value."""
        from termapy.builtins.viz.text_view import format_columns

        headers, values = format_columns(b"")
        assert headers == ["Text"]
        assert values == [""]

    def test_printable(self):
        """Printable ASCII shows as characters."""
        from termapy.builtins.viz.text_view import format_columns

        reset_crc_registry()
        headers, values = format_columns(b"Hi")
        assert values == ["Hi"]

    def test_unprintable(self):
        """Unprintable bytes show as dots."""
        from termapy.builtins.viz.text_view import format_columns

        reset_crc_registry()
        _, values = format_columns(b"\x80\x81")
        assert values == [".."]


# ── Modbus View Column API ───────────────────────────────────────────────



# ── Visualizer Loader ────────────────────────────────────────────────────


class TestLoadVisualizersFromDir:
    """Tests for visualizer discovery and loading."""

    def test_builtins_load(self):
        """Built-in viz directory loads Hex and Text."""
        vizs = load_visualizers_from_dir(builtins_viz_dir(), "built-in")

        names = [v.name for v in vizs]
        assert "Hex" in names
        assert "Text" in names

    def test_builtin_sort_order(self):
        """Hex sorts before Text."""
        vizs = load_visualizers_from_dir(builtins_viz_dir(), "built-in")

        by_name = {v.name: v for v in vizs}
        assert by_name["Hex"].sort_order < by_name["Text"].sort_order

    def test_builtin_description(self):
        """Built-ins have descriptions."""
        vizs = load_visualizers_from_dir(builtins_viz_dir(), "built-in")

        by_name = {v.name: v for v in vizs}
        assert by_name["Hex"].description != ""
        assert by_name["Text"].description != ""

    def test_empty_dir(self, tmp_path):
        """Empty directory returns no visualizers."""
        vizs = load_visualizers_from_dir(tmp_path, "test")

        assert vizs == []

    def test_nonexistent_dir(self, tmp_path):
        """Non-existent directory returns no visualizers."""
        vizs = load_visualizers_from_dir(tmp_path / "nope", "test")

        assert vizs == []

    def test_skip_underscore(self, tmp_path):
        """Files starting with _ are skipped."""
        (tmp_path / "_hidden.py").write_text(
            'NAME = "Hidden"\n'
            'def format_columns(d): return ["Col"], ["val"]\n'
            'def diff_columns(a, e, m): return ["Col"], ["e"], ["a"], ["match"]\n'
        )
        vizs = load_visualizers_from_dir(tmp_path, "test")

        assert vizs == []

    def test_custom_visualizer(self, tmp_path):
        """Custom .py file with valid column exports loads correctly."""
        (tmp_path / "custom.py").write_text(
            'NAME = "Custom"\n'
            'DESCRIPTION = "A test visualizer"\n'
            'SORT_ORDER = 99\n'
            'def format_columns(d): return ["Col"], ["custom"]\n'
            'def diff_columns(a, e, m): return ["Col"], ["e"], ["a"], ["match"]\n'
        )
        vizs = load_visualizers_from_dir(tmp_path, "test")

        assert len(vizs) == 1
        assert vizs[0].name == "Custom"
        assert vizs[0].description == "A test visualizer"
        assert vizs[0].sort_order == 99
        assert vizs[0].source == "test"
        actual_headers, actual_values = vizs[0].format_columns(b"")
        assert actual_values == ["custom"]

    def test_missing_name_skipped(self, tmp_path):
        """File without NAME is skipped."""
        (tmp_path / "bad.py").write_text(
            'def format_columns(d): return [], []\n'
            'def diff_columns(a, e, m): return [], [], [], []\n'
        )
        vizs = load_visualizers_from_dir(tmp_path, "test")

        assert vizs == []

    def test_missing_format_columns_skipped(self, tmp_path):
        """File without format_columns is skipped."""
        (tmp_path / "bad.py").write_text(
            'NAME = "Bad"\n'
            'def diff_columns(a, e, m): return [], [], [], []\n'
        )
        vizs = load_visualizers_from_dir(tmp_path, "test")

        assert vizs == []

    def test_defaults(self, tmp_path):
        """SORT_ORDER defaults to 50, DESCRIPTION to empty."""
        (tmp_path / "minimal.py").write_text(
            'NAME = "Min"\n'
            'def format_columns(d): return [], []\n'
            'def diff_columns(a, e, m): return [], [], [], []\n'
        )
        vizs = load_visualizers_from_dir(tmp_path, "test")

        assert vizs[0].sort_order == 50
        assert vizs[0].description == ""

    def test_broken_file_skipped(self, tmp_path):
        """File with syntax error is skipped without crashing."""
        (tmp_path / "broken.py").write_text("def bad(:\n")
        vizs = load_visualizers_from_dir(tmp_path, "test")

        assert vizs == []


# -- Demo AT visualizer -------------------------------------------------------


