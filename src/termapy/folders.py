"""Folder and file constants -- single source of truth.

Pure constants with no Textual or serial dependencies.
Import freely from any module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FolderSpec:
    """Definition of a per-config data folder."""

    name: str
    ext: str
    clearable: bool = False
    showable: bool = False
    dumpable: bool = False

    @property
    def pattern(self) -> str:
        """Glob pattern derived from extension."""
        return f"*{self.ext}" if self.ext != "*" else "*"


# Per-config data folders -- the master list.
# Everything else derives from this.
FOLDERS = [
    FolderSpec("run", ".run", showable=True, dumpable=True),
    FolderSpec("proto", ".pro", showable=True, dumpable=True),
    FolderSpec("plugin", ".py", showable=True, dumpable=True),
    FolderSpec("ss", "*", showable=True, clearable=True),
    FolderSpec("viz", ".py", showable=True, dumpable=True),
    FolderSpec("cap", "*", showable=True, dumpable=True, clearable=True),
    FolderSpec("prof", ".csv", showable=True, dumpable=True, clearable=True),
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

HISTORY_FILE = ".cmd_history.txt"
SEQ_FILE = ".cap_seq"
PROFILE_TMP_GLOB = "_profile_tmp_*.run"
