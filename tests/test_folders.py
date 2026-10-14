"""Tests for termapy.folders -- folder/file constants."""

import sys

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
    PROFILE_SUFFIX,
    PROFILE_TMP_GLOB,
    SEQ_FILE,
    SHOWABLE,
    SYMBOLS_SUFFIX,
    FolderSpec,
    default_file_name,
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
    def test_folder_set_is_pinned(self):
        """FOLDERS holds exactly these data folders; adding or dropping one is a deliberate edit."""
        expected = {"run", "proto", "plugin", "ss", "viz", "cap", "prof", "sym", "dev", "lib"}
        assert set(FOLDER_NAMES) == expected, "the data-folder set changed"
        assert len(FOLDERS) == len(expected), "no duplicate folder entries"

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


class TestDefaultFileName:
    """A typed name may leave off its known extension; the helper probes, never guesses."""

    def test_default_file_name_adds_the_extension_only_when_that_file_exists(self, tmp_path):
        """file-name-default-ext: a bare stem finds stem.run, and a stem with no such file stays as typed."""
        # Arrange
        (tmp_path / "hex_view.run").write_text("")

        # Act
        actual_found = default_file_name(tmp_path / "hex_view", ".run")
        actual_missing = default_file_name(tmp_path / "nothing", ".run")

        # Assert
        assert actual_found == tmp_path / "hex_view.run", "the extension is added when that file exists"
        assert actual_missing == tmp_path / "nothing", "the caller's not-found names what was typed"

    def test_the_name_as_typed_wins_when_both_exist(self, tmp_path):
        """A file named exactly as typed is opened even when name + ext also exists."""
        # Arrange
        (tmp_path / "notes").write_text("")
        (tmp_path / "notes.run").write_text("")

        # Act
        actual = default_file_name(tmp_path / "notes", ".run")

        # Assert
        assert actual == tmp_path / "notes", "the typed name is tried first"

    def test_a_full_name_is_not_extended_again(self, tmp_path):
        """A name that already carries its extension never becomes x.run.run."""
        # Arrange
        (tmp_path / "x.run").write_text("")

        # Act
        actual = default_file_name(tmp_path / "x.run", ".run")

        # Assert
        assert actual == tmp_path / "x.run"

    def test_a_dotted_stem_gets_the_extension(self, tmp_path):
        """v1.2 finds v1.2.run: the old "only with no suffix" rule never tried it."""
        # Arrange
        (tmp_path / "v1.2.run").write_text("")

        # Act
        actual = default_file_name(tmp_path / "v1.2", ".run")

        # Assert
        assert actual == tmp_path / "v1.2.run", "a dot in the stem is not an extension to respect"

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows forbids * in file names")
    def test_a_mixed_folder_tries_only_the_name_as_typed(self, tmp_path):
        """With ext "*" (cap/, ss/) there is no known extension to add, even when trace* exists."""
        # Arrange
        (tmp_path / "trace*").write_text("")

        # Act
        actual = default_file_name(tmp_path / "trace", "*")

        # Assert
        assert actual == tmp_path / "trace", "'*' is a glob marker, never a suffix"

    def test_a_folder_is_not_the_file(self, tmp_path):
        """A folder named like the stem is passed over for the file with the extension."""
        # Arrange
        (tmp_path / "boot").mkdir()
        (tmp_path / "boot.run").write_text("")

        # Act
        actual = default_file_name(tmp_path / "boot", ".run")

        # Assert
        assert actual == tmp_path / "boot.run", "exists means is_file, so a folder never counts"

    def test_a_two_part_extension(self, tmp_path):
        """A multi-part suffix such as .profile.json is appended whole."""
        # Arrange
        (tmp_path / "board.profile.json").write_text("{}")

        # Act
        actual = default_file_name(tmp_path / "board", PROFILE_SUFFIX)

        # Assert
        assert actual == tmp_path / "board.profile.json"
