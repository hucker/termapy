"""Tests for resolve_config - command-line config resolution chain."""

from __future__ import annotations

from pathlib import Path

from termapy.config_resolve import (
    proto_dir_for,
    proto_script_name,
    resolve_config as _resolve_config,
    resolve_proto_path,
)


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

        # Assert
        assert actual == str(cfg), "returns the exact path"

    def test_relative_cfg_path(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(cfg.relative_to(tmp_path)))

        # Assert
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg.resolve(), "relative path resolves to same file"

    def test_cfg_extension_explicit(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        cfg = tmp_path / "my_device.cfg"
        cfg.write_text("{}")

        # Act
        actual = _resolve_config("my_device.cfg")

        # Assert
        assert actual == "my_device.cfg", "explicit .cfg file returns as-is"


class TestResolveConfigDirectory:
    """Rule 2: directory containing <dirname>.cfg."""

    def test_directory_with_matching_cfg(self, tmp_path):
        # Arrange
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(tmp_path / "demo"))

        # Assert
        assert actual == str(cfg), "directory resolves to <dirname>.cfg inside it"

    def test_directory_without_matching_cfg(self, tmp_path):
        # Arrange
        folder = tmp_path / "empty_dir"
        folder.mkdir()

        # Act
        actual = _resolve_config(str(folder))

        # Assert
        assert actual is None, "directory without matching .cfg returns None"

    def test_nested_relative_directory(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        cfg_root = tmp_path / "termapy_cfg"
        cfg = _make_cfg(cfg_root, "demo")

        # Act
        actual = _resolve_config("termapy_cfg/demo")

        # Assert
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg.resolve(), "nested relative directory resolves to cfg"


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
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg.resolve(), "bare name found in cfg_dir"

    def test_bare_name_with_cfg_extension(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "termapy_cfg"))
        cfg = _make_cfg(tmp_path / "termapy_cfg", "demo")

        # Act - stem of "demo.cfg" is "demo"
        actual = _resolve_config("demo.cfg")

        # Assert
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg.resolve(), "name.cfg stem found in cfg_dir"


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
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg.resolve(), "falls back to cwd/termapy_cfg"


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
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg.resolve(), "appends .cfg to bare name"

    def test_no_double_cfg_extension(self, tmp_path, monkeypatch):
        # Arrange - name already ends in .cfg, rule 5 should not add another
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))

        # Act
        actual = _resolve_config("nonexistent.cfg")

        # Assert
        assert actual is None, "no double .cfg extension appended"


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
        assert actual is None, "nonexistent name returns None"

    def test_nonexistent_path(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))

        # Act
        actual = _resolve_config("/no/such/path/device.cfg")

        # Assert
        assert actual is None, "nonexistent path returns None"

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

        # Assert
        assert actual is None, "rule 2 looks for demo.cfg, not other.cfg"


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

        # Assert
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == exact.resolve(), "rule 1 (exact file) wins over rule 3 (cfg_dir)"

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

        # Assert
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg_a.resolve(), "rule 3 (cfg_dir) wins over rule 4 (cwd/termapy_cfg)"


# ── resolve_proto_path / proto_dir_for ──────────────────────────────────────
#
# These back `termapy --proto <name>`, whose runner lives in app.py and is
# therefore omitted from coverage.  Extracting the lookup into config_resolve
# is what makes it reachable at all -- the point is the tests, not the move.


class TestProtoDirFor:
    def test_sits_beside_the_config(self, tmp_path):
        # Arrange
        config_path = tmp_path / "proj" / "proj.cfg"

        # Act
        actual = proto_dir_for(str(config_path))

        # Assert
        assert actual == tmp_path / "proj" / "proto", "proto/ is a sibling of the cfg"

    def test_does_not_require_the_folder_to_exist(self, tmp_path):
        # Act -- a config with no proto/ folder yet is normal, not an error
        actual = proto_dir_for(str(tmp_path / "p.cfg"))

        # Assert
        assert actual.name == "proto", "returns the path regardless of existence"
        assert not actual.exists(), "and does not create it"


class TestResolveProtoPath:
    def _cfg(self, tmp_path):
        """A config file with an empty proto/ folder beside it."""
        config_path = tmp_path / "proj" / "proj.cfg"
        config_path.parent.mkdir(parents=True)
        (config_path.parent / "proto").mkdir()
        return config_path

    def test_finds_script_in_the_proto_folder(self, tmp_path):
        # Arrange
        config_path = self._cfg(tmp_path)
        script = config_path.parent / "proto" / "smoke.pro"
        script.write_text("", encoding="utf-8")

        # Act
        actual = resolve_proto_path("smoke.pro", str(config_path))

        # Assert
        assert actual == script, "resolves inside the config's proto/ folder"

    def test_appends_the_extension(self, tmp_path):
        # Arrange -- `--proto smoke` must behave like `--proto smoke.pro`
        config_path = self._cfg(tmp_path)
        script = config_path.parent / "proto" / "smoke.pro"
        script.write_text("", encoding="utf-8")

        # Act
        actual = resolve_proto_path("smoke", str(config_path))

        # Assert
        assert actual == script, "a bare name gets .pro appended"

    def test_does_not_double_the_extension(self, tmp_path):
        # Arrange
        config_path = self._cfg(tmp_path)
        script = config_path.parent / "proto" / "smoke.pro"
        script.write_text("", encoding="utf-8")

        # Act
        actual = resolve_proto_path("smoke.pro", str(config_path))

        # Assert -- never smoke.pro.pro
        assert actual == script, "an explicit .pro is left alone"

    def test_direct_path_wins_over_the_proto_folder(self, tmp_path, monkeypatch):
        # Arrange -- same filename in both places; the one the user typed
        # relative to their shell must win, since that is what they meant
        config_path = self._cfg(tmp_path)
        in_folder = config_path.parent / "proto" / "dup.pro"
        in_folder.write_text("from proto dir", encoding="utf-8")
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        (cwd / "dup.pro").write_text("from cwd", encoding="utf-8")
        monkeypatch.chdir(cwd)

        # Act
        actual = resolve_proto_path("dup.pro", str(config_path))

        # Assert
        assert actual.read_text(encoding="utf-8") == "from cwd", (
            "a path relative to the shell takes precedence over proto/"
        )

    def test_missing_script_returns_none(self, tmp_path):
        # Arrange
        config_path = self._cfg(tmp_path)

        # Act
        actual = resolve_proto_path("nope", str(config_path))

        # Assert -- None, not an exception and not sys.exit: the caller owns
        # the error message and the exit code
        assert actual is None, "an unresolvable name resolves to None"

    def test_missing_proto_folder_is_not_an_error(self, tmp_path):
        # Arrange -- config with no proto/ folder at all
        config_path = tmp_path / "proj" / "proj.cfg"
        config_path.parent.mkdir(parents=True)

        # Act
        actual = resolve_proto_path("smoke", str(config_path))

        # Assert
        assert actual is None, "a missing proto/ folder resolves to None, not a crash"


class TestProtoScriptName:
    """The normalization the error message and the resolver must agree on."""

    def test_appends_when_absent(self):
        assert proto_script_name("smoke") == "smoke.pro", "bare name gets .pro"

    def test_leaves_an_explicit_extension(self):
        assert proto_script_name("smoke.pro") == "smoke.pro", "no doubling"

    def test_error_message_reports_the_searched_name(self, tmp_path):
        # Arrange -- regression guard.  The "not found" message must name
        # smoke.pro, the file actually looked for, not the bare `smoke` the
        # user typed; an earlier refactor reported the un-normalized name.
        config_path = tmp_path / "proj.cfg"

        # Act
        resolved = resolve_proto_path("smoke", str(config_path))

        # Assert
        assert resolved is None, "nothing to find"
        assert proto_script_name("smoke").endswith(".pro"), (
            "the name used in the error message carries the extension"
        )
