"""Tests for the DEMO_JSON NDJSON simulator (Phase 5).

Two layers:
- Direct simulator tests (FakeSerialNDJSON byte-level round-trips).
- End-to-end via MCPHost: open DEMO_JSON, send commands via run_command,
  verify the response shape carries through to Claude.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from termapy.config import open_serial
from termapy.demo_ndjson import FakeSerialNDJSON

# ── Direct simulator tests ──────────────────────────────────────────────────


class TestSimulatorBasics:
    def test_open_serial_routes_demo_json(self):
        """A DEMO_JSON port in the nested serial config opens the NDJSON simulator."""
        # Arrange / Act -- v22 nested-serial shape.
        port = open_serial({"serial": {"port": "DEMO_JSON", "baud_rate": 115200}})
        # Assert
        assert isinstance(port, FakeSerialNDJSON), (
            "open_serial returns the NDJSON simulator for DEMO_JSON"
        )

    def test_first_read_delivers_ready_banner(self):
        """The first read is a ready event naming the BASSOMATIC device and its fw version."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        # Act
        # Read enough bytes to capture the full banner line.
        chunk = port.read(200)
        # Assert
        line = chunk.decode("utf-8").strip()
        parsed = json.loads(line)
        assert parsed["event"] == "ready", "banner is a ready event"
        assert parsed["device"] == "BASSOMATIC", "device name in banner"
        assert "fw" in parsed, "firmware version in banner"

    def test_request_with_id_correlates_in_response(self):
        """A request id is echoed in its ok response so callers can match replies to requests."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.write(b'{"cmd":"get_temp","id":7}\n')
        line = port.read(200).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert parsed["id"] == 7, "id round-trips for correlation"
        assert parsed["ok"] is True, "ok=True on success"
        assert "celsius" in parsed["result"], "result has celsius"

    def test_request_without_id_omits_id_in_response(self):
        """A request without an id gets a reply without one; the simulator invents no id."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.write(b'{"cmd":"get_temp"}\n')
        line = port.read(200).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert "id" not in parsed, "no id in => no id out"

    def test_unknown_cmd_returns_error_with_code(self):
        """An unknown cmd answers ok=false with code -1 and an error naming the cmd."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.write(b'{"cmd":"nope","id":1}\n')
        line = port.read(200).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert parsed["ok"] is False, "ok=False on unknown"
        assert parsed["code"] == -1, "unknown cmd has code -1"
        assert "nope" in parsed["error"], "error names the unknown cmd"

    def test_malformed_json_routes_as_async_error(self):
        """Unparseable input yields an id-less error, which the bridge routes as async."""
        # Arrange — broken input has no id; sim emits error without id
        # so the bridge will treat it as an async error.
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.write(b"this is not json\n")
        line = port.read(200).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert "id" not in parsed, "malformed -> async error (no id)"
        assert parsed["error"], "error message present"

    def test_set_threshold_updates_state(self):
        """set_threshold changes device state that a later get_status reports back."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act — set threshold then read it back via get_status
        port.write(b'{"cmd":"set_threshold","args":{"celsius":42.5},"id":1}\n')
        _r1 = port.read(200)
        port.write(b'{"cmd":"get_status","id":2}\n')
        line = port.read(400).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert parsed["result"]["threshold_c"] == 42.5, (
            "set_threshold updated state visible via get_status"
        )

    def test_set_mode_validates_enum(self):
        """set_mode rejects a value outside its enum with an unknown mode error."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.write(b'{"cmd":"set_mode","args":{"mode":"banana"},"id":1}\n')
        line = port.read(200).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert parsed["ok"] is False, "invalid enum rejected"
        assert "unknown mode" in parsed["error"].lower(), "error names unknown mode"

    def test_reset_is_fire_and_forget_re_emits_banner(self):
        """reset sends no response; the next read is a fresh ready banner."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.write(b'{"cmd":"reset"}\n')
        line = port.read(200).decode().strip()
        # Assert -- next read after reset is the banner, not a response
        parsed = json.loads(line)
        assert parsed.get("event") == "ready", "post-reset banner re-emitted"

    def test_emit_event_test_helper_queues_async_event(self):
        """emit_event queues an unsolicited event with its name and extra fields intact."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.emit_event("tick", value=42)
        line = port.read(200).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert parsed["event"] == "tick", "event name preserved"
        assert parsed["value"] == 42, "extra fields preserved"

    def test_emit_async_error_test_helper_queues_async_error(self):
        """emit_async_error queues an id-less error carrying its message and code."""
        # Arrange
        port = FakeSerialNDJSON()
        port.timeout = 0.5
        _drain_banner(port)
        # Act
        port.emit_async_error("watchdog", code=-99)
        line = port.read(200).decode().strip()
        # Assert
        parsed = json.loads(line)
        assert parsed["error"] == "watchdog", "error message preserved"
        assert parsed["code"] == -99, "code preserved"
        assert "id" not in parsed, "no id => async error per field_routing"


# ── End-to-end: MCPHost driving DEMO_JSON ───────────────────────────────────


pytest_mcp = pytest.importorskip(
    "mcp", reason="mcp SDK not installed; install with [mcp] extra"
)


class TestMcpHostAgainstDemoJson:
    @pytest.fixture
    def host(self, tmp_path):
        from termapy.defaults import default_cfg
        from termapy.mcp.server import MCPHost

        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO_JSON"
        cfg["auto_connect"] = True
        # NDJSON wants \n; default is \r.  Phase 6 will apply this from
        # the loaded profile; for now the test sets it explicitly.
        cfg["eol"] = "\n"
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        h = MCPHost(cfg, str(config_path), verbose=False)
        # Auto-connect.
        h._connect()
        # Settle the reader so the banner gets logged before tests fire.
        time.sleep(0.05)
        try:
            yield h
        finally:
            if h.engine.is_connected:
                h._disconnect()

    def test_run_command_can_use_expect_to_get_response(self, host):
        """/expect.regex after a /raw request returns the reply line carrying its id."""
        # Arrange — send raw, then /expect to wait for response line
        # Act
        asyncio.run(
            host.run_command_async(
                '/raw {"cmd":"get_temp","id":42}', "normal", 5.0
            )
        )
        result = asyncio.run(
            host.run_command_async(
                '/expect.regex timeout=2s match="id":\\s*42',
                "normal",
                5.0,
            )
        )
        # Assert -- /expect matched the response line
        assert result["success"] is True, "expect found the response"
        assert '"id":42' in result["value"] or '"id": 42' in result["value"], (
            "matched line contains the request id"
        )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _drain_banner(port: FakeSerialNDJSON) -> None:
    """Read and discard the startup banner so tests can focus on responses."""
    # The banner is ~50 bytes; reading 200 with a short timeout grabs it.
    saved_timeout = port.timeout
    port.timeout = 0.2
    port.read(200)
    port.timeout = saved_timeout
