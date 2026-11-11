"""Guard ARCHITECTURE.md's generated module tree, test table and marker table against hand edits and drift.

``scripts/sync_architecture.py`` writes the first two from the docstrings
and the marker table from pyproject.toml's marker registrations.  If a test
here fails, fix the docstring or the registration (not the doc) and run::

    uv run python scripts/sync_architecture.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from sync_architecture import (  # noqa: E402 -- path set above
    DOC,
    describe,
    main,
    rewrite,
    tracked_counts,
)


@pytest.mark.rule("architecture-generated")
def test_generated_blocks_are_in_sync():
    """The module tree, test table and marker table match the sources they are generated from."""
    assert main(["--check"]) == 0, (
        "ARCHITECTURE.md was hand-edited, or a docstring or marker registration changed; "
        "run: uv run python scripts/sync_architecture.py"
    )


@pytest.mark.rule("architecture-generated")
def test_every_block_keeps_its_markers():
    """Deleting a block's markers fails the sync, so none goes back to hand-kept silently."""
    # Arrange
    text = DOC.read_text(encoding="utf-8")

    # Act / Assert
    for name in ("module-tree", "test-table", "marker-table"):
        with pytest.raises(ValueError, match=f"{name}:start"):
            rewrite(text.replace(f"<!-- {name}:end -->", ""))


@pytest.mark.rule("architecture-generated")
def test_an_identifier_only_docstring_is_refused(tmp_path):
    """A module whose docstring opens with only a class name fails the sync instead of printing a bare row."""
    # Arrange
    module = tmp_path / "bare.py"
    module.write_text('"""CfgConfirm."""\n', encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match="no description to show"):
        describe(module, "dialogs/bare.py")


@pytest.mark.rule("architecture-generated")
def test_the_line_counts_stay_release_preps():
    """A tracked row keeps the count the doc has now; the sync never recounts a row that has one."""
    # Arrange -- a count no file has, planted on app.py's row
    text = re.sub(r"(├── app\.py\s+# \()\d+", r"\g<1>12345", DOC.read_text(encoding="utf-8"))

    # Act
    actual = tracked_counts(text)["app.py"]

    # Assert
    assert actual == 12345, (
        "the number already in the doc wins, so a feature branch never churns it"
    )
