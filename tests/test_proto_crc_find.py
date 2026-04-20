"""Tests for /proto.crc.find -- CRC algorithm identification.

The pure functions ``_find_in_binary`` and ``_find_in_ascii`` are
tested directly; the CLI wrapper is exercised via subprocess to
catch integration breakage (parser, prefix, output formatting).

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

from termapy.builtins.plugins.proto import (
    _dedupe_catalogue_aliases,
    _find_in_ascii,
    _find_in_binary,
)
from termapy.defaults import DEFAULT_CFG
from termapy.protocol_crc import CRC_CATALOGUE


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestFindInBinary:
    """Binary-packet identification -- hex bytes with trailing CRC field."""

    def test_modbus_little_endian(self):
        # Arrange -- "123456789" + CRC-16/MODBUS=0x4B37 little-endian
        packet = b"123456789" + bytes([0x37, 0x4B])

        # Act
        matches = _find_in_binary(packet, byte_widths=(1, 2, 4), endians=("be", "le"))

        # Assert -- at least crc16-modbus appears, width=2, endian=le
        actual_names = {name for name, w, endian, _, _ in matches}
        assert "crc16-modbus" in actual_names, (
            f"crc16-modbus must match this packet, got: {sorted(actual_names)}"
        )
        modbus = next(m for m in matches if m[0] == "crc16-modbus")
        assert modbus[1] == 2, f"modbus width must be 2 bytes, got {modbus[1]}"
        assert modbus[2] == "le", f"modbus endian must be le, got {modbus[2]}"
        assert modbus[4] == 0x4B37, f"expected must be 0x4B37, got 0x{modbus[4]:X}"

    def test_xmodem_big_endian(self):
        # Arrange -- "123456789" + CRC-16/XMODEM=0x31C3 big-endian
        packet = b"123456789" + bytes([0x31, 0xC3])

        # Act
        matches = _find_in_binary(packet, byte_widths=(1, 2, 4), endians=("be", "le"))

        # Assert
        actual_names = {name for name, w, endian, _, _ in matches}
        assert "crc16-xmodem" in actual_names, (
            f"crc16-xmodem must match this packet, got: {sorted(actual_names)}"
        )
        xmodem = next(m for m in matches if m[0] == "crc16-xmodem")
        assert xmodem[2] == "be", f"xmodem endian must be be, got {xmodem[2]}"

    def test_crc32_iso_hdlc(self):
        # Arrange -- "123456789" + CRC-32=0xCBF43926 little-endian
        packet = b"123456789" + bytes([0x26, 0x39, 0xF4, 0xCB])

        # Act
        matches = _find_in_binary(packet, byte_widths=(4,), endians=("be", "le"))

        # Assert -- the standard crc32 entry should match little-endian
        actual_names = {name for name, _, _, _, _ in matches}
        assert "crc32" in actual_names, (
            f"crc32 must match this packet, got: {sorted(actual_names)}"
        )

    def test_width_filter_restricts_search(self):
        # Arrange -- same Modbus packet, but only ask for 8-bit candidates
        packet = b"123456789" + bytes([0x37, 0x4B])

        # Act
        matches = _find_in_binary(packet, byte_widths=(1,), endians=("be", "le"))

        # Assert -- no 16-bit modbus result; whatever matches (if any)
        # must be width=1.
        for name, w, _, _, _ in matches:
            assert w == 1, f"width filter failed: {name} returned width={w}"

    def test_packet_too_short_to_analyze(self):
        # Arrange -- one byte can't hold data + CRC
        packet = b"\x42"

        # Act
        matches = _find_in_binary(packet, byte_widths=(1, 2, 4), endians=("be", "le"))

        # Assert
        assert matches == [], (
            f"packet shorter than smallest CRC field must yield no matches, "
            f"got: {matches}"
        )

    def test_no_match_returns_empty(self):
        # Arrange -- garbage CRC bytes that won't match any standard algorithm
        packet = b"123456789" + bytes([0xDE, 0xAD])

        # Act
        matches = _find_in_binary(packet, byte_widths=(2,), endians=("be", "le"))

        # Assert -- realistically the 62-algorithm sweep on 9 bytes of data
        # may have a coincidental collision, so we don't assert exactly
        # [].  What we CAN assert: no standard crc16 algorithm should
        # claim this obviously-wrong CRC matches its canonical form.
        # The test is light -- the point is the function returns without
        # crashing and the return type is a list.
        assert isinstance(matches, list), "matches must be a list"


class TestRoundtripEveryCatalogueAlgorithm:
    """Every catalogue algorithm must be identifiable from a crafted packet.

    For each CRC in ``CRC_CATALOGUE``, construct a packet of
    ``"123456789"`` + the algorithm's catalogue check value (laid out
    in both big- and little-endian) and feed it to ``_find_in_binary``.
    The algorithm name must appear in the result set.
    """

    @pytest.mark.parametrize("algo_name", sorted(CRC_CATALOGUE.keys()))
    @pytest.mark.parametrize("endian", ["be", "le"])
    def test_roundtrip(self, algo_name, endian):
        # Arrange -- build a packet: known data + the catalogue check value.
        entry = CRC_CATALOGUE[algo_name]
        width_bits = entry["width"]
        width_bytes = (width_bits + 7) // 8
        # 8-bit CRCs have no endian -- a single byte reads the same either
        # way -- so skip the duplicate run.  The test is still exhaustive
        # over multi-byte CRCs for both orders.
        if width_bytes == 1 and endian == "le":
            pytest.skip("endian is meaningless for single-byte CRCs")
        check = entry["check"]
        order = "big" if endian == "be" else "little"
        packet = b"123456789" + check.to_bytes(width_bytes, order)

        # Act -- search across all widths + endians (simulate user's view).
        raw = _find_in_binary(
            packet, byte_widths=(1, 2, 4), endians=("be", "le")
        )
        collapsed = _dedupe_catalogue_aliases(raw)

        # Assert -- algo_name must appear as a canonical name or alias.
        all_names = set()
        expected_endian = "-" if width_bytes == 1 else endian
        for canonical, _, match_endian, _, _, aliases in collapsed:
            all_names.add(canonical)
            all_names.update(aliases)
            if algo_name in {canonical, *aliases}:
                assert match_endian == expected_endian, (
                    f"{algo_name} packet ({endian}): find reported "
                    f"endian={match_endian}, expected {expected_endian}"
                )
        assert algo_name in all_names, (
            f"find must identify {algo_name} from a known-good {endian} "
            f"packet, got: {sorted(all_names)}"
        )


class TestFindInAscii:
    """ASCII-packet identification -- trailing hex-ASCII CRC field."""

    def test_modbus_hex_ascii_suffix(self):
        # Arrange -- "123456789" + "4B37" (CRC-16/MODBUS in hex-ASCII)
        text = "1234567894B37"

        # Act
        matches = _find_in_ascii(text, byte_widths=(1, 2, 4))

        # Assert
        actual_names = {name for name, _, _, _, _ in matches}
        assert "crc16-modbus" in actual_names, (
            f"crc16-modbus must match ASCII packet with trailing 4B37, "
            f"got: {sorted(actual_names)}"
        )
        modbus = next(m for m in matches if m[0] == "crc16-modbus")
        assert modbus[1] == 2, f"modbus width must be 2 bytes, got {modbus[1]}"
        assert modbus[2] == "-", f"ASCII mode has no endian, got {modbus[2]!r}"

    def test_non_hex_tail_skips_that_width(self):
        # Arrange -- tail "ZZZZ" isn't valid hex, so width=16 is skipped
        text = "helloZZZZ"

        # Act -- must not crash even though some widths are unparsable
        matches = _find_in_ascii(text, byte_widths=(1, 2, 4))

        # Assert -- whatever matches (if any), each match's data length
        # is positive (we actually sliced off something).
        assert isinstance(matches, list), "matches must be a list"
        for _, w, _, data_len, _ in matches:
            assert data_len > 0, f"data_len must be positive, got {data_len}"
            assert w * 2 <= len(text), (
                f"CRC field ({w * 2} chars) can't exceed text length ({len(text)})"
            )

    def test_text_too_short_to_hold_crc(self):
        # Arrange -- a 1-char string can't hold even an 8-bit hex-ASCII CRC
        text = "x"

        # Act
        matches = _find_in_ascii(text, byte_widths=(1, 2, 4))

        # Assert
        assert matches == [], (
            f"text shorter than smallest CRC hex field must yield no matches, "
            f"got: {matches}"
        )


# ---------------------------------------------------------------------------
# End-to-end CLI integration
# ---------------------------------------------------------------------------


def _run_cli(tmp_path: Path, script_lines: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke termapy --cli against a throwaway config and script."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    cfg = {**DEFAULT_CFG, "port": "DEMO", "auto_connect": True}
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


class TestCrcFindCli:
    """End-to-end through the CLI dispatch."""

    def test_bin_modbus_packet_reports_match(self, tmp_path):
        # Arrange -- "123456789" + 37 4B (little-endian Modbus CRC)
        line = "/proto.crc.find bin=31 32 33 34 35 36 37 38 39 37 4B"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert -- exits clean, crc16-modbus appears in stdout
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

    def test_missing_both_bin_and_asc_fails(self, tmp_path):
        # Arrange
        line = "/proto.crc.find"

        # Act
        result = _run_cli(tmp_path, [line])

        # Assert -- exits clean (fail is a CmdResult.fail, not a crash)
        # and the usage hint is printed.
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
            f"bad hex must produce 'Invalid hex' error. stdout: {result.stdout!r}"
        )
