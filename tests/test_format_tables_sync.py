"""Guard the format-spec type tables against hand edits and decoder drift.

``protocol/type_codes.py`` is the single source; ``scripts/sync_format_tables.py``
renders it into the help page and README, decoding every example through the
real parser.  If a test here fails, edit ``TYPE_CODES`` (not a generated
table) and run::

    uv run python scripts/sync_format_tables.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from sync_format_tables import TARGETS, main, render_table, rewrite  # noqa: E402 -- path set above

from termapy.protocol.type_codes import TypeCode  # noqa: E402


def test_generated_tables_are_in_sync():
    """Every generated type table in the help page and README matches TYPE_CODES."""
    assert main(["--check"]) == 0, (
        "a type table was hand-edited or TYPE_CODES changed; "
        "run: uv run python scripts/sync_format_tables.py"
    )


def test_every_target_carries_a_block():
    """Each target file keeps its markers, so no table silently goes back to hand-kept."""
    for path in TARGETS:
        rewrite(path.read_text(encoding="utf-8"), path)  # raises when the markers are gone


def test_example_must_parse_as_its_own_code():
    """A row whose example decodes as a different code fails the render, never prints a wrong row."""
    # Arrange -- U1 parses as U, so it cannot document h.
    bad = (TypeCode("h", "bytes", "Hex, per byte", ("U1",), bytes([0x0A])),)
    # Act / Assert
    with pytest.raises(ValueError, match="not as its row's code"):
        render_table(bad)
