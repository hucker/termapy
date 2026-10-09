"""Tests for termapy.folders -- folder/file constants."""

import pytest

from termapy.folders import (
    CLEARABLE,
    DUMPABLE,
    EXT_TO_FOLDER,
    FOLDER_MIGRATIONS,
    FOLDER_NAMES,
    FOLDER_PATTERNS,
    FOLDERS,
    HISTORY_FILE,
    PROFILE_TMP_GLOB,
    SEQ_FILE,
    SHOWABLE,
    SYMBOLS_SUFFIX,
    FolderSpec,
)


class TestFolderSpec:
    def test_frozen(self):
        """A FolderSpec is frozen, so a folder's flags cannot be flipped at runtime."""
        spec = FolderSpec("test", "*.txt")
        # frozen dataclass rejects mutation
        with pytest.raises(AttributeError):
            spec.name = "other"  # ty: ignore[invalid-assignment]

    def test_defaults(self):
        """A bare FolderSpec is listable and dumpable but never clearable -- wiping is opt-in."""
        # Defaults represent the "standard browsable folder" case:
        # user-facing, contents safe to inspect/export, NOT safe to wipe.
        spec = FolderSpec("test", "*.txt")
        assert spec.clearable is False, "clearable default (destructive opt-in)"
        assert spec.showable is True, "showable default (user-facing)"
        assert spec.dumpable is True, "dumpable default (exportable)"


class TestFolders:
    def test_eight_folders(self):
        """FOLDERS holds exactly ten data folders; adding or dropping one is a deliberate edit."""
        assert len(FOLDERS) == 10, "run, proto, plugin, ss, viz, cap, prof, sym, dev, lib"

    def test_folder_names_tuple(self):
        """FOLDER_NAMES is an immutable tuple including the run, proto, plugin, and sym folders."""
        assert isinstance(FOLDER_NAMES, tuple), "immutable"
        assert "run" in FOLDER_NAMES, "scripts folder"
        assert "proto" in FOLDER_NAMES, "protocol tests"
        assert "plugin" in FOLDER_NAMES, "plugins"
        assert "sym" in FOLDER_NAMES, "symbol tables"

    def test_clearable_derived(self):
        """Only output folders (ss, cap, prof) are wipeable; user-authored run scripts are not."""
        assert "ss" in CLEARABLE, "screenshots clearable"
        assert "cap" in CLEARABLE, "captures clearable"
        assert "prof" in CLEARABLE, "profiles clearable"
        assert "run" not in CLEARABLE, "scripts not clearable"

    def test_showable_derived(self):
        """Both generated screenshots and authored scripts appear in folder listings."""
        assert "ss" in SHOWABLE, "screenshots showable"
        assert "run" in SHOWABLE, "scripts showable"

    def test_dumpable_derived(self):
        """Text folders like run can be dumped; binary screenshots in ss cannot."""
        assert "run" in DUMPABLE, "scripts dumpable"
        assert "ss" not in DUMPABLE, "screenshots not dumpable"

    def test_folder_patterns(self):
        """Each folder's glob is built from its extension: *.run, *.pro, and *.py."""
        assert FOLDER_PATTERNS["run"] == "*.run", "script glob"
        assert FOLDER_PATTERNS["proto"] == "*.pro", "proto glob"
        assert FOLDER_PATTERNS["plugin"] == "*.py", "plugin glob"

    def test_ext_to_folder(self):
        """A file extension routes to its home folder: .run, .pro, and .py each map to one."""
        assert EXT_TO_FOLDER[".run"] == "run", ".run -> run/"
        assert EXT_TO_FOLDER[".pro"] == "proto", ".pro -> proto/"
        assert EXT_TO_FOLDER[".py"] == "plugin", ".py -> plugin/"

    def test_migrations(self):
        """The legacy plural names captures, scripts, and plugins stay in the rename table."""
        old_names = [m[0] for m in FOLDER_MIGRATIONS]
        assert "captures" in old_names, "legacy name"
        assert "scripts" in old_names, "legacy name"
        assert "plugins" in old_names, "legacy name"

    def test_special_filenames(self):
        """Fixed names for history, capture sequence, symbol sidecar, and temp profiles hold."""
        assert HISTORY_FILE == ".cmd_history.txt", "history file"
        assert SEQ_FILE == ".cap_seq", "sequence counter"
        assert SYMBOLS_SUFFIX.startswith(".") and SYMBOLS_SUFFIX.endswith(".json"), (
            "symbol sidecar is a dotted .json suffix beside the cfg"
        )
        assert PROFILE_TMP_GLOB == "_profile_tmp_*.run", "temp profiles"
