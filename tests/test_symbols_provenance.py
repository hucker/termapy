"""Unit tests for the symbol-table staleness verdict.

Pure functions over a table plus one ``stat``, so these build tables
directly rather than going through the dispatcher; the command-level
behavior lives in ``test_sym_commands.py::TestStaleness``.
"""

from __future__ import annotations

import os
import time

import pytest

from termapy.symbols import (
    IN_SYNC,
    STALE,
    UNKNOWN,
    Symbol,
    SymbolTable,
    check_staleness,
    make_recipe,
    make_witness,
)

ROWS = [Symbol("gTemp", 0x1000, 2, "bss")]


@pytest.fixture
def map_file(tmp_path):
    """A stand-in linker map with known size."""
    path = tmp_path / "mem.map"
    path.write_text("x" * 100, encoding="utf-8")
    return path


def _table(map_file, *, recipe=True, witness=True, source=None):
    """A table whose provenance points at ``map_file``."""
    return SymbolTable(
        ROWS,
        source=str(map_file) if source is None else source,
        recipe=make_recipe("xc32") if recipe else None,
        witness=make_witness(map_file) if witness else None,
    )


def _age(path, seconds: float) -> None:
    """Move a file's mtime well past the comparison tolerance."""
    when = time.time() + seconds
    os.utime(path, (when, when))


class TestInSync:

    def test_untouched_source_is_in_sync(self, map_file):
        # Arrange
        table = _table(map_file)

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == IN_SYNC, "an untouched map is in sync"
        assert verdict.reason == "", "in sync carries no complaint"
        assert verdict.is_stale is False, "in sync is not stale"

    def test_mtime_drift_inside_tolerance_is_in_sync(self, map_file):
        """A sub-second filesystem rounding artifact is not a rebuild."""
        # Arrange
        table = _table(map_file)
        _age(map_file, 1.0)  # inside the 2s tolerance

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == IN_SYNC, "rounding drift must not read as a rebuild"


class TestStale:

    def test_touched_source_is_stale_and_fixable(self, map_file):
        # Arrange
        table = _table(map_file)
        _age(map_file, 30.0)

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == STALE, "a rebuilt map is stale"
        assert verdict.is_stale is True, "the convenience property agrees"
        assert verdict.fixable is True, "a recorded converter makes it fixable"
        assert "rebuilt" in verdict.reason, "the reason says what happened"

    def test_rebuild_command_re_runs_the_recipe(self, map_file):
        # Arrange
        table = _table(map_file)
        _age(map_file, 30.0)

        # Act
        verdict = check_staleness(table)

        # Assert
        expected = f"/sym.import {map_file} format=xc32"
        assert verdict.command == expected, "the command re-runs the recorded converter"

    def test_command_honors_the_repl_prefix(self, map_file):
        """The suggestion must be typeable in a session with a custom prefix."""
        # Arrange
        table = _table(map_file)
        _age(map_file, 30.0)

        # Act
        verdict = check_staleness(table, prefix="!")

        # Assert
        assert verdict.command.startswith("!sym.import"), "the active prefix is used"

    def test_size_change_is_named_separately(self, map_file):
        """A different size proves content changed; mtime alone does not."""
        # Arrange
        table = _table(map_file)
        map_file.write_text("y" * 250, encoding="utf-8")

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == STALE, "a resized map is stale"
        assert "size changed" in verdict.reason, "the reason distinguishes a size change"

    def test_stale_without_a_recipe_is_not_fixable(self, map_file):
        """The third honest outcome: out of sync, and termapy cannot fix it."""
        # Arrange
        table = _table(map_file, recipe=False)
        _age(map_file, 30.0)

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == STALE, "the witness still detects the rebuild"
        assert verdict.fixable is False, "no recipe means termapy cannot rebuild it"
        assert verdict.command == "", "no command is fabricated for a table it cannot rebuild"


class TestUnknown:
    """``unknown`` is a real answer -- never 'probably fine'."""

    def test_no_witness_is_unknown_not_stale(self, map_file):
        """Every sidecar written before witnesses existed lands here."""
        # Arrange
        table = _table(map_file, witness=False)

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == UNKNOWN, "nothing to compare against"
        assert verdict.is_stale is False, "unknown must not warn like stale"

    def test_hand_written_source_is_unknown(self, tmp_path):
        """A source that is prose, not a path -- the demo table's shape."""
        # Arrange
        table = SymbolTable(ROWS, source="demo firmware (hand-written)")

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == UNKNOWN, "prose sources cannot be checked"
        assert verdict.is_stale is False, "a hand-written table is not 'stale'"

    def test_deleted_source_is_unknown_not_stale(self, map_file):
        """A map on an unmounted drive proves nothing about the table."""
        # Arrange
        table = _table(map_file)
        map_file.unlink()

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == UNKNOWN, "an unreadable source cannot be compared"
        assert "not readable" in verdict.reason, "the reason says why"

    def test_no_source_is_unknown(self):
        # Arrange
        table = SymbolTable(ROWS)

        # Act
        verdict = check_staleness(table)

        # Assert
        assert verdict.status == UNKNOWN, "a table with no source cannot be checked"


class TestRoundTrip:
    """Provenance survives save/load, and its absence stays absent."""

    def test_provenance_round_trips_through_the_file(self, map_file, tmp_path):
        # Arrange
        table = _table(map_file)
        dest = tmp_path / "sym" / "rig.symbols.json"

        # Act
        table.save(dest)
        loaded = SymbolTable.load(dest)

        # Assert
        assert loaded.recipe == {"converter": "xc32"}, "the recipe survives a round trip"
        assert loaded.witness == table.witness, "the witness survives a round trip"
        assert check_staleness(loaded).status == IN_SYNC, "the verdict survives too"

    def test_absent_provenance_is_not_written(self, tmp_path):
        """A table with no provenance round-trips without the new keys."""
        # Arrange
        table = SymbolTable(ROWS, source="hand-written")
        dest = tmp_path / "sym" / "rig.symbols.json"

        # Act
        table.save(dest)
        text = dest.read_text(encoding="utf-8")

        # Assert
        assert "recipe" not in text, "no empty recipe key is written"
        assert "witness" not in text, "no empty witness key is written"

    def test_malformed_provenance_fails_field_qualified(self, tmp_path):
        """Consistent with every other field: name the offender."""
        # Arrange
        dest = tmp_path / "bad.json"
        dest.write_text(
            '{"symbols_version": 1, "symbols": [], "recipe": "xc32"}', encoding="utf-8",
        )

        # Act / Assert
        with pytest.raises(ValueError, match="recipe"):
            SymbolTable.load(dest)
