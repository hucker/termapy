"""Folder and file constants -- single source of truth.

Pure constants with no Textual or serial dependencies.
Import freely from any module.
"""

from __future__ import annotations

from dataclasses import dataclass


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
SEQ_FILE = ".cap_seq"
PROFILE_TMP_GLOB = "_profile_tmp_*.run"
