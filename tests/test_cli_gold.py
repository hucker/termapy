"""CLI gold-standard tests - run .run scripts and compare stdout to expected output."""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow  # full CLI subprocess + ~100-command gold compare

GOLD_DIR = Path(__file__).parent / "cli_gold"


def _run_cli_script(
    script_name: str, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    """Run a .run script via CLI mode and return the completed process.

    Returns the whole ``CompletedProcess`` (not just stdout) so the caller
    can assert on the exit code and stderr as well: a run that produces the
    right stdout but crashes late (nonzero exit) or leaks a traceback would
    otherwise slip past a stdout-only comparison.

    Args:
        script_name: Name of the .run file in tests/cli_gold/.
        tmp_path: Temp directory for isolated demo config.

    Returns:
        The completed subprocess (stdout/stderr captured as text).
    """
    script_path = GOLD_DIR / script_name
    return subprocess.run(
        [
            sys.executable, "-c",
            "import sys; "
            f"sys.argv = ['termapy', '--cli', '--demo', "
            f"'--run', {str(script_path)!r}, "
            f"'--no-color', '--term-width', '120', "
            f"'--cfg-dir', {str(tmp_path)!r}]; "
            "from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


_VERBOSE_RE = re.compile(r"^\s*\[\d+/\d+\]")


def _normalize(text: str) -> list[str]:
    """Normalize output for comparison.

    Strips:
    - 'Running script:' lines (path varies by platform/location)
    - Verbose timing lines like '[1/3] AT (0.015s)' (nondeterministic)
    - Absolute paths replaced with <CFG_DIR>/demo/
    - Trailing whitespace
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        # Skip platform-dependent and nondeterministic lines
        if stripped.startswith("Running script:"):
            continue
        if stripped.startswith("Script") and "done (" in stripped:
            continue
        if _VERBOSE_RE.match(line):
            continue
        lines.append(line.rstrip())
    # Remove trailing empty lines
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _assert_gold(script_name: str, expected_name: str, tmp_path: Path) -> None:
    """Run a script and compare output to a gold file.

    Args:
        script_name: .run file in tests/cli_gold/.
        expected_name: .expected file in tests/cli_gold/.
        tmp_path: Temp directory for isolated demo config.
    """
    # Act
    result = _run_cli_script(script_name, tmp_path)

    # A clean gold run exits 0 and writes nothing to stderr; check both
    # before the stdout diff so a late crash or a leaked traceback is
    # reported as itself instead of hiding behind a confusing output diff.
    assert result.returncode == 0, (
        f"CLI exited {result.returncode} (expected 0); "
        f"stderr:\n{result.stderr}"
    )
    assert "Traceback (most recent call last)" not in result.stderr, (
        f"CLI leaked a traceback to stderr:\n{result.stderr}"
    )

    actual_text = result.stdout
    actual = _normalize(actual_text)

    # Expected
    expected_path = GOLD_DIR / expected_name
    expected = _normalize(expected_path.read_text(encoding="utf-8"))

    # Assert
    if actual != expected:
        diff = difflib.unified_diff(
            expected, actual,
            fromfile=f"expected ({expected_name})",
            tofile="actual",
            lineterm="",
        )
        diff_text = "\n".join(diff)

        # Forensic dump: save raw + normalized + diff so an intermittent
        # failure leaves evidence behind. Path is stable across runs so the
        # most recent failure always overwrites.
        failures_dir = GOLD_DIR / "_failures"
        failures_dir.mkdir(exist_ok=True)
        (failures_dir / f"{script_name}.actual_raw.txt").write_text(
            actual_text, encoding="utf-8"
        )
        (failures_dir / f"{script_name}.actual.txt").write_text(
            "\n".join(actual), encoding="utf-8"
        )
        (failures_dir / f"{script_name}.expected.txt").write_text(
            "\n".join(expected), encoding="utf-8"
        )
        (failures_dir / f"{script_name}.diff.txt").write_text(
            diff_text, encoding="utf-8"
        )

        raise AssertionError(
            f"CLI output does not match gold file.\n"
            f"Forensic artifacts written to {failures_dir}\n\n{diff_text}"
        )


class TestCliGold:

    def test_cli_basic(self, tmp_path):
        """Run cli_test.run and compare to cli_test.expected."""
        _assert_gold("cli_test.run", "cli_test.expected", tmp_path)
