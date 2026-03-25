"""Tests for _resolve_config - command-line config resolution chain."""

from __future__ import annotations

from pathlib import Path

from termapy.app import _resolve_config


def _make_cfg(base: Path, name: str) -> Path:
    """Create a termapy config folder structure: base/<name>/<name>.cfg."""
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    cfg = folder / f"{name}.cfg"
    cfg.write_text("{}")
    return cfg


class TestResolveConfigExactFile:
    """Rule 1: exact file path."""

    def test_absolute_cfg_path(self, tmp_path):
        # Arrange
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(cfg))

        # Assert - returns the exact path
        assert actual == str(cfg)

    def test_relative_cfg_path(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(cfg.relative_to(tmp_path)))

        # Assert
        assert Path(actual).resolve() == cfg.resolve()

    def test_cfg_extension_explicit(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        cfg = tmp_path / "my_device.cfg"
        cfg.write_text("{}")

        # Act
        actual = _resolve_config("my_device.cfg")

        # Assert
        assert actual == "my_device.cfg"


class TestResolveConfigDirectory:
    """Rule 2: directory containing <dirname>.cfg."""

    def test_directory_with_matching_cfg(self, tmp_path):
        # Arrange
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(tmp_path / "demo"))

        # Assert
        assert actual == str(cfg)

    def test_directory_without_matching_cfg(self, tmp_path):
        # Arrange
        folder = tmp_path / "empty_dir"
        folder.mkdir()

        # Act
        actual = _resolve_config(str(folder))

        # Assert - falls through, directory exists but no matching .cfg
        assert actual is None

    def test_nested_relative_directory(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        cfg_root = tmp_path / "termapy_cfg"
        cfg = _make_cfg(cfg_root, "demo")

        # Act
        actual = _resolve_config("termapy_cfg/demo")

        # Assert
        assert Path(actual).resolve() == cfg.resolve()


class TestResolveConfigCfgDir:
    """Rule 3: cfg_dir/<name>/<name>.cfg via configured cfg dir."""

    def test_bare_name(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "termapy_cfg"))
        cfg = _make_cfg(tmp_path / "termapy_cfg", "demo")

        # Act
        actual = _resolve_config("demo")

        # Assert
        assert Path(actual).resolve() == cfg.resolve()

    def test_bare_name_with_cfg_extension(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "termapy_cfg"))
        cfg = _make_cfg(tmp_path / "termapy_cfg", "demo")

        # Act - stem of "demo.cfg" is "demo"
        actual = _resolve_config("demo.cfg")

        # Assert
        assert Path(actual).resolve() == cfg.resolve()


class TestResolveConfigCwdFallback:
    """Rule 4: ./termapy_cfg/<name>/<name>.cfg via cwd."""

    def test_cwd_termapy_cfg(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        # Set cfg_dir to something that doesn't exist
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))
        cfg = _make_cfg(tmp_path / "termapy_cfg", "demo")

        # Act
        actual = _resolve_config("demo")

        # Assert
        assert Path(actual).resolve() == cfg.resolve()


class TestResolveConfigAppendCfg:
    """Rule 5: append .cfg to bare name."""

    def test_append_cfg_extension(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))
        cfg = tmp_path / "my_device.cfg"
        cfg.write_text("{}")

        # Act
        actual = _resolve_config("my_device")

        # Assert
        assert Path(actual).resolve() == cfg.resolve()

    def test_no_double_cfg_extension(self, tmp_path, monkeypatch):
        # Arrange - name already ends in .cfg, rule 5 should not add another
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))

        # Act
        actual = _resolve_config("nonexistent.cfg")

        # Assert
        assert actual is None


class TestResolveConfigNotFound:
    """Rule 6: returns None when nothing matches."""

    def test_nonexistent_name(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))

        # Act
        actual = _resolve_config("typo_name")

        # Assert
        assert actual is None

    def test_nonexistent_path(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))

        # Act
        actual = _resolve_config("/no/such/path/device.cfg")

        # Assert
        assert actual is None

    def test_directory_wrong_cfg_name(self, tmp_path, monkeypatch):
        # Arrange - directory "demo" exists but contains "other.cfg" not "demo.cfg"
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))
        folder = tmp_path / "demo"
        folder.mkdir()
        (folder / "other.cfg").write_text("{}")

        # Act
        actual = _resolve_config(str(folder))

        # Assert - rule 2 looks for demo.cfg, not other.cfg
        assert actual is None


class TestResolveConfigPriority:
    """Resolution chain priority - earlier rules win."""

    def test_exact_file_beats_directory(self, tmp_path, monkeypatch):
        # Arrange - "demo" is both a file and a directory name in cfg_dir
        monkeypatch.chdir(tmp_path)
        exact = tmp_path / "demo"
        exact.write_text("{}")  # file named "demo" in cwd
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "termapy_cfg"))
        _make_cfg(tmp_path / "termapy_cfg", "demo")

        # Act
        actual = _resolve_config("demo")

        # Assert - rule 1 (exact file) wins over rule 3 (cfg_dir)
        assert Path(actual).resolve() == exact.resolve()

    def test_cfg_dir_beats_cwd_fallback(self, tmp_path, monkeypatch):
        # Arrange - same name exists in both cfg_dir and cwd/termapy_cfg
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        cfg_dir_path = tmp_path / "configured_cfg"
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(cfg_dir_path))
        cfg_a = _make_cfg(cfg_dir_path, "demo")
        _make_cfg(tmp_path / "termapy_cfg", "demo")

        # Act
        actual = _resolve_config("demo")

        # Assert - rule 3 (cfg_dir) wins over rule 4 (cwd/termapy_cfg)
        assert Path(actual).resolve() == cfg_a.resolve()
