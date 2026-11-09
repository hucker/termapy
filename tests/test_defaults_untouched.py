"""A test that leaves ``DEFAULT_CFG`` changed fails, and the next test sees the real defaults.

The check is the autouse fixture ``_defaults_untouched`` in ``conftest.py``.
A fixture that fails a test cannot be proved from inside the run it guards,
so this drives a child pytest run: its conftest binds the real fixture, one
of its tests changes a default the way a shallow-copied rig did, and the
next one reads the default back.  The child is a separate process, so this
run's ``DEFAULT_CFG`` is never touched.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.rule("defaults-untouched")

_CONFTEST = Path(__file__).parent / "conftest.py"

# The child's conftest: load this suite's conftest under another name (the
# child's own module is ``conftest``) and bind only the fixture under test.
_CHILD_CONFTEST = f"""\
import importlib.util

_spec = importlib.util.spec_from_file_location("termapy_suite_conftest", {str(_CONFTEST)!r})
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)
_defaults_untouched = _suite._defaults_untouched
"""

_CHILD_TESTS = """\
from termapy.defaults import DEFAULT_CFG


def test_a_rig_changes_a_default():
    DEFAULT_CFG["json_fmt"]["indent"] = 4


def test_the_next_test_reads_the_default():
    assert DEFAULT_CFG["json_fmt"]["indent"] == 2
"""


@pytest.mark.rule("defaults-untouched")
@pytest.mark.slow
def test_a_test_that_changes_the_defaults_fails(tmp_path):
    """Changing DEFAULT_CFG fails that test, naming the section, and the next test sees the default."""
    # Arrange
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(_CHILD_CONFTEST, encoding="utf-8")
    (tmp_path / "test_child.py").write_text(_CHILD_TESTS, encoding="utf-8")

    # Act
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rE", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    out = result.stdout

    # Assert
    assert "2 passed, 1 error" in out, (
        f"the change errors that test, the next passes:\n{out[-2000:]}"
    )
    assert "ERROR at teardown of test_a_rig_changes_a_default" in out, "the culprit is named"
    assert "changed DEFAULT_CFG (json_fmt)" in out, "the message names the section"
