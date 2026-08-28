"""Tests for /term.request toggle and the request_mode dispatcher fallthrough.

Three surfaces:

- ``/term.request {on|off}`` — config toggle (uses ``_cfg_toggle``
  pattern, identical to ``/term.timestamps`` etc.).  Tests verify
  reads, writes, and that the value lands in ``cfg["request_mode"]``.
- ``ReplEngine._exec_request_mode(command)`` — the byte-oriented
  request/response executor invoked by ``dispatch_full`` when
  ``cfg["request_mode"]`` is true.  Tests inject fake serial callbacks
  on the PluginContext and verify the JSON envelope shape.
- Symmetric JSON input: ``{"cmd":"..."}`` is unwrapped; malformed JSON
  or JSON without a ``cmd`` key falls through to plain-text send.
"""

from __future__ import annotations

import json

import pytest

from termapy.plugins import CapabilitySet, InternalHandle, PluginContext
from termapy.repl import ReplEngine


@pytest.fixture
def repl_env(tmp_path, monkeypatch):
    """A ReplEngine wired enough for /term.request + bare-command dispatch.

    Resets launch_vars so FRONT_END from prior MCP-host tests doesn't
    bleed in and trigger the MCP-mode write_markup gate in
    _exec_request_mode.
    """
    from termapy import variables as _var_mod
    monkeypatch.setattr(_var_mod, "_LAUNCH_VARS", dict(_var_mod._LAUNCH_VARS))
    _var_mod._LAUNCH_VARS.pop("FRONT_END", None)

    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "echo": False,
        "eol": "\r",
        "encoding": "utf-8",
        "request_mode": False,
    }
    config_path = tmp_path / "test_cfg.cfg"
    config_path.write_text(json.dumps(cfg, indent=4))

    output: list[tuple[str, str | None]] = []
    markup_output: list[str] = []

    def write(text, color=None):
        output.append((text, color))

    def write_markup(text):
        markup_output.append(text)

    engine = ReplEngine(cfg, str(config_path), write)
    internal_handle = InternalHandle(
        plugins=engine._plugins,
        in_script=lambda: engine.in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        dispatch=engine.dispatch,
    )
    from termapy.plugins import IOHandle
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        internal=internal_handle,
        io=IOHandle(_write=write, _write_markup=write_markup),
        capabilities=CapabilitySet(
            interactive=True, gui_apps=True, serial_connected=True,
        ),
    )
    engine.set_context(ctx)
    # dispatch_full reads ctx.ns("flags")["echo"] as a precondition; init it
    # the same way TerminalHost does on startup so integration tests can
    # exercise the bare-line fallthrough.
    ctx.ns("flags")["echo"] = False
    ctx.ns("flags")["output_level"] = "normal"
    return engine, ctx, cfg, output, markup_output


# ── /term.request toggle ────────────────────────────────────────────────────


class TestTermRequestToggle:
    def test_toggle_default_off_sets_on(self, repl_env):
        # Arrange
        engine, ctx, cfg, _, _ = repl_env
        assert cfg.get("request_mode") is False, "starts off (precondition)"

        # Act -- explicit toggle verb (bare now queries)
        result = engine.dispatch("term.request toggle")

        # Assert
        assert result.success, "/term.request toggle succeeds"
        assert cfg.get("request_mode") is True, "request_mode flipped to on"
        assert result.value == "on", "result.value reports new state"

    def test_toggle_explicit_off_when_on(self, repl_env):
        # Arrange
        engine, ctx, cfg, _, _ = repl_env
        engine._apply_cfg("request_mode", True)

        # Act
        result = engine.dispatch("term.request off")

        # Assert
        assert cfg.get("request_mode") is False, "explicit off applied"
        assert result.value == "off", "value reports off"

    def test_toggle_explicit_on(self, repl_env):
        # Arrange
        engine, ctx, cfg, _, _ = repl_env

        # Act
        result = engine.dispatch("term.request on")

        # Assert
        assert cfg.get("request_mode") is True, "explicit on applied"
        assert result.value == "on", "value reports on"


# ── _exec_request_mode (the executor itself, not via dispatch_full) ─────────


class _FakeSerial:
    """Captures serial_io / drain / write / read_raw calls."""

    def __init__(self, response: bytes = b""):
        self.claimed = False
        self.released = False
        self.drained = 0
        self.writes: list[bytes] = []
        self.read_calls: list[int] = []
        self._response = response

    def claim(self):
        self.claimed = True

    def release(self):
        self.released = True

    def drain(self):
        self.drained += 1
        return 0

    def write(self, payload):
        self.writes.append(payload)

    def read_raw(self, timeout_ms=1000, frame_gap_ms=0):
        self.read_calls.append(timeout_ms)
        return self._response


def _rendered_envelope(output):
    """Parse the response envelope from the plain-output capture.

    The unified emitter writes it via ``io.result`` (plain channel);
    only the request-side ``{"cmd": ...}`` echo remains on markup.
    """
    assert output, "an envelope line was written to the plain channel"
    text, _color = output[-1]
    return json.loads(text)


def _wire_fake_serial(ctx, fake):
    """Replace ctx serial callbacks with the fake's methods."""
    ctx.serial.claim = fake.claim
    ctx.serial.release = fake.release
    ctx.serial.drain = fake.drain
    ctx.serial.write = fake.write
    ctx.serial.read_raw = fake.read_raw


class TestExecRequestMode:
    def test_value_is_response_text_and_display_carries_envelope(self, repl_env):
        # Arrange
        engine, ctx, _, output, _ = repl_env
        fake = _FakeSerial(response=b"5.5\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_voltage")

        # Assert -- value is the scalar (the response text); the JSON
        # envelope renders on the plain result channel
        assert fake.writes == [b"get_voltage\r"], "command + line ending sent"
        assert fake.claimed is True, "serial port claimed (suppresses display)"
        assert fake.released is True, "serial port released after"
        assert result.success is True, "success on clean response"
        assert result.value == "5.5", "value is the decoded + stripped text"
        assert result.data is None, "plain-text response has no structure"
        rendered = _rendered_envelope(output)
        assert rendered["cmd"] == "get_voltage", "display envelope: cmd"
        assert rendered["success"] is True, "display envelope: success"
        assert rendered["value"] == "5.5", "display envelope: response text"
        assert isinstance(rendered["elapsed_s"], float), "display: elapsed_s"

    def test_json_native_response_lands_in_data(self, repl_env):
        # Arrange -- the device answers with a JSON object (json-to-json)
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b'{"volts": 5.5, "ok": true}\r\n')
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_voltage")

        # Assert
        assert result.data == {"volts": 5.5, "ok": True}, (
            "JSON reply parsed into data"
        )
        assert result.value == '{"volts": 5.5, "ok": true}', (
            "raw wire text kept in value for fidelity"
        )

    def test_malformed_json_response_stays_text_only(self, repl_env):
        # Arrange -- looks like JSON, isn't
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"{broken\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("q")

        # Assert
        assert result.data is None, "unparseable reply is not faked into data"
        assert result.value == "{broken", "raw text still delivered in value"

    def test_strip_device_echo_removes_echoed_command(self, repl_env):
        # Arrange -- a half-duplex device echoes the command, then answers.
        engine, ctx, _, _, markup = repl_env
        engine._cfg_data["strip_device_echo"] = True
        fake = _FakeSerial(response=b"AT+VER\r\n1.2.3\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("AT+VER")

        # Assert -- the echoed command line is dropped; value is the answer.
        assert result.value == "1.2.3", "leading echo stripped"

    def test_strip_device_echo_off_keeps_echo(self, repl_env):
        # Arrange -- default off: the echoed line stays in the response.
        engine, ctx, _, _, markup = repl_env
        fake = _FakeSerial(response=b"AT+VER\r\n1.2.3\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("AT+VER")

        # Assert
        assert "AT+VER" in result.value, "echo kept when strip is off"

    def test_ansi_stripped_from_value_when_color_off(self, repl_env):
        # Arrange -- MCP defaults color off; device wraps its reply in SGR.
        engine, ctx, _, _, _ = repl_env
        ctx.ns("flags")["color"] = False
        fake = _FakeSerial(response=b"\x1b[32m5.5\x1b[0m\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_voltage")

        # Assert -- escape codes never reach the data value.
        assert result.value == "5.5", "ANSI stripped from value when color off"

    def test_ansi_kept_in_value_when_color_on(self, repl_env):
        # Arrange -- color on (TUI/CLI default): passthrough preserved.
        engine, ctx, _, _, _ = repl_env
        ctx.ns("flags")["color"] = True
        fake = _FakeSerial(response=b"\x1b[32m5.5\x1b[0m\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_voltage")

        # Assert -- with color on the raw escape sequence rides through.
        assert "\x1b[32m" in result.value, "ANSI preserved in value when color on"

    def test_err_pattern_matches_through_stripped_ansi(self, repl_env):
        # Arrange -- a colored error line must still match request_err_pattern
        # because the strip runs before the match.
        engine, ctx, cfg, _, _ = repl_env
        ctx.ns("flags")["color"] = False
        cfg["request_err_pattern"] = r"^ERR"
        fake = _FakeSerial(response=b"\x1b[31mERR: bad\x1b[0m\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("do_thing")

        # Assert -- detected as an error, and the message is escape-free.
        assert result.success is False, "colored error line still triggers err-pattern"
        assert result.error == "ERR: bad", "error text is ANSI-stripped"

    def test_envelope_rendered_to_terminal_as_single_line(self, repl_env):
        # Arrange
        engine, ctx, _, output, markup = repl_env
        fake = _FakeSerial(response=b"1.2.3")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("AT+VER")

        # Assert -- the request echo rides markup; the response envelope
        # renders as ONE line on the plain result channel, in the same
        # dialect as every termapy command.
        assert len(markup) == 1, "request echo emitted on markup"
        request_envelope = json.loads(markup[0])
        assert request_envelope == {"cmd": "AT+VER"}, "request envelope shape"
        response_envelope = _rendered_envelope(output)
        assert response_envelope["cmd"] == "AT+VER", "cmd in response envelope"
        assert response_envelope["value"] == "1.2.3", "value in response envelope"
        assert response_envelope["success"] is True, "success in response envelope"
        assert response_envelope["data"] is None, "plain text: no structure"
        assert response_envelope["output_lines"] == [], "device path: no prose"

    def test_envelope_on_send_error(self, repl_env):
        # Arrange -- writer raises OSError
        engine, ctx, _, output, _ = repl_env

        class _BrokenSerial(_FakeSerial):
            def write(self, payload):
                raise OSError("port disconnected")

        fake = _BrokenSerial()
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("anything")

        # Assert -- error on the CmdResult; the display envelope mirrors it
        assert result.success is False, "send failure -> CmdResult.fail"
        assert "Send error" in result.error, "error describes send failure"
        assert "port disconnected" in result.error, "wraps OSError message"
        assert result.value == "", "no response text on send error"
        rendered = _rendered_envelope(output)
        assert rendered["success"] is False, "display envelope: success=False"
        assert rendered["value"] == "", "display envelope: empty result"

    def test_empty_response_renders_empty_result(self, repl_env):
        # Arrange -- device timed out / no reply
        engine, ctx, _, output, _ = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("noisy_silence")

        # Assert -- success with an empty scalar; display line still emitted
        assert result.success is True, "send succeeded; no exception"
        assert result.value == "", "empty bytes -> empty value"
        assert result.error == "", "no error on empty response"
        rendered = _rendered_envelope(output)
        assert rendered["value"] == "", "display envelope: empty result"

    def test_decode_uses_configured_encoding(self, repl_env):
        # Arrange -- latin-1 encoded response
        engine, ctx, cfg, _, _ = repl_env
        cfg["encoding"] = "latin-1"
        fake = _FakeSerial(response=b"caf\xe9")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("name?")

        # Assert
        assert result.value == "café", "latin-1 decoded correctly"

    def test_strip_trims_whitespace_in_result(self, repl_env):
        # Arrange -- response has leading/trailing whitespace + line endings
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"  5.5\r\n  ")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_voltage")

        # Assert -- whitespace trimmed
        assert result.value == "5.5", "value stripped"

    def test_read_window_uses_cfg_default_response_timeout(self, repl_env):
        # Arrange -- cfg carries a non-default response window.
        # Regression: the executor used to read the nonexistent key
        # "response_timeout_ms", so the cfg value never reached read_raw
        # and the window was silently hardwired to 1000 ms.
        engine, ctx, cfg, _, _ = repl_env
        cfg["default_response_timeout_ms"] = 2500
        fake = _FakeSerial(response=b"OK")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("slow_help")

        # Assert
        assert fake.read_calls == [2500], \
            "cfg default_response_timeout_ms reaches the read window"

    def test_read_window_falls_back_to_1000_without_cfg_key(self, repl_env):
        # Arrange -- fixture cfg deliberately lacks the key
        engine, ctx, cfg, _, _ = repl_env
        assert "default_response_timeout_ms" not in cfg, "precondition"
        fake = _FakeSerial(response=b"OK")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("anything")

        # Assert
        assert fake.read_calls == [1000], "fallback window is 1000 ms"


class TestDeviceErrorDetection:
    """request_err_pattern detects device-side error responses."""

    def test_default_pattern_matches_err_prefix(self, repl_env):
        # Arrange
        engine, ctx, cfg, _, markup = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        fake = _FakeSerial(response=b"ERR: unknown command: foo")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("foo")

        # Assert -- CmdResult.fail with the device text as the error
        assert result.success is False, "device error -> CmdResult.fail"
        assert result.error == "ERR: unknown command: foo", \
            "error is device text"
        assert result.value == "", \
            "value empty (text moved to error)"

    def test_default_pattern_matches_error_prefix(self, repl_env):
        # Arrange
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        fake = _FakeSerial(response=b"ERROR: timeout")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("ping")

        # Assert
        assert result.success is False, "ERROR prefix detected"
        assert "timeout" in result.error, "error message captured"

    def test_default_pattern_matches_fault_prefix(self, repl_env):
        # Arrange
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        fake = _FakeSerial(response=b"FAULT 17: overcurrent")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("status")

        # Assert
        assert result.success is False, "FAULT prefix detected"

    def test_default_pattern_case_insensitive(self, repl_env):
        # Arrange -- lowercase also matches
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        fake = _FakeSerial(response=b"err: lowercase too")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("test")

        # Assert
        assert result.success is False, "case-insensitive match"

    def test_ok_response_not_flagged(self, repl_env):
        # Arrange -- normal response shouldn't trip the pattern
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        fake = _FakeSerial(response=b"OK\r\nresult: 42")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("ping")

        # Assert
        assert result.success is True, "OK response is not an error"
        assert "OK" in result.value, "response captured normally"

    def test_word_boundary_avoids_false_positives(self, repl_env):
        # Arrange -- a response starting with "ERRATIC" shouldn't trip
        # the "ERR" alternative (word boundary required).
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        fake = _FakeSerial(response=b"ERRATIC behavior reported")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("diag")

        # Assert -- not flagged as error (ERRATIC != ERR + word boundary)
        assert result.success is True, "word boundary prevents false positive"

    def test_empty_pattern_disables_detection(self, repl_env):
        # Arrange -- even an ERR-prefixed response is treated as normal
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = ""
        fake = _FakeSerial(response=b"ERR: foo")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("bar")

        # Assert -- detection disabled; treated as normal text response
        assert result.success is True, "empty pattern disables detection"
        assert result.value == "ERR: foo", "treated as text"
        assert result.error == "", "no error claimed"

    def test_custom_pattern_override(self, repl_env):
        # Arrange -- user provided a different error convention
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"^FAILED"
        fake = _FakeSerial(response=b"FAILED: bad")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("test")

        # Assert
        assert result.success is False, "custom pattern matches FAILED"
        assert result.error == "FAILED: bad", "error captured"
        assert result.value == "", "value empty when errored"

    def test_malformed_pattern_does_not_crash(self, repl_env):
        # Arrange -- broken regex (unclosed bracket) shouldn't crash
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"[unclosed"
        fake = _FakeSerial(response=b"OK")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("ping")

        # Assert -- treated as success (no detection) instead of crashing
        assert result.success is True, "malformed regex falls back to success"
        assert result.value == "OK", "response delivered despite bad pattern"

    def test_device_error_display_envelope_shows_error(self, repl_env):
        # Arrange -- check the scrollback display reflects the device error
        engine, ctx, cfg, output, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        fake = _FakeSerial(response=b"ERR: unknown command")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("foo")

        # Assert -- TUI/CLI scrollback envelope shows success=false,
        # error=device text, empty result (no duplication)
        response_envelope = _rendered_envelope(output)
        assert response_envelope["success"] is False, "display success=false"
        assert response_envelope["error"] == "ERR: unknown command", \
            "display error is device text"
        assert response_envelope["value"] == "", \
            "display result empty when device errored"

    def test_session_override_takes_precedence_over_cfg(self, repl_env):
        # Arrange -- cfg has the standard default; session override is
        # set to "" (disabled).  An ERR response should NOT be flagged.
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"
        ctx.ns("flags")["request_err_pattern_override"] = ""
        fake = _FakeSerial(response=b"ERR: foo")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("bar")

        # Assert -- session override (empty -> disable) beats cfg default
        assert result.success is True, "session override disables detection"
        assert result.value == "ERR: foo", "treated as normal text"
        assert result.error == "", "no error claimed"

    def test_session_override_can_set_custom_pattern(self, repl_env):
        # Arrange -- cfg says no detection (empty); session sets ^FAIL
        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = ""
        ctx.ns("flags")["request_err_pattern_override"] = r"^FAIL"
        fake = _FakeSerial(response=b"FAIL: nope")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("test")

        # Assert -- session override wins; FAIL prefix detected
        assert result.success is False, "session override matches FAIL"
        assert result.error == "FAIL: nope", "device text is the error"
        assert result.value == "", "value empty when device errored"


class TestTermRequestCommand:
    """The ``/term.request`` REPL command -- on/off + err=<regex> arg."""

    def test_off_then_on_resets_err_override(self, repl_env):
        # Arrange -- the user-facing scenario from the design discussion:
        # disable err detection temporarily, then re-enable with
        # /term.request on.  Expected: the off->on cycle clears the
        # session override so detection returns to cfg default.
        # The user does NOT need to remember the default regex.
        from termapy.builtins.commands.term import _handler_request

        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"

        # User disables detection mid-session
        _handler_request(ctx, "err=")
        assert ctx.ns("flags").get("request_err_pattern_override") == "", \
            "override set to empty (disabled)"

        # User toggles off then on
        _handler_request(ctx, "off")
        _handler_request(ctx, "on")

        # Assert -- override cleared; cfg default is back in effect
        assert "request_err_pattern_override" not in ctx.ns("flags"), \
            "/term.request on cleared the session override"

    def test_on_with_err_arg_sets_session_override(self, repl_env):
        # Arrange
        from termapy.builtins.commands.term import _handler_request

        engine, ctx, cfg, _, _ = repl_env
        cfg["request_err_pattern"] = r"(?i)^(ERROR|ERR|FAULT)\b"

        # Act
        _handler_request(ctx, "on err=^CRITICAL")

        # Assert -- mode on, override set to the user's pattern
        assert cfg["request_mode"] is True, "request_mode enabled"
        actual = ctx.ns("flags")["request_err_pattern_override"]
        assert actual == "^CRITICAL", "session override set to user pattern"

    def test_err_arg_alone_does_not_toggle_mode(self, repl_env):
        # Arrange -- request_mode starts off
        from termapy.builtins.commands.term import _handler_request

        engine, ctx, cfg, _, _ = repl_env
        cfg["request_mode"] = False

        # Act
        _handler_request(ctx, "err=^FOO")

        # Assert -- override set but mode unchanged
        assert cfg["request_mode"] is False, \
            "err= alone does not flip request_mode"
        assert ctx.ns("flags")["request_err_pattern_override"] == "^FOO"

    def test_unknown_token_fails(self, repl_env):
        # Arrange
        from termapy.builtins.commands.term import _handler_request

        engine, ctx, cfg, _, _ = repl_env

        # Act
        result = _handler_request(ctx, "asdf")

        # Assert
        assert result.success is False, "unknown token rejected"
        assert "Unknown token" in result.error


# ── dispatch_full integration: bare line + request_mode -> envelope ────────


class TestDispatchFullRequestMode:
    def test_request_mode_off_falls_through_to_term_send(self, repl_env):
        # Arrange -- request_mode is False; bare line goes through /term.send
        engine, ctx, cfg, output, markup = repl_env
        assert cfg.get("request_mode") is False, "precondition"

        captured: list[bytes] = []
        ctx.serial.write = captured.append
        ctx.serial.is_connected = lambda: True  # /term.send self-checks this

        # Act
        engine.dispatch_full(
            "AT+VER",
            serial_write=captured.append,
            is_connected=lambda: True,
        )

        # Assert -- /term.send wrote the bytes; no envelope on terminal
        assert captured == [b"AT+VER\r"], "fire-and-forget write happened"
        assert markup == [], "no JSON envelope rendered"

    def test_request_mode_on_emits_envelope(self, repl_env):
        # Arrange
        engine, ctx, cfg, output, markup = repl_env
        cfg["request_mode"] = True
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine.dispatch_full(
            "get_voltage",
            serial_write=fake.write,
            is_connected=lambda: True,
        )

        # Assert -- request echo on markup; the response envelope renders
        # in the ONE dialect on the plain result channel.
        assert len(markup) == 1, "request echo rendered"
        request_envelope = json.loads(markup[0])
        assert request_envelope == {"cmd": "get_voltage"}, "request envelope"
        response_envelope = _rendered_envelope(output)
        assert response_envelope["cmd"] == "get_voltage", "cmd in response"
        assert response_envelope["value"] == "5.5", "value in response"
        assert result.value == "5.5", "CmdResult.value is the response text"


# ── Symmetric JSON input: {"cmd": "..."} unwrapped ──────────────────────────


class TestJsonInputUnwrapping:
    def test_json_object_with_cmd_field_unwrapped(self, repl_env):
        # Arrange -- input is JSON object {"cmd":"AT+VER"}
        engine, ctx, _, _, markup = repl_env
        fake = _FakeSerial(response=b"1.2.3")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode('{"cmd":"AT+VER"}')

        # Assert -- send happened with unwrapped command, not the JSON literal
        assert fake.writes == [b"AT+VER\r"], "unwrapped cmd sent to device"
        # Display envelope cmd reflects the unwrapped value, not the wrapper
        rendered = json.loads(markup[-1])
        assert rendered["cmd"] == "AT+VER", "display cmd is unwrapped"
        assert result.value == "1.2.3", "device response captured in value"

    def test_json_object_with_extra_fields_unwrapped_extras_ignored(self, repl_env):
        # Arrange -- v1 ignores extra top-level fields; only `cmd` is read
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"OK")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode('{"cmd":"reset","timeout_ms":5000,"extra":"ignored"}')

        # Assert
        assert fake.writes == [b"reset\r"], "only cmd extracted; extras ignored"

    def test_json_array_falls_through_to_plain_text(self, repl_env):
        # Arrange -- JSON array is valid JSON but not a dict; treat as plain
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("[1,2,3]")

        # Assert -- sent literally because no `cmd` to extract
        assert fake.writes == [b"[1,2,3]\r"], "array sent literally"

    def test_json_dict_without_cmd_falls_through_to_plain_text(self, repl_env):
        # Arrange -- valid JSON dict but no `cmd` field -> send literal
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode('{"a":"b"}')

        # Assert -- sent literally; the device might natively accept JSON
        assert fake.writes == [b'{"a":"b"}\r'], "dict-without-cmd sent literally"

    def test_malformed_json_falls_through_to_plain_text(self, repl_env):
        # Arrange -- looks like JSON (starts {, ends }) but isn't valid
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act -- malformed JSON; graceful fallback
        engine._exec_request_mode("{not valid json}")

        # Assert -- sent literally, no crash
        assert fake.writes == [b"{not valid json}\r"], "malformed sent literal"

    def test_plain_text_unaffected(self, repl_env):
        # Arrange -- plain text without any JSON shape
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"value=42")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_volt")

        # Assert -- sent verbatim; value is the device's response text
        assert fake.writes == [b"get_volt\r"], "plain text sent verbatim"
        assert result.value == "value=42", "response text in value"

    def test_json_with_null_cmd_returns_error_envelope(self, repl_env):
        # Arrange -- explicit error path: cmd field present but null
        engine, ctx, _, _, markup = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode('{"cmd":null}')

        # Assert -- explicit failure; nothing sent to device
        assert result.success is False, "null cmd produces failure"
        assert fake.writes == [], "no bytes sent on bad cmd"
        assert "non-empty string" in result.error, (
            "error names the requirement"
        )

    def test_json_with_empty_cmd_returns_error_envelope(self, repl_env):
        # Arrange
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode('{"cmd":""}')

        # Assert
        assert result.success is False, "empty cmd produces failure"
        assert fake.writes == [], "no bytes sent on empty cmd"

    def test_json_with_numeric_cmd_returns_error_envelope(self, repl_env):
        # Arrange
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode('{"cmd":42}')

        # Assert
        assert result.success is False, "numeric cmd produces failure"
        assert fake.writes == [], "no bytes sent on non-string cmd"

    def test_json_input_with_whitespace_padding_still_unwraps(self, repl_env):
        # Arrange -- leading/trailing whitespace shouldn't defeat detection
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode('   {"cmd":"AT"}   ')

        # Assert
        assert fake.writes == [b"AT\r"], "whitespace stripped before unwrap"


# ── Symmetric request envelope: always rendered in request_mode ─────────────


class TestRequestEnvelopeEcho:
    """In request_mode the JSON conversation IS the visible state.
    Both request and response envelopes always render, regardless of
    echo_input.  echo_input is a plain-text ergonomic knob; it has no
    effect when request_mode is active.
    """

    def test_request_and_response_envelopes_always_render(self, repl_env):
        # Arrange -- request_mode is the only knob that matters
        engine, ctx, cfg, output, markup = repl_env
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("get_voltage")

        # Assert -- request echo on markup; response envelope on the
        # plain result channel, in the one dialect
        assert len(markup) == 1, "exactly one request-echo line"
        request_envelope = json.loads(markup[0])
        assert request_envelope == {"cmd": "get_voltage"}, (
            "request envelope is exactly {cmd: <text>}"
        )
        response_envelope = _rendered_envelope(output)
        assert response_envelope["cmd"] == "get_voltage", "response cmd"
        assert response_envelope["value"] == "5.5", "response value"
        assert response_envelope["success"] is True, "response success"

    def test_echo_input_off_does_not_suppress_request_envelope(self, repl_env):
        # Arrange -- echo_input is irrelevant in request_mode; the
        # request envelope is the protocol view, not a debug echo.
        engine, ctx, cfg, _, markup = repl_env
        cfg["echo"] = False
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("get_voltage")

        # Assert -- request envelope still renders despite echo_input=False
        assert len(markup) == 1, (
            "echo_input=false has no effect when request_mode is on"
        )
        request_envelope = json.loads(markup[0])
        assert request_envelope == {"cmd": "get_voltage"}, (
            "request envelope renders independently of echo_input"
        )

    def test_echo_input_on_does_not_double_render(self, repl_env):
        # Arrange -- echo_input=True must not produce a second request
        # envelope (regression net for the unconditional render).
        engine, ctx, cfg, _, markup = repl_env
        cfg["echo"] = True
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("get_voltage")

        # Assert -- still exactly one request echo, not two
        assert len(markup) == 1, "echo_input=True does not double the request"

    def test_request_envelope_reflects_unwrapped_cmd_not_raw_input(self, repl_env):
        # Arrange -- JSON input gets unwrapped; the echo should show
        # the canonical form, not the raw input string.
        engine, ctx, cfg, _, markup = repl_env
        fake = _FakeSerial(response=b"OK")
        _wire_fake_serial(ctx, fake)

        # Act -- input has extra fields; should be dropped from echo
        engine._exec_request_mode('{"cmd":"reset","timeout_ms":5000,"x":"y"}')

        # Assert -- request envelope shows just {"cmd":"reset"}, no extras
        assert len(markup) == 1, "one request-echo line on markup"
        request_envelope = json.loads(markup[0])
        actual_keys = set(request_envelope.keys())
        expected_keys = {"cmd"}
        assert actual_keys == expected_keys, (
            f"request envelope strips extras; got keys={actual_keys}"
        )
        assert request_envelope["cmd"] == "reset", "shows unwrapped command"


class TestDispatchFullEchoGating:
    """The legacy echo path in dispatch_full goes through the
    ``echo_markup`` callback (not ctx.write).  Tests pass a recording
    callback to verify whether dispatch_full chose to emit a legacy
    echo or skipped it because request_mode=on.
    """

    def test_request_mode_off_uses_legacy_echo(self, repl_env):
        # Arrange -- echo flag on (/echo), request_mode off: legacy echo
        # fires.  The bare-command echo now reads the live flag, not
        # cfg['echo_input'] (which seeds the flag at init).
        engine, ctx, cfg, output, markup = repl_env
        ctx.ns("flags")["echo"] = True
        cfg["request_mode"] = False
        ctx.serial.is_connected = lambda: True
        captured: list[bytes] = []
        legacy_echos: list[str] = []

        # Act
        engine.dispatch_full(
            "AT+VER",
            serial_write=captured.append,
            is_connected=lambda: True,
            echo_markup=legacy_echos.append,
        )

        # Assert -- legacy echo callback fired with formatted echo line;
        # no JSON envelope on the markup channel.
        assert captured == [b"AT+VER\r"], "send happened"
        assert any("AT+VER" in line for line in legacy_echos), (
            "legacy plain-text echo emitted via echo_markup"
        )
        assert markup == [], "no JSON envelope when request_mode=off"

    def test_request_mode_on_suppresses_legacy_echo(self, repl_env):
        # Arrange -- echo_input on, request_mode on: legacy echo suppressed,
        # JSON request envelope rendered instead.
        engine, ctx, cfg, output, markup = repl_env
        cfg["echo"] = True
        cfg["request_mode"] = True
        fake = _FakeSerial(response=b"OK")
        _wire_fake_serial(ctx, fake)
        legacy_echos: list[str] = []

        # Act
        engine.dispatch_full(
            "AT+VER",
            serial_write=fake.write,
            is_connected=lambda: True,
            echo_markup=legacy_echos.append,
        )

        # Assert -- the legacy echo callback was never called (gated out);
        # the markup channel got the request + response JSON envelopes.
        assert legacy_echos == [], (
            "legacy plain-text echo suppressed in request_mode"
        )
        assert len(markup) == 1, "request echo rendered on markup"
        request_envelope = json.loads(markup[0])
        assert request_envelope == {"cmd": "AT+VER"}, (
            "the markup line is the request envelope"
        )
        response_envelope = _rendered_envelope(output)
        assert response_envelope["cmd"] == "AT+VER", (
            "response envelope on the plain result channel"
        )
