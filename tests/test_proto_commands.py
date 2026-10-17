"""The non-CRC half of the /proto family driven through the dispatcher on the CLI DEMO rig.

Until 2026-10-12 only ``/proto.run`` on a TOML script and ``/proto.send
--dry-run`` had run in-process: the flat-script runner had never executed
under a test, the connected ``/proto.send`` path (TX display, inline
delays, the hex dump of a long reply, the quoted-text view) was unexecuted,
and ``/proto.help`` and ``/proto.info`` were named by no test at all.
These tests drive each command the way a user or a script does --
``repl.dispatch_full`` on a ``CLITerminal`` connected to the DEMO port
(the ``tests/test_port_commands.py`` shape) -- and pin the scriptable
``value``, the printed lines and the effect on the device and the session
flags.  A send without its line ending is never answered, and the DEMO
device, like a real one, keeps the partial command in its input buffer
and prepends it to the next line -- so each test gets its own rig.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.folders import ensure_folder
from termapy.protocol import get_crc_registry


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port, its cfg folder under tmp_path."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200}}
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


def _pro(folder: Path, name: str, text: str) -> Path:
    """Write a ``.pro`` script and return its path."""
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def _proto_folder(cli: CLITerminal) -> Path:
    """The rig's proto/ data folder, created the one sanctioned way."""
    return ensure_folder(cli.repl.ctx.fs.proto_dir)


# One of every flat directive: a passing text expect, a quiet and a loud
# device command, flush, delay, a failing expect, a hex expect with
# wildcards, and an unlabeled expect that times out.
_FLAT_EVERY_DIRECTIVE = """\
# every flat directive once
@name Flat demo
@timeout 1s
@frame_gap 100ms
@strip_ansi on
label: hello
send: "AT\\r"
expect: "OK\\r\\n"
@quiet
cmd: AT
@loud
cmd: AT
flush: 10ms
delay: 10ms
label: wrong answer
send: "AT\\r"
expect: "NOPE"
label: wildcard
send: "AT\\r"
expect: 4F 4B ** **
timeout: 50ms
expect: "never"
"""

# Script-level setup and teardown, a per-test setup, a failing test and a
# test that times out (AT without its line ending is never answered).
_TOML_MIXED = """\
name = "Mixed"
frame_gap = "100ms"
strip_ansi = true
setup = ["AT"]
teardown = ["AT"]

[[test]]
name = "ok"
send = '"AT\\r"'
expect = '"OK\\r\\n"'

[[test]]
name = "wrong"
setup = ["AT"]
send = '"AT\\r"'
expect = '"NOPE"'

[[test]]
name = "silent"
timeout = "50ms"
send = '"AT"'
expect = '"x"'
"""


# ── help and the bare root ──────────────────────────────────────────────────


class TestHelpAndRoot:
    def test_proto_help_renders_the_page_and_lists_the_scripts(self, cli, capsys):
        """/proto.help is /help proto plus an AVAILABLE PROTO FILES tree of the proto folder."""
        _pro(_proto_folder(cli), "probe.pro", "@name x\n")
        capsys.readouterr()
        result = cli.repl.dispatch_full("/proto.help")
        out = capsys.readouterr().out
        assert result.value == "proto", "the resolved name, so a script can confirm the match"
        assert "/proto.send" in out, "the page lists the subcommands"
        assert "AVAILABLE PROTO FILES" in out and "probe.pro" in out, (
            "the folder's scripts are appended"
        )

    def test_bare_proto_lists_without_a_picker(self, cli):
        """bare-declared: a bare /proto runs /proto.list where no picker exists (the CLI); the TUI opens its picker."""
        bare = cli.repl.dispatch_full("/proto")
        listed = cli.repl.dispatch_full("/proto.list")
        assert bare.success, bare.error
        assert bare.value == listed.value, "bare /proto is /proto.list"

    def test_a_stray_argument_on_the_root_is_refused(self, cli):
        """/proto with an argument but no subcommand fails with the one sanctioned bare-parent redirect."""
        result = cli.repl.dispatch_full("/proto bogus")
        assert result.success is False
        assert result.error == "Usage: /proto.<sub>  (try /proto.help)"


# ── /proto.hex and /proto.info ──────────────────────────────────────────────


class TestHex:
    def test_bare_hex_queries_without_changing_the_flag(self, cli, capsys):
        """A bare /proto.hex reports the state and mutates nothing (setting commands query on a bare call)."""
        flags = cli.repl.ctx.ns("flags")
        capsys.readouterr()
        result = cli.repl.dispatch_full("/proto.hex")
        assert result.value == "off"
        assert flags["hex"] is False, "a bare setting command never flips"
        assert "Hex display mode disabled." in capsys.readouterr().out

    def test_toggle_on_and_off_drive_the_shared_hex_flag(self, cli):
        """toggle flips, a vocabulary token sets, and the flag /term.hex shares follows each one."""
        flags = cli.repl.ctx.ns("flags")
        assert cli.repl.dispatch_full("/proto.hex toggle").value == "on"
        assert flags["hex"] is True, "toggle flipped the shared flag"
        assert cli.repl.dispatch_full("/proto.hex off").value == "off"
        assert flags["hex"] is False, "off set it"
        assert cli.repl.dispatch_full("/proto.hex yes").value == "on", "any vocabulary token sets"

    def test_an_unrecognized_token_is_refused_with_the_vocabulary(self, cli):
        """A token outside the boolean vocabulary fails naming the twelve tokens and toggle, and nothing flips."""
        result = cli.repl.dispatch_full("/proto.hex maybe")
        assert result.success is False
        assert (
            result.error
            == "Invalid value: maybe (use on/off/true/false/yes/no/1/0/y/n/t/f or toggle)"
        )
        assert cli.repl.ctx.ns("flags")["hex"] is False, "a refused token never flips the setting"


class TestInfo:
    def test_info_reports_hex_mode_and_the_connection(self, cli, capsys):
        """/proto.info prints the hex flag and the connection, returns both as booleans in data and as one key=value string."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/proto.info")
        out = capsys.readouterr().out
        assert result.value == "hex=off, connected=yes"
        assert result.data == {"hex": False, "connected": True}, (
            "the record a script or agent reads"
        )
        assert "Hex mode : off" in out and "Connected: yes" in out, (
            "the aligned rows a person reads"
        )

    def test_info_tracks_the_flag_and_a_disconnect(self, cli):
        """The value follows /proto.hex and the port: on after hex on, no after a disconnect."""
        cli.repl.dispatch_full("/proto.hex on")
        cli._disconnect()
        assert cli.repl.dispatch_full("/proto.info").value == "hex=on, connected=no"


# ── /proto.send on the open port ────────────────────────────────────────────


class TestSend:
    def test_bare_send_is_a_usage_error(self, cli):
        """/proto.send without data renders the registered synopsis."""
        result = cli.repl.dispatch_full("/proto.send")
        assert result.success is False
        assert result.error.startswith("Usage: /proto.send "), result.error

    def test_a_text_frame_returns_the_reply_as_hex(self, cli, capsys):
        """A quoted text frame is sent as-is and the reply comes back as the hex value with an RX hex+ASCII line."""
        capsys.readouterr()
        result = cli.repl.dispatch_full('/proto.send "AT\\r"')
        assert result.value == "4F4B0D0A", "OK CR LF, the DEMO's answer to AT"
        assert "RX: 4F 4B 0D 0A  |OK..|" in capsys.readouterr().out

    def test_verbose_shows_the_tx_bytes_and_the_timing(self, cli, capsys):
        """At verbose level the TX line and the byte-count/elapsed footer appear around the RX line."""
        capsys.readouterr()
        cli.repl.dispatch_full('/proto.send --verbose "AT\\r"')
        out = capsys.readouterr().out
        assert "TX: 41 54 0D  |AT.|" in out, "the frame as sent"
        assert "(4 bytes, " in out, "the footer"

    def test_ascii_renders_the_reply_as_quoted_text(self, cli, capsys):
        """--ascii swaps the hex+sidebar row for one quoted escape-string."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/proto.send --ascii 41 54 0D")
        assert result.value == "4F4B0D0A"
        assert 'RX: "OK\\r\\n"' in capsys.readouterr().out

    def test_a_long_reply_uses_the_multi_line_hex_dump(self, cli, capsys):
        """A reply over 16 bytes is shown as a byte-count header and offset-addressed dump rows."""
        capsys.readouterr()
        result = cli.repl.dispatch_full('/proto.send "AT+TEXTDUMP 3\\r"')
        out = capsys.readouterr().out
        assert len(bytes.fromhex(result.value)) > 16, "three readings exceed one dump row"
        assert "RX " in out and " bytes:" in out, "the header"
        assert "0010 |" in out, "a second dump row"

    def test_a_crc_suffixed_frame_is_accepted_by_the_device(self, cli):
        """A frame sent with a CRC algorithm name gets a valid trailer: the Modbus device answers the request instead of raising an exception frame."""
        # Act
        result = cli.repl.dispatch_full("/proto.send crc16-modbus 01 03 00 00 00 0A")
        frame = bytes.fromhex(result.value)

        # Assert
        assert frame[:3] == b"\x01\x03\x14", "slave 1, function 3, 20 data bytes: a real answer"
        algo = get_crc_registry()["crc16-modbus"]
        actual_crc = int.from_bytes(frame[-2:], "little")
        assert actual_crc == algo.compute(frame[:-2]), "the reply's own trailer verifies"

    def test_ascii_escapes_the_non_printable_bytes_of_a_reply(self, cli, capsys):
        """--ascii renders control bytes with their escapes: NUL as \\0 and the rest as \\xNN."""
        capsys.readouterr()
        cli.repl.dispatch_full("/proto.send --ascii crc16-modbus 01 03 00 00 00 0A")
        assert 'RX: "\\x01\\x03\\x14\\0' in capsys.readouterr().out

    def test_inline_delays_pace_the_segments(self, cli, capsys):
        """A ~delay between segments is honored and shown as a marker in the verbose TX line, with the device still answering."""
        capsys.readouterr()
        result = cli.repl.dispatch_full('/proto.send --verbose 01 02 ~2ms "AT\\r"')
        out = capsys.readouterr().out
        assert "[~2ms]" in out, "the delay marker"
        assert "01 02" in out and '"AT\\r"' in out, "hex and text segments each rendered"
        assert result.value != "", "the device answered the frame"

    def test_a_sub_millisecond_delay_spin_waits(self, cli):
        """A delay under 1 ms takes the spin-wait branch and the frame still goes out whole."""
        result = cli.repl.dispatch_full('/proto.send ~500us "AT\\r"')
        assert result.value == "4F4B0D0A"

    def test_dry_run_with_delays_lists_the_markers(self, cli, capsys):
        """--dry-run with delays prints each segment and marker in order and returns the data bytes only."""
        capsys.readouterr()
        result = cli.repl.dispatch_full("/proto.send --dry-run 00 ~2ms 01")
        assert "TX (dry-run): 00 [~2ms] 01" in capsys.readouterr().out
        assert result.value == "0001", "delays are not data"

    def test_a_crc_with_no_data_segment_is_sent_on_its_own(self, cli):
        """An algorithm followed only by a delay appends the CRC of nothing as its own segment."""
        algo = get_crc_registry()["crc16-modbus"]
        expected = algo.compute(b"").to_bytes(2, "little").hex().upper()
        result = cli.repl.dispatch_full("/proto.send --dry-run crc16-modbus ~1ms")
        assert result.value == expected

    def test_a_bad_token_is_a_parse_error(self, cli):
        """A token that is neither hex, quoted text nor a delay fails as a Parse error."""
        result = cli.repl.dispatch_full("/proto.send zz")
        assert result.success is False
        assert result.error.startswith("Parse error: "), result.error

    @pytest.mark.slow
    def test_no_reply_is_reported_with_an_empty_value(self, cli, capsys):
        """A frame the device never answers (AT without its line ending) reports (no response) after the 1 s wait, and scripts see an empty value."""
        capsys.readouterr()
        result = cli.repl.dispatch_full('/proto.send --verbose "AT"')
        assert result.success is True, "no reply is a result, not an error"
        assert result.value == ""
        assert "RX: (no response)" in capsys.readouterr().out


# ── /proto.run ──────────────────────────────────────────────────────────────


class TestRun:
    def test_bare_run_is_a_usage_error(self, cli):
        """/proto.run without a file renders the registered synopsis."""
        result = cli.repl.dispatch_full("/proto.run")
        assert result.error.startswith("Usage: /proto.run "), result.error

    def test_a_missing_file_names_the_folder_once(self, cli, capsys):
        """A file found neither as given nor in proto/ fails with one line naming both, printed once."""
        proto_dir = cli.repl.ctx.fs.proto_dir
        capsys.readouterr()
        result = cli.repl.dispatch_full("/proto.run nope")
        out = capsys.readouterr().out
        assert result.error == f"File not found: nope (also checked {proto_dir})"
        assert out.count("File not found") == 1, (
            "the message is the Error line, not a red line and then the Error line"
        )

    def test_a_stem_resolves_to_a_pro_file_in_the_proto_folder(self, cli):
        """A bare stem gets .pro appended and is looked up in the config's proto/ folder; the value is that path."""
        script = _pro(_proto_folder(cli), "flat.pro", 'send: "AT\\r"\nexpect: "OK\\r\\n"\n')
        result = cli.repl.dispatch_full("/proto.run flat")
        assert result.success is True, result.error
        assert Path(result.value) == script.resolve()

    def test_a_dotted_stem_resolves_to_its_pro_file(self, cli):
        """file-name-default-ext: /proto.run smoke.v2 finds proto/smoke.v2.pro; a dot in the stem no longer blocks .pro."""
        script = _pro(_proto_folder(cli), "smoke.v2.pro", 'send: "AT\\r"\nexpect: "OK\\r\\n"\n')
        result = cli.repl.dispatch_full("/proto.run smoke.v2")
        assert result.success is True, result.error
        assert Path(result.value) == script.resolve()

    def test_a_flat_script_runs_every_directive(self, cli, tmp_path, capsys):
        """A flat script runs its sends, expects, quiet/loud device commands, flush and delay, and tallies PASS and FAIL."""
        # Arrange
        script = _pro(tmp_path, "every.pro", _FLAT_EVERY_DIRECTIVE)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full(f"/proto.run {script}")
        out = capsys.readouterr().out

        # Assert
        assert (result.success, result.error) == (False, "2 of 4 tests failed"), (
            "a failed expect fails the run"
        )
        assert Path(result.value) == script.resolve(), "the value is still the script that ran"
        assert "Flat demo" in out and "every.pro - 13 steps" in out, (
            "the banner: @name, then the step count"
        )
        assert "[PROTO] hello" in out and "PASS (4 bytes, " in out, (
            "the text expect matched OK CR LF"
        )
        assert "[PROTO] wrong answer" in out, "a labeled failing expect"
        assert "[PROTO] wildcard" in out, "the hex expect with ** wildcards"
        assert "(timeout after 50ms)" in out, "the unlabeled expect timed out"
        assert out.count("[PROTO]") == 3, "an unlabeled expect gets no step line"
        assert out.count("CMD: AT") == 1 and "CMD: flushed 4 bytes" in out, (
            "the quiet cmd is silent, the loud one is shown"
        )
        assert "Results: 2/4 PASS" in out

    def test_a_toml_script_runs_setup_teardown_and_reports_failures(self, cli, tmp_path, capsys):
        """A TOML script runs the script and per-test setup commands and the teardown, and reports a mismatch and a timeout as FAIL."""
        # Arrange
        script = _pro(tmp_path, "mixed.pro", _TOML_MIXED)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full(f"/proto.run {script}")
        out = capsys.readouterr().out

        # Assert
        assert (result.success, result.error) == (False, "2 of 3 tests failed"), (
            "a failed test fails the run"
        )
        assert out.count("CMD: AT") == 3, "script setup, the second test's setup, script teardown"
        assert "[PROTO] silent" in out and "(timeout after 50ms)" in out, (
            "an unanswered send is a timeout FAIL"
        )
        assert "Results: 1/3 PASS" in out

    def test_a_failing_suite_answers_json_with_its_results(self, cli, tmp_path, capsys):
        """failure-is-failure: a failing suite answers --json with success false and the results as data.

        Until 2026-10-16 /proto.run returned success whatever failed, so a
        regression .run or an agent saw a failing suite pass; ``termapy
        --proto`` already exited 1.
        """
        # Arrange
        script = _pro(tmp_path, "mixed.pro", _TOML_MIXED)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full(f"/proto.run {script} --json")
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

        # Assert
        assert (envelope["success"], envelope["error"]) == (False, "2 of 3 tests failed")
        assert result.data["summary"]["failed"] == 2, "the results document rides on the failure"
        assert envelope["data"]["summary"] == result.data["summary"], "and reaches the envelope"

    def test_a_flat_script_without_steps_is_refused(self, cli, tmp_path):
        """A flat script with directives but no steps fails as having no steps."""
        script = _pro(tmp_path, "nothing.pro", "@name nothing\n")
        result = cli.repl.dispatch_full(f"/proto.run {script}")
        assert result.error == "Script has no steps."

    def test_a_toml_script_without_tests_is_refused(self, cli, tmp_path):
        """A TOML script whose test array is empty fails as having no tests."""
        script = _pro(tmp_path, "none.pro", 'name = "none"\ntest = []\n')
        result = cli.repl.dispatch_full(f"/proto.run {script}")
        assert result.error == "Script has no tests."

    def test_a_toml_validation_error_is_reported_as_toml(self, cli, tmp_path):
        """A TOML script missing a required field reports the TOML parser's error, not a flat-format syntax error on line 1."""
        script = _pro(tmp_path, "missing_send.pro", '[[test]]\nname = "no send"\nexpect = "01"\n')
        result = cli.repl.dispatch_full(f"/proto.run {script}")
        assert result.error == "Script error: Test 1: missing 'send' field"


# ── /proto.debug outside the TUI ────────────────────────────────────────────


class TestDebug:
    def test_debug_is_refused_outside_the_tui(self, cli):
        """/proto.debug needs the TUI: the CLI refuses it by capability before the handler runs."""
        result = cli.repl.dispatch_full("/proto.debug anything")
        assert result.error == "/proto.debug requires: tui_mode (not available in this environment)"
