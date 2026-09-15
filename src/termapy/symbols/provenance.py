"""Is the loaded symbol table still the one the map would produce?

A symbol table is derived data: a linker map goes in, rows come out.  The
map is rebuilt on every compile, so the table goes stale silently, and a
stale table is worse than none -- ``/mem.dump gTemp`` reads a real address
that no longer belongs to ``gTemp``.

Two optional fields on the sidecar answer the two separate questions:

- **witness** -- what ``source`` looked like at import (``mtime`` + ``size``).
  Answers *is this stale*, for one ``os.stat`` and no file read.
- **recipe** -- how the table was produced (``{"converter": "xc32"}``).
  Answers *can termapy fix it*.  Its ABSENCE is meaningful, not missing
  data: a hand-written table has no recipe and never will.

That yields the three honest outcomes in :data:`STATUSES`, and only three.
The third -- stale with no recipe -- is a legitimate permanent state, the
same spirit as a ``data=None`` command: termapy reports what moved and
stops, because the rows came from a person or a model and fabricating a
rebuild would be worse than saying nothing.

**What the witness actually proves.** ``mtime``/``size`` detect that the
map was REBUILT, not that the symbols changed: a no-op recompile reports
stale even when every address is identical.  That is deliberate and the
prose says "rebuilt", never "changed" -- hashing a multi-megabyte map on
every config load to remove a cheap false positive is the wrong trade,
and re-running a converter is cheap and idempotent anyway.

**Nothing here regenerates anything.**  These are pure functions over a
table and the filesystem; the caller renders the verdict.  A terminal
that silently rewrote the symbol table mid-session would be worse than a
yellow line, so the report is the whole feature -- ``/sym.import`` stays
the thing that acts, and the user runs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

# The three outcomes.  Stable strings: they reach agents through
# ``/sym.info`` ``data=`` and must be matchable, not prose.
IN_SYNC: Final[str] = "in_sync"
STALE: Final[str] = "stale"
UNKNOWN: Final[str] = "unknown"

STATUSES: Final[tuple[str, ...]] = (IN_SYNC, STALE, UNKNOWN)


@dataclass(frozen=True, slots=True)
class Staleness:
    """The verdict for one table.

    Attributes:
        status: One of :data:`STATUSES`.  ``unknown`` means the question
            could not be asked -- no witness, or the source is gone --
            never "probably fine".
        reason: One phrase naming what moved, or why nothing could be
            checked.  Empty when in sync.
        fixable: True when a recipe is recorded AND the source is
            readable, so re-running it would work.
        command: The ``/sym.import`` line that rebuilds it, or "" when
            there is no recipe to re-run.
    """

    status: str = UNKNOWN
    reason: str = ""
    fixable: bool = False
    command: str = ""

    @property
    def is_stale(self) -> bool:
        """True only for a positive stale verdict (``unknown`` is not stale)."""
        return self.status == STALE


def make_witness(path: Path) -> dict[str, Any] | None:
    """Record what ``path`` looks like now, or None when it can't be read.

    Args:
        path: The source file being imported.

    Returns:
        ``{"mtime": float, "size": int}``, or None -- an unreadable
        source is recorded as no witness at all, so a later check says
        ``unknown`` instead of comparing against invented numbers.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"mtime": stat.st_mtime, "size": stat.st_size}


def make_recipe(converter: str) -> dict[str, Any]:
    """The recipe for a table produced by a named converter.

    A dict rather than a bare string because the recipe is discriminated:
    ``converter`` is the only kind today, and a future kind adds a key
    instead of changing this one's type.
    """
    return {"converter": converter}


def check_staleness(table: Any, *, prefix: str = "/") -> Staleness:
    """Compare a table's witness against its source on disk.

    Pure apart from one ``stat``: never reads the map, never writes, and
    never rebuilds.

    Args:
        table: The loaded ``SymbolTable``.
        prefix: REPL command prefix, for the suggested rebuild line.

    Returns:
        The verdict.  ``unknown`` whenever the question cannot be asked
        -- no source, no witness, or a source that is gone -- which is a
        real answer and not a failure.
    """
    source = (getattr(table, "source", "") or "").strip()
    witness = getattr(table, "witness", None)
    recipe = getattr(table, "recipe", None)
    command = _rebuild_command(source, recipe, prefix)

    if not source:
        return Staleness(UNKNOWN, "no source recorded")
    if not witness:
        # Hand-written tables and every sidecar written before witnesses
        # existed land here.  Not a defect, and not worth a warning.
        return Staleness(UNKNOWN, "no witness recorded", command=command)

    path = Path(source)
    try:
        stat = path.stat()
    except OSError:
        # A source that names something unreadable -- a hand-written
        # "demo firmware" string, a map on a drive that isn't mounted, a
        # build tree that moved.  Nothing can be concluded either way.
        return Staleness(UNKNOWN, f"source not readable: {source}", command=command)

    moved = _what_moved(witness, stat)
    if not moved:
        return Staleness(IN_SYNC)
    # The BASENAME, not the full path: every surface that shows this also
    # shows ``source`` right above it, and a real map path is long enough
    # to bury the sentence that matters.
    return Staleness(
        STALE,
        f"{path.name} was {moved} since import",
        fixable=bool(command),
        command=command,
    )


def _what_moved(witness: dict[str, Any], stat: Any) -> str:
    """Name the change, or "" when the witness still matches.

    Size is reported before mtime: a different size is proof the content
    changed, while mtime alone only proves the file was written.
    """
    size = witness.get("size")
    if isinstance(size, int) and size != stat.st_size:
        return "rebuilt (size changed)"
    mtime = witness.get("mtime")
    if isinstance(mtime, (int, float)) and not _same_mtime(float(mtime), stat.st_mtime):
        return "rebuilt"
    return ""


def _same_mtime(recorded: float, actual: float) -> bool:
    """Compare mtimes with a tolerance wider than any filesystem's resolution.

    FAT32 stores 2-second granularity and network shares round; a table
    copied between filesystems must not read as stale for a rounding
    artifact.  Two seconds is coarser than any real edit-then-check.
    """
    return abs(recorded - actual) <= 2.0


def _rebuild_command(source: str, recipe: Any, prefix: str) -> str:
    """The ``/sym.import`` line that re-runs the recipe, or "".

    Empty whenever there is nothing to re-run -- no recipe, no source, or
    a recipe kind this version doesn't know.  The caller uses the empty
    string as "not fixable", so this is the one place that decides it.
    """
    if not source or not isinstance(recipe, dict):
        return ""
    converter = recipe.get("converter")
    if not isinstance(converter, str) or not converter:
        return ""
    return f"{prefix}sym.import {source} format={converter}"
