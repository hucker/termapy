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

# JSON envelopes carry wall-clock timing; mask it so the json-mode gold
# section stays deterministic.  Everything else in an envelope is exact.
_ELAPSED_RE = re.compile(r'"elapsed_s": \d+(?:\.\d+)?')

# File listings (/run.list, /proto.list) show each file's size.  The demo
# files are copied from the checkout, and a checkout is CRLF on Windows
# (core.autocrlf) but LF on Linux CI, so the byte counts differ per
# platform.  Mask the size column INCLUDING the padding in front of it (the
# column is right-aligned, so "1.0 KB" and "990 B" pad differently); the
# age column stays literal because --demo writes the files seconds before
# the listing runs ("just now").
_SIZE_RE = re.compile(r"\s+\d+(?:\.\d)? (?:B|KB|MB)\b")


def _normalize(text: str) -> list[str]:
    """Normalize output for comparison.

    Strips:
    - 'Running script:' lines (path varies by platform/location)
    - Verbose timing lines like '[1/3] AT (0.015s)' (nondeterministic)
    - ``"elapsed_s": <n>`` inside JSON envelopes -> ``"elapsed_s": 0``
    - File sizes in listings (``1.2 KB``) -> ``<SIZE>`` (CRLF/LF checkouts differ)
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
        line = _ELAPSED_RE.sub('"elapsed_s": 0', line)
        line = _SIZE_RE.sub("  <SIZE>", line)
        lines.append(line.rstrip())
    lines = _sort_listing_runs(lines)
    # Remove trailing empty lines
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _sort_listing_runs(lines: list[str]) -> list[str]:
    """Sort each run of consecutive masked listing lines by name.

    Listings are newest-first, but --demo writes every demo file in one
    burst, so their relative mtimes are filesystem timing noise (the order
    changed between two consecutive runs on NTFS).  On the information the
    listing actually shows -- every age is "just now" -- they tie, so the
    gold asserts the SET of lines in each listing, not their order.
    """
    out: list[str] = []
    run: list[str] = []
    for line in lines:
        if "<SIZE>" in line:
            run.append(line)
            continue
        out.extend(sorted(run))
        run = []
        out.append(line)
    out.extend(sorted(run))
    return out


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
