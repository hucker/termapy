"""Tests for termapy.folders -- folder/file constants."""

from termapy.folders import (
    CLEARABLE,
    DUMPABLE,
    EXT_TO_FOLDER,
    FOLDER_MIGRATIONS,
    FOLDER_NAMES,
    FOLDER_PATTERNS,
    FOLDERS,
    FolderSpec,
    HISTORY_FILE,
    PROFILE_TMP_GLOB,
    SEQ_FILE,
    SHOWABLE,
)


class TestFolderSpec:
    def test_frozen(self):
        spec = FolderSpec("test", "*.txt")
        try:
            spec.name = "other"
            assert False  # should not reach here
        except AttributeError:
            pass  # frozen dataclass rejects mutation

    def test_defaults(self):
        spec = FolderSpec("test", "*.txt")
        assert spec.clearable is False  # default
        assert spec.showable is False  # default
        assert spec.dumpable is False  # default


class TestFolders:
    def test_seven_folders(self):
        assert len(FOLDERS) == 7  # run, proto, plugin, ss, viz, cap, prof

    def test_folder_names_tuple(self):
        assert isinstance(FOLDER_NAMES, tuple)  # immutable
        assert "run" in FOLDER_NAMES  # scripts folder
        assert "proto" in FOLDER_NAMES  # protocol tests
        assert "plugin" in FOLDER_NAMES  # plugins

    def test_clearable_derived(self):
        assert "ss" in CLEARABLE  # screenshots clearable
        assert "cap" in CLEARABLE  # captures clearable
        assert "prof" in CLEARABLE  # profiles clearable
        assert "run" not in CLEARABLE  # scripts not clearable

    def test_showable_derived(self):
        assert "ss" in SHOWABLE  # screenshots showable
        assert "run" in SHOWABLE  # scripts showable

    def test_dumpable_derived(self):
        assert "run" in DUMPABLE  # scripts dumpable
        assert "ss" not in DUMPABLE  # screenshots not dumpable

    def test_folder_patterns(self):
        assert FOLDER_PATTERNS["run"] == "*.run"  # script glob
        assert FOLDER_PATTERNS["proto"] == "*.pro"  # proto glob
        assert FOLDER_PATTERNS["plugin"] == "*.py"  # plugin glob

    def test_ext_to_folder(self):
        assert EXT_TO_FOLDER[".run"] == "run"  # .run -> run/
        assert EXT_TO_FOLDER[".pro"] == "proto"  # .pro -> proto/
        assert EXT_TO_FOLDER[".py"] == "plugin"  # .py -> plugin/

    def test_migrations(self):
        old_names = [m[0] for m in FOLDER_MIGRATIONS]
        assert "captures" in old_names  # legacy name
        assert "scripts" in old_names  # legacy name
        assert "plugins" in old_names  # legacy name

    def test_special_filenames(self):
        assert HISTORY_FILE == ".cmd_history.txt"  # history file
        assert SEQ_FILE == ".cap_seq"  # sequence counter
        assert PROFILE_TMP_GLOB == "_profile_tmp_*.run"  # temp profiles
