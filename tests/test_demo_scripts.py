"""Every shipped demo script runs clean on the CLI against the demo device (registry ``demo-scripts-run-clean``).

Each ``builtins/demo/run/*.run`` runs as ``termapy --cli --demo --run`` in
its own process, the way a user runs it, and the failing lines it prints
must be exactly the ones it is known to produce: a demonstrated miss
(``expect_test.run`` ends on a timeout on purpose) or a TUI-only command
the CLI's gate refuses by capability (``smoke_test.run`` takes
screenshots).  Found 2026-11-14: ``crc_tour.run`` had spelled ``cmd=`` the
pre-July way for four months and ``smoke_test.run`` opened with a command
that no longer exists; the tour is not in the gold, and nothing ran either.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.builtins import demo as demo_package
from termapy.folders import FOLDER_PATTERNS, RUN
from termapy.plugins.capabilities import refusal_text

pytestmark = [pytest.mark.slow, pytest.mark.rule("demo-scripts-run-clean")]

DEMO_RUN = Path(demo_package.__file__).parent / "run"
SCRIPTS = sorted(DEMO_RUN.glob(FOLDER_PATTERNS[RUN]))

# The failing lines a script prints on purpose, by name: each is the
# script's own demonstration, so it must still appear.
DEMONSTRATED_MISSES: dict[str, tuple[str, ...]] = {
    "expect_test.run": ('Expect "NOPE" timeout after 2s',),  # test 4: a miss aborts the script
}

# A device ``ERROR:`` reply a script provokes on purpose, by name.  Any
# other is a defect the transcript shows: found 2026-11-21, when the two
# bytes a ZMODEM sender ends with headed the device's next command
# (``Unknown command 'OOAT+FS.LIST'``) and this test passed.
DEMONSTRATED_DEVICE_ERRORS: dict[str, tuple[str, ...]] = {
    "at_demo.run": ("Unknown command 'BOGUS'",),
}

# Scripts written for the TUI: the CLI's gate refuses these commands for
# the capability named, and nothing else in the script may fail.
TUI_ONLY: dict[str, tuple[str, tuple[str, ...]]] = {
    "smoke_test.run": ("screen_capture", ("ss.svg", "ss.txt")),
}

_ERROR_LINE = re.compile(r"^\s*Error: (.*)$")
_DEVICE_ERROR_LINE = re.compile(r"^\s*ERROR: (.*)$")

# A protocol byte that leaked to the terminal: a device that speaks when the
# host is not reading.  ZMODEM's headers (ZRQINIT / ZRINIT / ZFIN) start with
# "**", which no device reply or termapy line ever does, so a line beginning
# "**" in a transcript is always a leak -- found by hand when a ZMODEM header
# printed between commands and its last line parsed as a stray command
# (2026-11-14).  A device ``ERROR:`` reply is legitimate (a script may send a
# bad command on purpose), so it is not flagged here.
_LEAKED_PROTOCOL = re.compile(r"^\s*\*\*")


def _run_demo_script(script: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run ``script`` the way a user does: the CLI on the demo device, in a fresh config root."""
    cfg_root = tmp_path / "termapy_cfg"
    cfg_root.mkdir()
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            f"sys.argv = ['termapy', '--cli', '--demo', '--run', {str(script)!r}, "
            f"'--no-color', '--term-width', '120', '--cfg-dir', {str(cfg_root)!r}]; "
            "from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=[script.name for script in SCRIPTS])
def test_a_demo_script_runs_without_an_error(script: Path, tmp_path: Path) -> None:
    """A demo script's failing lines are exactly the ones it demonstrates or the CLI cannot serve."""
    # Arrange
    demonstrated = set(DEMONSTRATED_MISSES.get(script.name, ()))
    allowed = set(demonstrated)
    if script.name in TUI_ONLY:
        capability, commands = TUI_ONLY[script.name]
        allowed |= {refusal_text(command, [capability], "/") for command in commands}

    # Act
    completed = _run_demo_script(script, tmp_path)
    errors = [
        match.group(1)
        for line in completed.stdout.splitlines()
        if (match := _ERROR_LINE.match(line)) is not None
    ]

    leaked = [line for line in completed.stdout.splitlines() if _LEAKED_PROTOCOL.match(line)]
    device_errors = [
        match.group(1)
        for line in completed.stdout.splitlines()
        if (match := _DEVICE_ERROR_LINE.match(line)) is not None
    ]
    demonstrated_device = set(DEMONSTRATED_DEVICE_ERRORS.get(script.name, ()))

    # Assert
    assert completed.returncode == 0, f"{script.name} crashed: {completed.stderr}"
    unexpected = [error for error in errors if error not in allowed]
    assert unexpected == [], f"{script.name} failed on lines it should not: {unexpected}"
    assert demonstrated <= set(errors), f"{script.name} no longer demonstrates its miss"
    unexpected_device = [error for error in device_errors if error not in demonstrated_device]
    assert unexpected_device == [], (
        f"{script.name} drew a device error it does not demonstrate: {unexpected_device}"
    )
    assert demonstrated_device <= set(device_errors), (
        f"{script.name} no longer demonstrates its device error"
    )
    assert leaked == [], (
        f"{script.name} leaked a protocol byte or device error to the transcript "
        f"(a device spoke while the host was not reading): {leaked}"
    )


def test_the_demo_ships_scripts() -> None:
    """The demo folder holds scripts, so an empty glob cannot pass the suite as a clean run."""
    assert len(SCRIPTS) >= 5, f"demo scripts found: {[script.name for script in SCRIPTS]}"
