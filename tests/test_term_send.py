"""Tests for Phase 2.5: /term.send <text> + dispatch_full fallthrough rewrite.

The serial-send fallthrough in dispatch_full now rewrites bare user
input to ``/term.send <text>`` and dispatches.  Goal: every action
has a discoverable slash-command name.

These tests cover:
- /term.send happy path (bytes on the wire match config encoding/line ending)
- /term.send disconnect handling
- /term.send error handling (bad serial_write)
- Byte-equivalence: typing a bare line vs. /term.send <line> produces the
  same wire bytes for the simple case.
"""

from __future__ import annotations

import json

import pytest

from termapy.plugins import PluginContext
from termapy.repl import ReplEngine


# ── Shared fixture (slimmed-down dispatch_env from test_engine.py) ──────────


@pytest.fixture
def env(tmp_path):
    """Create an engine + ctx with capture lists for callbacks."""
    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "line_ending": "\r",
        "encoding": "utf-8",
    }
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)

    output: list[tuple[str, str | None]] = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))

    from termapy.plugins import IOHandle, SerialHandle
    serial_writes: list[bytes] = []
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        io=IOHandle(write=lambda t, c=None: output.append((t, c))),
        serial=SerialHandle(
            is_connected=lambda: True,
            write=lambda data: serial_writes.append(data),
        ),
    )
    eng.set_context(ctx)
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex_mode"] = False

    return eng, ctx, output, serial_writes


# ── /term.send happy paths ──────────────────────────────────────────────────


class TestTermSendHappyPath:
    def test_sends_bytes_with_default_line_ending(self, env):
        # Arrange
        eng, ctx, _output, writes = env
        # Act
        result = eng.dispatch("term.send AT+VER")
        # Assert
        assert result.success is True, "command succeeded"
        assert writes == [b"AT+VER\r"], "bytes match: text + default \\r"

    def test_uses_lf_line_ending_from_cfg(self, env):
        # Arrange
        eng, ctx, _output, writes = env
        eng._cfg_data["line_ending"] = "\n"
        # Act
        eng.dispatch("term.send hello")
        # Assert
        assert writes == [b"hello\n"], "newline line ending honored"

    def test_uses_crlf_line_ending(self, env):
        # Arrange
        eng, ctx, _output, writes = env
        eng._cfg_data["line_ending"] = "\r\n"
        # Act
        eng.dispatch("term.send hello")
        # Assert
        assert writes == [b"hello\r\n"], "CRLF line ending honored"

    def test_uses_utf8_encoding(self, env):
        # Arrange
        eng, ctx, _output, writes = env
        # Act
        eng.dispatch("term.send café")  # café
        # Assert
        assert writes == [b"caf\xc3\xa9\r"], "utf-8 encoding for non-ASCII"

    def test_uses_latin1_encoding_when_configured(self, env):
        # Arrange
        eng, ctx, _output, writes = env
        eng._cfg_data["encoding"] = "latin-1"
        # Act
        eng.dispatch("term.send café")
        # Assert
        assert writes == [b"caf\xe9\r"], "latin-1 encoding honored"


# ── /term.send error paths ──────────────────────────────────────────────────


class TestTermSendErrors:
    def test_no_args_returns_usage(self, env):
        # Arrange
        eng, _ctx, _output, writes = env
        # Act
        result = eng.dispatch("term.send")
        # Assert
        assert result.success is False, "no args is a usage error"
        assert "Usage" in result.error, "error names the usage"
        assert writes == [], "no bytes written"

    def test_disconnected_returns_not_connected(self, env, tmp_path):
        # Arrange — rebuild ctx with is_connected returning False
        cfg = {"port": "COM4", "line_ending": "\r", "encoding": "utf-8"}
        config_path = tmp_path / "cfg2" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        output: list = []
        eng2 = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
        writes: list[bytes] = []
        from termapy.plugins import IOHandle, SerialHandle
        ctx2 = PluginContext(
            cfg=cfg,
            config_path=str(config_path),
            io=IOHandle(write=lambda t, c=None: output.append((t, c))),
            serial=SerialHandle(
                is_connected=lambda: False,
                write=lambda data: writes.append(data),
            ),
        )
        eng2.set_context(ctx2)
        ctx2.ns("flags")["echo"] = True
        ctx2.ns("flags")["output_level"] = "verbose"
        # Act
        result = eng2.dispatch("term.send AT+VER")
        # Assert
        assert result.success is False, "disconnect blocks send"
        assert result.error == "Not connected.", "preserves legacy message"
        assert writes == [], "nothing sent when not connected"

    def test_serial_write_raises_returns_send_error(self, env):
        # Arrange
        eng, ctx, _output, _writes = env

        def boom(_data: bytes) -> None:
            raise OSError("port closed")

        ctx.serial.write = boom
        # Act
        result = eng.dispatch("term.send AT+VER")
        # Assert
        assert result.success is False, "OSError surfaces as failure"
        assert "Send error" in result.error, "error names the failure mode"
        assert "port closed" in result.error, "preserves underlying message"


# ── Byte-equivalence: bare line vs. explicit /term.send ─────────────────────


class TestByteEquivalence:
    """The fallthrough rewrites a bare line to /term.send <text>.  For
    inputs without serial transforms (variables, templates), the wire
    bytes from typing the bare line should match /term.send <line>
    exactly.  This is the core invariant of the refactor."""

    @pytest.mark.parametrize(
        "text",
        ["AT", "ATZ", "AT+VER", "AT+CSQ", "hello world"],
    )
    def test_bare_line_matches_explicit_term_send(self, env, text):
        # Arrange — capture bytes from a bare-line dispatch_full
        eng_a, _ctx_a, _output_a, writes_a = env
        # Act
        eng_a.dispatch_full(
            text,
            log=lambda d, t: None,
            echo_markup=lambda t: None,
            status=lambda t, c: None,
            serial_write=writes_a.append,
            is_connected=lambda: True,
        )
        bare_bytes = list(writes_a)

        # Now do the same via explicit /term.send dispatch on a fresh fixture
        # (we can't reuse env's writes — they share the list with ctx).
        writes_b: list[bytes] = []
        eng_a.ctx.serial.write = lambda data: writes_b.append(data)
        eng_a.dispatch(f"term.send {text}")
        explicit_bytes = list(writes_b)
        # Assert
        assert bare_bytes == explicit_bytes, (
            f"bare-line and /term.send produce identical wire bytes "
            f"for {text!r}"
        )


# ── /term.send is in the catalog ────────────────────────────────────────────


class TestCatalogPresence:
    def test_term_send_resolves_as_a_subcommand(self, env):
        # Arrange
        eng, _ctx, _output, _writes = env
        # Act — dispatch a no-args invocation; should fail with Usage, not
        # "Unknown command".
        result = eng.dispatch("term.send")
        # Assert
        assert result.success is False, "no-args is a failure"
        assert "Unknown command" not in result.error, (
            "/term.send is registered as a known subcommand"
        )
        assert "Usage" in result.error, "fails with usage hint"
