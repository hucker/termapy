"""Tests for the /app builtin plugin.

Surface under test:

- /app.explore         open the app folder(s) via ctx.fs.open_file
- /app.state           print state.json path
- /app.state.dump      print state.json contents
- /app.config          print config.json path
- /app.config.dump     print config.json contents
- /app.config.edit     create-if-missing + open config.json via ctx.fs.open_file

No bare /app handler -- the top-level Command has handler=None.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from termapy.builtins.commands import app as app_plugin

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def pin_app_dirs(monkeypatch, tmp_path):
    """Redirect both app_state_dir and app_config_dir to tmp_path."""
    state_dir = tmp_path / "state"
    config_dir = tmp_path / "config"
    monkeypatch.setenv("TERMAPY_STATE_DIR", str(state_dir))
    monkeypatch.setenv("TERMAPY_CONFIG_DIR", str(config_dir))
    return state_dir, config_dir


class _CtxRecorder:
    """Tiny PluginContext stand-in that records write/open_file calls.

    Wraps a real :class:`PluginContext` so the namespaced handles
    (``ctx.io._write``, ``ctx.fs.open_file``) work transparently.
    Exposes ``self.lines`` (write-output capture) and ``self.open_file``
    (a MagicMock for assertion convenience) directly on the recorder.
    """

    def __init__(self):
        from termapy.plugins import (
            CapabilitySet,
            FilesystemHandle,
            IOHandle,
            PluginContext,
        )

        self.lines: list[str] = []
        self.open_file = MagicMock()
        self._ctx = PluginContext(
            io=IOHandle(_write=lambda text, color=None: self.lines.append(text)),
            fs=FilesystemHandle(_open_file_impl=self.open_file),
            capabilities=CapabilitySet(gui_apps=True),
        )

    def __getattr__(self, name):
        # Forward any other attribute access (cfg, io, fs, ...) to the
        # underlying real PluginContext.  Direct attributes on self
        # (lines, open_file, _ctx) take precedence via normal lookup.
        return getattr(self._ctx, name)

    def write(self, text: str) -> None:
        self.lines.append(text)


@pytest.fixture
def ctx():
    return _CtxRecorder()


# -- /app.explore ------------------------------------------------------------


class TestAppExplore:
    def test_opens_both_when_different(self, pin_app_dirs, ctx):
        """Distinct state and config folders each get opened, one call per folder."""
        # Arrange - state and config at distinct tmp paths (Linux-like).
        state_dir, config_dir = pin_app_dirs

        # Act
        result = app_plugin._handler_explore(ctx, "")

        # Assert - two distinct paths opened.
        actual_call_count = ctx.open_file.call_count
        expected_call_count = 2
        assert result.success, "ok"
        assert actual_call_count == expected_call_count, \
            "two distinct dirs -> two opens"
        opened = {call.args[0] for call in ctx.open_file.call_args_list}
        assert state_dir in opened, "state dir was opened"
        assert config_dir in opened, "config dir was opened"

    def test_dedupes_when_same(self, monkeypatch, tmp_path, ctx):
        """When state and config share one folder it opens once, not twice."""
        # Arrange - both resolve to the SAME path (Windows/macOS behavior).
        same = tmp_path / "shared"
        monkeypatch.setenv("TERMAPY_STATE_DIR", str(same))
        monkeypatch.setenv("TERMAPY_CONFIG_DIR", str(same))

        # Act
        result = app_plugin._handler_explore(ctx, "")

        # Assert
        actual_call_count = ctx.open_file.call_count
        expected_call_count = 1
        assert result.success, "ok"
        assert actual_call_count == expected_call_count, \
            "deduped when state and config resolve to one path"


# -- /app.list ---------------------------------------------------------------


class TestAppList:
    def test_list_creates_no_folder(self, pin_app_dirs, ctx):
        """reads-never-write: /app.list on a fresh machine lists nothing and creates no app folder."""
        # Arrange
        state_dir, config_dir = pin_app_dirs

        # Act
        result = app_plugin._handler_list(ctx, "")

        # Assert
        assert result.success, "an empty listing is not an error"
        assert result.data == [], "no files yet"
        assert not state_dir.exists(), "the state folder is not created by listing it"
        assert not config_dir.exists(), "nor the config folder"

    def test_list_is_the_file_table_with_a_folder_column(self, pin_app_dirs, ctx):
        """Split state and config folders (Linux) list as one file table whose FOLDER column says where."""
        # Arrange -- one file in each folder
        from termapy.app_dirs import save_app_state

        state_dir, config_dir = pin_app_dirs
        save_app_state({"checked": 1})
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{}\n", encoding="utf-8")
        expected = {"state.json": str(state_dir), "config.json": str(config_dir)}

        # Act
        result = app_plugin._handler_list(ctx, "")

        # Assert
        actual = {record["name"]: record["folder"] for record in result.data}
        assert actual == expected, "each file names the folder it is in"
        assert all("bytes" in record and "mtime" in record for record in result.data), (
            "the records are file_record's, so SIZE and UPDATED read as in every list"
        )
        assert [column.title for column in result.view.columns] == [
            "NAME", "SIZE", "UPDATED", "FOLDER",
        ], "the shared file columns, then FOLDER"

    def test_list_one_folder_is_the_title(self, monkeypatch, tmp_path, ctx):
        """Where state and config share a folder (Windows, macOS), that folder is the title and no FOLDER column."""
        # Arrange
        same = tmp_path / "termapy"
        monkeypatch.setenv("TERMAPY_STATE_DIR", str(same))
        monkeypatch.setenv("TERMAPY_CONFIG_DIR", str(same))
        same.mkdir()
        (same / "state.json").write_text("{}\n", encoding="utf-8")

        # Act
        result = app_plugin._handler_list(ctx, "")

        # Assert
        assert result.view.title == str(same), "the one folder titles the table"
        assert [column.title for column in result.view.columns] == ["NAME", "SIZE", "UPDATED"], (
            "no FOLDER column when there is one folder"
        )
        assert result.value == "state.json", "the value is the names, as in every list"


# -- /app.state --------------------------------------------------------------


class TestAppState:
    def test_prints_path(self, pin_app_dirs, ctx):
        """Prints the full state.json path and returns it as the scriptable value."""
        # Arrange
        state_dir, _ = pin_app_dirs

        # Act
        result = app_plugin._handler_state(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines)
        assert result.success, "ok"
        assert str(state_dir / "state.json") in actual, "full path printed"
        assert result.value == str(state_dir / "state.json"), \
            "value equals the path (scriptable)"

    def test_dump_missing_file_is_empty_object(self, pin_app_dirs, ctx):
        """A missing state.json dumps as {} and succeeds rather than erroring."""
        # Act
        result = app_plugin._handler_state_dump(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines).strip()
        assert result.success, "missing file is ok"
        assert actual == "{}", f"empty dump, got {actual!r}"

    def test_dump_populated(self, pin_app_dirs, ctx):
        """An existing state.json dumps with its nested keys and values intact."""
        # Arrange
        state_dir, _ = pin_app_dirs
        state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"update_check": {"last_checked": "2026-04-17T10:00:00+00:00"}}
        (state_dir / "state.json").write_text(json.dumps(payload))

        # Act
        result = app_plugin._handler_state_dump(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines)
        assert result.success, "read succeeded"
        assert "update_check" in actual, "feature subkey shown"
        assert "2026-04-17" in actual, "value preserved"


# -- /app.config -------------------------------------------------------------


class TestAppConfig:
    def test_prints_path(self, pin_app_dirs, ctx):
        """Prints the full config.json path under the app config folder."""
        # Arrange
        _, config_dir = pin_app_dirs

        # Act
        result = app_plugin._handler_config(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines)
        assert result.success, "ok"
        assert str(config_dir / "config.json") in actual, "full path printed"

    def test_dump_missing_file_is_empty_object(self, pin_app_dirs, ctx):
        """A missing config.json dumps as {} and succeeds rather than erroring."""
        # Act
        result = app_plugin._handler_config_dump(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines).strip()
        assert result.success, "missing file is ok"
        assert actual == "{}", "empty dump"

    def test_dump_populated(self, pin_app_dirs, ctx):
        """An existing config.json dumps with its keys and values intact."""
        # Arrange
        _, config_dir = pin_app_dirs
        config_dir.mkdir(parents=True, exist_ok=True)
        payload = {"theme": "dark", "timestamps": True}
        (config_dir / "config.json").write_text(json.dumps(payload))

        # Act
        result = app_plugin._handler_config_dump(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines)
        assert result.success, "read succeeded"
        assert "theme" in actual, "key preserved"
        assert "dark" in actual, "value preserved"


# -- /app.config.edit --------------------------------------------------------


class TestAppConfigEdit:
    def test_creates_file_if_missing(self, pin_app_dirs, ctx):
        """A missing config.json is seeded with {} before it is opened for editing."""
        # Arrange
        _, config_dir = pin_app_dirs

        # Act
        result = app_plugin._handler_config_edit(ctx, "")

        # Assert
        config_file = config_dir / "config.json"
        assert result.success, "ok"
        assert config_file.exists(), "created empty config.json"
        actual_content = config_file.read_text().strip()
        assert actual_content == "{}", "seeded with empty JSON object"
        ctx.open_file.assert_called_once_with(config_file)

    def test_preserves_existing_file(self, pin_app_dirs, ctx):
        """Editing opens an existing config.json without overwriting its content."""
        # Arrange
        _, config_dir = pin_app_dirs
        config_dir.mkdir(parents=True, exist_ok=True)
        payload = {"theme": "dark"}
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(payload))

        # Act
        result = app_plugin._handler_config_edit(ctx, "")

        # Assert - content is NOT overwritten
        actual = json.loads(config_file.read_text())
        assert result.success, "ok"
        assert actual == payload, "existing content preserved"
        ctx.open_file.assert_called_once_with(config_file)
