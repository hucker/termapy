"""The hw_loopback gate: a demanded run fails loud, and nothing runs on GitHub Actions.

``release_prep`` passes ``--require-hw-loopback`` so a release cannot read green
on a machine where the bench is unplugged, while plain ``--run-hw-loopback``
keeps skipping so a developer run on a machine that is not the rig stays
green.  On GitHub Actions both flags are refused before collection, so the
hw_loopback suite never runs there in any form; ``sw_loopback`` tests
(pyserial's ``loop://``) are not hardware and are not affected.  Each is driven as a real child pytest
run aimed at a serial number no adapter carries: ``resolve_port`` only
enumerates, so no port is opened and nothing is written to any device.

The two bench-behavior tests are themselves skipped on GitHub Actions: their
child runs pass an hw_loopback flag, which is exactly what is refused there, and
running them anyway would enumerate the runner's ports.  The refusal test
sets ``GITHUB_ACTIONS`` in its own child, so it runs everywhere.  These live
outside ``test_hw_loopback.py`` because that module is deselected
unless an hw_loopback flag is passed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
_NO_SUCH_ADAPTER = "TERMAPY-NO-SUCH-ADAPTER"

# Presence is GitHub's signal (see conftest.pytest_configure).
_bench_only = pytest.mark.skipif(
    "GITHUB_ACTIONS" in os.environ,
    reason="passes an hw_loopback flag, which GitHub Actions refuses by design",
)


def _run_hw_loopback_suite(
    flag: str, *, on_github: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run the hw_loopback suite in a child pytest, aimed at an adapter that does not exist.

    ``on_github`` sets ``GITHUB_ACTIONS`` in the child, the way a runner does.
    """
    env = {**os.environ, "TERMAPY_HW_LOOPBACK_SN": _NO_SUCH_ADAPTER, "COLUMNS": "200"}
    if on_github:
        env["GITHUB_ACTIONS"] = "true"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(TESTS / "test_hw_loopback.py"),
            flag,
            "-m",
            "hw_loopback",
            "-n",
            "0",
            "--no-cov",
            "-p",
            "no:cacheprovider",
            "-q",
            "-ra",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=TESTS.parent,
        timeout=180,
        check=False,
    )


@pytest.mark.slow
@pytest.mark.parametrize("flag", ["--run-hw-loopback", "--require-hw-loopback"])
def test_hw_loopback_flags_are_refused_on_github_actions(flag):
    """hw-loopback-never-on-github: on GitHub Actions either hw_loopback flag stops the session before any test is collected."""
    # Act
    result = _run_hw_loopback_suite(flag, on_github=True)

    # Assert
    assert result.returncode == 4, (
        f"pytest's usage-error exit, before collection:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
    )
    assert "never runs on GitHub Actions" in result.stderr, "the refusal says why"
    assert "collected" not in result.stdout, (
        "nothing was collected, so no fixture and no port enumeration ran"
    )


@_bench_only
@pytest.mark.slow
def test_require_hw_loopback_fails_without_the_rig():
    """hw-loopback-demanded-fails-loud: under --require-hw-loopback a missing adapter fails the run and names the adapter."""
    # Act
    result = _run_hw_loopback_suite("--require-hw-loopback")

    # Assert
    assert result.returncode == 1, (
        f"a run that demanded the rig must not read green without it:\n{result.stdout[-2000:]}"
    )
    assert _NO_SUCH_ADAPTER in result.stdout, "the failure names the serial number it looked for"
    assert "skipped" not in result.stdout.splitlines()[-1], (
        "no test may quietly skip under the demand"
    )


@_bench_only
@pytest.mark.slow
def test_run_hw_loopback_still_skips_without_the_rig():
    """Plain --run-hw-loopback on a machine that is not the rig skips, so developer runs elsewhere stay green."""
    # Act
    result = _run_hw_loopback_suite("--run-hw-loopback")

    # Assert
    assert result.returncode == 0, (
        f"an absent rig is a skip, not a failure:\n{result.stdout[-2000:]}"
    )
    assert "skipped" in result.stdout.splitlines()[-1], "the summary reports the skips"
