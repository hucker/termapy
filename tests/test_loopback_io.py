"""End-to-end I/O tests using pyserial's ``loop://`` backend.

``loop://`` is a pure-Python virtual serial port (part of pyserial):
whatever is written to it is immediately readable.  It exercises the
full send-and-receive path of termapy -- encoding, line ending, CRC
append, capture -- against *real* pyserial code, just with a
bytes-in-bytes-out pipe instead of a device.

Complementary to ``test_cli_gold.py`` which drives the DEMO fake
device (tests termapy's own protocol simulation).  Here we test the
pyserial-integration layer: bytes actually go out, actually come
back, and termapy's transform / capture / display logic handles the
round-trip correctly.

The tests use ``/cap.text`` to record the RX stream for a bounded
window, then read the capture file and assert on its contents --
simpler and more reliable than racing the reader thread with
``/expect``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow  # real-pyserial loopback + subprocess CLI tests


def _run_cli(
    tmp_path: Path,
    cfg: dict,
    script_lines: list[str],
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Launch termapy --cli against a throwaway config + script.

    ``cfg`` is merged onto a minimal baseline (port, baud_rate,
    auto_connect).  The script is written to the config's run/
    directory and invoked via ``--run``.
    """
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    run_dir = proj_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    baseline = {
        "port": "loop://",
        "baud_rate": 115200,
        "auto_connect": True,
    }
    baseline.update(cfg)
    (proj_dir / "proj.cfg").write_text(json.dumps(baseline, indent=4))

    script_path = run_dir / "test.run"
    # Force UTF-8: termapy's script reader decodes as UTF-8, so the
    # default platform encoding (cp1252 on Windows) would corrupt
    # non-ASCII characters in script payloads.
    script_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")

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
        timeout=timeout,
    )


def _cap_file(tmp_path: Path, name: str) -> Path:
    """Path where ``/cap.text <name>`` writes its capture file."""
    return tmp_path / "proj" / "cap" / name


class TestLoopbackRoundTrip:
    """Bytes sent to ``loop://`` come back on the next read."""

    def test_plain_send_roundtrips(self, tmp_path):
        # Arrange -- /cap.bin with exact byte target so the capture
        # closes on target-hit (reliable), not on timeout (races
        # with --run script teardown).  Default line_ending is "\r"
        # so "hello-world\r" = 12 bytes.
        lines = [
            "/cap.bin out.bin bytes=12 timeout=500ms cmd=hello-world",
            "/delay 700ms",
        ]

        # Act
        result = _run_cli(tmp_path, {}, lines)

        # Assert
        assert result.returncode == 0, (
            f"exit {result.returncode}; stderr={result.stderr!r}"
        )
        cap = _cap_file(tmp_path, "out.bin").read_bytes()
        assert cap == b"hello-world\r", (
            f"loopback echo must be exactly 'hello-world\\r', got {cap!r}"
        )

    def test_raw_send_bypasses_transforms(self, tmp_path):
        # Arrange -- /raw sends the exact bytes with no line ending
        # and no transforms.  "ABC-raw-XYZ" = 11 bytes, no trailing CR.
        lines = [
            "/cap.bin raw.bin bytes=11 timeout=500ms cmd=/raw ABC-raw-XYZ",
            "/delay 700ms",
        ]

        # Act
        result = _run_cli(tmp_path, {}, lines)

        # Assert
        assert result.returncode == 0, (
            f"exit {result.returncode}; stderr={result.stderr!r}"
        )
        cap = _cap_file(tmp_path, "raw.bin").read_bytes()
        assert cap == b"ABC-raw-XYZ", (
            f"/raw must send exactly 11 bytes without line ending, "
            f"got {cap!r}"
        )


class TestLoopbackLineEndings:
    """The configured line ending reaches the wire."""

    @pytest.mark.parametrize(
        "line_ending, suffix",
        [("\r", b"\r"), ("\n", b"\n"), ("\r\n", b"\r\n")],
    )
    def test_line_ending_passthrough(self, tmp_path, line_ending, suffix):
        # Arrange -- send "ping"; the configured ending should follow.
        # Size the capture target to the exact expected byte count so
        # the capture closes on target-hit (reliable) rather than
        # timeout (flushes on script teardown, which races).
        cfg = {"line_ending": line_ending}
        target_bytes = 4 + len(suffix)  # "ping" + line ending
        lines = [
            f"/cap.bin ending.bin bytes={target_bytes} timeout=500ms cmd=ping",
            "/delay 700ms",
        ]

        # Act
        result = _run_cli(tmp_path, cfg, lines)

        # Assert -- capture file contains exactly "ping" + configured ending.
        assert result.returncode == 0, (
            f"exit {result.returncode}; stderr={result.stderr!r}"
        )
        cap_bytes = _cap_file(tmp_path, "ending.bin").read_bytes()
        expected = b"ping" + suffix
        assert cap_bytes == expected, (
            f"line_ending={line_ending!r}: expected capture bytes {expected!r}, "
            f"got {cap_bytes!r}"
        )


class TestLoopbackProtoSend:
    """/proto.send appends the CRC and the full packet round-trips."""

    def test_modbus_crc_roundtrip(self, tmp_path):
        # Arrange -- ``/proto.send crc16-modbus 01 03 00 00 00 0A``
        # appends CRC-16/MODBUS (little-endian: C5 CD) producing 8
        # bytes on the wire.  With loop:// the same 8 bytes come
        # back on the RX side.
        lines = [
            "/cap.bin crc.bin bytes=8 timeout=500ms "
            "cmd=/proto.send crc16-modbus 01 03 00 00 00 0A",
            "/delay 700ms",
        ]

        # Act
        result = _run_cli(tmp_path, {}, lines)

        # Assert -- exact byte sequence: 01 03 00 00 00 0A C5 CD.
        assert result.returncode == 0, (
            f"exit {result.returncode}; stderr={result.stderr!r}"
        )
        cap_bytes = _cap_file(tmp_path, "crc.bin").read_bytes()
        expected = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0xC5, 0xCD])
        assert cap_bytes[:8] == expected, (
            f"CRC round-trip byte mismatch.  expected={expected.hex()}, "
            f"got={cap_bytes[:8].hex()}"
        )


class TestLoopbackEncoding:
    """The ``encoding`` config key affects the bytes that are sent."""

    def test_latin1_non_ascii_roundtrips_as_single_byte(self, tmp_path):
        # Arrange -- Latin-1 encodes "é" as a single byte (0xE9);
        # UTF-8 encodes it as two bytes (0xC3 0xA9).  Sending the
        # same character under each encoding must produce different
        # byte sequences on the wire.  loop:// echoes the exact
        # bytes so /cap.bin proves the encoding config took effect.
        cfg = {"encoding": "latin-1", "line_ending": ""}
        lines = [
            # Send the single-byte Latin-1 "é"; line_ending is empty
            # so the capture holds exactly the encoded byte.
            "/cap.bin latin.bin bytes=1 timeout=500ms cmd=\u00e9",
            "/delay 700ms",
        ]

        # Act
        result = _run_cli(tmp_path, cfg, lines)

        # Assert
        assert result.returncode == 0, (
            f"exit {result.returncode}; stderr={result.stderr!r}"
        )
        cap_bytes = _cap_file(tmp_path, "latin.bin").read_bytes()
        assert cap_bytes == b"\xe9", (
            f"latin-1 'é' must encode to 0xE9, got {cap_bytes!r}"
        )


class TestLoopbackCliReaderTaps:
    """CLI's serial reader must feed the expect watcher and text capture.

    Both are fed from the host's line batch (the TUI does this in
    ``app._write_batch``).  Before ``CLITerminal._start_reader``'s
    ``on_lines`` grew the same taps, ``/expect`` timed out and
    ``/cap.text`` recorded nothing in CLI mode -- while ``/cap.bin``
    worked because binary capture is fed at the byte level in
    ``serial_port.read``.  These tests drive the real ``loop://`` path
    and would fail against the pre-fix reader.
    """

    def test_text_capture_records_rx(self, tmp_path):
        # Arrange -- capture text for a bounded window, send a line that
        # the loopback echoes back into that window.
        lines = [
            "/cap.text out.txt timeout=800ms",
            "hello-text-2607",
            "/delay 1000ms",
        ]

        # Act
        result = _run_cli(tmp_path, {}, lines)

        # Assert
        assert result.returncode == 0, (
            f"exit {result.returncode}; stderr={result.stderr!r}"
        )
        cap = _cap_file(tmp_path, "out.txt").read_text(encoding="utf-8")
        assert "hello-text-2607" in cap, (
            f"CLI text capture must record the loopback echo, got {cap!r}"
        )

    def test_expect_matches_rx(self, tmp_path):
        # Arrange -- send a line, then /expect its echo.  feed_lines
        # buffers retroactively, so the match holds even if the echo
        # lands before the predicate is installed.
        lines = [
            "hello-expect-2607",
            "/expect timeout=2s match=hello-expect",
        ]

        # Act
        result = _run_cli(tmp_path, {}, lines)

        # Assert
        actual = result.stdout.lower()
        assert result.returncode == 0, (
            f"/expect should match the echo, not time out; "
            f"exit {result.returncode}, stderr={result.stderr!r}"
        )
        assert "matched" in actual, (
            f"expected an expect-match message, got {result.stdout!r}"
        )
