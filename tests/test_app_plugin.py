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
    (``ctx.io.write``, ``ctx.fs.open_file``) work transparently.
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
            io=IOHandle(write=lambda text, color=None: self.lines.append(text)),
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


# -- /app.state --------------------------------------------------------------


class TestAppState:
    def test_prints_path(self, pin_app_dirs, ctx):
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
        # Act
        result = app_plugin._handler_state_dump(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines).strip()
        assert result.success, "missing file is ok"
        assert actual == "{}", f"empty dump, got {actual!r}"

    def test_dump_populated(self, pin_app_dirs, ctx):
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
        # Arrange
        _, config_dir = pin_app_dirs

        # Act
        result = app_plugin._handler_config(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines)
        assert result.success, "ok"
        assert str(config_dir / "config.json") in actual, "full path printed"

    def test_dump_missing_file_is_empty_object(self, pin_app_dirs, ctx):
        # Act
        result = app_plugin._handler_config_dump(ctx, "")

        # Assert
        actual = "\n".join(ctx.lines).strip()
        assert result.success, "missing file is ok"
        assert actual == "{}", "empty dump"

    def test_dump_populated(self, pin_app_dirs, ctx):
        # Arrange
        _, config_dir = pin_app_dirs
        config_dir.mkdir(parents=True, exist_ok=True)
        payload = {"theme": "dark", "show_timestamps": True}
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
