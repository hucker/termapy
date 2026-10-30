"""The shared ``.delete`` folder handler (``/cap.delete``, ``/run.delete``, ...).

A folder of termapy's own output (ss/, cap/, prof/) takes a name or a
pattern; a folder of what someone wrote (run/, proto/, plugin/) takes one
name and asks first.  A pattern asks first too, where the host can ask
(the TUI and CLI); MCP's destructive gate asked already.  Finding nothing
to delete fails.  A name stays inside its folder, like ``.show``.
"""

from __future__ import annotations

import json

import pytest

from termapy.folder_ops import build_folder_subcommands
from termapy.plugins import CapabilitySet, InternalHandle, IOHandle, PluginContext, UsageError
from termapy.plugins.handles.ui import UIHandle


def _ctx(cfg_file, *, answer: bool | None = None, changed: list[str] | None = None):
    """A ctx on ``cfg_file``; ``answer`` is the user's reply, None for a host that cannot ask."""
    questions: list[str] = []

    def confirm(message: str) -> bool:
        questions.append(message)
        return bool(answer)

    ctx = PluginContext(
        io=IOHandle(_write=lambda text, color=None: None),
        config_path=str(cfg_file),
        capabilities=CapabilitySet(confirm_dialog=answer is not None),
        ui=UIHandle(_confirm_impl=confirm),
        internal=InternalHandle(folder_changed=(changed if changed is not None else []).append),
    )
    return ctx, questions


def _setup(tmp_path, folder: str = "cap", names: tuple[str, ...] = ("a.txt", "b.txt")):
    """A cfg with ``names`` in its ``folder``, a dotfile beside them and a file outside."""
    cfg_dir = tmp_path / "cfg"
    data_dir = cfg_dir / folder
    data_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "test.cfg"
    cfg_file.write_text(json.dumps({"port": ""}), encoding="utf-8")
    for name in names:
        (data_dir / name).write_text("x", encoding="utf-8")
    (data_dir / ".gitignore").write_text("*\n", encoding="utf-8")
    (cfg_dir / "outside.txt").write_text("keep", encoding="utf-8")
    return cfg_file, data_dir


def _delete(folder: str):
    """The handler ``/<folder>.delete`` dispatches, as the folder's spec declares it."""
    handler = build_folder_subcommands(folder)["delete"].handler
    assert handler is not None, "the delete subcommand has a handler"
    return handler


def _names(data_dir) -> list[str]:
    """The files left in ``data_dir``, sorted."""
    return sorted(path.name for path in data_dir.iterdir())


class TestGeneratedFile:
    def test_one_named_file_goes_without_a_question(self, tmp_path):
        """/cap.delete a.txt removes that file only, and asks nothing."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, questions = _ctx(cfg_file, answer=False)

        # Act
        result = _delete("cap")(ctx, "a.txt")

        # Assert
        assert result.value == "1", result.error
        assert _names(data_dir) == [".gitignore", "b.txt"], "only the named file is gone"
        assert questions == [], "one generated file is not asked about"

    def test_the_extension_is_optional(self, tmp_path):
        """/run.profile.delete boot finds boot.csv, the folder's extension added."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path, "prof", ("boot.csv",))
        ctx, _ = _ctx(cfg_file)

        # Act
        result = _delete("prof")(ctx, "boot")

        # Assert
        assert result.success, result.error
        assert not (data_dir / "boot.csv").exists(), "boot.csv deleted by its stem"

    def test_twice_in_a_row_the_second_finds_nothing(self, tmp_path):
        """Deleting the same name twice fails the second time, naming the file."""
        # Arrange
        cfg_file, _ = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file)
        handler = _delete("cap")

        # Act
        first = handler(ctx, "a.txt")
        second = handler(ctx, "a.txt")

        # Assert
        assert first.success, first.error
        assert second.error.startswith("File not found: a.txt"), second.error

    def test_a_name_outside_the_folder_is_refused(self, tmp_path):
        """../outside.txt escapes cap/, so it is refused and the file stays."""
        # Arrange
        cfg_file, _ = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file)

        # Act
        result = _delete("cap")(ctx, "../outside.txt")

        # Assert
        assert result.error == "Path escapes cap/: ../outside.txt"
        assert (tmp_path / "cfg" / "outside.txt").exists(), "nothing outside the folder is touched"


class TestPattern:
    def test_a_yes_deletes_what_the_pattern_matches(self, tmp_path):
        """/cap.delete adc_* asks with the count, then deletes the matches and nothing else."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path, names=("adc_1.csv", "ADC_2.csv", "notes.txt"))
        ctx, questions = _ctx(cfg_file, answer=True)

        # Act
        result = _delete("cap")(ctx, "adc_*")

        # Assert
        assert result.value == "2", "both matches deleted, case ignored"
        assert questions == ["Delete 2 files from cap/ (adc_*)? This cannot be undone."]
        assert _names(data_dir) == [".gitignore", "notes.txt"], "what the pattern missed stays"

    def test_star_deletes_every_listed_file_but_a_dotfile(self, tmp_path):
        """* is the pattern of every file; a dotfile is not a listed file, so it stays."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file, answer=True)

        # Act
        result = _delete("cap")(ctx, "*")

        # Assert
        assert result.value == "2", "both files deleted"
        assert _names(data_dir) == [".gitignore"], "the dotfile is left"

    def test_a_no_deletes_nothing(self, tmp_path):
        """Declining the question fails the command and keeps every file."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file, answer=False)

        # Act
        result = _delete("cap")(ctx, "*")

        # Assert
        assert result.error == "Nothing deleted from cap/"
        assert _names(data_dir) == [".gitignore", "a.txt", "b.txt"], "every file kept"

    def test_a_host_that_cannot_ask_deletes_without_a_question(self, tmp_path):
        """With no prompt (MCP, whose gate asked already) a pattern deletes straight away."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, questions = _ctx(cfg_file, answer=None)

        # Act
        result = _delete("cap")(ctx, "*")

        # Assert
        assert result.value == "2", "both files deleted"
        assert questions == [], "there was no one to ask"

    def test_matching_nothing_fails_the_second_time_as_the_first(self, tmp_path):
        """A pattern that finds nothing to delete fails, * on an empty folder included, every time."""
        # Arrange
        cfg_file, _ = _setup(tmp_path, names=())
        ctx, questions = _ctx(cfg_file, answer=True)
        handler = _delete("cap")

        # Act
        first = handler(ctx, "*")
        second = handler(ctx, "*")

        # Assert
        assert (first.error, second.error) == ("No files in cap/ match: *",) * 2
        assert questions == [], "nothing to delete, nothing to ask"


class TestAuthored:
    def test_a_file_someone_wrote_asks_first(self, tmp_path):
        """/run.delete foo asks, naming the file, and deletes it on a yes."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path, "run", ("foo.run", "bar.run"))
        ctx, questions = _ctx(cfg_file, answer=True)

        # Act
        result = _delete("run")(ctx, "foo")

        # Assert
        assert result.value == "1", result.error
        assert questions == ["Delete foo.run from run/? This cannot be undone."]
        assert _names(data_dir) == [".gitignore", "bar.run"], "only foo.run is gone"

    def test_a_no_keeps_the_file(self, tmp_path):
        """Declining keeps the script and fails the command."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path, "proto", ("modbus.pro",))
        ctx, _ = _ctx(cfg_file, answer=False)

        # Act
        result = _delete("proto")(ctx, "modbus")

        # Assert
        assert result.error == "Nothing deleted from proto/"
        assert (data_dir / "modbus.pro").exists(), "kept"

    def test_a_pattern_is_refused(self, tmp_path):
        """run/ deletes one named file at a time: a pattern is refused before anything is asked."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path, "run", ("foo.run",))
        ctx, questions = _ctx(cfg_file, answer=True)

        # Act
        result = _delete("run")(ctx, "*")

        # Assert
        assert result.error == "Invalid name: * (run/ deletes one named file at a time)"
        assert (data_dir / "foo.run").exists() and questions == [], "nothing asked, nothing deleted"


def test_a_delete_tells_the_frontend_the_folder_changed(tmp_path):
    """After deleting, the handler calls ctx.internal.folder_changed so the TUI refreshes its counts."""
    # Arrange
    cfg_file, _ = _setup(tmp_path)
    changed: list[str] = []
    ctx, _ = _ctx(cfg_file, changed=changed)

    # Act
    _delete("cap")(ctx, "a.txt")

    # Assert
    assert changed == ["cap"], "the folder that changed, once"


def test_bare_is_a_usage_error(tmp_path):
    """Bare /cap.delete names nothing, so it is a usage error, never a wipe."""
    cfg_file, data_dir = _setup(tmp_path)
    ctx, _ = _ctx(cfg_file, answer=True)
    with pytest.raises(UsageError):
        _delete("cap")(ctx, "")
    assert (data_dir / "a.txt").exists(), "nothing deleted"
