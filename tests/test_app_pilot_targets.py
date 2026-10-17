"""app.py state transitions the August Pilot pass listed as next.

Escape and Down in ``on_key``, the script overlay's mount and teardown,
the config picker's result dispatch, and the headless ``--proto`` entry
(a plain call with an argparse-shaped namespace, no Pilot).  Boots the
real ``SerialTerminal`` on a throwaway DEMO config, the
``tests/test_app_pilot.py`` pattern.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pilot_wait import until
from textual.widgets import Input, OptionList, Static

from termapy.defaults import DEFAULT_CFG
from termapy.dialogs import ConfirmDialog, FilenameDialog
from termapy.dialogs.config_editor import ConfigEditor
from termapy.dialogs.config_picker import ConfigPicker
from termapy.dialogs.quick_setup import QuickSetup


def _run(scenario) -> None:
    """Run one async Pilot scenario (the suite is otherwise sync)."""
    asyncio.run(scenario())


def _write_cfg(tmp_path, name: str = "proj", **overrides) -> tuple[dict, str]:
    """A DEMO-port config on disk, shaped the way the CLI builds one."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    proj = tmp_path / name
    proj.mkdir(exist_ok=True)
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "title": name, **overrides}
    path = proj / f"{name}.cfg"
    path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    cfg = dict(cfg)
    cfg["_config_path"] = str(path)
    return cfg, str(path)


@pytest.fixture
def app_factory(tmp_path):
    """Build a SerialTerminal against a throwaway DEMO config."""

    def _make(**overrides):
        from termapy.app import SerialTerminal

        cfg, path = _write_cfg(tmp_path, **overrides)
        app = SerialTerminal(cfg, path)
        # The checkout has a real termapy_cfg/plugin/; keep it out of the boot.
        app.repl.global_root = tmp_path
        return app, cfg, path

    return _make


async def _submit(pilot, text: str) -> None:
    """Type ``text`` into the prompt and press Enter."""
    inp = pilot.app.query_one("#cmd", Input)
    inp.value = text
    inp.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


# ── on_key ──────────────────────────────────────────────────────────────────


class TestOnKey:
    def test_escape_ends_a_history_browse_and_clears_the_prompt(self, app_factory):
        """Up recalls the last command; Escape then leaves the browse and empties the prompt."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                await _submit(pilot, "/print one")
                inp = app.query_one("#cmd", Input)
                inp.focus()
                await pilot.pause()

                # Act
                await pilot.press("up")
                recalled = (inp.value, app._history_nav.browsing)
                await pilot.press("escape")
                await pilot.pause()

                # Assert
                assert recalled == ("/print one", True), "Up started a browse on the last command"
                assert (inp.value, app._history_nav.browsing) == ("", False), "Escape ended it"

        _run(scenario)

    def test_down_walks_forward_through_the_browse(self, app_factory):
        """Two Ups reach the older command; one Down comes back to the newer one."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                await _submit(pilot, "/print one")
                await _submit(pilot, "/print two")
                inp = app.query_one("#cmd", Input)
                inp.focus()
                await pilot.pause()

                # Act
                await pilot.press("up")
                await pilot.press("up")
                older = inp.value
                await pilot.press("down")
                await pilot.pause()

                # Assert
                assert older == "/print one", "two Ups reach the older entry"
                assert inp.value == "/print two", "Down steps forward again"

        _run(scenario)

    def test_down_moves_focus_into_the_command_popup(self, app_factory):
        """With the / popup open, Down hands focus to it instead of walking history."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._show_commands()
                await pilot.pause()
                popup = app.query_one("#history-popup", OptionList)
                assert popup.has_class("visible"), "the popup opened"
                app.query_one("#cmd", Input).focus()
                await pilot.pause()

                # Act
                await pilot.press("down")
                await pilot.pause()

                # Assert
                assert app.focused is popup, "Down focuses the popup"

        _run(scenario)

    def test_escape_closes_the_command_popup(self, app_factory):
        """Escape on an open / popup hides it and returns focus to the prompt."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._show_commands()
                await pilot.pause()
                popup = app.query_one("#history-popup", OptionList)

                # Act
                await pilot.press("escape")
                await pilot.pause()

                # Assert
                assert popup.has_class("visible") is False, "hidden"
                assert app.focused is app.query_one("#cmd", Input), "prompt focused again"

        _run(scenario)

    def test_escape_cancels_a_transfer_while_the_port_is_claimed(self, app_factory):
        """While a capture holds the port, Escape sets the transfer-cancel event rather than touching history."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._connect() is True, "DEMO port should connect"
                await pilot.pause()
                result = app._dispatch_single("/cap.bin t.bin bytes=100000")
                assert result.success is True, result.error
                await pilot.pause()
                assert app._engine.serial_claimed is True, "the capture claims the port"

                # Act
                await pilot.press("escape")
                await pilot.pause()

                # Assert
                assert app._xfer_cancel.is_set(), "Escape asks the transfer to stop"
                app._dispatch_single("/cap.stop")
                await pilot.pause()

        _run(scenario)

    def test_keys_are_left_to_a_modal_on_top(self, app_factory):
        """With a dialog open, Up does not touch the prompt underneath."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                await _submit(pilot, "/print one")
                inp = app.query_one("#cmd", Input)
                inp.value = "typed"
                app.push_screen(ConfirmDialog("Sure?"))
                await pilot.pause()

                # Act
                await pilot.press("up")
                await pilot.pause()

                # Assert
                assert inp.value == "typed", "history recall is suppressed under a modal"

        _run(scenario)


# ── script overlay ───────────────────────────────────────────────────────────


def _write_scripts(cfg_path: str, **scripts: str) -> None:
    """Write ``name.run`` files into the config's run/ folder."""
    folder = Path(cfg_path).parent / "run"
    folder.mkdir(exist_ok=True)
    for name, text in scripts.items():
        (folder / f"{name}.run").write_text(text, encoding="utf-8")


class TestScriptOverlay:
    def test_a_typed_run_mounts_the_overlay_and_restores_the_bar(self, app_factory):
        """A typed /run disables the prompt under a Stop overlay that names the script chain, then hands the bar and focus back."""

        async def scenario():
            app, _, path = app_factory()
            _write_scripts(path, outer="/run inner\n/delay 300ms\n", inner="/delay 300ms\n")
            async with app.run_test() as pilot:
                await pilot.pause()
                bar = app.query_one("#bottom-bar")
                inp = app.query_one("#cmd", Input)
                labels: set[str] = set()
                prompt_disabled_during = False

                # Act -- type the run, then watch the overlay until it is gone
                await _submit(pilot, "/run outer")
                assert await until(pilot, lambda: bool(bar.query("#script-stop"))), (
                    "overlay mounted"
                )
                for _ in range(250):
                    if not bar.query("#script-stop"):
                        break
                    labels.add(str(bar.query_one("#script-label", Static).render()))
                    prompt_disabled_during = prompt_disabled_during or inp.disabled
                    await pilot.pause(0.02)
                assert await until(pilot, lambda: app.focused is inp), "focus returns to the prompt"

                # Assert
                assert any("outer.run" in label and "inner.run" not in label for label in labels), (
                    f"the outer script alone, with its step counter: {labels}"
                )
                assert any("outer.run → inner.run" in label for label in labels), labels
                assert prompt_disabled_during is True, "the prompt is off while the script runs"
                assert (inp.disabled, bar.disabled) == (False, False), "prompt and bar usable again"
                assert all(child.display for child in bar.children), "the buttons are back"

        _run(scenario)

    def test_the_stop_button_ends_a_script(self, app_factory):
        """Clicking the overlay's Stop after a TYPED /run ends the script at the next line.

        The typed path had disabled the bottom bar before the overlay
        mounted into it, so the Stop button was inert and only Escape
        stopped a script; the started handler now enables the bar it owns.
        """

        async def scenario():
            app, _, path = app_factory()
            # Fifty 100 ms lines: five seconds if left alone, and the stop
            # is checked between lines, so it lands within one of them.
            _write_scripts(path, slow="/delay 100ms\n" * 50)
            async with app.run_test() as pilot:
                await pilot.pause()
                bar = app.query_one("#bottom-bar")

                # Act
                await _submit(pilot, "/run slow")
                assert await until(pilot, lambda: bool(bar.query("#script-stop"))), (
                    "overlay mounted"
                )
                await pilot.click("#script-stop")
                gone = await until(pilot, lambda: not bar.query("#script-stop"), seconds=2.0)

                # Assert
                assert gone is True, "the script ended on Stop, not after five seconds"
                assert app.query_one("#cmd", Input).disabled is False

        _run(scenario)


# ── config picker results ────────────────────────────────────────────────────


class TestConfigPicked:
    def test_none_is_a_no_op(self, app_factory):
        """Cancelling the picker changes nothing."""

        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._on_config_picked(None)
                await pilot.pause()
                assert (app.config_path, len(app.screen_stack)) == (path, 1)

        _run(scenario)

    def test_load_switches_to_the_picked_config(self, app_factory, tmp_path):
        """A load result swaps the app onto that config (DEMO needs no port check)."""

        async def scenario():
            app, _, _ = app_factory()
            _, other = _write_cfg(tmp_path, name="other")
            async with app.run_test() as pilot:
                await pilot.pause()
                app._on_config_picked(("load", other))
                await pilot.pause()
                assert app.config_path == other, "switched"
                assert app.cfg["title"] == "other", "the new config's values are live"

        _run(scenario)

    def test_load_of_a_broken_file_reports_and_stays(self, app_factory, tmp_path):
        """A load result naming an unreadable file reports the failure and keeps the current config."""

        async def scenario():
            app, _, path = app_factory()
            broken = tmp_path / "broken.cfg"
            broken.write_text("{not json", encoding="utf-8")
            async with app.run_test() as pilot:
                await pilot.pause()
                app._on_config_picked(("load", str(broken)))
                await pilot.pause()
                assert "Failed to load config" in app._get_screen_text()
                assert app.config_path == path, "unchanged"

        _run(scenario)

    def test_new_opens_quick_setup(self, app_factory):
        """A new result opens the quick-setup dialog."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._on_config_picked(("new",))
                await pilot.pause()
                assert isinstance(app.screen, QuickSetup)

        _run(scenario)

    def test_edit_opens_the_editor_on_that_file(self, app_factory, tmp_path):
        """An edit result opens the config editor on the named file; an unreadable one reports instead."""

        async def scenario():
            app, _, path = app_factory()
            broken = tmp_path / "broken.cfg"
            broken.write_text("{not json", encoding="utf-8")
            async with app.run_test() as pilot:
                await pilot.pause()
                app._on_config_picked(("edit", path))
                await pilot.pause()
                editor = app.screen
                assert isinstance(editor, ConfigEditor) and editor.config_path == path
                await pilot.press("escape")
                await pilot.pause()
                app._on_config_picked(("edit", str(broken)))
                await pilot.pause()
                assert "Failed to load config" in app._get_screen_text()
                assert len(app.screen_stack) == 1, "no editor for a file that will not load"

        _run(scenario)

    def test_rename_prompts_for_a_name(self, app_factory):
        """A rename result asks for the new name first, in the filename dialog."""

        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._on_config_picked(("rename", path))
                await pilot.pause()
                assert isinstance(app.screen, FilenameDialog)

        _run(scenario)

    def test_delete_asks_first_and_deletes_on_yes(self, app_factory, tmp_path):
        """A delete result confirms; No keeps the file, Yes removes it and reports."""

        async def scenario():
            app, _, _ = app_factory()
            _, other = _write_cfg(tmp_path, name="other")
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act / Assert -- No first
                app._on_config_picked(("delete", other))
                await pilot.pause()
                assert isinstance(app.screen, ConfirmDialog)
                await pilot.click("#confirm-no")
                await pilot.pause()
                assert Path(other).exists(), "No keeps the file"

                # Act / Assert -- then Yes
                app._on_config_picked(("delete", other))
                await pilot.pause()
                await pilot.click("#confirm-yes")
                await pilot.pause()
                assert not Path(other).exists(), "Yes deletes it"
                assert "Deleted config: other.cfg" in app._get_screen_text()

        _run(scenario)

    def test_deleting_the_active_config_reopens_the_picker(self, app_factory):
        """Deleting the config in use clears the active path and reopens the picker to choose another."""

        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._on_config_picked(("delete", path))
                await pilot.pause()
                await pilot.click("#confirm-yes")
                await pilot.pause()
                assert app.config_path == "", "nothing is active"
                assert isinstance(app.screen, ConfigPicker), "pick another"

        _run(scenario)


# ── headless --proto ─────────────────────────────────────────────────────────


_AT_PASS = (
    'frame_gap = "50ms"\n\n[[test]]\nname = "AT"\nsend = \'"AT\\r"\'\nexpect = \'"OK\\r\\n"\'\n'
)
_AT_FAIL = (
    'frame_gap = "50ms"\n\n[[test]]\nname = "AT"\nsend = \'"AT\\r"\'\nexpect = \'"NO\\r\\n"\'\n'
)


def _write_pro(cfg_path: str, name: str, text: str) -> None:
    folder = Path(cfg_path).parent / "proto"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.pro").write_text(text, encoding="utf-8")


class TestHeadlessProto:
    """``termapy --proto`` is a function call away: an argparse-shaped namespace in, an exit code out."""

    def test_a_passing_script_exits_zero_with_a_summary(self, tmp_path, capsys):
        """Every test passing prints one summary line and exits 0."""
        from termapy.app import _run_proto_headless

        _, path = _write_cfg(tmp_path)
        _write_pro(path, "smoke", _AT_PASS)
        with pytest.raises(SystemExit) as exit_info:
            _run_proto_headless(SimpleNamespace(demo=False, config=path, proto="smoke"))
        assert exit_info.value.code == 0
        assert "1/1 PASS" in capsys.readouterr().out

    def test_a_failing_script_exits_one(self, tmp_path, capsys):
        """A failed test turns the summary to FAIL and the exit code to 1."""
        from termapy.app import _run_proto_headless

        _, path = _write_cfg(tmp_path)
        _write_pro(path, "smoke", _AT_FAIL)
        with pytest.raises(SystemExit) as exit_info:
            _run_proto_headless(SimpleNamespace(demo=False, config=path, proto="smoke"))
        assert exit_info.value.code == 1
        assert "0/1 FAIL" in capsys.readouterr().out

    def test_a_missing_script_exits_two(self, tmp_path, capsys):
        """A script that is not in proto/ is a usage error: message on stderr, exit 2."""
        from termapy.app import _run_proto_headless

        _, path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exit_info:
            _run_proto_headless(SimpleNamespace(demo=False, config=path, proto="nope"))
        assert exit_info.value.code == 2
        assert "proto file not found" in capsys.readouterr().err

    def test_an_unreadable_config_exits_two(self, tmp_path, capsys):
        """A config that will not load is reported on stderr with exit 2."""
        from termapy.app import _run_proto_headless

        broken = tmp_path / "broken.cfg"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit) as exit_info:
            _run_proto_headless(SimpleNamespace(demo=False, config=str(broken), proto="smoke"))
        assert exit_info.value.code == 2
        assert "failed to load config" in capsys.readouterr().err
