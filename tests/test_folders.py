"""Tests for termapy.folders -- folder/file constants."""

import contextlib
import sys

import pytest

from termapy.folders import (
    DUMPABLE,
    EXT_TO_FOLDER,
    FOLDER_EXTS,
    FOLDER_MIGRATIONS,
    FOLDER_NAMES,
    FOLDER_PATTERNS,
    FOLDERS,
    GENERATED,
    HISTORY_FILE,
    PROFILE_SUFFIX,
    PROFILE_TMP_GLOB,
    SEQ_FILE,
    SHOWABLE,
    SYMBOLS_SUFFIX,
    FolderSpec,
    default_file_name,
    inside,
    is_inside,
    new_file_name,
)


class TestFolderSpec:
    def test_frozen(self):
        """A FolderSpec is frozen, so a folder's flags cannot be flipped at runtime."""
        spec = FolderSpec("test", "*.txt")
        # frozen dataclass rejects mutation
        with pytest.raises(AttributeError):
            spec.name = "other"  # ty: ignore[invalid-assignment]

    def test_defaults(self):
        """A bare FolderSpec is listable and dumpable, and not generated -- wiping by pattern is opt-in."""
        # Defaults represent the "standard browsable folder" case:
        # user-facing, contents safe to inspect/export, written by a person.
        spec = FolderSpec("test", "*.txt")
        assert spec.generated is False, "generated default (wiping by pattern is opt-in)"
        assert spec.showable is True, "showable default (user-facing)"
        assert spec.dumpable is True, "dumpable default (exportable)"


class TestFolders:
    def test_folder_set_is_pinned(self):
        """FOLDERS holds exactly these data folders; adding or dropping one is a deliberate edit."""
        expected = {
            "run",
            "proto",
            "plugin",
            "ss",
            "viz",
            "cap",
            "prof",
            "sym",
            "dev",
            "lib",
            "firmware",
        }
        assert set(FOLDER_NAMES) == expected, "the data-folder set changed"
        assert len(FOLDERS) == len(expected), "no duplicate folder entries"

    def test_folder_names_tuple(self):
        """FOLDER_NAMES is an immutable tuple including the run, proto, plugin, and sym folders."""
        assert isinstance(FOLDER_NAMES, tuple), "immutable"
        assert "run" in FOLDER_NAMES, "scripts folder"
        assert "proto" in FOLDER_NAMES, "protocol tests"
        assert "plugin" in FOLDER_NAMES, "plugins"
        assert "sym" in FOLDER_NAMES, "symbol tables"

    def test_generated_derived(self):
        """Only output folders (ss, cap, prof) are wipeable; user-authored run scripts are not."""
        assert "ss" in GENERATED, "screenshots generated"
        assert "cap" in GENERATED, "captures generated"
        assert "prof" in GENERATED, "profiles generated"
        assert "run" not in GENERATED, "scripts are written by a person"

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


class TestNewFileName:
    """A new file gets its folder's extension once, appended when the typed name lacks it."""

    @pytest.mark.rule("file-name-default-ext")
    def test_a_bare_name_gets_the_extension_and_a_full_one_keeps_it(self):
        """file-name-default-ext, write side: boot -> boot.run, boot.run stays, never boot.run.run."""
        # Act
        actual = [new_file_name(name, ".run") for name in ("boot", "boot.run")]

        # Assert
        assert actual == ["boot.run", "boot.run"], "appended once, never doubled"

    def test_the_extension_table_mirrors_folders(self):
        """FOLDER_EXTS holds each folder's FolderSpec.ext, the one place a file type is said."""
        # Act
        actual = FOLDER_EXTS

        # Assert
        assert actual == {spec.name: spec.ext for spec in FOLDERS}, "derived, never hand-kept"
        assert actual["run"] == ".run" and actual["proto"] == ".pro", "termapy's own two types"


class TestDefaultFileName:
    """A typed name may leave off its known extension; the helper probes, never guesses."""

    @pytest.mark.rule("file-name-default-ext")
    def test_default_file_name_adds_the_extension_only_when_that_file_exists(self, tmp_path):
        """A bare stem finds stem.run, and a stem with no such file stays as typed."""
        # Arrange
        (tmp_path / "hex_view.run").write_text("")

        # Act
        actual_found = default_file_name(tmp_path / "hex_view", ".run")
        actual_missing = default_file_name(tmp_path / "nothing", ".run")

        # Assert
        assert actual_found == tmp_path / "hex_view.run", (
            "the extension is added when that file exists"
        )
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


class TestInside:
    @pytest.mark.rule("path-inside-one-check")
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("x.json", "x.json"),
            ("sub/deep/x.json", "sub/deep/x.json"),
            ("sub/../x.json", "x.json"),
            (".", "."),
        ],
    )
    def test_a_name_that_stays_answers_its_path(self, tmp_path, name, expected):
        """A name below the folder, one that climbs and comes back, and the folder itself."""
        # Act
        actual = inside(tmp_path / "box", name)

        # Assert
        assert actual == (tmp_path / "box" / expected).resolve(), "resolved, under the folder"

    @pytest.mark.rule("path-inside-one-check")
    @pytest.mark.parametrize("name", ["../x.json", "sub/../../x.json", "..", "/etc/passwd"])
    def test_a_name_that_leaves_answers_none(self, tmp_path, name):
        """A climb out and an absolute path, which replaces the folder when joined."""
        assert inside(tmp_path / "box", name) is None, f"{name} leaves box/"

    def test_an_absolute_path_back_inside_is_inside(self, tmp_path):
        """An absolute path is judged by where it is, so one that names a file in the folder is inside."""
        # Arrange
        target = tmp_path / "box" / "x.json"

        # Act / Assert
        assert inside(tmp_path / "box", target) == target.resolve(), "absolute, but in box/"

    @pytest.mark.rule("path-inside-one-check")
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows path forms")
    @pytest.mark.parametrize("form", ["rooted", "other drive"])
    def test_the_windows_forms_that_are_not_absolute_still_leave(self, tmp_path, form):
        """\\x and D:x are not absolute, so a text check passes them; resolving shows they leave."""
        # Arrange
        box = tmp_path / "box"
        drive = "Z:" if box.drive.upper() != "Z:" else "Y:"
        name = "\\Windows\\x.json" if form == "rooted" else f"{drive}x.json"

        # Act / Assert
        assert inside(box, name) is None, f"{name} leaves box/"

    @pytest.mark.rule("path-inside-one-check")
    def test_a_link_out_of_the_folder_is_outside(self, tmp_path):
        """A link inside the folder that points out of it is followed, so writing through it is refused."""
        # Arrange
        box = tmp_path / "box"
        box.mkdir()
        (tmp_path / "away").mkdir()
        try:
            (box / "door").symlink_to(tmp_path / "away", target_is_directory=True)
        except OSError:
            pytest.skip("this account cannot make symlinks")

        # Act / Assert
        assert inside(box, "door/x.json") is None, "the link leads out of box/"

    def test_is_inside_measures_a_relative_path_from_the_working_folder(self, tmp_path):
        """A relative path already means a place: is_inside reads it from the working folder, not the folder asked about."""
        # Arrange -- the working folder is tmp_path, and box/x.txt would be inside box/
        box = tmp_path / "box"
        box.mkdir()

        # Act
        with contextlib.chdir(tmp_path):
            relative = is_inside("x.txt", box)
            joined = inside(box, "x.txt")

        # Assert
        assert relative is False, "x.txt here is tmp_path/x.txt, outside box/"
        assert joined is not None, "the same name joined to box/ is inside it: the other function"

    def test_is_inside_takes_a_path_in_the_folder(self, tmp_path):
        """A path in the folder, at any depth, is inside; the folder's parent is not."""
        box = tmp_path / "box"
        assert is_inside(box / "a" / "b.txt", box) is True, "below box/"
        assert is_inside(tmp_path, box) is False, "box/'s parent"
