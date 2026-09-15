"""Folder and file constants -- single source of truth.

Pure constants with no Textual or serial dependencies, plus the two
operations that create and remove a per-config data folder.  Import
freely from any module.

A data folder exists while something is in it, and not otherwise.
Reads never create one: listing an absent ``cap/`` is "empty", not an
error.  Writes always create one: every code path that puts a file in a
data folder, or opens the folder in the file manager so the user can
drop a file in, goes through :func:`ensure_folder`.  Empty ones are
removed by :func:`prune_empty_folders` at config load and at app stop
(``ReplEngine.fire_lifecycle``), so a config that never captures or
scripts holds just its cfg, log, history and report.  ``--demo`` ships
the four folders it populates (``run/ proto/ plugin/ sym/``).  This is a
rule, not a migration: it holds for the folder ``/cap.clear`` empties
next month as much as for one an older termapy created eagerly.
``tests/test_architecture.py`` keeps a raw ``mkdir`` off data folders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass(frozen=True)
class FolderSpec:
    """Definition of a per-config data folder.

    Defaults represent the common ("standard browsable folder") case:
    user-facing, contents safe to inspect/export, NOT safe to wipe.
    Override flags only when an entry departs from that shape.

    Attributes:
        name: Folder name under the cfg dir.
        ext: File extension that identifies entries (``.run``, ``.py``,
            ...).  Use ``"*"`` for mixed-content folders (``ss``, ``cap``).
        clearable: True when ``/<name>.clear`` may safely wipe contents.
            Default False -- destructive ops are explicit opt-in.
        showable: True when the folder appears in user-facing listings
            (``/help``, ``/cfg.info``, the title-bar buttons).  Default
            True -- override to False for an internal/secret folder.
        dumpable: True when ``/<name>.dump`` may export folder contents.
            Default True -- override to False for folders whose contents
            shouldn't leave the session (e.g. screenshots = binary
            artifacts, not exportable as text).
    """

    name: str
    ext: str
    clearable: bool = False
    showable: bool = True
    dumpable: bool = True

    @property
    def pattern(self) -> str:
        """Glob pattern derived from extension."""
        return f"*{self.ext}" if self.ext != "*" else "*"


# Per-config data folders -- the master list.  Everything else derives
# from this.  Convention: a folder with no flags is a standard browsable
# folder (showable + dumpable, not clearable).  Add ``clearable=True``
# for folders safe to wipe; add ``dumpable=False`` when contents are
# binary / shouldn't be exported.
FOLDERS = [
    FolderSpec("run",    ".run"),
    FolderSpec("proto",  ".pro"),
    FolderSpec("plugin", ".py"),
    FolderSpec("ss",     "*",    clearable=True, dumpable=False),
    FolderSpec("viz",    ".py"),
    FolderSpec("cap",    "*",    clearable=True),
    FolderSpec("prof",   ".csv", clearable=True),
    # Symbol tables (*.symbols.json) and, later, user converter modules.
    FolderSpec("sym",    "*"),
]

# -- Derived from FOLDERS (do not edit manually) ------------------------------

# Named folder constants -- for use in imports instead of bare strings.
# These are derived from FOLDERS so the name string is defined exactly once.
_BY_NAME = {f.name: f for f in FOLDERS}
RUN = _BY_NAME["run"].name
PROTO = _BY_NAME["proto"].name
PLUGIN = _BY_NAME["plugin"].name
SS = _BY_NAME["ss"].name
VIZ = _BY_NAME["viz"].name
CAP = _BY_NAME["cap"].name
PROF = _BY_NAME["prof"].name
SYM = _BY_NAME["sym"].name

# All folder names as a tuple
FOLDER_NAMES = tuple(f.name for f in FOLDERS)

# Folder name -> glob pattern
FOLDER_PATTERNS = {f.name: f.pattern for f in FOLDERS}

# File extension -> folder name (first folder wins for shared extensions)
EXT_TO_FOLDER = {}
for _f in FOLDERS:
    if _f.ext != "*" and _f.ext not in EXT_TO_FOLDER:
        EXT_TO_FOLDER[_f.ext] = _f.name

# Capability sets
CLEARABLE = frozenset(f.name for f in FOLDERS if f.clearable)
SHOWABLE = frozenset(f.name for f in FOLDERS if f.showable)
DUMPABLE = frozenset(f.name for f in FOLDERS if f.dumpable)

# -- Migration ----------------------------------------------------------------

FOLDER_MIGRATIONS = [
    ("captures", "cap"),
    ("scripts", "run"),
    ("plugins", "plugin"),
]

# -- Special filenames --------------------------------------------------------

# Per-config history lives NEXT TO the config file as <stem>.history;
# HISTORY_FILE is only the no-config fallback name (in the cfg root).
HISTORY_FILE = ".cmd_history.txt"
HISTORY_SUFFIX = ".history"
LOG_SUFFIX = ".log"
# The /cfg.info project report, also written at exit.
INFO_REPORT_SUFFIX = ".md"
# The v2 device profile the MCP host auto-loads by convention.
PROFILE_SUFFIX = ".profile.json"
# The symbol table, in the sym/ folder (it lived beside the cfg until
# 2026-09; cfg_data_dir migrates it on first load).
SYMBOLS_SUFFIX = ".symbols.json"
SEQ_FILE = ".cap_seq"
PROFILE_TMP_GLOB = "_profile_tmp_*.run"

# -- Stem-named sidecars ------------------------------------------------------

# Every file named after the config's stem, with the folder it lives in
# (None = beside the .cfg).  ONE table: rename_config carries each of these
# to the new name, and cfg_data_dir moves any that has a folder out of the
# root on first load.  A sidecar missing from here is a sidecar the rename
# will miss -- which is how <stem>.log got left behind.
SIDECARS: tuple[tuple[str, str | None], ...] = (
    (HISTORY_SUFFIX, None),
    (LOG_SUFFIX, None),
    (INFO_REPORT_SUFFIX, None),
    (PROFILE_SUFFIX, None),
    (SYMBOLS_SUFFIX, SYM),
)

# -- Lifecycle: how a data folder comes and goes ------------------------------


def ensure_folder(folder: Path) -> Path:
    """Create ``folder`` (and any missing parents) and return it.

    The ONE way a data folder comes into being.  Call it at the write,
    never at a read: ``ensure_folder(ctx.fs.cap_dir) / name``.
    """
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def prune_empty_folders(root: Path, names: Iterable[str]) -> list[str]:
    """Remove each ``root/<name>`` that is a folder with nothing in it.

    The ONE way a data folder goes away.  Only the given ``names``
    (``FOLDER_NAMES`` for a config folder, ``(PLUGIN,)`` for the cfg
    root), so a folder the user made is never touched, and only when
    truly empty: a lone dotfile such as ``.gitkeep`` keeps it.  A folder
    that cannot be removed (in use, permissions) is left alone; this is
    non-critical file I/O.

    Args:
        root: The folder holding the data folders.
        names: Data folder names to consider.

    Returns:
        The names removed, for tests and callers that report.
    """
    removed: list[str] = []
    for name in names:
        folder = root / name
        try:
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                removed.append(name)
        except OSError:
            continue
    return removed
