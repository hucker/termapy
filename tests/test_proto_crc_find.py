"""Tests for /proto.crc.find -- CRC algorithm identification.

The identification work itself is ``crcglot.detect`` (covered
exhaustively in crcglot's own test suite); these tests verify
termapy's three input modes (``bin=`` / ``asc=`` / ``cmd=``) and the
dispatch + formatting wrapper around it.

Known check values used throughout (CRCs of ``"123456789"``):

* crc16-modbus : 0x4B37
* crc16-xmodem : 0x31C3
* crc32        : 0xCBF43926
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.builtins.commands.proto import _crc_find
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import (
    InternalHandle,
    IOHandle,
    PluginContext,
    SerialHandle,
)
from termapy.protocol import get_crc_registry


# ---------------------------------------------------------------------------
# End-to-end CLI integration (bin= and asc= modes)
# ---------------------------------------------------------------------------


def _run_cli(
    tmp_path: Path, script_lines: list[str]
) -> subprocess.CompletedProcess[str]:
    """Invoke termapy --cli against a throwaway config and script."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    # Port lives nested under cfg["serial"] post-v22; building a flat
    # ``{**DEFAULT_CFG, "port": "DEMO"}`` would silently ignore the
    # port (the loader reads ``cfg["serial"]["port"]``) and the demo
    # connect would fail with "Cannot open ?: Port not found".
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "DEMO"},
        "auto_connect": True,
    }
    (proj_dir / "proj.cfg").write_text(json.dumps(cfg, indent=4))

    script_path = tmp_path / "crc_find.run"
    script_path.write_text("\n".join(script_lines) + "\n")

    return subprocess.run(
        [
            sys.executable, "-c",
            "import sys; "
            f"sys.argv = ['termapy', 'proj', '--cli', "
            f"'--cfg-dir', {str(tmp_path)!r}, "
            f"'--run', {str(script_path)!r}, "
            f"'--no-color', '--term-width', '120']; "
            "from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.slow
class TestCrcFindCli:
    """End-to-end through the CLI dispatch."""

    def test_bin_modbus_packet_reports_match(self, tmp_path):
        # Arrange -- "123456789" + 37 4B (little-endian Modbus CRC)
        line = "/proto.crc.find bin=31 32 33 34 35 36 37 38 39 37 4B"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert
        actual_code = result.returncode
        expected_code = 0
        assert actual_code == expected_code, (
            f"/proto.crc.find must exit 0, got {actual_code}. "
            f"stderr: {result.stderr!r}"
        )
        assert "crc16-modbus" in result.stdout, (
            f"/proto.crc.find with a Modbus packet must report crc16-modbus. "
            f"stdout: {result.stdout!r}"
        )

    def test_asc_modbus_packet_reports_match(self, tmp_path):
        # Arrange -- "123456789" + "4B37" (Modbus CRC as hex-ASCII suffix)
        line = "/proto.crc.find asc=1234567894B37"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert
        assert result.returncode == 0, f"exit code: {result.returncode}"
        assert "crc16-modbus" in result.stdout, (
            f"asc= form with trailing hex-ASCII Modbus CRC must match. "
            f"stdout: {result.stdout!r}"
        )

    def test_missing_input_mode_fails(self, tmp_path):
        # Arrange
        line = "/proto.crc.find"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert -- fail is a CmdResult.fail (not a crash) and usage printed.
        assert result.returncode == 0, f"exit code: {result.returncode}"
        assert "Usage" in result.stdout, (
            f"missing bin/asc/cmd must print usage. stdout: {result.stdout!r}"
        )

    def test_invalid_hex_fails_gracefully(self, tmp_path):
        # Arrange -- "ZZ" isn't a valid hex byte
        line = "/proto.crc.find bin=01 ZZ 03"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert
        assert result.returncode == 0, f"exit code: {result.returncode}"
        assert "Invalid hex" in result.stdout, (
            f"bad hex must produce 'Invalid hex' error. "
            f"stdout: {result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# cmd= mode unit tests -- mock the serial handle so the test can verify
# the parse + send + receive + detect path without a real device.
# ---------------------------------------------------------------------------


@pytest.fixture
def find_env():
    """PluginContext with a mocked serial that captures TX and replays a
    canned RX through ``read_raw``.

    ``response[0]`` is a mutable holder so individual tests can set the
    fake response before calling the handler.
    """
    output: list[tuple[str, str | None]] = []
    tx_bytes: list[bytes] = []
    response: list[bytes] = [b""]

    ctx = PluginContext(
        internal=InternalHandle(),
        io=IOHandle(
            _write=lambda t, c=None: output.append((t, c)),
            _write_markup=lambda t: output.append((t, "markup")),
        ),
        serial=SerialHandle(
            is_connected=lambda: True,
            write=lambda data: tx_bytes.append(data),
            read_raw=lambda timeout_ms=1000, frame_gap_ms=0: response[0],
        ),
    )
    return ctx, output, tx_bytes, response


class TestCrcFindCmdMode:
    """``cmd=`` sends a command, captures the response, runs detect on it."""

    def test_cmd_mode_sends_and_detects(self, find_env):
        # Arrange -- build a Modbus response: data + LE CRC (the algo's
        # natural wire order), then drive the handler with cmd=<send>.
        ctx, output, tx_bytes, response = find_env
        registry = get_crc_registry()
        data = b"\x01\x03\x04\x00\xc8\x01\xf4"
        crc_int = registry["crc16-modbus"].compute(data)
        crc_le = crc_int.to_bytes(2, "big")[::-1]
        response[0] = data + crc_le

        # Act
        result = _crc_find(ctx, "cmd=01 03 00 00 00 0A")

        # Assert
        actual = result.value
        expected = "crc16-modbus"
        assert tx_bytes, "the command bytes were written to the port"
        assert tx_bytes[0] == b"\x01\x03\x00\x00\x00\x0a", (
            f"TX matches the cmd= payload, got: {tx_bytes[0]!r}"
        )
        assert actual == expected, (
            f"single-match result.value carries the detected algo, "
            f"got {actual!r}"
        )

    def test_cmd_mode_no_response_returns_empty(self, find_env):
        # Arrange -- read_raw returns no bytes (silent device).
        ctx, output, tx_bytes, response = find_env
        response[0] = b""

        # Act
        result = _crc_find(ctx, "cmd=01 02 03")

        # Assert
        actual = result.value
        expected = ""
        no_response_lines = [t for t, _ in output if "no response" in t]
        assert no_response_lines, "the 'no response' line is shown"
        assert actual == expected, "value is empty when nothing arrived"

    def test_cmd_mode_fails_when_disconnected(self):
        # Arrange -- is_connected returns False.
        output: list = []
        ctx = PluginContext(
            internal=InternalHandle(),
            io=IOHandle(_write=lambda t, c=None: output.append((t, c))),
            serial=SerialHandle(
                is_connected=lambda: False,
                write=lambda data: None,
                read_raw=lambda timeout_ms=1000, frame_gap_ms=0: b"",
            ),
        )

        # Act
        result = _crc_find(ctx, "cmd=01 02 03")

        # Assert
        actual = result.error
        expected = "Not connected."
        assert not result.success, "handler reports failure"
        assert actual == expected, (
            f"error matches the standard 'Not connected.' message, "
            f"got {actual!r}"
        )

    def test_cmd_empty_payload_fails(self, find_env):
        # Arrange
        ctx, _, _, _ = find_env

        # Act
        result = _crc_find(ctx, "cmd=")

        # Assert
        assert not result.success, "empty cmd= is rejected"
        assert "Empty cmd=" in result.error, (
            f"error mentions the empty payload, got {result.error!r}"
        )
