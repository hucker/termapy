"""The ``--require-hardware`` gate: a run that demands the loopback rig fails without it.

``release_prep`` passes ``--require-hardware`` so a release cannot read green
on a machine where the bench is unplugged, while plain ``--run-hardware``
keeps skipping so a developer run on a machine that is not the rig stays
green.  Both are driven as real child pytest runs aimed at a serial number no
adapter carries: ``resolve_port`` only enumerates, so no port is opened and
nothing is written to any device.  These live outside
``test_hardware_loopback.py`` because that module is deselected unless a
hardware flag is passed, and this guard must run in every suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
_NO_SUCH_ADAPTER = "TERMAPY-NO-SUCH-ADAPTER"


def _run_loopback_suite(flag: str) -> subprocess.CompletedProcess[str]:
    """Run the loopback suite in a child pytest, aimed at an adapter that does not exist."""
    env = {**os.environ, "TERMAPY_LOOPBACK_SN": _NO_SUCH_ADAPTER, "COLUMNS": "200"}
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", str(TESTS / "test_hardware_loopback.py"),
            flag, "-m", "hardware", "-n", "0", "--no-cov", "-p", "no:cacheprovider", "-q", "-ra",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=TESTS.parent,
        timeout=180,
        check=False,
    )


@pytest.mark.slow
def test_require_hardware_fails_without_the_rig():
    """hardware-demanded-fails-loud: under --require-hardware a missing adapter fails the run and names the adapter."""
    # Act
    result = _run_loopback_suite("--require-hardware")

    # Assert
    assert result.returncode == 1, (
        f"a run that demanded the rig must not read green without it:\n{result.stdout[-2000:]}"
    )
    assert _NO_SUCH_ADAPTER in result.stdout, "the failure names the serial number it looked for"
    assert "skipped" not in result.stdout.splitlines()[-1], "no test may quietly skip under the demand"


@pytest.mark.slow
def test_run_hardware_still_skips_without_the_rig():
    """Plain --run-hardware on a machine that is not the rig skips, so developer runs elsewhere stay green."""
    # Act
    result = _run_loopback_suite("--run-hardware")

    # Assert
    assert result.returncode == 0, f"an absent rig is a skip, not a failure:\n{result.stdout[-2000:]}"
    assert "skipped" in result.stdout.splitlines()[-1], "the summary reports the skips"
