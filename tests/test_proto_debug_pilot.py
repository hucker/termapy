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

                # Assert
                assert "1/1 PASS" in text, f"the run ran, got {text!r}"
                assert stop.disabled is True, "Stop goes dark when the run ends"
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

                # Assert
                assert "Stopping" in stopping, "the press is acknowledged at once"
                assert "0/1 PASS" in text and "stopped by user" in text, (
                    f"one test ran, then the stop landed; got {text!r}"
                )
        _run(scenario)
