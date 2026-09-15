"""The /cfg.info project report: its tails, and the exit hook that rewrites it.

The report is a plugin artifact, so it is exercised through the real
dispatcher and the real lifecycle on a ``ReplEngine`` over a temp config,
never by calling the writer directly.  Log and history files are written
to disk the way the frontends leave them, then the report is read back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.config import cfg_history_path, cfg_log_path, rename_config
from termapy.folders import INFO_REPORT_SUFFIX
from termapy.plugins import CapabilitySet, InternalHandle, IOHandle, PluginContext
from termapy.repl import ReplEngine


def _build(tmp_path: Path, *, interactive: bool = True, oneshot: bool = False):
    """Engine, config path and captured output over ``<tmp>/rig/rig.cfg``."""
    cfg = {
        "serial": {
            "port": "", "baud_rate": 115200, "custom_baud": False, "byte_size": 8,
            "parity": "N", "stop_bits": 1, "flow_control": "none",
        },
        "echo": False,
        "eol": "\r",
    }
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    output: list[tuple[str, str | None]] = []

    def write(text, color=None):
        output.append((text, color))

    # global_root: the checkout has a real termapy_cfg/plugin/; keep it out.
    engine = ReplEngine(cfg, str(config_path), write, global_root=tmp_path)
    internal = InternalHandle(
        plugins=engine._plugins,
        in_script=lambda: engine.in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        dispatch=engine.dispatch,
    )
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        internal=internal,
        io=IOHandle(_write=write, _write_markup=lambda text: output.append((text, "markup"))),
        capabilities=CapabilitySet(interactive=interactive, filesystem_unconfined=True),
        dispatch=engine.dispatch,
        is_oneshot=lambda: oneshot,
    )
    engine.set_context(ctx)
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "normal"
    flags["hex"] = False
    return engine, config_path, output


def _report(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.stem}{INFO_REPORT_SUFFIX}")


def _lines(n: int, prefix: str) -> str:
    return "".join(f"{prefix} {i}\n" for i in range(1, n + 1))


@pytest.fixture
def rig(tmp_path):
    return _build(tmp_path)


class TestReportContents:

    def test_tails_the_log_and_history(self, rig):
        # Arrange -- more lines than the tail keeps, so the cut is visible
        engine, config_path, _ = rig
        Path(cfg_log_path(str(config_path))).write_text(_lines(60, "log"), encoding="utf-8")
        Path(cfg_history_path(str(config_path))).write_text(_lines(60, "hist"), encoding="utf-8")

        # Act
        result = engine.dispatch("cfg.info")

        # Assert
        assert result.success, result.error
        text = _report(config_path).read_text(encoding="utf-8")
        assert "## Log (last 40 lines)" in text, "the log section says how much it kept"
        assert "## History (last 40 lines)" in text, "so does the history section"
        assert "log 60" in text and "hist 60" in text, "the newest lines are kept"
        assert "log 20\n" not in text and "hist 20\n" not in text, "older lines are cut"
        assert "log 21\n" in text and "hist 21\n" in text, "exactly the last 40"

    def test_sections_absent_when_files_are(self, rig):
        # Arrange -- a fresh config: no log, no history yet
        engine, config_path, _ = rig

        # Act
        engine.dispatch("cfg.info")

        # Assert
        text = _report(config_path).read_text(encoding="utf-8")
        assert "## Log" not in text and "## History" not in text, (
            "a missing file yields no section rather than an empty one"
        )
        assert text.startswith("# Project: rig"), "the report itself is still written"

    def test_fence_survives_backticks_in_the_log(self, rig):
        # Arrange -- device output can contain a markdown fence
        engine, config_path, _ = rig
        Path(cfg_log_path(str(config_path))).write_text("before\n```\nafter\n", encoding="utf-8")

        # Act
        engine.dispatch("cfg.info")

        # Assert
        text = _report(config_path).read_text(encoding="utf-8")
        assert "````text\nbefore\n```\nafter\n````" in text, (
            "a four-backtick fence contains the three-backtick line intact"
        )


class TestExitHook:

    def test_on_app_stop_writes_the_report(self, rig):
        # Arrange
        engine, config_path, _ = rig
        assert not _report(config_path).exists(), "precondition: nothing written yet"

        # Act -- what every frontend fires on graceful shutdown
        engine.fire_lifecycle("on_app_stop")

        # Assert
        assert "# Project: rig" in _report(config_path).read_text(encoding="utf-8"), (
            "the exit hook wrote the report unasked"
        )

    def test_oneshot_does_not_write(self, tmp_path):
        # Arrange -- --run / --exec: a scripted invocation must not churn files
        engine, config_path, _ = _build(tmp_path, oneshot=True)

        # Act
        engine.fire_lifecycle("on_app_stop")

        # Assert
        assert not _report(config_path).exists(), "one-shot sessions leave the report alone"

    def test_non_interactive_does_not_write(self, tmp_path):
        # Arrange -- the MCP server's capability set has no interactive flag
        engine, config_path, _ = _build(tmp_path, interactive=False)

        # Act
        engine.fire_lifecycle("on_app_stop")

        # Assert
        assert not _report(config_path).exists(), (
            "a server loading a config must not write into its folder as a side effect"
        )

    def test_hook_is_silent(self, rig):
        # Arrange
        engine, config_path, output = rig
        before = len(output)

        # Act
        engine.fire_lifecycle("on_app_stop")

        # Assert -- the explicit command is the loud form; the hook says nothing
        assert _report(config_path).exists(), "precondition: it did write"
        assert len(output) == before, "no chatter from the automatic write"


class TestRename:

    def test_rename_carries_the_report(self, tmp_path):
        # Arrange -- a report exists under the old name
        engine, config_path, _ = _build(tmp_path)
        engine.dispatch("cfg.info")
        assert _report(config_path).exists(), "precondition"

        # Act
        new_path = Path(rename_config(str(config_path), "bench"))

        # Assert
        assert (new_path.parent / f"bench{INFO_REPORT_SUFFIX}").exists(), (
            "the report follows the rename like the history sidecar"
        )
        assert not (new_path.parent / f"rig{INFO_REPORT_SUFFIX}").exists(), (
            "and no stale copy under the old name is left behind"
        )
