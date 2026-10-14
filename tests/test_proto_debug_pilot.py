"""The proto debug screen's Run and Stop buttons, driven headless with Pilot.

The screen's running flag used to be ``self._running`` -- which is also
Textual's own ``MessagePump._running``, True for the life of a mounted
screen -- so a guard on it refused every Run with "Already running".
The worker had masked the collision by overwriting the flag; the first
manual test after the guard was added (2026-10-11) found it.  Boots the
real app on the DEMO port (``tests/test_app_pilot.py`` pattern).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.widgets import Button, Static

from termapy.defaults import DEFAULT_CFG
from termapy.dialogs import ProtoPicker
from termapy.proto_debug import ProtoDebugScreen

_ONE_AT_TEST = """\
name = "One test"
frame_gap = "50ms"

[[test]]
name = "AT basic"
send = '"AT\\r"'
expect = '"OK\\r\\n"'
"""

# Five sends the demo never answers (a bare 00, no line ending), 2 s each.
_SILENT_TESTS = 'frame_gap = "50ms"\ntimeout = "2s"\n' + "".join(
    f'[[test]]\nname = "T{i}"\nsend = "00"\nexpect = "01"\n' for i in range(5)
)


def _run(scenario) -> None:
    """Run one async Pilot scenario (the suite is otherwise sync)."""
    asyncio.run(scenario())


def _make_app(tmp_path: Path, script_text: str):
    """A SerialTerminal on a throwaway DEMO config whose proto/ holds one script."""
    from termapy.app import SerialTerminal

    proj = tmp_path / "proj"
    (proj / "proto").mkdir(parents=True)
    script = proj / "proto" / "probe.pro"
    script.write_text(script_text, encoding="utf-8")
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}}
    path = proj / "proj.cfg"
    path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    cfg = dict(cfg)
    cfg["_config_path"] = str(path)
    app = SerialTerminal(cfg, str(path))
    # The checkout has a real termapy_cfg/plugin/; keep it out of the boot.
    app.repl.global_root = tmp_path
    return app, script


async def _open_debug_screen(pilot, script: Path) -> ProtoDebugScreen:
    """Connect the DEMO port and open the debug screen on ``script``."""
    await pilot.pause()
    assert pilot.app._connect() is True, "DEMO port should connect"
    await pilot.pause()
    result = pilot.app._dispatch_single(f"/proto.debug {script}")
    assert result.success is True, result.error
    await pilot.pause(0.5)
    screen = pilot.app.screen
    assert isinstance(screen, ProtoDebugScreen), "the debug screen is on top"
    return screen


async def _status_after(pilot, screen: ProtoDebugScreen, needle: str, seconds: float) -> str:
    """Poll the status bar until it contains ``needle`` or ``seconds`` pass."""
    status = screen.query_one("#proto-debug-status", Static)
    for _ in range(int(seconds * 10)):
        await pilot.pause(0.1)
        text = str(status.render())
        if needle in text:
            return text
    return str(status.render())


async def _disabled_after(pilot, button: Button, seconds: float) -> bool:
    """Poll until ``button`` is disabled or ``seconds`` pass; return its final state.

    The run worker posts the summary to the status bar BEFORE it clears the
    run state (the optional JSON write sits between the two), so reading
    Stop the moment the summary appears races the worker, and leaving
    ``run_test()`` then tears the screen down under the worker's last
    widget call.  Both lost under full-suite load on 2026-10-13.  Wait for
    the end state: Stop dark means the worker has finished.
    """
    for _ in range(int(seconds * 10)):
        if button.disabled:
            return True
        await pilot.pause(0.1)
    return button.disabled


class TestRunButton:

    def test_run_starts_a_run_and_reports_the_summary(self, tmp_path):
        """Run on a freshly opened screen starts the run and ends with the PASS summary and Stop dark again."""
        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                screen = await _open_debug_screen(pilot, script)
                stop = screen.query_one("#btn-dbg-stop", Button)
                assert stop.disabled is True, "nothing to stop before a run"

                # Act -- a one-test run finishes in a few frame gaps, so
                # Stop's live state during a run is pinned by the Stop test
                await pilot.click("#btn-dbg-send")
                text = await _status_after(pilot, screen, "PASS", 5.0)
                stop_dark = await _disabled_after(pilot, stop, 5.0)

                # Assert
                assert "1/1 PASS" in text, f"the run ran, got {text!r}"
                assert stop_dark is True, "Stop goes dark when the run ends"
        _run(scenario)


class TestArguments:

    def test_bad_arguments_are_refused_before_a_screen_opens(self, tmp_path):
        """/proto.debug refuses a missing file, a flat script and a TOML script without tests, each by its own message; bare opens the Proto picker, never the debug screen."""
        async def scenario():
            # Arrange -- the TUI rig, since the CLI refuses /proto.debug by capability
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            flat = script.parent / "flat.pro"
            flat.write_text('send: "AT\\r"\nexpect: "OK\\r\\n"\n', encoding="utf-8")
            none = script.parent / "none.pro"
            none.write_text('name = "none"\ntest = []\n', encoding="utf-8")
            async with app.run_test() as pilot:
                await pilot.pause()
                assert pilot.app._connect() is True, "DEMO port should connect"
                await pilot.pause()

                # Act
                bare = pilot.app._dispatch_single("/proto.debug")
                missing = pilot.app._dispatch_single("/proto.debug nope")
                not_toml = pilot.app._dispatch_single(f"/proto.debug {flat}")
                no_tests = pilot.app._dispatch_single(f"/proto.debug {none}")
                await pilot.pause()

                # Assert
                assert bare.success, f"bare /proto.debug opens the Proto picker: {bare.error}"
                assert isinstance(pilot.app.screen, ProtoPicker), "bare offers the picker to choose a script"
                assert missing.error.startswith("File not found: nope"), missing.error
                assert not_toml.error.startswith("Script error: TOML parse error"), "the debug screen is TOML-only"
                assert no_tests.error == "Script has no tests."
                assert not isinstance(pilot.app.screen, ProtoDebugScreen), "no refusal opened the screen"
        _run(scenario)


class TestStopButton:

    @pytest.mark.slow
    def test_stop_ends_the_run_after_the_current_test(self, tmp_path):
        """Stop during a run of silent tests ends it after the test in flight, with 'stopped by user' in the summary."""
        async def scenario():
            app, script = _make_app(tmp_path, _SILENT_TESTS)
            async with app.run_test() as pilot:
                screen = await _open_debug_screen(pilot, script)

                # Act -- Run, then Stop while the first 2 s test is waiting
                await pilot.click("#btn-dbg-send")
                await pilot.pause(0.2)
                await pilot.click("#btn-dbg-stop")
                stopping = str(screen.query_one("#proto-debug-status", Static).render())
                text = await _status_after(pilot, screen, "stopped by user", 6.0)
                # The summary lands before the worker clears its run state;
                # leaving run_test() in that gap tears the screen down under
                # the worker's last widget call (WorkerFailed, 2026-10-13).
                stop_dark = await _disabled_after(pilot, screen.query_one("#btn-dbg-stop", Button), 5.0)

                # Assert
                assert "Stopping" in stopping, "the press is acknowledged at once"
                assert "0/1 PASS" in text and "stopped by user" in text, (
                    f"one test ran, then the stop landed; got {text!r}"
                )
                assert stop_dark is True, "Stop goes dark once the stopped run has finished"
        _run(scenario)
