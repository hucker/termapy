"""app.py state transitions the August Pilot pass listed as next.

Escape and Down in ``on_key``, the script overlay's mount and teardown,
the config picker's result dispatch, the session log across a config
switch, and the headless ``--proto`` entry (a plain call with an
argparse-shaped namespace, no Pilot).  Boots the
real ``SerialTerminal`` on a throwaway DEMO config, the
``tests/test_app_pilot.py`` pattern.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pilot_wait import until
from textual.widgets import Button, Input, OptionList, RichLog, Static

from termapy.defaults import DEFAULT_CFG
from termapy.dialogs import ConfirmDialog, FilenameDialog, PortPicker
from termapy.dialogs.config_editor import ConfigEditor
from termapy.dialogs.quick_setup import QuickSetup
from termapy.port_control import reserved_serial


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


def _confirm_ready(app) -> bool:
    """A ConfirmDialog is up with both buttons laid out, so a click lands.

    The dialog is pushed from the command's worker; its buttons mount a
    beat later and get a place on screen later still, and a click before
    layout misses (seen under suite load).
    """
    if not isinstance(app.screen, ConfirmDialog):
        return False
    buttons = app.screen.query("#confirm-yes, #confirm-no")
    return len(buttons) == 2 and all(button.region.width > 0 for button in buttons)


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

    def test_load_by_serial_number_connects_without_asking_for_a_port(self, app_factory, tmp_path):
        """port-present-one-decider: a config naming its port by serial number loads like a COM name, no picker."""

        async def scenario():
            # Arrange: DEMO answers to its own serial number, so no hardware is needed
            app, _, _ = app_factory()
            serial = DEFAULT_CFG["serial"]
            assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
            port = reserved_serial("DEMO")
            _, other = _write_cfg(
                tmp_path, name="board", serial={**serial, "port": port}, auto_connect=True
            )
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act: what the config picker hands back when the user picks it
                app._on_config_picked(("load", other))
                await pilot.pause()

                # Assert
                assert not isinstance(app.screen, PortPicker), "its port is here: no picker"
                assert app.config_path == other, "switched"
                connected = await until(pilot, lambda: app.is_connected)
                assert connected, "auto_connect found the port by its serial number"
                assert "Connected: DEMO" in app._get_screen_text(), "the screen names the device"
                log = Path(app.session_log_path()).read_text(encoding="utf-8")
                assert f"Resolved {port} -> DEMO" in log, "the log keeps how the number resolved"

        _run(scenario)

    def test_tooltips_show_the_device_as_port_and_the_number_as_serial(self, app_factory):
        """port-display: a serial-number config's tooltips say port = the device, serial # = the number."""

        async def scenario():
            # Arrange: DEMO answers to its own serial number, so no hardware is needed
            serial = DEFAULT_CFG["serial"]
            assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
            number = reserved_serial("DEMO")
            app, _, _ = app_factory(serial={**serial, "port": number}, auto_connect=True)
            async with app.run_test() as pilot:
                connected = await until(pilot, lambda: app.is_connected)
                assert connected, "auto_connect found DEMO by its serial number"

                # Act
                app._update_title()
                await pilot.pause()

                # Assert: each title-bar tooltip, as the user hovers it
                tooltips = {
                    button: str(app.query_one(f"#title-{button}", Button).tooltip)
                    for button in ("left", "center", "right")
                }
                assert tooltips["left"].startswith("DEMO\n"), (
                    "the port tooltip is titled by the device"
                )
                for button, text in tooltips.items():
                    assert f"= {number}" in text, f"{button}: the serial number is shown"
                for button in ("center", "right"):
                    rows = dict(
                        line.split("=", 1) for line in tooltips[button].splitlines() if "=" in line
                    )
                    rows = {key.strip(): value.strip() for key, value in rows.items()}
                    assert rows["port"] == "DEMO", f"{button}: port is the device, not {number}"
                    assert rows["serial #"] == number, f"{button}: the number is the serial"
                left_rows = dict(
                    line.split("=", 1) for line in tooltips["left"].splitlines() if "=" in line
                )
                left_rows = {key.strip(): value.strip() for key, value in left_rows.items()}
                assert left_rows["Serial #"] == number, (
                    "the port tooltip labels its rows as /port.chip does"
                )

        _run(scenario)

    def test_load_of_a_port_not_here_asks_for_one(self, app_factory, tmp_path):
        """A config whose port matches nothing attached opens the port picker before switching."""
        from serial.tools.list_ports import comports

        if not list(comports()):
            pytest.skip("no ports attached: the load switches with '(no ports found)' instead")

        async def scenario():
            # Arrange
            app, _, path = app_factory()
            serial = DEFAULT_CFG["serial"]
            assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
            _, other = _write_cfg(tmp_path, name="gone", serial={**serial, "port": "NO-SUCH-SN"})
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app._on_config_picked(("load", other))
                await pilot.pause()

                # Assert
                assert isinstance(app.screen, PortPicker), "nothing matches: the user picks a port"
                assert app.config_path == path, "no switch until a port is picked"

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

    def test_delete_asks_first_and_deletes_the_folder_on_yes(self, app_factory, tmp_path):
        """config-deleted-once: the picker's Delete is /cfg.delete; No keeps the config, Yes removes its folder."""

        async def scenario():
            app, _, _ = app_factory()
            _, other = _write_cfg(tmp_path, name="other")
            (Path(other).parent / "run").mkdir()
            (Path(other).parent / "run" / "x.run").write_text("/print hi\n", encoding="utf-8")

            async with app.run_test() as pilot:
                await pilot.pause()

                # Act / Assert -- No first
                app._on_config_picked(("delete", other))
                asked = await until(pilot, lambda: _confirm_ready(app))
                assert asked, "the delete question is asked"
                await pilot.click("#confirm-no")
                # Wait for the first round to finish -- its dialog gone and
                # its refusal reported -- or the second round's wait below
                # can see this dialog still closing and click into it.
                declined = await until(
                    pilot,
                    lambda: (
                        not isinstance(app.screen, ConfirmDialog)
                        and "Config not deleted: other" in app._get_screen_text()
                    ),
                )
                assert declined, "No is reported and the dialog closes"
                assert Path(other).exists(), "No keeps the config"

                # Act / Assert -- then Yes
                app._on_config_picked(("delete", other))
                await until(pilot, lambda: _confirm_ready(app))
                await pilot.click("#confirm-yes")
                gone = await until(pilot, lambda: not Path(other).parent.exists())
                assert gone, "Yes deletes the whole folder, run script and all"
                reported = await until(
                    pilot, lambda: "Deleted config other (2 files)." in app._get_screen_text()
                )
                assert reported, "the deletion is reported"

        _run(scenario)

    def test_deleting_the_active_config_is_refused(self, app_factory):
        """config-deleted-once: the active config's folder holds the session's log, so Delete refuses it."""

        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app._on_config_picked(("delete", path))
                refused = await until(
                    pilot,
                    lambda: "Cannot delete the active config: proj" in app._get_screen_text(),
                )

                # Assert
                assert refused, "the refusal is reported"
                assert Path(path).exists(), "the active config is untouched"
                assert app.config_path == path, "the session keeps its config"

        _run(scenario)


# ── script picker results ────────────────────────────────────────────────────


class TestScriptPickedDelete:
    def test_the_pickers_delete_is_run_delete(self, app_factory):
        """one-handler-per-command: the Run picker's Delete is /run.delete, which asks, deletes and recounts the Scripts button."""

        async def scenario():
            app, _, path = app_factory()
            _write_scripts(path, x="/print hi\n")
            script = Path(path).parent / "run" / "x.run"

            def _scripts_tooltip() -> str:
                return str(app.query_one("#btn-scripts").tooltip)

            async with app.run_test() as pilot:
                await pilot.pause()
                assert "1 available" in _scripts_tooltip(), "precondition: one script counted"

                # Act
                app._on_script_picked(("delete", str(script)))
                asked = await until(pilot, lambda: _confirm_ready(app))
                await pilot.click("#confirm-yes")
                gone = await until(pilot, lambda: not script.exists())
                recounted = await until(pilot, lambda: "empty" in _scripts_tooltip())

                # Assert
                assert asked, "the command asks before deleting a script someone wrote"
                assert gone, "Yes deletes the script"
                assert recounted, "folder_changed refreshes the Scripts tooltip"

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


class TestSessionLogFollowsConfig:
    """The session log is the active config's history, so a switch moves it.

    Until 2026-10-18 a switch kept writing to the FIRST config's log for
    the whole session, while ``/log.*`` and the Log button named the new
    config's, so the log a user opened stayed empty.
    """

    @staticmethod
    def _text(path) -> str:
        log = Path(path)
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def test_a_switch_moves_the_log_to_the_new_config(self, app_factory, tmp_path):
        """After A -> B a typed line lands in B's log, not A's, and A says where it went."""

        async def scenario():
            app, _, _ = app_factory(name="first")
            async with app.run_test() as pilot:
                await pilot.pause()
                first_log = app.session_log_path()
                second_cfg, second_path = _write_cfg(tmp_path, name="second")

                # Act
                app._switch_config(second_cfg, second_path)
                await pilot.pause()
                await _submit(pilot, "/print after-the-switch")
                second_log = app.session_log_path()
                await until(pilot, lambda: "after-the-switch" in self._text(second_log))

                # Assert
                assert second_log != first_log, "each config has its own log"
                assert "after-the-switch" in self._text(second_log), "the session writes B's log"
                assert "after-the-switch" not in self._text(first_log), "and no longer A's"
                assert "Switched to config: second" in self._text(first_log), (
                    "A's log says where the session went"
                )
                assert "Session Start" in self._text(second_log), "B's log opens with its marker"

        asyncio.run(scenario())

    def test_switching_back_reopens_the_first_log(self, app_factory, tmp_path):
        """A -> B -> A: the line after returning is in A's log again, and B says so."""

        async def scenario():
            app, _, _ = app_factory(name="first")
            async with app.run_test() as pilot:
                await pilot.pause()
                first_log = app.session_log_path()
                second_cfg, second_path = _write_cfg(tmp_path, name="second")
                app._switch_config(second_cfg, second_path)
                await pilot.pause()
                second_log = app.session_log_path()
                # A fresh dict, as a real switch loads one from disk: the
                # app owns the dict it booted with.
                first_again, first_path = _write_cfg(tmp_path, name="first")

                # Act
                app._switch_config(first_again, first_path)
                await pilot.pause()
                await _submit(pilot, "/print back-in-first")
                await until(pilot, lambda: "back-in-first" in self._text(first_log))

                # Assert
                assert app.session_log_path() == first_log, "the log is A's again"
                assert "back-in-first" in self._text(first_log), (
                    "the line after returning is in A's"
                )
                assert "back-in-first" not in self._text(second_log), "not in B's"
                assert "Switched to config: first" in self._text(second_log), (
                    "B's log says where the session went"
                )

        asyncio.run(scenario())

    def test_every_session_start_has_its_end(self, app_factory, tmp_path):
        """A switch ends A's log after its Switched line, and the exit ends B's, one end per start."""

        async def scenario():
            app, _, _ = app_factory(name="first")
            async with app.run_test() as pilot:
                await pilot.pause()
                first_log = app.session_log_path()
                second_cfg, second_path = _write_cfg(tmp_path, name="second")

                # Act -- switch, then leave the context: the app exits
                app._switch_config(second_cfg, second_path)
                await pilot.pause()
                second_log = app.session_log_path()

            # Assert
            first = self._text(first_log).splitlines()
            second = self._text(second_log).splitlines()
            assert "Switched to config: second" in first[-2], "A says where the session went"
            assert "Session End" in first[-1], "and then that it ended there"
            assert "Session End" in second[-1], "the exit ends the log the session is in"
            for lines in (first, second):
                starts = sum("Session Start" in line for line in lines)
                ends = sum("Session End" in line for line in lines)
                assert starts == ends == 1, "each log: one start, one end"

        asyncio.run(scenario())

    def test_startup_load_report_is_in_the_session_log(self, app_factory):
        """prestart-before-start: the log opens at pre-start, so the startup load table is in it."""

        async def scenario():
            app, _, path = app_factory(name="first")
            plugin = Path(path).parent / "plugin"
            plugin.mkdir()
            (plugin / "hello.py").write_text(
                "from termapy.plugins import CmdResult, Command\n\n\n"
                "def _handler(ctx, args):\n"
                '    return CmdResult.ok(value="")\n\n\n'
                'COMMAND = Command(name="hello", help="test", handler=_handler)\n',
                encoding="utf-8",
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                log = app.session_log_path()

                # Act
                await until(pilot, lambda: "Loaded - 1 file" in self._text(log))

                # Assert
                logged = self._text(log)
                assert logged.index("Session Start") < logged.index("Loaded - 1 file"), (
                    "the log was open before the config load printed its table"
                )
                assert "plugin/hello.py" in logged, "the table's rows are logged too"

        asyncio.run(scenario())

    @pytest.mark.skipif(sys.platform == "darwin", reason="macOS VS Code passes Ctrl keys through")
    def test_vscode_tip_shows_once_and_is_logged(self, app_factory, tmp_path, monkeypatch):
        """The VS Code tip is first on screen once per run, not again on a switch, and all of it is logged."""
        monkeypatch.setenv("TERM_PROGRAM", "vscode")

        def screen(app) -> list[str]:
            return [strip.text.strip() for strip in app.query_one("#output", RichLog).lines]

        async def scenario():
            app, _, _ = app_factory(name="first")
            async with app.run_test() as pilot:
                await pilot.pause()
                first_log = app.session_log_path()
                at_start = screen(app)
                second_cfg, second_path = _write_cfg(tmp_path, name="second")

                # Act -- a switch clears the pane; the tip must not come back
                app._switch_config(second_cfg, second_path)
                await pilot.pause()
                after_switch = screen(app)

                # Assert
                tips = [line for line in at_start if line.startswith("VS Code detected")]
                assert len(tips) == 1, "shown once at start"
                assert at_start[1].startswith("VS Code detected"), "first on screen, under its rule"
                assert not any(line.startswith("VS Code detected") for line in after_switch), (
                    "once per run, not once per config load"
                )
                logged = self._text(first_log)
                assert "VS Code detected: F-keys" in logged, "the tip is in the log"
                assert "Other:       Alt+P=palette" in logged, "every line of it, not a summary"

        asyncio.run(scenario())

    def test_configs_sharing_one_log_file_keep_it_open(self, app_factory, tmp_path):
        """Two configs naming one log_file: the switch keeps the file, with no second marker."""

        async def scenario():
            shared = tmp_path / "shared.log"
            app, _, _ = app_factory(name="first", log_file=str(shared))
            async with app.run_test() as pilot:
                await pilot.pause()
                second_cfg, second_path = _write_cfg(tmp_path, name="second", log_file=str(shared))

                # Act
                app._switch_config(second_cfg, second_path)
                await pilot.pause()
                await _submit(pilot, "/print still-shared")
                await until(pilot, lambda: "still-shared" in self._text(shared))

                # Assert
                logged = self._text(shared)
                assert "still-shared" in logged, "the session still writes the shared file"
                assert logged.count("Session Start") == 1, "one file, one session marker"
                assert "Switched to config" not in logged, "no switch trace when the log stays"

        asyncio.run(scenario())


class TestLogStartStop:
    """/log.stop closes the session log until /log.start, and a delete leaves it stopped.

    Until 2026-10-30 there was no stop, and a /log.delete silently ended
    the log until the next config switch.
    """

    @staticmethod
    def _text(path) -> str:
        log = Path(path)
        return log.read_text(encoding="utf-8") if log.exists() else ""

    @staticmethod
    async def _type(pilot, text: str, shows: str) -> bool:
        """Submit ``text`` and wait for a screen line starting ``shows``, so the next command runs after it."""
        await _submit(pilot, text)
        return await until(
            pilot, lambda: any(line.strip().startswith(shows) for line in _screen(pilot.app))
        )

    def test_stop_then_start_leaves_a_marked_gap(self, app_factory):
        """What runs between /log.stop and /log.start never reaches the log; a marker stands at each end."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                log = app.session_log_path()

                # Act
                await self._type(pilot, "/print before-stop", "before-stop")
                await self._type(pilot, "/log.stop", "Log stopped")
                await self._type(pilot, "/print while-stopped", "while-stopped")
                await self._type(pilot, "/log.start", "Log started")
                await self._type(pilot, "/print after-start", "after-start")
                await until(pilot, lambda: "after-start" in self._text(log))

                # Assert
                logged = self._text(log)
                actual = [
                    logged.find(mark)
                    for mark in ("before-stop", "Log Stopped", "Log Started", "after-start")
                ]
                assert -1 not in actual and actual == sorted(actual), (
                    f"the gap's markers stand between the lines around it: {actual}"
                )
                assert "while-stopped" not in logged, "nothing is written while stopped"

        asyncio.run(scenario())

    def test_a_second_stop_or_start_changes_nothing_and_says_so(self, app_factory):
        """Twice in a row: the second /log.stop and /log.start each say the state and add no marker."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                log = app.session_log_path()
                steps = [
                    ("/log.stop", "Log stopped"),
                    ("/log.stop", "Log already stopped."),
                    ("/log.start", "Log started"),
                    ("/log.start", "Log already running"),
                ]

                # Act
                shown = [await self._type(pilot, text, shows) for text, shows in steps]
                await self._type(pilot, "/print all-done", "all-done")
                await until(pilot, lambda: "all-done" in self._text(log))

                # Assert
                assert shown == [True] * len(steps), f"each answer reaches the screen: {shown}"
                logged = self._text(log)
                actual = (logged.count("Log Stopped"), logged.count("Log Started"))
                assert actual == (1, 1), "one marker at each end of the one gap"

        asyncio.run(scenario())

    def test_a_switch_while_stopped_opens_no_log(self, app_factory, tmp_path):
        """Stopped lasts through a config switch; /log.start then opens the new config's log."""

        async def scenario():
            app, _, _ = app_factory(name="first")
            async with app.run_test() as pilot:
                await pilot.pause()
                first_log = app.session_log_path()
                await self._type(pilot, "/log.stop", "Log stopped")
                second_cfg, second_path = _write_cfg(tmp_path, name="second")

                # Act
                app._switch_config(second_cfg, second_path)
                await pilot.pause()
                second_log = app.session_log_path()
                await self._type(pilot, "/print in-second", "in-second")
                await self._type(pilot, "/log.start", "Log started")
                await self._type(pilot, "/print second-logged", "second-logged")
                await until(pilot, lambda: "second-logged" in self._text(second_log))

                # Assert
                second = self._text(second_log)
                assert "in-second" not in second, "the switch opened no log while stopped"
                assert "in-second" not in self._text(first_log), "nor kept writing the first"
                assert "Log Started" in second, "/log.start opened the second config's log"
                assert "second-logged" in second, "and the session writes it"

        asyncio.run(scenario())

    def test_a_delete_leaves_the_log_stopped_until_start(self, app_factory):
        """After /log.delete nothing recreates the file; /log.start begins a new one at its marker."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                log = app.session_log_path()
                assert log is not None and log.exists(), "the TUI keeps a log"

                # Act
                await self._type(pilot, "/log.delete", "Deleted")
                await self._type(pilot, "/print after-delete", "after-delete")
                recreated = log.exists()
                await self._type(pilot, "/log.start", "Log started")
                await self._type(pilot, "/print new-log", "new-log")
                await until(pilot, lambda: "new-log" in self._text(log))

                # Assert
                assert recreated is False, "nothing recreated the file while stopped"
                lines = self._text(log).splitlines()
                assert "Log Started" in lines[0], "the new file begins at /log.start"
                assert "after-delete" not in "\n".join(lines), "the stopped stretch is not in it"

        asyncio.run(scenario())


# ── Clickable output ────────────────────────────────────────────────────────


def _screen(app) -> list[str]:
    """The output pane's lines as text."""
    return [strip.text for strip in app.query_one("#output", RichLog).lines]


def _text_offset(app, text: str) -> tuple[int, int]:
    """The output pane offset of the first visible occurrence of ``text``."""
    log = app.query_one("#output", RichLog)
    lines = _screen(app)
    index = next(i for i, line in enumerate(lines) if text in line)
    x = log.content_region.x - log.region.x + lines[index].index(text) + 1
    y = log.content_region.y - log.region.y + index - round(log.scroll_offset.y)
    return x, y


async def _click_text(pilot, app, text: str) -> None:
    """Click the first visible occurrence of ``text`` in the output pane with the mouse."""
    await pilot.click("#output", offset=_text_offset(app, text))
    await pilot.pause()


async def _hover_text(pilot, app, text: str) -> None:
    """Rest the mouse on the first visible occurrence of ``text`` in the output pane."""
    await pilot.hover("#output", offset=_text_offset(app, text))
    await pilot.pause()


class TestClickableOutput:
    """links-are-data: a clicked list entry runs as if typed; links live in a bounded table."""

    def test_clicking_a_listed_script_runs_it_twice_like_typing(self, app_factory):
        """A click on /run.list's NAME runs the script, echoed and into history, and again on a second click."""

        async def scenario():
            app, _, path = app_factory(quiet_start=True)
            _write_scripts(path, hello="/print clicked-ok\n")
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await _submit(pilot, "/run.list")
                await until(pilot, lambda: any("hello.run" in line for line in _screen(app)))

                # Act
                await _click_text(pilot, app, "hello.run")
                await until(pilot, lambda: sum("clicked-ok" in line for line in _screen(app)) == 1)
                await until(pilot, lambda: not app.query_one("#bottom-bar").disabled)
                await _click_text(pilot, app, "hello.run")
                await until(pilot, lambda: sum("clicked-ok" in line for line in _screen(app)) == 2)

                # Assert
                runs = sum("clicked-ok" in line for line in _screen(app))
                assert runs == 2, "each click runs the script, the second as well as the first"
                assert app.history[-1] == "/run hello.run", "the click went into history as typed"

        asyncio.run(scenario())

    def test_a_prefill_link_fills_the_input_and_runs_nothing(self, app_factory):
        """A prefill link puts its command, with the prefix, in the input line for Enter."""

        async def scenario():
            from termapy.plugins.views import Link

            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                link_id = app._register_link(Link("dev.use part", run=False))

                # Act
                app.action_click_link(link_id)
                await pilot.pause()

                # Assert
                actual = app.query_one("#cmd", Input).value
                assert actual == "/dev.use part ", (
                    "the command waits in the input line, a space after it"
                )
                assert "/dev.use part" not in app.history, "nothing ran"

        asyncio.run(scenario())

    def test_a_typo_errors_fix_runs_when_it_only_shows(self, app_factory):
        """A did-you-mean for a command that only shows (/help) runs on a click, as typed."""

        async def scenario():
            app, _, _ = app_factory(quiet_start=True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await _submit(pilot, "/held")
                await until(
                    pilot, lambda: any("did you mean /help?" in line for line in _screen(app))
                )

                # Act
                await _click_text(pilot, app, "/help?")
                await until(pilot, lambda: any("-- Terminal --" in line for line in _screen(app)))

                # Assert
                assert app.history[-1] == "/help", "the click ran the fixed command, into history"

        asyncio.run(scenario())

    def test_a_typo_errors_fix_waits_when_it_acts(self, app_factory):
        """A did-you-mean for a command that acts (/run) fills the input with the fixed line and runs nothing."""

        async def scenario():
            app, _, _ = app_factory(quiet_start=True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await _submit(pilot, "/rnu welcome")
                await until(
                    pilot, lambda: any("did you mean /run?" in line for line in _screen(app))
                )

                # Act
                await _click_text(pilot, app, "/run?")
                await pilot.pause()

                # Assert
                actual = app.query_one("#cmd", Input).value
                assert actual == "/run welcome ", "the fixed command waits in the input line"
                assert "/run welcome" not in app.history, "nothing ran"

        asyncio.run(scenario())

    def test_an_expired_link_says_so(self, app_factory):
        """A click on a link no longer in the table runs nothing and tells the user why."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app.action_click_link(10**9)
                await pilot.pause()

                # Assert
                assert any("That link has expired" in line for line in _screen(app)), (
                    "an old link is never a silent no-op"
                )

        asyncio.run(scenario())

    def test_the_link_table_stays_bounded(self, app_factory):
        """Registering far more links than the limit keeps only the newest, the oldest gone."""

        async def scenario():
            from termapy.plugins.views import Link

            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                ids = [app._register_link(Link(f"x {n}")) for n in range(app._LINK_LIMIT + 50)]

                # Assert
                assert len(app._links) == app._LINK_LIMIT, "a long session holds a bounded table"
                assert ids[0] not in app._links and ids[-1] in app._links, "the oldest go first"

        asyncio.run(scenario())

    def test_text_that_looks_like_a_link_stays_text(self, app_factory):
        """Output carrying click markup prints it literally: only a view's column makes a link."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                bait = "[@click=app.click_link(0)]bait[/]"

                # Act
                app.repl.ctx.io.output(bait)
                await pilot.pause()

                # Assert
                log = app.query_one("#output", RichLog)
                line = next(strip for strip in log.lines if "bait" in strip.text)
                assert bait in line.text, "the markup is shown as characters"
                metas = [segment.style.meta for segment in line if segment.style]
                assert not any("@click" in meta for meta in metas), "and carries no action"

        asyncio.run(scenario())

    def test_a_help_link_follows_the_configured_prefix(self, app_factory):
        """With cmd_prefix "!", a SUBCOMMANDS name opens !help <name>, and the log holds plain text."""

        async def scenario():
            app, _, _ = app_factory(cmd_prefix="!")
            async with app.run_test(size=(120, 80)) as pilot:
                await pilot.pause()
                await _submit(pilot, "!help cfg")
                await until(pilot, lambda: any("!cfg.loaded " in line for line in _screen(app)))

                # Act
                await _click_text(pilot, app, "!cfg.loaded ")
                await until(pilot, lambda: app.history[-1] == "!help cfg.loaded")

                # Assert
                assert app.history[-1] == "!help cfg.loaded", (
                    "the live prefix, never a hard-coded /"
                )
                logged = Path(app.session_log_path()).read_text(encoding="utf-8")
                assert "  !cfg.loaded" in logged, "the help rows are in the log"
                assert "[cyan]" not in logged, "as the text they show, never their markup"

        asyncio.run(scenario())

    def test_the_help_page_opens_the_picker_its_button_opens(self, app_factory):
        """In /help run a click on the name fills the input, and on "Run picker" opens the Run picker."""

        async def scenario():
            from termapy.dialogs import ScriptPicker

            app, _, _ = app_factory(quiet_start=True)
            async with app.run_test(size=(120, 60)) as pilot:
                await pilot.pause()
                await _submit(pilot, "/help run")
                await until(pilot, lambda: any("Run picker" in line for line in _screen(app)))

                # Act
                await _click_text(pilot, app, "/run - ")
                filled = app.query_one("#cmd", Input).value
                await _click_text(pilot, app, "Run picker")
                await until(pilot, lambda: isinstance(app.screen, ScriptPicker))

                # Assert
                assert filled == "/run ", "the name waits in the input line, ready for a file"
                assert isinstance(app.screen, ScriptPicker), "the words do what the Run button does"
                assert app.history[-1] == "/run", "the click went into history as typed"

        asyncio.run(scenario())

    def test_help_run_ends_with_the_scripts_indented_and_clickable(self, app_factory):
        """/help run lists the scripts 2 spaces in, as the page's rows are, and a click on one runs it."""

        async def scenario():
            app, _, path = app_factory(quiet_start=True)
            _write_scripts(path, hello="/print clicked-ok\n")
            async with app.run_test(size=(120, 60)) as pilot:
                await pilot.pause()
                await _submit(pilot, "/help run")
                await until(pilot, lambda: any("hello.run" in line for line in _screen(app)))
                row = next(line for line in _screen(app) if "hello.run" in line)

                # Act
                await _click_text(pilot, app, "hello.run")
                await until(pilot, lambda: any("clicked-ok" in line for line in _screen(app)))

                # Assert
                assert row.startswith("  hello.run"), "in the page's column, not flush left"
                assert app.history[-1] == "/run hello.run", "the click runs it, as in /run.list"

        asyncio.run(scenario())

    def test_hovering_a_link_says_what_a_click_runs(self, app_factory):
        """Over a /run.list name Textual shows the command a click runs; over plain text it clears."""

        async def scenario():
            from rich.text import Text
            from textual.widgets import Tooltip

            app, _, path = app_factory(quiet_start=True)
            _write_scripts(path, hello="/print hi\n")
            async with app.run_test(size=(120, 40), tooltips=True) as pilot:
                await pilot.pause()
                await _submit(pilot, "/run.list")
                await until(pilot, lambda: any("hello.run" in line for line in _screen(app)))
                log = app.query_one("#output", RichLog)
                tooltip = app.screen.get_child_by_type(Tooltip)

                # Act
                await _hover_text(pilot, app, "hello.run")
                over = log.tooltip
                shown = await until(pilot, lambda: tooltip.display, seconds=2)
                await _hover_text(pilot, app, "run/ - ")
                off = log.tooltip

                # Assert
                assert isinstance(over, Text), "a Text: a command's [brackets] stay literal"
                assert over.plain == "Click to run: /run hello.run", "the command, as typed"
                assert shown, "Textual displays it once the mouse rests, not just holds it"
                assert off is None, "plain text has no tooltip"

        asyncio.run(scenario())

    def test_a_fill_link_and_an_expired_link_say_so(self, app_factory):
        """A command's name on its own page says it fills the input; a link no longer held says it expired."""

        async def scenario():
            app, _, _ = app_factory(quiet_start=True)
            async with app.run_test(size=(120, 60), tooltips=True) as pilot:
                await pilot.pause()
                await _submit(pilot, "/help run")
                await until(pilot, lambda: any("Run picker" in line for line in _screen(app)))
                log = app.query_one("#output", RichLog)

                # Act
                await _hover_text(pilot, app, "/run - ")
                fill = log.tooltip

                # Assert
                assert fill is not None and fill.plain == "Click to fill the input: /run", (
                    "nothing runs: Enter does"
                )
                assert app._describe_link(10**9) == app._EXPIRED_LINK_MSG, (
                    "an old link says so before the click, not after"
                )

        asyncio.run(scenario())

    def test_a_filled_command_takes_what_you_type_next(self, app_factory):
        """A clicked name fills the input with a trailing space, nothing selected, so typing adds to it."""

        async def scenario():
            app, _, _ = app_factory(quiet_start=True)
            async with app.run_test(size=(120, 60)) as pilot:
                await pilot.pause()
                await _submit(pilot, "/help cfg.loaded")
                await until(pilot, lambda: any("/cfg.loaded - " in line for line in _screen(app)))
                inp = app.query_one("#cmd", Input)

                # Act
                await _click_text(pilot, app, "/cfg.loaded - ")
                await until(pilot, lambda: inp.has_focus)
                filled = inp.value
                await pilot.press("x")

                # Assert
                assert filled == "/cfg.loaded ", "ends with a space, ready for what follows"
                assert inp.value == "/cfg.loaded x", (
                    "the first key adds to the command, not over it"
                )

        asyncio.run(scenario())
