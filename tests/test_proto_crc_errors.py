"""Regression test: /proto.crc.{c,python,rust} with an unknown algorithm.

A previous refactor (``bug/prefix-help``) bulk-replaced ``/`` with
``{prefix}`` in plugin help strings.  One of those edits landed on
an f-string in ``_crc_codegen``'s error branch, turning
``f"Unknown algorithm: {name}. Use /proto.crc.list..."`` into
``f"... {prefix}proto.crc.list ..."`` where ``prefix`` was not
defined in scope.  The bug was invisible to the test suite because
no test passed an unknown algorithm name to the codegen handlers
-- the branch was unreachable in practice.  ``uvx ty check`` caught
it at release-prep time.

This test exercises that error branch directly so the same class
of bug can't recur: unknown algorithm names on every codegen
command must fail gracefully, mention the bad name, and point the
user at ``proto.crc.list`` using the live prefix (never the literal
string ``{prefix}``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.defaults import DEFAULT_CFG


def _run_cli(tmp_path: Path, script_lines: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke termapy --cli against a throwaway config and script."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    cfg = {**DEFAULT_CFG, "port": "DEMO", "auto_connect": True}
    (proj_dir / "proj.cfg").write_text(json.dumps(cfg, indent=4))

    script_path = tmp_path / "crc_errs.run"
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


class TestCrcCodegenUnknownAlgorithm:
    """Every codegen command must handle unknown names without crashing.

    The guard is narrow on purpose: we check that a well-known
    sentinel string -- "Unknown algorithm" -- appears and that the
    live prefix (the literal ``/`` in the default config) reaches
    the user, not the unsubstituted ``{prefix}`` placeholder.
    """

    @pytest.mark.parametrize("lang", ["c", "python", "rust"])
    def test_unknown_name_fails_gracefully(self, tmp_path, lang):
        # Arrange -- a name that cannot exist in the CRC catalogue.
        fake = "crcDOES_NOT_EXIST"

        # Act
        result = _run_cli(tmp_path, [f"/proto.crc.{lang} {fake}"])

        # Assert -- process exits cleanly (the error is caught, not
        # crashed), the user sees the message, and no literal
        # ``{prefix}`` leaks through.
        actual_code = result.returncode
        expected_code = 0
        assert actual_code == expected_code, (
            f"/proto.crc.{lang} unknown-name must exit 0, got {actual_code}. "
            f"stderr: {result.stderr!r}"
        )
        assert "Unknown algorithm" in result.stdout, (
            f"/proto.crc.{lang} {fake} should print 'Unknown algorithm'. "
            f"stdout: {result.stdout!r}"
        )
        assert fake in result.stdout, (
            f"/proto.crc.{lang} {fake} should echo the bad name back. "
            f"stdout: {result.stdout!r}"
        )
        assert "{prefix}" not in result.stdout, (
            f"/proto.crc.{lang} leaked the literal {{prefix}} placeholder. "
            f"stdout: {result.stdout!r}"
        )
        assert "/proto.crc.list" in result.stdout, (
            f"/proto.crc.{lang} should point the user at /proto.crc.list. "
            f"stdout: {result.stdout!r}"
        )

    def test_crc_calc_unknown_name_fails_gracefully(self, tmp_path):
        # Act -- exercise the sibling _crc_calc handler's error path.
        result = _run_cli(tmp_path, ["/proto.crc.calc crcDOES_NOT_EXIST 01 02"])

        # Assert
        assert result.returncode == 0, (
            f"/proto.crc.calc must exit 0, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        assert "/proto.crc.list" in result.stdout, (
            f"/proto.crc.calc should point the user at /proto.crc.list. "
            f"stdout: {result.stdout!r}"
        )
        assert "{prefix}" not in result.stdout, (
            "/proto.crc.calc leaked the literal {prefix} placeholder"
        )

    def test_crc_info_unknown_name_fails_gracefully(self, tmp_path):
        # Act
        result = _run_cli(tmp_path, ["/proto.crc.info crcDOES_NOT_EXIST"])

        # Assert
        assert result.returncode == 0, (
            f"/proto.crc.info must exit 0, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        assert "/proto.crc.list" in result.stdout, (
            f"/proto.crc.info should point the user at /proto.crc.list. "
            f"stdout: {result.stdout!r}"
        )
        assert "{prefix}" not in result.stdout, (
            "/proto.crc.info leaked the literal {prefix} placeholder"
        )
