"""Tests for resolve_config - command-line config resolution chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from termapy.config_resolve import (
    find_config,
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


def _tree(root: Path) -> list[str]:
    """Every path under ``root``, relative and sorted: a snapshot to diff."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


class TestFindConfig:
    def test_find_config_writes_nothing(self, tmp_path, monkeypatch):
        """reads-never-write: the startup scan finds the config and changes no file (``--check`` relies on it)."""
        # Arrange -- one config, plus a pre-.cfg folder the scan once renamed
        monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))
        cfg = _make_cfg(tmp_path, "bench")
        legacy = tmp_path / "old" / "old.json"
        legacy.parent.mkdir()
        legacy.write_text("{}")
        expected = _tree(tmp_path)

        # Act
        found, _ = find_config()

        # Assert
        assert found == str(cfg), "the one config is auto-loaded"
        assert _tree(tmp_path) == expected, "nothing renamed or created by a scan"


class TestResolveConfigExactFile:
    """Rule 1: exact file path."""

    def test_absolute_cfg_path(self, tmp_path):
        """An absolute path to an existing cfg file is returned unchanged."""
        # Arrange
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(cfg))

        # Assert
        assert actual == str(cfg), "returns the exact path"

    def test_relative_cfg_path(self, tmp_path, monkeypatch):
        """A cwd-relative path into a config folder resolves to that same cfg file."""
        # Arrange
        monkeypatch.chdir(tmp_path)
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(cfg.relative_to(tmp_path)))

        # Assert
        assert actual is not None, "resolver returned a path"
        assert Path(actual).resolve() == cfg.resolve(), "relative path resolves to same file"

    def test_cfg_extension_explicit(self, tmp_path, monkeypatch):
        """A loose name.cfg in cwd is returned as typed, without a config folder around it."""
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
        """Naming a config folder selects the <folder>.cfg inside it."""
        # Arrange
        cfg = _make_cfg(tmp_path, "demo")

        # Act
        actual = _resolve_config(str(tmp_path / "demo"))

        # Assert
        assert actual == str(cfg), "directory resolves to <dirname>.cfg inside it"

    def test_directory_without_matching_cfg(self, tmp_path):
        """A folder holding no <folder>.cfg resolves to None rather than a guess."""
        # Arrange
        folder = tmp_path / "empty_dir"
        folder.mkdir()

        # Act
        actual = _resolve_config(str(folder))

        # Assert
        assert actual is None, "directory without matching .cfg returns None"

    def test_nested_relative_directory(self, tmp_path, monkeypatch):
        """A multi-segment relative folder path such as termapy_cfg/demo still resolves."""
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
        """A bare config name is found under the configured cfg dir."""
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
        """name.cfg with no file in cwd falls through to the cfg dir by its stem."""
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
        """When the cfg dir lacks the name, ./termapy_cfg/<name>/<name>.cfg is used."""
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
        """A bare name with no config folder anywhere finds name.cfg in cwd."""
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
        """A missing name.cfg resolves to None; .cfg is never appended a second time."""
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
        """A typo with no match anywhere in the chain resolves to None."""
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))

        # Act
        actual = _resolve_config("typo_name")

        # Assert
        assert actual is None, "nonexistent name returns None"

    def test_nonexistent_path(self, tmp_path, monkeypatch):
        """A path-shaped argument to a missing file resolves to None."""
        # Arrange
        monkeypatch.chdir(tmp_path)
        import termapy.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CFG_DIR", str(tmp_path / "nonexistent"))

        # Act
        actual = _resolve_config("/no/such/path/device.cfg")

        # Assert
        assert actual is None, "nonexistent path returns None"

    def test_directory_wrong_cfg_name(self, tmp_path, monkeypatch):
        """A folder's cfg must share the folder's name; any other .cfg there is ignored."""
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
        """An existing file named exactly as typed wins over a cfg-dir config of that name."""
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
        """The configured cfg dir wins over ./termapy_cfg when both hold the name."""
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
        """A config's proto/ folder is a sibling of its cfg file."""
        # Arrange
        config_path = tmp_path / "proj" / "proj.cfg"

        # Act
        actual = proto_dir_for(str(config_path))

        # Assert
        assert actual == tmp_path / "proj" / "proto", "proto/ is a sibling of the cfg"

    def test_does_not_require_the_folder_to_exist(self, tmp_path):
        """proto_dir_for is pure path math: it neither needs nor creates the folder."""
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
        """--proto smoke.pro finds it in the config's proto/ folder as typed, never as smoke.pro.pro."""
        # Arrange
        config_path = self._cfg(tmp_path)
        script = config_path.parent / "proto" / "smoke.pro"
        script.write_text("", encoding="utf-8")

        # Act
        actual = resolve_proto_path("smoke.pro", str(config_path))

        # Assert
        assert actual == script, "resolves inside the config's proto/ folder"

    def test_appends_the_extension(self, tmp_path):
        """--proto smoke finds smoke.pro, so the extension is optional."""
        # Arrange -- `--proto smoke` must behave like `--proto smoke.pro`
        config_path = self._cfg(tmp_path)
        script = config_path.parent / "proto" / "smoke.pro"
        script.write_text("", encoding="utf-8")

        # Act
        actual = resolve_proto_path("smoke", str(config_path))

        # Assert
        assert actual == script, "a bare name gets .pro appended"

    def test_direct_path_wins_over_the_proto_folder(self, tmp_path, monkeypatch):
        """A script path relative to the shell beats a same-named file in proto/."""
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
        assert actual is not None, "a path was resolved"
        assert actual.read_text(encoding="utf-8") == "from cwd", (
            "a path relative to the shell takes precedence over proto/"
        )

    def test_missing_script_returns_none(self, tmp_path):
        """An unfound script yields None so the caller owns the message and exit code."""
        # Arrange
        config_path = self._cfg(tmp_path)

        # Act
        actual = resolve_proto_path("nope", str(config_path))

        # Assert -- None, not an exception and not sys.exit: the caller owns
        # the error message and the exit code
        assert actual is None, "an unresolvable name resolves to None"

    def test_missing_proto_folder_is_not_an_error(self, tmp_path):
        """A config with no proto/ folder resolves to None rather than raising."""
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
        """A bare script name is normalized to carry .pro."""
        assert proto_script_name("smoke") == "smoke.pro", "bare name gets .pro"

    def test_leaves_an_explicit_extension(self):
        """A name that already ends in .pro is left as is."""
        assert proto_script_name("smoke.pro") == "smoke.pro", "no doubling"

    def test_error_message_reports_the_searched_name(self, tmp_path, capsys):
        """The --proto not-found message names smoke.pro, the file searched, not the bare input."""
        # Arrange -- regression guard: an earlier refactor reported the
        # un-normalized name the user typed.  Drives the real headless
        # entry, which prints the message and exits 2.
        import json
        from types import SimpleNamespace

        from termapy.app import _run_proto_headless
        from termapy.defaults import DEFAULT_CFG

        config_path = tmp_path / "proj" / "proj.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(DEFAULT_CFG), encoding="utf-8")
        args = SimpleNamespace(demo=False, config=str(config_path), proto="smoke")

        # Act
        with pytest.raises(SystemExit) as exc:
            _run_proto_headless(args)

        # Assert
        assert exc.value.code == 2, "missing proto file is a usage-level exit"
        message = capsys.readouterr().err.splitlines()[0]
        assert message.startswith("termapy: proto file not found:"), message
        assert message.endswith("smoke.pro"), "the message names the normalized file"
