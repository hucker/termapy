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

from termapy.plugins import CapabilitySet, EngineAPI, PluginContext
from termapy.repl import ReplEngine


@pytest.fixture
def repl_env(tmp_path):
    """A ReplEngine wired enough for /term.request + bare-command dispatch."""
    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "echo_input": False,
        "line_ending": "\r",
        "encoding": "utf-8",
        "os_cmd_enabled": False,
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
    engine_api = EngineAPI(
        prefix="/",
        plugins=engine._plugins,
        in_script=lambda: engine.in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        coerce_type=ReplEngine._coerce_type,
        dispatch=engine.dispatch,
    )
    from termapy.plugins import IOHandle
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        engine=engine_api,
        io=IOHandle(write=write, write_markup=write_markup),
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

        # Act
        result = engine.dispatch("term.request")

        # Assert
        assert result.success, "/term.request (no arg) toggles successfully"
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


def _wire_fake_serial(ctx, fake):
    """Replace ctx serial callbacks with the fake's methods."""
    ctx.serial.claim = fake.claim
    ctx.serial.release = fake.release
    ctx.serial.drain = fake.drain
    ctx.serial.write = fake.write
    ctx.serial.read_raw = fake.read_raw


class TestExecRequestMode:
    def test_envelope_shape_on_success(self, repl_env):
        # Arrange
        engine, ctx, _, _, markup = repl_env
        fake = _FakeSerial(response=b"5.5\r\n")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_voltage")

        # Assert -- write happened with line ending; envelope correct
        assert fake.writes == [b"get_voltage\r"], "command + line ending sent"
        assert fake.claimed is True, "serial port claimed (suppresses display)"
        assert fake.released is True, "serial port released after"
        envelope = result.value
        assert envelope["cmd"] == "get_voltage", "cmd field"
        assert envelope["success"] is True, "success on clean response"
        assert envelope["error"] == "", "no error"
        assert envelope["result"] == "5.5", "decoded + stripped response"
        assert isinstance(envelope["elapsed_s"], float), "elapsed_s float"

    def test_envelope_rendered_to_terminal_as_single_line(self, repl_env):
        # Arrange
        engine, ctx, _, _, markup = repl_env
        fake = _FakeSerial(response=b"1.2.3")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("AT+VER")

        # Assert -- two markup lines: request envelope, then response.
        # Both parse as JSON.  Request envelope is exactly {cmd}; the
        # response envelope adds success/error/elapsed_s/result.
        assert len(markup) == 2, "request + response envelopes emitted"
        request_envelope = json.loads(markup[0])
        response_envelope = json.loads(markup[1])
        assert request_envelope == {"cmd": "AT+VER"}, "request envelope shape"
        assert response_envelope["cmd"] == "AT+VER", "cmd in response envelope"
        assert response_envelope["result"] == "1.2.3", "result in response envelope"
        assert response_envelope["success"] is True, "success in response envelope"

    def test_envelope_on_send_error(self, repl_env):
        # Arrange -- writer raises OSError
        engine, ctx, _, _, markup = repl_env

        class _BrokenSerial(_FakeSerial):
            def write(self, payload):
                raise OSError("port disconnected")

        fake = _BrokenSerial()
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("anything")

        # Assert -- envelope present with error populated; result.success False
        assert result.success is False, "send failure -> CmdResult.fail"
        envelope = result.value
        assert "Send error" in envelope["error"], "error describes send failure"
        assert "port disconnected" in envelope["error"], "wraps OSError message"
        assert envelope["success"] is False, "success=False in envelope"
        assert envelope["result"] == "", "no response text on send error"

    def test_empty_response_renders_empty_result(self, repl_env):
        # Arrange -- device timed out / no reply
        engine, ctx, _, _, markup = repl_env
        fake = _FakeSerial(response=b"")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("noisy_silence")

        # Assert -- envelope still emitted; success=True (send happened); result=""
        envelope = result.value
        assert envelope["success"] is True, "send succeeded; no exception"
        assert envelope["result"] == "", "empty bytes -> empty result"
        assert envelope["error"] == "", "no error on empty response"

    def test_decode_uses_configured_encoding(self, repl_env):
        # Arrange -- latin-1 encoded response
        engine, ctx, cfg, _, _ = repl_env
        cfg["encoding"] = "latin-1"
        fake = _FakeSerial(response=b"caf\xe9")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("name?")

        # Assert
        assert result.value["result"] == "café", "latin-1 decoded correctly"

    def test_strip_trims_whitespace_in_result(self, repl_env):
        # Arrange -- response has leading/trailing whitespace + line endings
        engine, ctx, _, _, _ = repl_env
        fake = _FakeSerial(response=b"  5.5\r\n  ")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine._exec_request_mode("get_voltage")

        # Assert -- whitespace trimmed
        assert result.value["result"] == "5.5", "result stripped"


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
        engine, ctx, cfg, _, markup = repl_env
        cfg["request_mode"] = True
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        result = engine.dispatch_full(
            "get_voltage",
            serial_write=fake.write,
            is_connected=lambda: True,
        )

        # Assert -- request + response envelopes rendered; response in value
        assert len(markup) == 2, "request + response envelopes rendered"
        request_envelope = json.loads(markup[0])
        response_envelope = json.loads(markup[1])
        assert request_envelope == {"cmd": "get_voltage"}, "request envelope"
        assert response_envelope["cmd"] == "get_voltage", "cmd in response"
        assert response_envelope["result"] == "5.5", "result in response"
        assert result.value["cmd"] == "get_voltage", "envelope in CmdResult.value"


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
        # Envelope cmd reflects the unwrapped value, not the JSON wrapper
        assert result.value["cmd"] == "AT+VER", "envelope cmd is unwrapped"
        assert result.value["result"] == "1.2.3", "device response captured"

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

        # Assert -- sent verbatim, envelope cmd matches input
        assert fake.writes == [b"get_volt\r"], "plain text sent verbatim"
        assert result.value["cmd"] == "get_volt", "envelope cmd matches input"

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
        assert "non-empty string" in result.value["error"], (
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
        engine, ctx, cfg, _, markup = repl_env
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("get_voltage")

        # Assert -- exactly two markup lines, both JSON, in order
        assert len(markup) == 2, "exactly two envelope lines (request, response)"
        request_envelope = json.loads(markup[0])
        response_envelope = json.loads(markup[1])
        assert request_envelope == {"cmd": "get_voltage"}, (
            "request envelope is exactly {cmd: <text>}"
        )
        assert response_envelope["cmd"] == "get_voltage", "response cmd"
        assert response_envelope["result"] == "5.5", "response result"
        assert response_envelope["success"] is True, "response success"

    def test_echo_input_off_does_not_suppress_request_envelope(self, repl_env):
        # Arrange -- echo_input is irrelevant in request_mode; the
        # request envelope is the protocol view, not a debug echo.
        engine, ctx, cfg, _, markup = repl_env
        cfg["echo_input"] = False
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("get_voltage")

        # Assert -- request envelope still renders despite echo_input=False
        assert len(markup) == 2, (
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
        cfg["echo_input"] = True
        fake = _FakeSerial(response=b"5.5")
        _wire_fake_serial(ctx, fake)

        # Act
        engine._exec_request_mode("get_voltage")

        # Assert -- still exactly two envelopes, not three
        assert len(markup) == 2, "echo_input=True does not double the request"

    def test_request_envelope_reflects_unwrapped_cmd_not_raw_input(self, repl_env):
        # Arrange -- JSON input gets unwrapped; the echo should show
        # the canonical form, not the raw input string.
        engine, ctx, cfg, _, markup = repl_env
        fake = _FakeSerial(response=b"OK")
        _wire_fake_serial(ctx, fake)

        # Act -- input has extra fields; should be dropped from echo
        engine._exec_request_mode('{"cmd":"reset","timeout_ms":5000,"x":"y"}')

        # Assert -- request envelope shows just {"cmd":"reset"}, no extras
        assert len(markup) == 2, "request + response envelopes"
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
        # Arrange -- echo_input on, request_mode off: legacy echo fires.
        engine, ctx, cfg, output, markup = repl_env
        cfg["echo_input"] = True
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
        cfg["echo_input"] = True
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
        assert len(markup) == 2, "request + response envelopes rendered"
        # Both lines parse as JSON.
        request_envelope = json.loads(markup[0])
        response_envelope = json.loads(markup[1])
        assert request_envelope == {"cmd": "AT+VER"}, (
            "first markup line is the request envelope"
        )
        assert response_envelope["cmd"] == "AT+VER", (
            "second markup line is the response envelope"
        )
