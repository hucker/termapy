"""The capture overlay, the delay bar, and the read-only info views, driven in the real app.

``capture_view.py`` and ``info_views.py`` are free functions over app
state; the only honest test is the app running them.  Boots the real
``SerialTerminal`` on a throwaway DEMO config, the
``tests/test_app_pilot.py`` pattern.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, Static

from termapy import info_views
from termapy.defaults import DEFAULT_CFG
from termapy.dialogs import PortPicker

# Wide enough that a temp-folder path renders on one line; at the 80x24
# headless default the info view wraps them and the text is unmatchable.
_SIZE = (220, 40)


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


async def _until(pilot, predicate, seconds: float = 5.0) -> bool:
    """Poll ``predicate`` every 20 ms until true or ``seconds`` pass."""
    for _ in range(int(seconds * 50)):
        if predicate():
            return True
        await pilot.pause(0.02)
    return predicate()


def _log_text(app) -> str:
    """The session log as written so far."""
    return Path(app.session_log_path()).read_text(encoding="utf-8")


def _squash(text: str) -> str:
    """``text`` with every run of whitespace removed: the output widget wraps a long path."""
    return "".join(text.split())


# ── capture overlay ──────────────────────────────────────────────────────────


class TestCaptureOverlay:
    def test_a_text_capture_shows_progress_and_ends_on_its_timer(self, app_factory):
        """A timed text capture mounts the overlay with a live label, disables the prompt, and tears down when the timer fires."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                assert app._connect() is True, "DEMO port should connect"
                bar = app.query_one("#bottom-bar")
                inp = app.query_one("#cmd", Input)

                # Act
                await _submit(pilot, "/cap.text t.txt timeout=1s")
                assert await _until(pilot, lambda: bool(bar.query("#cap-stop"))), "overlay mounted"
                prompt_disabled_during = inp.disabled
                labelled = await _until(
                    pilot,
                    lambda: (
                        "Capturing -> t.txt" in str(bar.query_one("#cap-label", Static).render())
                    ),
                )
                gone = await _until(pilot, lambda: not bar.query("#cap-stop"), seconds=4.0)

                # Assert
                assert (prompt_disabled_during, labelled, gone) == (True, True, True)
                assert inp.disabled is False, "prompt back after the capture"
                log = _log_text(app)
                assert "capture start:" in log and "mode=text" in log, log
                assert "capture end:" in log, log

        _run(scenario)

    def test_the_stop_button_ends_a_binary_capture(self, app_factory):
        """A byte-target capture shows bytes against the target; Stop ends it and releases the port claim."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                assert app._connect() is True, "DEMO port should connect"
                bar = app.query_one("#bottom-bar")

                # Act
                await _submit(pilot, "/cap.bin t.bin bytes=100000")
                assert await _until(pilot, lambda: bool(bar.query("#cap-stop"))), "overlay mounted"
                claimed_during = app._engine.serial_claimed
                labelled = await _until(
                    pilot,
                    lambda: "/ 97.7 KB" in str(bar.query_one("#cap-label", Static).render()),
                )
                await pilot.click("#cap-stop")
                gone = await _until(pilot, lambda: not bar.query("#cap-stop"), seconds=2.0)

                # Assert
                assert (claimed_during, labelled, gone) == (True, True, True)
                assert app._engine.serial_claimed is False, "the claim ends with the capture"
                assert "capture end:" in _log_text(app)

        _run(scenario)

    def test_a_second_capture_is_refused_while_one_runs(self, app_factory):
        """Starting a capture over an active one fails the command and says so."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                assert app._connect() is True, "DEMO port should connect"
                first = app._dispatch_single("/cap.text a.txt timeout=5s")
                await pilot.pause()

                # Act
                second = app._dispatch_single("/cap.text b.txt timeout=5s")
                await pilot.pause()

                # Assert
                assert first.success is True, first.error
                assert second.success is False and second.error == "Capture not started."
                assert "Capture already active" in app._get_screen_text()
                app._dispatch_single("/cap.stop")
                await pilot.pause()

        _run(scenario)

    def test_a_scripted_capture_leaves_the_bar_to_the_script(self, app_factory):
        """Inside a script the capture mounts no overlay of its own; the bar is usable again when the script ends."""

        async def scenario():
            app, _, path = app_factory()
            folder = Path(path).parent / "run"
            folder.mkdir()
            # The delay keeps the script alive long enough to be watched.
            (folder / "cap.run").write_text(
                "/cap.bin t.bin bytes=100000\n/delay 300ms\n/cap.stop\n",
                encoding="utf-8",
            )
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                assert app._connect() is True, "DEMO port should connect"
                bar = app.query_one("#bottom-bar")
                cap_overlay_seen = False

                # Act
                await _submit(pilot, "/run cap")
                assert await _until(pilot, lambda: bool(bar.query("#script-stop"))), (
                    "script overlay mounted"
                )
                for _ in range(250):
                    if not bar.query("#script-stop"):
                        break
                    cap_overlay_seen = cap_overlay_seen or bool(bar.query("#cap-label"))
                    await pilot.pause(0.02)

                # Assert
                assert cap_overlay_seen is False, "the script overlay owns the bar"
                assert not bar.query("#cap-label"), "nothing left behind"
                assert app.query_one("#cmd", Input).disabled is False
                log = _log_text(app)
                assert "capture start:" in log and "capture end:" in log, log

        _run(scenario)


# ── the delay bar ────────────────────────────────────────────────────────────


class TestDelayBar:
    """Two delay paths: a scripted /delay animates in the script overlay's label through the
    engine's blocking handler; one typed at the prompt runs the capture-view progress bar."""

    def test_stop_ends_a_scripted_delay_as_a_stopped_script(self, app_factory):
        """Stop during a scripted /delay of seconds ends it at the next tick; the script reports itself stopped."""

        async def scenario():
            app, _, path = app_factory()
            folder = Path(path).parent / "run"
            folder.mkdir()
            (folder / "slow.run").write_text("/delay 3s\n", encoding="utf-8")
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                bar = app.query_one("#bottom-bar")

                # Act
                await _submit(pilot, "/run slow")
                assert await _until(pilot, lambda: bool(bar.query("#script-stop"))), (
                    "overlay mounted"
                )
                barred = await _until(
                    pilot,
                    lambda: "0s/3s" in str(bar.query_one("#script-label", Static).render()),
                )
                await pilot.click("#script-stop")
                gone = await _until(pilot, lambda: not bar.query("#script-stop"), seconds=2.0)

                # Assert
                assert barred is True, "the delay bar rides the script label"
                assert gone is True, "stopped well inside the 3 s"
                assert "Script stopped." in app._get_screen_text()

        _run(scenario)

    def test_escape_cancels_a_delay_typed_at_the_prompt(self, app_factory):
        """A /delay typed at the prompt runs the progress bar; Escape cancels it at the next tick and says so."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()

                # Act
                await _submit(pilot, "/delay 3s")
                await pilot.pause(0.3)  # one tick of the bar
                await pilot.press("escape")
                reported = await _until(
                    pilot,
                    lambda: "Delay 3s canceled." in app._get_screen_text(),
                    seconds=2.0,
                )

                # Assert
                assert reported is True, "cancelled well inside the 3 s"

        _run(scenario)


# ── info views ───────────────────────────────────────────────────────────────


class TestConfigInfo:
    def test_verbose_shows_the_config_paths(self, app_factory):
        """At verbose output the config info lists the folder, file, transfer root (cap/ by default), and log."""

        async def scenario():
            app, _, path = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                app._dispatch_single("/term.output verbose")
                await pilot.pause()

                # Act
                app._show_config_info(path)
                await pilot.pause()

                # Assert
                text = _squash(app._get_screen_text())
                resolved = Path(path).resolve()
                assert _squash(f"Config dir: {resolved.parent}") in text, text
                assert _squash(f"Config file: {resolved}") in text
                assert _squash(f"Xfer root: {resolved.parent / 'cap'}") in text, (
                    "cap/ when no root is configured"
                )
                assert "Logfile:" in text

        _run(scenario)

    def test_a_configured_transfer_root_is_shown(self, app_factory, tmp_path):
        """A file_xfer_root in the config replaces the cap/ default in the listing."""

        async def scenario():
            root = tmp_path / "xfer"
            app, _, path = app_factory(file_xfer_root=str(root))
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                app._dispatch_single("/term.output verbose")
                await pilot.pause()
                app._show_config_info(path)
                await pilot.pause()
                assert _squash(f"Xfer root: {root.resolve()}") in _squash(app._get_screen_text())

        _run(scenario)

    def test_silent_below_verbose(self, app_factory):
        """At the default output level the config info prints nothing."""

        async def scenario():
            app, _, path = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                app._show_config_info(path)
                await pilot.pause()
                assert "Config dir:" not in app._get_screen_text()

        _run(scenario)


class TestPortPicker:
    def test_the_picker_opens_unless_exactly_one_port(self, app_factory):
        """With zero or several ports the picker opens; with exactly one it would switch to it unasked, so that case is skipped here."""
        from serial.tools.list_ports import comports

        if len(list(comports())) == 1:
            pytest.skip("exactly one port: the view would pick it and switch without a dialog")

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                info_views.show_port_picker(app)
                await pilot.pause()
                assert isinstance(app.screen, PortPicker)

        _run(scenario)


class TestCommandPopup:
    def test_the_filter_narrows_to_matching_commands(self, app_factory):
        """Typed text filters the popup to commands containing it; a command with a required arg gets only the fill-in row."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                app.query_one("#cmd", Input).value = "/proto.run"

                # Act
                app._show_commands()
                await pilot.pause()

                # Assert
                popup = app.query_one("#history-popup", OptionList)
                ids = {
                    option.id
                    for option in (popup.get_option_at_index(i) for i in range(popup.option_count))
                    if option.id
                }
                assert ids == {"repl:proto.run"}, (
                    "one command, fill-in row only (its arg is required)"
                )
                assert app._popup_mode == "commands"

        _run(scenario)

    def test_no_match_shows_a_placeholder(self, app_factory):
        """A filter nothing matches shows a disabled placeholder naming it."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                app.query_one("#cmd", Input).value = "zzzq"
                app._show_commands()
                await pilot.pause()
                popup = app.query_one("#history-popup", OptionList)
                first = popup.get_option_at_index(0)
                assert first.disabled and "(no commands match 'zzzq')" in str(first.prompt)

        _run(scenario)

    def test_selecting_a_fill_in_row_puts_the_command_in_the_prompt(self, app_factory):
        """Choosing a command with arguments fills the prompt with it and a trailing space, ready for the args."""

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=_SIZE) as pilot:
                await pilot.pause()
                inp = app.query_one("#cmd", Input)
                inp.value = "/proto.run"
                app._show_commands()
                await pilot.pause()

                # Act
                await pilot.press("enter")
                await pilot.pause()

                # Assert
                assert inp.value == "/proto.run ", "filled, with a space for the args"
                assert app.query_one("#history-popup", OptionList).has_class("visible") is False

        _run(scenario)
