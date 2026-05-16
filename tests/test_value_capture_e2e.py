"""End-to-end check that handlers populate ``CmdResult.value``.

Runs ``tests/fixtures/value_capture.run`` through a real CLI
subprocess.  The script uses ``/var.capture <name> /<cmd> ...`` to
dispatch each handler with the ``.silent`` modifier and stash the
returned ``CmdResult.value``, then prints ``label=$(name)`` lines.

The test asserts each ``label=expected_value`` pair appears in
stdout.  If a handler regresses to a bare ``CmdResult.ok()``, the
captured variable is empty and the corresponding line breaks --
this is the failing-test the per-handler unit tests should also
catch, surfaced as an integration check that closely mirrors how
an LLM or human would script against the value plumbing.

Marked ``slow`` because subprocess startup dominates the runtime.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).parent / "fixtures" / "value_capture.run"


def _run(tmp_path: Path) -> str:
    """Invoke the CLI on the fixture script and return stdout."""
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; "
            f"sys.argv = ['termapy', '--cli', '--demo', "
            f"'--run', {str(FIXTURE)!r}, "
            f"'--no-color', '--term-width', '120', "
            f"'--cfg-dir', {str(tmp_path)!r}]; "
            "from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


# Expected captured values.  ``''`` (empty) means the script
# prints ``label=`` with nothing after the equals sign -- which is
# the correct behavior for ``/seq`` immediately after a reset.
EXPECTED_CAPTURES = [
    ("seq.empty", ""),
    ("seq.reset", "reset"),
    ("var.set", "bar"),
    ("var.get", "bar"),
    # /var.clear returns len(_VARS) before clearing.  At that point
    # the dict holds FOO + ALPHA + BETA plus the two capture-vars
    # /var.capture leaked into it (v_var_set, v_var_get).  Five.
    ("var.clear", "5"),
    ("env.set", "hello_world"),
    ("env.list", "hello_world"),
    ("cfg.get", "115200"),
    ("cfg.auto", "9600"),
    ("cfg.get2", "9600"),
    ("port.set", "COM99"),
    # /port.disconnect snapshots cfg["port"] before tearing the
    # connection down.  After /port COM99 above, the configured
    # name is COM99, so that's what scripting captures.
    ("port.disconnect", "COM99"),
]


class TestValueCaptureE2E:

    def test_every_handler_populates_cmdresult_value(self, tmp_path):
        # Act
        stdout = _run(tmp_path)

        # Assert
        lines = stdout.splitlines()
        for label, expected in EXPECTED_CAPTURES:
            needle = f"{label}={expected}"
            assert any(needle in line for line in lines), (
                f"Missing capture line {needle!r} in script output.\n"
                f"This usually means /{label.split('.')[0]} regressed to a "
                f"bare CmdResult.ok() and its .value is now None.\n"
                f"Full stdout:\n{stdout}"
            )

    def test_script_runs_to_completion(self, tmp_path):
        # Act
        stdout = _run(tmp_path)

        # Assert -- guards against the script bailing midway
        # (a regression that breaks /var.capture would short-circuit
        # the rest of the run; we want a single clear test for "the
        # script reached the end").
        assert "BEGIN value-capture e2e" in stdout, "start banner missing"
        assert "END value-capture e2e" in stdout, (
            "end banner missing -- script aborted before completion"
        )
