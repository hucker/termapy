"""The data-folder rule, driven end to end from a config with NO data folders.

A data folder exists while something is in it (``termapy.folders``):
``ensure_folder`` at every write, ``prune_empty_folders`` at config load and
app stop.  ``cfg_data_dir`` creates none, so every test here starts from a
bare ``rig/rig.cfg`` and asserts that the writer under test brings its folder
into being -- that is what keeps the next writer honest -- and that the prune
takes only what the rule says it may.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Input

from termapy.builtins.commands.cfg import _all_sections
from termapy.cli import CLITerminal
from termapy.config import global_plugins_dir
from termapy.defaults import DEFAULT_CFG
from termapy.dialogs import ProtoEditor, ScriptEditor
from termapy.folders import FOLDER_NAMES, ensure_folder, prune_empty_folders
from termapy.plugins import CapabilitySet

XC32_MAP = Path(__file__).parent / "fixtures" / "maps" / "xc32_sample.map"


def _write_cfg(tmp_path: Path, name: str = "rig") -> tuple[dict, Path]:
    """A DEMO-port config on disk with NOTHING beside it: no data folder at all."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200},
        "eol": "\r",
        # The TUI tests boot the app; chasing a port would add the demo
        # device's timing to what is a filesystem test.
        "auto_connect": False,
    }
    config_path = tmp_path / name / f"{name}.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    cfg["_config_path"] = str(config_path)
    return cfg, config_path


def _data_folders(config_path: Path) -> list[str]:
    """The folders that exist beside ``config_path``, sorted."""
    return sorted(path.name for path in config_path.parent.iterdir() if path.is_dir())


def _grant_gui_apps(ctx) -> list[str]:
    """Grant ``gui_apps`` on ``ctx`` and record what ``ctx.fs.open_file`` opens.

    Mirrors ``tests/test_cli.py``: CapabilitySet is frozen, so union a fresh
    set into both snapshots.  ``_open_file_impl`` is the host's seam (wired
    to ``open_with_system`` in production); a list stands in for the file
    manager here.
    """
    caps = ctx.capabilities.union(CapabilitySet(gui_apps=True))
    ctx.capabilities = caps
    ctx.fs.capabilities = caps
    opened: list[str] = []
    ctx.fs._open_file_impl = lambda path: opened.append(str(path))
    return opened


@pytest.fixture
def cli(tmp_path):
    """A connected CLI host over a bare config: zero data folders at start."""
    cfg, config_path = _write_cfg(tmp_path)
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    terminal.repl.fire_lifecycle("on_app_start")
    assert _data_folders(config_path) == [], "precondition: loading created no data folder"
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


def _run(scenario) -> None:
    """Run one async Pilot scenario (the suite is otherwise sync)."""
    asyncio.run(scenario())


class _Host(App):
    """Bare host: no widgets of its own, just a stage for a modal."""


# -- The two operations ------------------------------------------------------


class TestEnsureFolder:
    def test_creates_nested_parents_and_returns_the_folder(self, tmp_path):
        # Arrange
        folder = tmp_path / "rig" / "cap"

        # Act
        actual = ensure_folder(folder)

        # Assert
        assert actual == folder, "returns the folder so it composes: ensure_folder(d) / name"
        assert folder.is_dir(), "created, parents included"

    def test_idempotent_and_content_preserving(self, tmp_path):
        # Arrange
        folder = ensure_folder(tmp_path / "cap")
        (folder / "keep.txt").write_text("x", encoding="utf-8")

        # Act
        ensure_folder(folder)

        # Assert
        assert (folder / "keep.txt").is_file(), "a second call leaves the contents alone"


class TestPruneEmptyFolders:
    def test_removes_only_empty_folders_named_in_folders(self, tmp_path):
        # Arrange -- every data folder empty except run/ (a file) and sym/ (a dotfile),
        # plus three folders that are not termapy's to remove.
        for name in FOLDER_NAMES:
            (tmp_path / name).mkdir()
        (tmp_path / "run" / "hello.run").write_text("/print hi\n", encoding="utf-8")
        (tmp_path / "sym" / ".gitkeep").write_text("", encoding="utf-8")
        for name in ("mcp", "custom", "scripts"):
            (tmp_path / name).mkdir()

        # Act
        removed = prune_empty_folders(tmp_path, FOLDER_NAMES)

        # Assert
        expected_removed = sorted(set(FOLDER_NAMES) - {"run", "sym"})
        assert sorted(removed) == expected_removed, "reports exactly the empties it took"
        actual = sorted(path.name for path in tmp_path.iterdir() if path.is_dir())
        expected = ["custom", "mcp", "run", "scripts", "sym"]
        assert actual == expected, (
            "a file, a lone dotfile, and any folder not in FOLDERS all keep their folder"
        )

    def test_missing_root_is_a_no_op(self, tmp_path):
        # Act
        actual = prune_empty_folders(tmp_path / "nope", FOLDER_NAMES)

        # Assert
        assert actual == [], "nothing to prune, nothing raised"

    def test_global_plugins_dir_is_a_path_not_a_creation(self, tmp_path):
        # Act
        actual = global_plugins_dir(tmp_path)

        # Assert
        assert actual == tmp_path / "plugin", "the cfg root's plugin/ path"
        assert not actual.exists(), "a read: resolving the path creates nothing"


# -- When the prune runs: config load and app stop, in every frontend ----------


class TestPruneAtLoadAndStop:
    def test_cli_config_load_drops_empty_data_folders_and_keeps_the_rest(self, tmp_path):
        # Arrange -- the layout an older termapy left behind, plus folders that are
        # not termapy's, plus an empty global plugin/ on the cfg root.
        cfg, config_path = _write_cfg(tmp_path)
        for name in (*FOLDER_NAMES, "mcp", "custom"):
            (config_path.parent / name).mkdir()
        (tmp_path / "plugin").mkdir()
        terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
        terminal.repl.global_root = tmp_path

        # Act -- the lifecycle every frontend fires once the config is current
        terminal.repl.fire_lifecycle("on_app_start")

        # Assert
        actual = _data_folders(config_path)
        expected = ["custom", "mcp"]
        assert actual == expected, "every empty data folder is gone; mcp/ and the user's stay"
        assert not (tmp_path / "plugin").exists(), "the global plugin/ follows the same rule"

    def test_app_stop_drops_a_folder_the_user_emptied_during_the_session(self, cli):
        # Arrange -- a capture landed in cap/, then the user cleared it
        parent = Path(cli.config_path).parent
        (ensure_folder(parent / "cap") / "old.txt").write_text("x", encoding="utf-8")
        cleared = cli.repl.dispatch("cap.clear")
        assert cleared.success and (parent / "cap").is_dir(), (
            "precondition: /cap.clear empties the folder but does not remove it"
        )

        # Act
        cli.repl.fire_lifecycle("on_app_stop")

        # Assert
        assert not (parent / "cap").exists(), "an emptied folder does not survive the session"

    def test_tui_boot_drops_empty_data_folders(self, tmp_path):
        async def scenario():
            from termapy.app import SerialTerminal

            # Arrange
            cfg, config_path = _write_cfg(tmp_path)
            for name in (*FOLDER_NAMES, "custom"):
                (config_path.parent / name).mkdir()
            app = SerialTerminal(cfg, str(config_path))
            app.repl.global_root = tmp_path

            # Act -- boot fires on_app_start through the same engine wiring
            async with app.run_test() as pilot:
                await pilot.pause()

                # Assert
                actual = _data_folders(config_path)
                assert actual == ["custom"], "the TUI prunes at boot exactly as the CLI does"

        _run(scenario)


# -- Every writer creates its folder (writes always create) --------------------


class TestCliWritersCreateTheirFolder:
    def test_cap_text_creates_cap(self, cli):
        # Act
        result = cli.repl.dispatch("cap.text out.txt timeout=5s")
        cli.repl.dispatch("cap.stop")

        # Assert
        assert result.success, f"/cap.text starts from a bare config: {result.error}"
        assert (Path(cli.config_path).parent / "cap" / "out.txt").is_file(), (
            "cap/ appeared with the capture file in it"
        )

    def test_run_record_creates_run(self, cli):
        # Act
        result = cli.repl.dispatch("run.record session")
        cli.repl.dispatch("run.record")  # stop

        # Assert
        assert result.success, f"/run.record starts from a bare config: {result.error}"
        assert (Path(cli.config_path).parent / "run" / "session.run").is_file(), (
            "run/ appeared with the recording in it"
        )

    def test_run_profile_creates_prof(self, cli):
        # Arrange -- the user's script; the folder it sits in is theirs to make
        parent = Path(cli.config_path).parent
        (parent / "run").mkdir()
        (parent / "run" / "hello.run").write_text("/print hi\n", encoding="utf-8")

        # Act
        result = cli.repl.dispatch("run.profile hello.run")

        # Assert
        assert result.success, f"/run.profile runs from a bare config: {result.error}"
        assert list((parent / "prof").glob("*.csv")), "prof/ appeared with the timing CSV"

    def test_run_profile_cmd_creates_run_and_prof(self, cli):
        # Act
        result = cli.repl.dispatch("run.profile.cmd /print hi")

        # Assert
        parent = Path(cli.config_path).parent
        assert result.success, f"/run.profile.cmd runs from a bare config: {result.error}"
        assert (parent / "run").is_dir(), "run/ appeared for the temporary script"
        assert list((parent / "prof").glob("*.csv")), "prof/ appeared with the timing CSV"

    def test_run_edit_creates_run_before_opening_the_new_file(self, cli):
        # Arrange
        opened = _grant_gui_apps(cli.ctx)

        # Act
        result = cli.repl.dispatch("run.edit fresh")

        # Assert
        expected = Path(cli.config_path).parent / "run" / "fresh.run"
        assert result.success, f"/run.edit opens a new script from a bare config: {result.error}"
        assert expected.parent.is_dir(), "run/ appeared so the editor has somewhere to save"
        assert opened == [str(expected)], "the new script was handed to the editor"

    def test_sym_import_creates_sym(self, cli):
        # Act
        result = cli.repl.dispatch(f"sym.import {XC32_MAP}")

        # Assert
        assert result.success, f"/sym.import writes from a bare config: {result.error}"
        assert (Path(cli.config_path).parent / "sym" / "rig.symbols.json").is_file(), (
            "sym/ appeared with the table in it"
        )

    @pytest.mark.parametrize(
        ("command", "folder"),
        [
            ("ss.explore", "ss"),
            ("cap.explore", "cap"),
            ("run.explore", "run"),
            ("proto.explore", "proto"),
            ("plugin.explore", "plugin"),
            ("run.profile.explore", "prof"),
            ("edit.run.explore", "run"),
            ("edit.proto.explore", "proto"),
            ("edit.plugin.explore", "plugin"),
        ],
    )
    def test_explore_creates_the_folder_it_opens(self, cli, command, folder):
        """Opening a folder for the user to drop a file in is the one read that creates."""
        # Arrange
        opened = _grant_gui_apps(cli.ctx)
        expected = Path(cli.config_path).parent / folder

        # Act
        result = cli.repl.dispatch(command)

        # Assert
        assert result.success, f"/{command} succeeds from a bare config: {result.error}"
        assert expected.is_dir(), f"{folder}/ exists by the time the file manager shows it"
        assert opened == [str(expected)], "and that is the folder handed to the file manager"

    @pytest.mark.slow  # a real (simulated) YMODEM transfer through the reader thread
    def test_ymodem_recv_creates_cap(self, cli):
        # Arrange -- put the demo device in send mode, as tests/test_ymodem.py does;
        # the settle wait lets its "OK" reach the queue before the handler drains it.
        with cli.ctx.serial.io():
            cli.ctx.serial.write(b"AT+YMODEM=SEND firmware_v1.bin\r")
        time.sleep(0.05)

        # Act
        result = cli.repl.dispatch("xfer.ymodem.recv")

        # Assert
        assert result.success, f"/xfer.ymodem.recv into a bare config: {result.error}"
        assert (Path(cli.config_path).parent / "cap" / "firmware_v1.bin").is_file(), (
            "cap/ appeared with the received file in it"
        )


class TestTuiWritersCreateTheirFolder:
    def test_screenshot_actions_create_ss(self, tmp_path):
        async def scenario():
            from termapy.app import SerialTerminal

            # Arrange
            cfg, config_path = _write_cfg(tmp_path)
            app = SerialTerminal(cfg, str(config_path))
            app.repl.global_root = tmp_path
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app.action_screenshot()
                app.action_text_screenshot()

                # Assert
                ss_dir = config_path.parent / "ss"
                assert len(list(ss_dir.glob("*.svg"))) == 1, "ss/ appeared with the SVG"
                assert len(list(ss_dir.glob("*.txt"))) == 1, "and the text screenshot"

        _run(scenario)

    def test_ss_txt_hook_creates_ss(self, tmp_path):
        async def scenario():
            from termapy.app import SerialTerminal
            from termapy.app_hooks import _hook_ss_txt

            # Arrange
            cfg, config_path = _write_cfg(tmp_path)
            app = SerialTerminal(cfg, str(config_path))
            app.repl.global_root = tmp_path
            async with app.run_test() as pilot:
                await pilot.pause()
                outcome: dict = {}

                def run_hook():
                    # Written for a dispatch worker: it marshals through
                    # _on_main, so it must not run on the event loop's thread.
                    try:
                        outcome["result"] = _hook_ss_txt(app, app.repl.ctx, "")
                    except Exception as exc:  # noqa: BLE001 - the assertion is the type
                        outcome["error"] = exc

                # Act
                worker = threading.Thread(target=run_hook, daemon=True)
                worker.start()
                deadline = time.monotonic() + 5.0
                while worker.is_alive() and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)  # keep the loop serving _on_main

                # Assert
                assert not worker.is_alive(), "the hook finished within the deadline"
                assert "error" not in outcome, f"/ss.txt raised: {outcome.get('error')!r}"
                assert outcome["result"].success, "/ss.txt reports success"
                assert list((config_path.parent / "ss").glob("*.txt")), "ss/ appeared"

        _run(scenario)

    def test_ss_and_captures_buttons_create_and_open_their_folder(self, tmp_path):
        async def scenario():
            from termapy.app import SerialTerminal

            # Arrange
            cfg, config_path = _write_cfg(tmp_path)
            app = SerialTerminal(cfg, str(config_path))
            app.repl.global_root = tmp_path
            async with app.run_test() as pilot:
                await pilot.pause()
                opened = _grant_gui_apps(app.repl.ctx)

                # Act
                app.action_open_screenshot()
                app._open_captures_dir()

                # Assert
                expected = [
                    str((config_path.parent / "ss").resolve()),
                    str((config_path.parent / "cap").resolve()),
                ]
                assert opened == expected, "each button hands its folder to the file manager"
                assert (config_path.parent / "ss").is_dir(), "ss/ exists for the user to see"
                assert (config_path.parent / "cap").is_dir(), "cap/ exists for the user to see"

        _run(scenario)

    def test_script_editor_save_creates_run(self, tmp_path):
        async def scenario():
            # Arrange
            run_dir = tmp_path / "rig" / "run"
            app = _Host()
            async with app.run_test() as pilot:
                results: list = []
                app.push_screen(ScriptEditor(run_dir), callback=results.append)
                await pilot.pause()
                app.screen.query_one("#sed-name", Input).value = "fresh"

                # Act
                await pilot.click("#sed-save")
                await pilot.pause()

                # Assert
                expected = run_dir / "fresh.run"
                assert expected.is_file(), "Save created run/ and the script"
                assert results == [str(expected)], "and dismissed with its path"

        _run(scenario)

    def test_proto_editor_save_creates_proto(self, tmp_path):
        async def scenario():
            # Arrange
            proto_dir = tmp_path / "rig" / "proto"
            app = _Host()
            async with app.run_test() as pilot:
                results: list = []
                app.push_screen(ProtoEditor(proto_dir), callback=results.append)
                await pilot.pause()
                app.screen.query_one("#ped-name", Input).value = "fresh"

                # Act
                await pilot.click("#ped-save")
                await pilot.pause()

                # Assert
                expected = proto_dir / "fresh.pro"
                assert expected.is_file(), "Save created proto/ and the script"
                assert results == [str(expected)], "and dismissed with its path"

        _run(scenario)


# -- /cfg.info shows what is on disk -----------------------------------------


class TestCfgInfoTree:
    def test_lists_only_folders_that_exist(self, tmp_path):
        # Arrange
        _, config_path = _write_cfg(tmp_path)

        # Act
        before = [name for name, _ in _all_sections(str(config_path))]
        (ensure_folder(config_path.parent / "cap") / "x.txt").write_text("", encoding="utf-8")
        after = _all_sections(str(config_path))

        # Assert
        assert before == ["rig.cfg", "rig.log"], "a bare config lists no folder at all"
        assert [name for name, _ in after] == ["rig.cfg", "rig.log", "cap/"], (
            "a folder is listed once it exists, and only that one"
        )
        assert dict(after)["cap/"] == ["x.txt"], "with its files"
