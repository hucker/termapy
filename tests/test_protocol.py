"""Tests for termapy.protocol — hex utilities, framing, matching, script parsing."""

import pytest

from termapy.protocol import (
    FrameCollector,
    Step,
    diff_bytes,
    format_hex,
    format_hex_dump,
    load_proto_script,
    match_response,
    parse_data,
    parse_hex,
    parse_pattern,
    parse_proto_script,
    parse_toml_script,
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

    def test_length_mismatch_too_long(self):
        data, mask = parse_pattern("01 03")
        actual = b"\x01\x03\x05"
        assert match_response(data, actual, mask) is False

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
        """Extra bytes in actual → 'extra'."""
        expected = b"\x01"
        actual = b"\x01\x02\x03"
        mask = b"\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match", "extra", "extra"]

    def test_empty_both(self):
        """Empty expected and actual → empty result."""
        result = diff_bytes(b"", b"", b"")

        assert result == []

    def test_mixed(self):
        """Mix of match, mismatch, wildcard, extra."""
        expected = b"\x01\x00\x03"
        actual = b"\x01\xFF\x04\x05"
        mask = b"\xff\x00\xff"
        result = diff_bytes(expected, actual, mask)

        assert result == ["match", "wildcard", "mismatch", "extra"]
