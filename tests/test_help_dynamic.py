"""Tests for ``help_dynamic`` helpers.

These are the building blocks for every dynamic ``long_help`` callable.
They cover:
    - green/state_line/compose string shapes
    - folder_dir resolution via ctx attributes and config_path fallback
    - file_count with the canonical pattern per folder
    - port_setting / port_status defensive behavior
    - cfg_name / cfg_count / cfg_status
    - ns_count guarding a bad ctx
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from termapy.folders import FOLDER_PATTERNS
from termapy.help_dynamic import (
    cfg_count,
    cfg_name,
    cfg_status,
    compose,
    file_count,
    folder_dir,
    folder_line,
    green,
    ns_count,
    port_setting,
    port_status,
    state_line,
)


# ─── Fixtures / tiny fakes ───────────────────────────────────────────────────

def _fake_ctx(
    tmp_path: Path,
    *,
    cfg_name: str = "demo",
    port=None,
    connected: bool = False,
):
    """Build a SimpleNamespace that looks enough like a PluginContext.

    Creates ``termapy_cfg/<cfg_name>/`` with all subfolders and a cfg file
    so the helpers have real paths to scan.
    """
    cfg_root = tmp_path / "termapy_cfg"
    cfg_dir_ = cfg_root / cfg_name
    cfg_dir_.mkdir(parents=True)
    (cfg_dir_ / f"{cfg_name}.cfg").write_text("{}", encoding="utf-8")
    for sub in ("run", "proto", "plugin", "ss", "viz", "cap", "prof"):
        (cfg_dir_ / sub).mkdir()
    ctx = SimpleNamespace(
        config_path=str(cfg_dir_ / f"{cfg_name}.cfg"),
        scripts_dir=cfg_dir_ / "run",
        proto_dir=cfg_dir_ / "proto",
        ss_dir=cfg_dir_ / "ss",
        cap_dir=cfg_dir_ / "cap",
        prof_dir=cfg_dir_ / "prof",
        port=lambda: port,
        is_connected=lambda: connected,
        ns=lambda name: {},
    )
    return ctx, cfg_dir_


class TestMarkup:
    """Trivial string-shape helpers."""

    def test_green_wraps_text(self):
        # Act
        actual = green("hello")

        # Assert
        assert actual == "[green]hello[/]", "green uses Rich markup tag"

    def test_state_line_formats_label_and_value(self):
        # Act
        actual = state_line("baud", 115200)

        # Assert
        expected = "[green]Current baud = 115200[/]"
        assert actual == expected, "state_line composes canonical shape"

    def test_compose_drops_empty_parts(self):
        # Act
        actual = compose("one", "", None, "two")

        # Assert
        assert actual == "one\n\ntwo", "compose joins non-empty with blank line"

    def test_compose_all_empty_returns_empty(self):
        # Act
        actual = compose("", "", None)

        # Assert
        assert actual == "", "compose of nothing is empty"


class TestFolderHelpers:
    """``folder_dir`` / ``file_count`` / ``folder_line``."""

    def test_folder_dir_uses_direct_ctx_attr(self, tmp_path):
        # Arrange
        ctx, cfg_dir_ = _fake_ctx(tmp_path)

        # Act
        actual = folder_dir(ctx, "run")

        # Assert
        assert actual == cfg_dir_ / "run", "run/ maps to ctx.scripts_dir"

    def test_folder_dir_falls_back_to_config_path_for_viz(self, tmp_path):
        # Arrange - viz has no direct ctx attribute; derive from config_path
        ctx, cfg_dir_ = _fake_ctx(tmp_path)

        # Act
        actual = folder_dir(ctx, "viz")

        # Assert
        assert actual == cfg_dir_ / "viz", "viz/ derived from config_path"

    def test_folder_dir_returns_none_when_ctx_has_nothing(self):
        # Arrange - empty ctx, no config_path, no scripts_dir
        ctx = SimpleNamespace(config_path="")

        # Act
        actual = folder_dir(ctx, "viz")

        # Assert
        assert actual is None, "no state, no dir"

    def test_file_count_uses_folder_pattern(self, tmp_path):
        # Arrange - create 3 .run files + 1 non-matching file
        ctx, cfg_dir_ = _fake_ctx(tmp_path)
        for i in range(3):
            (cfg_dir_ / "run" / f"s{i}.run").write_text("x", encoding="utf-8")
        (cfg_dir_ / "run" / "ignored.txt").write_text("x", encoding="utf-8")

        # Act
        actual = file_count(ctx, "run")

        # Assert - FOLDER_PATTERNS["run"] is "*.run"
        expected = 3
        assert actual == expected, "file_count respects *.run glob"
        assert FOLDER_PATTERNS["run"] == "*.run", "run pattern sanity check"

    def test_file_count_zero_when_folder_missing(self, tmp_path):
        # Arrange - no folder at all
        ctx = SimpleNamespace(config_path=str(tmp_path / "missing" / "x.cfg"))

        # Act
        actual = file_count(ctx, "viz")

        # Assert
        assert actual == 0, "missing folder yields zero, not error"

    def test_folder_line_pluralizes(self, tmp_path):
        # Arrange - 0, 1, 2 files -> "files", "file", "files"
        ctx, cfg_dir_ = _fake_ctx(tmp_path)

        # Act / Assert - zero
        actual_zero = folder_line(ctx, "run")
        assert actual_zero == "[green]0 files in run/[/]", "zero plural"

        # Act / Assert - one
        (cfg_dir_ / "run" / "a.run").write_text("x", encoding="utf-8")
        actual_one = folder_line(ctx, "run")
        assert actual_one == "[green]1 file in run/[/]", "singular"

        # Act / Assert - two
        (cfg_dir_ / "run" / "b.run").write_text("x", encoding="utf-8")
        actual_two = folder_line(ctx, "run")
        assert actual_two == "[green]2 files in run/[/]", "plural"

    def test_folder_line_custom_noun(self, tmp_path):
        # Arrange
        ctx, cfg_dir_ = _fake_ctx(tmp_path)
        (cfg_dir_ / "ss" / "cap.txt").write_text("x", encoding="utf-8")

        # Act
        actual = folder_line(ctx, "ss", noun="screenshot")

        # Assert
        assert actual == "[green]1 screenshot in ss/[/]", "custom noun respected"


class TestPortHelpers:
    """``port_setting`` / ``port_status`` against a fake pyserial."""

    def test_port_setting_returns_none_when_port_missing(self, tmp_path):
        # Arrange
        ctx, _ = _fake_ctx(tmp_path, port=None)

        # Act
        actual = port_setting(ctx, "baudrate")

        # Assert
        assert actual is None, "no port => None, no exception"

    def test_port_setting_reads_live_attr(self, tmp_path):
        # Arrange - fake port object with baudrate
        fake = SimpleNamespace(baudrate=9600, bytesize=8)
        ctx, _ = _fake_ctx(tmp_path, port=fake)

        # Act
        actual = port_setting(ctx, "baudrate")

        # Assert
        assert actual == 9600, "reads live pyserial attribute"

    def test_port_status_not_connected_when_closed(self, tmp_path):
        # Arrange
        ctx, _ = _fake_ctx(tmp_path, connected=False)

        # Act
        actual = port_status(ctx)

        # Assert
        assert actual == "[green]Not connected[/]", "closed port -> canned text"

    def test_port_status_summary_when_open(self, tmp_path):
        # Arrange
        fake = SimpleNamespace(
            port="COM3", baudrate=115200, bytesize=8, parity="N", stopbits=1,
        )
        ctx, _ = _fake_ctx(tmp_path, port=fake, connected=True)

        # Act
        actual = port_status(ctx)

        # Assert
        expected = "[green]Connected: COM3 @ 115200 8N1[/]"
        assert actual == expected, "open port summary shape"

    def test_port_status_degrades_if_is_connected_raises(self, tmp_path):
        # Arrange - is_connected raising should not crash the helper
        ctx, _ = _fake_ctx(tmp_path)
        ctx.is_connected = lambda: (_ for _ in ()).throw(RuntimeError("x"))

        # Act
        actual = port_status(ctx)

        # Assert
        assert actual == "[green]Not connected[/]", "exception -> safe default"


class TestCfgHelpers:
    """``cfg_name`` / ``cfg_count`` / ``cfg_status``."""

    def test_cfg_name_from_config_path(self, tmp_path):
        # Arrange
        ctx, _ = _fake_ctx(tmp_path, cfg_name="acme")

        # Act
        actual = cfg_name(ctx)

        # Assert
        assert actual == "acme", "name is the parent directory's stem"

    def test_cfg_name_empty_when_no_path(self):
        # Arrange
        ctx = SimpleNamespace(config_path="")

        # Act
        actual = cfg_name(ctx)

        # Assert
        assert actual == "", "empty path -> empty name"

    def test_cfg_count_only_counts_dirs_with_matching_cfg(self, tmp_path):
        # Arrange - one valid config + one stray directory without cfg
        ctx, cfg_dir_ = _fake_ctx(tmp_path, cfg_name="demo")
        stray = cfg_dir_.parent / "stray"
        stray.mkdir()
        # empty subdir, no .cfg inside

        # Act
        actual = cfg_count(ctx)

        # Assert
        assert actual == 1, "only dirs containing <name>.cfg count"

    def test_cfg_count_multiple(self, tmp_path):
        # Arrange - two configs side by side
        ctx, cfg_dir_ = _fake_ctx(tmp_path, cfg_name="demo")
        other = cfg_dir_.parent / "other"
        other.mkdir()
        (other / "other.cfg").write_text("{}", encoding="utf-8")

        # Act
        actual = cfg_count(ctx)

        # Assert
        assert actual == 2, "multiple siblings counted"

    def test_cfg_status_singular_and_plural(self, tmp_path):
        # Arrange - singular
        ctx, _ = _fake_ctx(tmp_path, cfg_name="demo")

        # Act
        actual = cfg_status(ctx)

        # Assert
        expected = "[green]Active cfg = demo (1 config available)[/]"
        assert actual == expected, "singular form for one"

    def test_cfg_status_empty_when_no_active(self):
        # Arrange
        ctx = SimpleNamespace(config_path="")

        # Act
        actual = cfg_status(ctx)

        # Assert
        assert actual == "", "no active -> compose drops it"


class TestNsCount:
    def test_ns_count_returns_len(self):
        # Arrange
        ctx = SimpleNamespace(ns=lambda name: {"a": 1, "b": 2})

        # Act
        actual = ns_count(ctx, "whatever")

        # Assert
        assert actual == 2, "returns length of namespace dict"

    def test_ns_count_zero_when_ns_raises(self):
        # Arrange
        def _bad(name):
            raise RuntimeError("bad ctx")

        ctx = SimpleNamespace(ns=_bad)

        # Act
        actual = ns_count(ctx, "whatever")

        # Assert
        assert actual == 0, "bad ns -> zero, no exception"
