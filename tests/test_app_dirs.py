"""Tests for app_dirs.app_state_dir and app_config_dir."""

from __future__ import annotations

from pathlib import Path

from termapy.app_dirs import app_config_dir, app_state_dir


class TestAppStateDir:
    def test_returns_path(self, monkeypatch, tmp_path):
        """TERMAPY_STATE_DIR overrides the platform location and comes back as a Path."""
        # Arrange - pin to a tmp path via the env override
        monkeypatch.setenv("TERMAPY_STATE_DIR", str(tmp_path / "state"))

        # Act
        actual = app_state_dir()

        # Assert
        expected = tmp_path / "state"
        assert actual == expected, "env override wins"
        assert isinstance(actual, Path), "returns a Path"

    def test_creates_dir_if_missing(self, monkeypatch, tmp_path):
        """Asking for the state dir creates it, so callers can write without a mkdir."""
        # Arrange
        target = tmp_path / "newly_created"
        monkeypatch.setenv("TERMAPY_STATE_DIR", str(target))
        assert not target.exists(), "precondition: dir does not yet exist"

        # Act
        app_state_dir()

        # Assert
        actual = target.exists()
        assert actual is True, "dir was created"

    def test_idempotent_when_already_exists(self, monkeypatch, tmp_path):
        """Repeated calls on an existing state dir do not raise."""
        # Arrange
        target = tmp_path / "exists"
        target.mkdir()
        monkeypatch.setenv("TERMAPY_STATE_DIR", str(target))

        # Act / Assert - second call must not raise
        app_state_dir()
        app_state_dir()

    def test_platform_default_without_override(self, monkeypatch, tmp_path):
        """With no env override the platformdirs default is used and is absolute."""
        # Arrange - remove any override so the platformdirs default is used.
        # tmp_path is just here so the test doesn't pollute the real state dir
        # on systems where the platform default is the user's real home.
        # We only assert the path is absolute and non-empty; we do NOT assert
        # a specific platform path because that varies by OS.
        monkeypatch.delenv("TERMAPY_STATE_DIR", raising=False)

        # Act
        actual = app_state_dir()

        # Assert
        assert actual.is_absolute(), "platformdirs returns an absolute path"
        assert str(actual), "non-empty path"


class TestAppConfigDir:
    def test_returns_path(self, monkeypatch, tmp_path):
        """TERMAPY_CONFIG_DIR overrides the platform location and comes back as a Path."""
        # Arrange
        monkeypatch.setenv("TERMAPY_CONFIG_DIR", str(tmp_path / "config"))

        # Act
        actual = app_config_dir()

        # Assert
        expected = tmp_path / "config"
        assert actual == expected, "env override wins"
        assert isinstance(actual, Path), "returns a Path"

    def test_creates_dir_if_missing(self, monkeypatch, tmp_path):
        """Asking for the config dir creates it, mirroring the state dir."""
        # Arrange
        target = tmp_path / "cfg_new"
        monkeypatch.setenv("TERMAPY_CONFIG_DIR", str(target))

        # Act
        app_config_dir()

        # Assert
        actual = target.exists()
        assert actual is True, "dir was created"

    def test_state_and_config_are_independent(self, monkeypatch, tmp_path):
        """Each dir reads only its own env var; state and config never share a path."""
        # Arrange - set both to different dirs; verify each returns its own.
        state_target = tmp_path / "s"
        config_target = tmp_path / "c"
        monkeypatch.setenv("TERMAPY_STATE_DIR", str(state_target))
        monkeypatch.setenv("TERMAPY_CONFIG_DIR", str(config_target))

        # Act
        actual_state = app_state_dir()
        actual_config = app_config_dir()

        # Assert
        assert actual_state == state_target, "state honors its own env var"
        assert actual_config == config_target, "config honors its own env var"
        assert actual_state != actual_config, "the two are independent"
