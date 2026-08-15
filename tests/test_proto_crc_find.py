"""Tests for /proto.crc.find -- CRC algorithm identification.

The identification work itself is ``crcglot.detect`` (covered
exhaustively in crcglot's own test suite); these tests verify
termapy's two input modes (``bin=`` and ``asc=``) and the dispatch +
formatting wrapper around it.

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

from termapy.defaults import DEFAULT_CFG

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
            f"missing bin/asc must print usage. stdout: {result.stdout!r}"
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

    def test_normal_mode_suppresses_header_and_hint(self, tmp_path):
        # Regression -- normal mode should NOT show the old "1 match:"
        # header or the "Generate source" hint (both demoted under the
        # answer/context/hints model: header dropped as noise, hint
        # moved to verbose-only).
        line = "/proto.crc.find bin=31 32 33 34 35 36 37 38 39 37 4B"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert
        out = result.stdout
        assert "crc16-modbus" in out, (
            f"match line still present at normal level. stdout: {out!r}"
        )
        assert "1 match" not in out, (
            f"the 'N matches:' header should be gone. stdout: {out!r}"
        )
        assert "Generate source" not in out, (
            f"the codegen hint should not show at normal level. "
            f"stdout: {out!r}"
        )

    def test_verbose_mode_shows_generate_hint(self, tmp_path):
        # The codegen hint lives at status level -- it only appears
        # when the user opts into verbose output.
        line = "/proto.crc.find.verbose bin=31 32 33 34 35 36 37 38 39 37 4B"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert
        out = result.stdout
        assert "Generate source" in out, (
            f"--verbose / .verbose should surface the codegen hint. "
            f"stdout: {out!r}"
        )
        assert "/proto.crc.c crc16-modbus" in out, (
            f"hint points at the per-language codegen command. "
            f"stdout: {out!r}"
        )


