"""The shared ``.delete <name|*>`` folder handler (``/cap.delete``, ``/ss.delete``, ...).

One named file goes without a question; ``*`` deletes every file in the
folder after a yes where the host can ask (the TUI and CLI), and without
one where it cannot (MCP, whose destructive gate asked already).  A name
stays inside its folder, like ``.show`` and ``.dump``.
"""

from __future__ import annotations

import json

import pytest

from termapy.folder_ops import build_folder_subcommands
from termapy.plugins import CapabilitySet, IOHandle, PluginContext, UsageError
from termapy.plugins.handles.ui import UIHandle


def _ctx(cfg_file, *, answer: bool | None = None):
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


class TestOneFile:
    def test_a_named_file_goes_without_a_question(self, tmp_path):
        """/cap.delete a.txt removes that file only, and asks nothing."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, questions = _ctx(cfg_file, answer=False)

        # Act
        result = _delete("cap")(ctx, "a.txt")

        # Assert
        assert result.success, result.error
        assert result.value == "1", "the count deleted"
        assert not (data_dir / "a.txt").exists(), "the named file is gone"
        assert (data_dir / "b.txt").exists(), "its neighbor stays"
        assert questions == [], "one named file is not asked about"

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
        assert second.error == "File not found: a.txt"

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


class TestEveryFile:
    def test_a_yes_deletes_every_file(self, tmp_path):
        """/cap.delete * asks with the count, then deletes every file but the dotfile."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, questions = _ctx(cfg_file, answer=True)

        # Act
        result = _delete("cap")(ctx, "*")

        # Assert
        assert result.success, result.error
        assert result.value == "2", "the count deleted"
        assert questions == ["Delete every file in cap/ (2 files)? This cannot be undone."]
        actual = sorted(path.name for path in data_dir.iterdir())
        expected = [".gitignore"]
        assert actual == expected, "a dotfile is not a listed file, so * leaves it"

    def test_a_no_deletes_nothing(self, tmp_path):
        """Declining the question fails the command and keeps every file."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file, answer=False)

        # Act
        result = _delete("cap")(ctx, "*")

        # Assert
        assert result.error == "Nothing deleted from cap/"
        assert (data_dir / "a.txt").exists() and (data_dir / "b.txt").exists(), "both kept"

    def test_a_host_that_cannot_ask_deletes_without_a_question(self, tmp_path):
        """With no prompt (MCP, whose gate asked already) * deletes straight away."""
        # Arrange
        cfg_file, data_dir = _setup(tmp_path)
        ctx, questions = _ctx(cfg_file, answer=None)

        # Act
        result = _delete("cap")(ctx, "*")

        # Assert
        assert result.value == "2", "both files deleted"
        assert questions == [], "there was no one to ask"
        assert not (data_dir / "a.txt").exists(), "deleted"

    def test_an_empty_folder_asks_nothing(self, tmp_path):
        """* on an empty folder succeeds with 0 and no question, the second time as the first."""
        # Arrange
        cfg_file, _ = _setup(tmp_path, names=())
        ctx, questions = _ctx(cfg_file, answer=True)
        handler = _delete("cap")

        # Act
        first = handler(ctx, "*")
        second = handler(ctx, "*")

        # Assert
        assert (first.value, second.value) == ("0", "0"), "nothing to delete, twice"
        assert questions == [], "an empty folder is not asked about"


def test_bare_is_a_usage_error(tmp_path):
    """Bare /cap.delete names nothing, so it is a usage error, never a wipe."""
    cfg_file, data_dir = _setup(tmp_path)
    ctx, _ = _ctx(cfg_file, answer=True)
    with pytest.raises(UsageError):
        _delete("cap")(ctx, "")
    assert (data_dir / "a.txt").exists(), "nothing deleted"
