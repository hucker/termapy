"""File-argument completion: the ``Command.files`` declaration and the engine's reader.

A command whose first argument names a file in a data folder declares the
folder (``files="run"``); ``ReplEngine.complete_file_arg`` lists that folder
for every frontend (registry ``file-arg-declared``).  Real folders under
``tmp_path`` throughout -- the folder listing IS the behavior.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from termapy.plugins import PluginInfo
from termapy.repl import ReplEngine


def _touch(path, age_s: float = 0.0) -> None:
    """Create ``path`` with an mtime ``age_s`` seconds in the past."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    when = time.time() - age_s
    os.utime(path, (when, when))


@pytest.fixture
def engine(tmp_path):
    """A ReplEngine on a throwaway config whose data folders start absent."""
    config_path = tmp_path / "proj" / "proj.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps({}), encoding="utf-8")
    eng = ReplEngine({}, str(config_path), lambda text, color=None: None)
    return eng, config_path.parent


class TestCompleteFileArg:
    """``complete_file_arg`` answers from the declared folder, nothing else."""

    def test_matches_come_newest_first(self, engine):
        """file-arg-declared: matches are listed newest first, as /run.list orders them."""
        # Arrange
        eng, cfg_dir = engine
        _touch(cfg_dir / "run" / "a_old.run", age_s=100)
        _touch(cfg_dir / "run" / "a_new.run")
        line = "/run a"

        # Act
        actual = eng.complete_file_arg(line)

        # Assert
        expected = (len("/run "), ["a_new.run", "a_old.run"])
        assert actual == expected, "names replace the partial, newest first"

    def test_matching_ignores_case_and_keeps_the_names_case(self, engine):
        """A lowercase partial matches a capitalized file, which comes back as spelled on disk."""
        eng, cfg_dir = engine
        _touch(cfg_dir / "run" / "Welcome.run")
        assert eng.complete_file_arg("/run wel") == (5, ["Welcome.run"]), (
            "the match ignores case; the name keeps its own"
        )

    def test_only_the_folders_pattern_is_listed(self, engine):
        """A stray file of another type in run/ is not offered to /run."""
        eng, cfg_dir = engine
        _touch(cfg_dir / "run" / "notes.txt")
        _touch(cfg_dir / "run" / "boot.run")
        assert eng.complete_file_arg("/run ") == (5, ["boot.run"]), "run/ lists *.run only"

    def test_each_command_reads_its_own_folder(self, engine):
        """/proto.run lists proto/ and /cap.dump lists cap/, not run/."""
        # Arrange
        eng, cfg_dir = engine
        _touch(cfg_dir / "run" / "x.run")
        _touch(cfg_dir / "proto" / "x.pro")
        _touch(cfg_dir / "cap" / "x.bin")

        # Act
        actual_proto = eng.complete_file_arg("/proto.run x")
        actual_cap = eng.complete_file_arg("/cap.dump x")

        # Assert
        assert actual_proto == (11, ["x.pro"]), "/proto.run completes from proto/"
        assert actual_cap == (10, ["x.bin"]), "/cap.dump completes from cap/"

    def test_a_file_saved_after_the_first_call_is_offered_on_the_next(self, engine):
        """The folder is listed per call: no stale cache hides a file saved mid-session."""
        # Arrange
        eng, cfg_dir = engine
        before = eng.complete_file_arg("/run n")
        _touch(cfg_dir / "run" / "new.run")

        # Act
        after = eng.complete_file_arg("/run n")

        # Assert
        assert before == (5, []), "a declared command with no match is (start, []), not None"
        assert after == (5, ["new.run"]), "the second call sees the new file"

    def test_an_output_level_suffix_resolves_like_dispatch(self, engine):
        """/run.silent completes like /run, because dispatch strips the level suffix."""
        eng, cfg_dir = engine
        _touch(cfg_dir / "run" / "quiet_one.run")
        assert eng.complete_file_arg("/run.silent q") == (12, ["quiet_one.run"]), (
            "plugin_for resolution, so the suffix cannot drift from dispatch"
        )

    @pytest.mark.parametrize(
        ("line", "why"),
        [
            ("/print w", "an undeclared command"),
            ("/run.rename a b", "the second argument"),
            ("/run", "no argument started yet"),
            ("run w", "a line without the REPL prefix"),
        ],
    )
    def test_lines_outside_a_declared_first_argument_get_none(self, engine, line, why):
        """None, not an empty list, for anything that is not a declared first argument."""
        eng, cfg_dir = engine
        _touch(cfg_dir / "run" / "w.run")
        assert eng.complete_file_arg(line) is None, f"None for {why}"

    def test_no_config_loaded_offers_nothing(self):
        """Without a config there are no data folders, so the cwd fallback is never listed."""
        # A no-config engine resolves data folders to the cwd; listing it
        # would return (start, [...]), so None proves the folder was skipped.
        eng = ReplEngine({}, "", lambda text, color=None: None)
        assert eng.complete_file_arg("/run w") is None, "no config -> no completion"


class TestDeclarations:
    """The ``files=`` declaration is validated and reaches every builtin that takes a file."""

    def test_an_unknown_folder_fails_at_construction(self):
        """A typo in files= fails loud at load, naming the folder, instead of completing nothing."""
        with pytest.raises(ValueError, match="unknown files folder 'runs'"):
            PluginInfo(
                name="x", args="{filename}", help="h", handler=lambda ctx, args: None, files="runs"
            )

    @pytest.mark.parametrize(
        ("command", "folder"),
        [
            ("run", "run"),
            ("run.help", "run"),
            ("run.legacy", "run"),
            ("run.edit", "run"),
            ("run.dump", "run"),
            ("run.rename", "run"),
            ("proto.run", "proto"),
            ("proto.debug", "proto"),
            ("proto.dump", "proto"),
            ("proto.rename", "proto"),
            ("cap.dump", "cap"),
            ("cap.rename", "cap"),
            ("ss.rename", "ss"),
            ("plugin.dump", "plugin"),
            ("plugin.rename", "plugin"),
            ("sym.load", "sym"),
            ("edit.run", "run"),
            ("edit.proto", "proto"),
            ("edit.plugin", "plugin"),
        ],
    )
    def test_builtin_file_commands_declare_their_folder(self, engine, command, folder):
        """Every builtin whose first argument is a data-folder file declares that folder."""
        eng, _ = engine
        plugin = eng.plugin_for(command)
        assert plugin is not None, f"/{command} is registered"
        assert plugin.files == folder, f"/{command} completes from {folder}/"
