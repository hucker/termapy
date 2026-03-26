"""CLI gold-standard tests — run .run scripts and compare stdout to expected output."""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

GOLD_DIR = Path(__file__).parent / "cli_gold"


def _run_cli_script(script_name: str, tmp_path: Path) -> str:
    """Run a .run script via CLI mode and return stdout.

    Args:
        script_name: Name of the .run file in tests/cli_gold/.
        tmp_path: Temp directory for isolated demo config.

    Returns:
        Captured stdout as a string with normalized line endings.
    """
    script_path = GOLD_DIR / script_name
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; "
            f"sys.argv = ['termapy', '--cli', '--demo', "
            f"'--run', {str(script_path)!r}, "
            f"'--no-color', '--term-width', '120', "
            f"'--cfg-dir', {str(tmp_path)!r}]; "
            "from termapy.app import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


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
    actual_text = _run_cli_script(script_name, tmp_path)
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
        raise AssertionError(
            f"CLI output does not match gold file.\n\n{diff_text}"
        )


class TestCliGold:

    def test_cli_basic(self, tmp_path):
        """Run cli_test.run and compare to cli_test.expected."""
        _assert_gold("cli_test.run", "cli_test.expected", tmp_path)
