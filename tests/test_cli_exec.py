"""End-to-end tests for ``--exec`` / ``-e`` one-shot CLI mode.

These are subprocess-spawning tests (mirroring ``test_cli_gold.py`` and
``test_cli_prefix.py``) because the behavior we care about -- exit
code, stdout shape, autorun suppression -- only manifests through the
real entry point.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.slow  # subprocess spawn + full app boot


def _run(*extra_args: str, cfg_dir: str) -> subprocess.CompletedProcess:
    """Spawn ``termapy --cli --demo`` with extra args.  Captures both
    streams and returns the CompletedProcess for the caller to inspect
    ``returncode``, ``stdout``, ``stderr``."""
    return subprocess.run(
        [
            sys.executable, "-c",
            "import sys; "
            f"sys.argv = ['termapy', '--cli', '--demo', '--no-color', "
            f"'--cfg-dir', {cfg_dir!r}, "
            + ", ".join(repr(extra_arg) for extra_arg in extra_args)
            + "]; from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCliExec:
    """``--exec``/``-e`` runs one command, prints to stdout, exits."""

    def test_exec_success_exits_zero(self, tmp_path):
        # Arrange / Act -- /help port is a deterministic local command
        # with multi-line output.  Always succeeds.
        result = _run("--exec", "/help port", cfg_dir=str(tmp_path))

        # Assert
        actual_code = result.returncode
        expected_code = 0
        assert actual_code == expected_code, (
            f"--exec /help port should exit 0, got {actual_code}; "
            f"stderr={result.stderr!r}"
        )
        assert "port" in result.stdout.lower(), (
            f"stdout should mention port help, got {result.stdout!r}"
        )

    def test_exec_unknown_command_exits_one(self, tmp_path):
        # Arrange / Act -- a command that doesn't exist.  Dispatch
        # returns CmdResult.fail, exec mode maps that to exit code 1.
        result = _run("--exec", "/notacommand", cfg_dir=str(tmp_path))

        # Assert
        actual_code = result.returncode
        expected_code = 1
        assert actual_code == expected_code, (
            f"--exec on unknown command should exit 1, got {actual_code}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_exec_and_run_are_mutually_exclusive(self, tmp_path):
        # Arrange / Act -- argparse-level mutual exclusion enforced
        # in entry.py.  Should reject before doing anything.
        result = _run(
            "--run", "any.run", "--exec", "AT+VER",
            cfg_dir=str(tmp_path),
        )

        # Assert
        actual_code = result.returncode
        assert actual_code != 0, (
            f"--run + --exec must reject, got exit 0; stderr={result.stderr!r}"
        )
        combined = (result.stdout + result.stderr).lower()
        assert "mutually exclusive" in combined, (
            f"error message should call out mutual exclusion, got "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
