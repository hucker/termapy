"""Pilot-driven tests for the real SerialTerminal app.

Every other Pilot test in the suite drives a minimal ``_Host`` stage holding
one widget.  These boot the FULL app headless via ``app.run_test()`` and
assert on its state, which is the only way to reach ``app.py``'s glue: those
methods coordinate ``self`` -- disconnect, swap the repl's cfg, reset history
navigation -- so there is no pure function to extract and unit-test.  Driving
the app is the test.

Known limits (see the TUI focus work): ``run_test()`` does not model a real
terminal's focus/blur, and ``@work`` thread timing does not reproduce
faithfully.  So these cover STATE TRANSITIONS, not input-focus behavior.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
import serial

from termapy.defaults import DEFAULT_CFG


def _run(scenario) -> None:
    """Run one async Pilot scenario (the suite is otherwise sync)."""
    asyncio.run(scenario())


def _write_cfg(tmp_path, name: str = "proj", **overrides) -> tuple[dict, str]:
    """A DEMO-port config on disk, shaped the way the CLI builds one."""
    proj = tmp_path / name
    proj.mkdir(parents=True, exist_ok=True)
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**serial, "port": "DEMO"},
        # No auto-connect: these tests are about app state, and chasing a
        # port would make them depend on the demo device's timing.
        "auto_connect": False,
        **overrides,
    }
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
        return SerialTerminal(cfg, path), cfg, path
    return _make


class TestAppBoots:
    """Booting the real app is itself coverage: __init__ and compose run."""

    def test_mounts_its_widget_tree(self, app_factory):
        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange / Act done by run_test(); Assert on the result
                widgets = list(pilot.app.query("*"))
                assert len(widgets) > 20, (
                    f"the full app composes a real widget tree, got {len(widgets)}"
                )
                assert app.config_path == path, "app holds the config it was given"

        _run(scenario)

    def test_starts_disconnected_when_auto_connect_is_off(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.is_connected is False, (
                    "auto_connect=False leaves the port closed"
                )

        _run(scenario)

    def test_repl_engine_is_wired_to_the_config(self, app_factory):
        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.repl.config_path == path, (
                    "the REPL engine points at the same config as the app"
                )

        _run(scenario)


class TestSwitchConfig:
    """``_switch_config`` is pure orchestration -- 36 lines of self.* wiring.

    It cannot be unit-tested because it IS the wiring; driving the app is
    the only way to observe that every collaborator got updated.
    """

    def test_swaps_every_piece_of_config_state(self, app_factory, tmp_path):
        async def scenario():
            app, _, first_path = app_factory(name="first")
            async with app.run_test() as pilot:
                await pilot.pause()
                second_cfg, second_path = _write_cfg(
                    tmp_path, name="second", hex=True, line_no=True,
                )

                # Act
                app._switch_config(second_cfg, second_path)
                await pilot.pause()

                # Assert -- every collaborator the method touches
                actual = (
                    app.config_path,
                    app.repl.config_path,
                    app.repl.ctx.config_path,
                    app.repl.ctx.ns("flags")["hex"],
                    app._show_line_numbers,
                )
                expected = (second_path, second_path, second_path, True, True)
                assert actual == expected, (
                    "app, repl, ctx, flags and display state all follow the swap"
                )
                assert app.config_path != first_path, "the old config is gone"

        _run(scenario)

    def test_surfaces_config_warnings(self, app_factory, tmp_path):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                warned_cfg, warned_path = _write_cfg(tmp_path, name="warned")
                warned_cfg["_config_warnings"] = ["stale key: 'widgets'"]

                # Act
                app._switch_config(warned_cfg, warned_path)
                await pilot.pause()

                # Assert -- the warning is consumed, not left on the cfg to
                # resurface on every later read
                assert "_config_warnings" not in warned_cfg, (
                    "warnings are popped once surfaced"
                )

        _run(scenario)

    def test_reports_a_migration_and_clears_its_steps(self, app_factory, tmp_path):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                migrated, migrated_path = _write_cfg(tmp_path, name="migrated")
                migrated["_migrated_from"] = 27
                migrated["_migration_steps"] = ["v27 -> v28: renamed foo"]

                # Act
                app._switch_config(migrated, migrated_path)
                await pilot.pause()

                # Assert -- both migration keys are consumed so a later
                # save cannot write them back into the user's file
                assert "_migrated_from" not in migrated, "migration marker popped"
                assert "_migration_steps" not in migrated, "step list popped"

        _run(scenario)

    def test_drops_stale_migration_steps_without_a_migration(
        self, app_factory, tmp_path
    ):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a hand-edited cfg carrying steps but no marker
                stale, stale_path = _write_cfg(tmp_path, name="stale")
                stale["_migration_steps"] = ["bogus"]

                # Act
                app._switch_config(stale, stale_path)
                await pilot.pause()

                # Assert
                assert "_migration_steps" not in stale, (
                    "a stale step list is dropped silently, not reported"
                )

        _run(scenario)


class TestInputLine:
    """Driving the REPL input reaches on_input_submitted and on_key.

    These are the app's primary interaction path and cannot be reached any
    other way -- they are Textual event handlers by definition.
    """

    def test_submitting_a_repl_command_dispatches_it(self, app_factory):
        async def scenario():
            from textual.widgets import Input
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                cmd_input = app.query_one("#cmd", Input)

                # Act -- type a REPL command and press enter
                cmd_input.value = "/print pilot-was-here"
                cmd_input.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                # Assert -- the input clears, which only happens on the
                # submit path after dispatch
                assert cmd_input.value == "", "the input clears after submit"

        _run(scenario)

    def test_submitted_command_enters_history(self, app_factory):
        async def scenario():
            from textual.widgets import Input
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                cmd_input = app.query_one("#cmd", Input)

                # Act
                cmd_input.value = "/print remembered"
                cmd_input.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                # Assert
                assert "/print remembered" in app.history, (
                    "a submitted command is recorded in history"
                )

        _run(scenario)

    def test_empty_submit_is_ignored_by_default(self, app_factory):
        async def scenario():
            from textual.widgets import Input
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                before = list(app.history)
                cmd_input = app.query_one("#cmd", Input)

                # Act -- bare enter, with send_bare_enter defaulted off
                cmd_input.value = ""
                cmd_input.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                # Assert
                assert list(app.history) == before, (
                    "a bare enter adds nothing to history when send_bare_enter is off"
                )

        _run(scenario)

    def test_duplicate_command_is_not_double_recorded(self, app_factory):
        async def scenario():
            from textual.widgets import Input
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                cmd_input = app.query_one("#cmd", Input)

                # Act -- submit the same command twice
                for _ in range(2):
                    cmd_input.value = "/print twice"
                    cmd_input.focus()
                    await pilot.pause()
                    await pilot.press("enter")
                    await pilot.pause()

                # Assert -- history keeps the most recent, not both
                assert app.history.count("/print twice") == 1, (
                    "an earlier duplicate is removed rather than stacked"
                )

        _run(scenario)


class TestOutputBatching:
    """``_write_batch`` is the serial reader's path onto the screen."""

    def test_writes_lines_to_the_output_log(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- the batched write the reader thread uses
                app._write_batch(["alpha", "beta", "gamma"])
                await pilot.pause()

                # Assert -- reaching here without raising means the batch
                # path ran against real widgets; a torn-down or missing
                # #output would have raised
                assert app.is_running, "the app survives a batched write"

        _run(scenario)

    def test_empty_batch_is_harmless(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app._write_batch([])
                await pilot.pause()

                # Assert
                assert app.is_running, "an empty batch is a no-op, not an error"

        _run(scenario)


class TestFindBar:
    """``_update_find_bar`` swaps the live output for a frozen, highlighted copy.

    It is driven by ``/find`` through ``ctx.internal.update_find_bar``, so the
    state dict is the plugin's contract -- these build it directly rather than
    routing through the plugin, keeping the test about the UI half.
    """

    @staticmethod
    def _state(**overrides) -> dict:
        """A find-state snapshot shaped like find.current_state().

        Kept honest by test_fixture_matches_the_real_contract below: a
        hand-rolled dict that drifts from the plugin would let these tests
        pass while the real UI broke on a missing key.
        """
        state = {
            "pattern": "beta",
            "total": 1,
            "matches": [(2, "beta line")],
            "scrollback_text": "alpha line\nbeta line\ngamma line",
            "index": 0,
            "line_no": 2,
            "snippet": "beta line",
        }
        state.update(overrides)
        return state

    def test_fixture_matches_the_real_contract(self):
        # Arrange -- drive the real plugin so current_state() builds a
        # genuine snapshot to compare shapes against
        from termapy.builtins.commands import find

        find._active = find._Active(
            pattern="beta",
            matches=[(2, "beta line")],
            current=0,
            scrollback_text="alpha line\nbeta line\ngamma line",
        )
        try:
            real = find.current_state()
        finally:
            find._active = None

        # Assert -- same keys, so a contract change fails HERE rather than
        # silently diverging from what the UI actually receives
        actual, expected = set(self._state()), set(real)
        assert actual == expected, (
            f"fixture drifted from find.current_state(): "
            f"missing {expected - actual}, extra {actual - expected}"
        )

    def test_activating_shows_the_bar_and_freezes_output(self, app_factory):
        async def scenario():
            from textual.widgets import RichLog
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app._update_find_bar(self._state())
                await pilot.pause()

                # Assert -- the frozen view takes over from the live log
                live = app.query_one("#output", RichLog)
                frozen = app.query_one("#output-find", RichLog)
                actual = (live.display, frozen.display)
                assert actual == (False, True), (
                    "find hides live #output and reveals the frozen #output-find"
                )

        _run(scenario)

    def test_activating_reveals_every_find_bar_widget(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app._update_find_bar(self._state())
                await pilot.pause()

                # Assert
                hidden = [sel for sel in app._FIND_BAR_WIDGETS
                          if not app.query_one(sel).display]
                assert hidden == [], f"every find widget is shown, hidden: {hidden}"

        _run(scenario)

    def test_record_button_steps_aside_for_the_bar(self, app_factory):
        async def scenario():
            from textual.widgets import Button
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app._update_find_bar(self._state())
                await pilot.pause()

                # Assert -- find takes the row, Record yields it
                assert app.query_one("#btn-record", Button).display is False, (
                    "the Record button hides while the find bar owns the row"
                )

        _run(scenario)

    def test_deactivating_restores_the_live_view(self, app_factory):
        async def scenario():
            from textual.widgets import RichLog
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._update_find_bar(self._state())
                await pilot.pause()

                # Act -- state=None is the "close find" signal
                app._update_find_bar(None)
                await pilot.pause()

                # Assert
                live = app.query_one("#output", RichLog)
                frozen = app.query_one("#output-find", RichLog)
                actual = (live.display, frozen.display)
                assert actual == (True, False), (
                    "closing find restores live #output and hides the frozen copy"
                )

        _run(scenario)

    def test_zero_matches_leaves_the_live_view_alone(self, app_factory):
        async def scenario():
            from textual.widgets import RichLog
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- a pattern that matched nothing: there is no frozen
                # view worth building, so the live output must stay put
                app._update_find_bar(
                    self._state(total=0, matches=[], index=-1)
                )
                await pilot.pause()

                # Assert
                assert app.query_one("#output", RichLog).display is True, (
                    "a zero-match find does not freeze the live output"
                )

        _run(scenario)

    def test_status_label_reports_position(self, app_factory):
        async def scenario():
            from textual.widgets import Label
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- second of three matches
                app._update_find_bar(self._state(
                    total=3,
                    matches=[(1, "a"), (2, "b"), (3, "c")],
                    index=1,
                ))
                await pilot.pause()

                # Assert -- the slim label is "n/m", 1-based for humans.
                # Textual 8.x exposes the text as `.content`, not the older
                # `.renderable`.
                text = str(app.query_one("#find-status", Label).content)
                assert "2" in text and "3" in text, (
                    f"status shows current/total, got {text!r}"
                )

        _run(scenario)


class TestSerialRxHandoff:
    """The reader hands RX to the main thread WITHOUT blocking on it.

    Blocking was the root of three defects: the reader stalled long enough for
    the driver to silently drop bytes, and ``disconnect()`` waited on the
    reader from the very thread the reader was waiting for, so teardown was
    guaranteed to time out (docs/review/2026-08-19-v0.74.0-opus-5.md, T2/T15).
    """

    def test_enqueue_never_blocks_and_posts_one_wakeup(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- simulate the reader thread queueing three chunks
                # back to back with no drain in between.
                for i in range(3):
                    pilot.app._rx_enqueue(("lines", [f"line {i}"]))

                # Assert -- all three are buffered, but only ONE wake-up is
                # in flight; that conflation is what bounds the message queue.
                assert len(pilot.app._rx_events) == 3, "every event is retained"
                assert pilot.app._rx_posted is True, "a wake-up is pending"

                # Act -- let the handler run.
                await pilot.pause()

                # Assert
                assert pilot.app._rx_events == [], "handler drained the buffer"
                assert pilot.app._rx_posted is False, "re-armed for the next event"
                text = pilot.app._get_screen_text()
                for i in range(3):
                    assert f"line {i}" in text, f"line {i} should have rendered"

        _run(scenario)

    def test_clear_does_not_overtake_the_lines_before_it(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- ordering across event KINDS is the reason they
                # share one list: a clear-screen escape must wipe the lines
                # queued before it and keep the ones after.
                pilot.app._rx_enqueue(("lines", ["BEFORE-CLEAR"]))
                pilot.app._rx_enqueue(("clear",))
                pilot.app._rx_enqueue(("lines", ["AFTER-CLEAR"]))

                # Act
                await pilot.pause()

                # Assert
                text = pilot.app._get_screen_text()
                assert "AFTER-CLEAR" in text, "post-clear output must survive"
                assert "BEFORE-CLEAR" not in text, (
                    "the clear must be applied AFTER the lines queued before "
                    "it, not reordered ahead of them"
                )

        _run(scenario)

    def test_backlog_is_bounded_and_the_drop_is_reported(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a main thread far enough behind that the backlog
                # exceeds the cap.  Unlike the driver-level loss this design
                # replaced, the drop must be counted and surfaced.
                limit = pilot.app._RX_EVENT_LIMIT
                for i in range(limit + 50):
                    pilot.app._rx_enqueue(("lines", [f"flood {i}"]))

                # Assert -- bounded before any drain happens.
                actual = len(pilot.app._rx_events)
                assert actual == limit, f"backlog capped at {limit}, got {actual}"
                assert pilot.app._rx_dropped == 50, "dropped lines are counted"

                # Act
                await pilot.pause()

                # Assert -- the newest data survives and the loss is visible.
                text = pilot.app._get_screen_text()
                assert "fell behind" in text, (
                    "dropping display data must be reported, never silent"
                )
                assert f"flood {limit + 49}" in text, "newest lines are kept"

        _run(scenario)

    def test_device_output_reaches_the_screen_through_the_real_reader(
        self, app_factory
    ):
        """End-to-end: read_loop -> on_lines -> _rx_enqueue -> handler -> RichLog.

        The other tests in this class drive ``_rx_enqueue`` directly, so they
        would still pass if the reader were wired to the wrong callback. This
        one connects the real DEMO device and waits for its reply to render.
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- real connect, real reader thread.
                connected = pilot.app._connect()
                assert connected is True, "DEMO port should connect"
                await pilot.pause()

                # Act -- ask the simulated device something it answers.
                pilot.app._serial_write(b"AT\r")

                # Assert -- poll rather than sleep a fixed time: passes the
                # instant the line lands, and only fails on a real stall.
                deadline = asyncio.get_running_loop().time() + 5.0
                text = ""
                while asyncio.get_running_loop().time() < deadline:
                    await pilot.pause()
                    text = pilot.app._get_screen_text()
                    if "OK" in text:
                        break
                    await asyncio.sleep(0.02)
                assert "OK" in text, (
                    "device reply never reached the screen -- the reader's "
                    f"non-blocking handoff is broken. Screen was: {text!r}"
                )
                pilot.app._disconnect()

        _run(scenario)

    def test_disconnect_leaves_no_reader_thread_behind(self, app_factory):
        """Teardown joins the reader instead of hoping it stopped.

        The old handshake set an Event, waited ``READER_STOP_WAIT_S``, and
        proceeded regardless -- which with RX in flight meant proceeding while
        the reader was still alive (T1/T2).
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- real connect, real engine-owned reader thread.
                assert pilot.app._connect() is True, "DEMO port should connect"
                await pilot.pause()
                thread = pilot.app._engine._reader_thread
                assert thread is not None, "the engine should own a reader thread"
                assert thread.is_alive() is True, "reader should be running"

                # Act
                pilot.app._disconnect()
                await pilot.pause()

                # Assert
                assert thread.is_alive() is False, (
                    "reader thread outlived _disconnect(); teardown must join "
                    "it, not wait on an Event and proceed"
                )
                assert pilot.app._engine.is_connected is False, "engine disconnected"

        _run(scenario)


class _RaisingPort:
    """A SerialPort whose write() fails, e.g. the adapter is unplugged mid-send.

    Shaped like ``SerialPort`` for the two members ``_serial_write`` touches.
    """

    def __init__(self) -> None:
        self.writes = 0

    def write(self, data: bytes) -> None:
        self.writes += 1
        raise serial.SerialException("simulated unplug")


class TestSharedStateRaces:
    """Fields the reader thread nulls under the main thread's feet.

    Each of these methods used to read a shared field, pass a truth check,
    and then use the field -- so a teardown landing in that gap produced an
    exception the handler did not catch.  The fixes bind the field to a
    local and widen the caught set; these tests drive the *observable* half
    (a handle that is closed, a widget that is gone, a port that fails),
    which is what the uncaught exception rode in on.
    """

    def test_write_failure_is_reported_not_raised(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a port that fails the way an unplugged adapter does
                port = _RaisingPort()
                app._engine._serial_port = port

                # Act -- the send path every typed line and script uses
                app._serial_write(b"AT\r")
                await pilot.pause()

                # Assert
                assert port.writes == 1, "the write was attempted on the bound port"
                text = app._get_screen_text()
                assert "Write failed" in text, (
                    "a failed send must be reported, not raised into the "
                    f"dispatch worker. Screen was: {text!r}"
                )
                assert app.is_running, "a failed write must not kill the app"

        _run(scenario)

    def test_missing_port_logs_instead_of_writing(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- exactly what the reader's finally leaves behind
                app._engine._serial_port = None
                app._open_log()

                # Act
                app._serial_write(b"\x01\x02")
                await pilot.pause()

                # Assert
                logged = Path(app._log_path()).read_text(encoding="utf-8")
                assert "01 02" in logged, (
                    "with no port the bytes go to the log as hex, not to a "
                    f"None write. Log was: {logged!r}"
                )

        _run(scenario)

    def test_log_line_survives_a_handle_closed_under_it(self, app_factory):
        """``/log.delete`` closes ``log_fh`` from a dispatch worker.

        The reader thread can already be inside ``_log_line`` when that
        happens: the write then lands on a closed file, which raises
        ``ValueError`` -- not the ``OSError`` the handler used to catch.
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                app._open_log()
                assert app.log_fh is not None, "a config on disk opens a log"
                app.log_fh.close()  # the close half of /log.delete

                # Act / Assert -- reaching the next line is the assertion
                app._log_line("<", "arrives after the close")
                await pilot.pause()
                assert app.is_running, (
                    "a log handle closed under the writer must be swallowed, "
                    "not raised onto the reader thread"
                )

        _run(scenario)

    def test_markup_write_survives_a_missing_output_widget(self, app_factory):
        """``_write_output_markup`` was the one output helper with no guard.

        Its siblings (``_status``, ``_set_status_bar``) already caught the
        shutdown race; this one queried ``#output`` bare, so a plugin writing
        markup during teardown raised ``NoMatches``.
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- the widget is gone, as during teardown
                from textual.widgets import RichLog
                await app.query_one("#output", RichLog).remove()
                await pilot.pause()

                # Act / Assert -- reaching the next line is the assertion
                app._write_output_markup("[green]late markup[/green]")
                assert app.is_running, (
                    "markup written after #output is gone must be dropped, "
                    "not raised"
                )

        _run(scenario)


class TestDispatchContention:
    """D3: a refused dispatch is not a delayed command, it is a lost one.

    The guard must stay non-blocking on the MAIN thread -- a worker holding
    it can be parked in ``call_from_thread``, so a main thread that waits
    closes the cycle.  Off main there is no cycle, so a worker waits rather
    than dropping the line on the floor.
    """

    def test_a_worker_waits_for_the_guard_instead_of_dropping_the_line(
        self, app_factory
    ):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- another thread holds the guard, the way a
                # command still running holds it.
                held = threading.Event()
                release = threading.Event()

                def holder() -> None:
                    app._dispatch_guard.acquire()
                    held.set()
                    release.wait(5)
                    app._dispatch_guard.release()

                keeper = threading.Thread(target=holder)
                keeper.start()
                assert held.wait(5), "precondition: the guard is held"

                # Act -- a dispatch worker asks for it mid-hold
                box: list = []
                worker = threading.Thread(
                    target=lambda: box.append(app._dispatch_single("/print waited"))
                )
                worker.start()
                await asyncio.sleep(0.2)

                # Assert -- waiting, not refused.  The main thread stayed
                # responsive throughout (this await proves it).
                assert box == [], (
                    "an off-main dispatch must wait for the guard, not skip "
                    f"the line, got {box}"
                )

                # Act -- the holder finishes
                release.set()
                deadline = asyncio.get_running_loop().time() + 5.0
                while not box and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                worker.join(5)
                keeper.join(5)

                # Assert
                assert box and box[0].success is True, (
                    "the line must run once the guard frees; on a serial tool "
                    f"a skipped line never reaches the device. Got {box}"
                )

        _run(scenario)

    def test_the_main_thread_is_refused_immediately(self, app_factory):
        """Deadlock-freedom, pinned.

        If main ever waits for the guard it can park behind a worker that is
        itself parked in ``call_from_thread`` waiting for main.
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                held = threading.Event()
                release = threading.Event()

                def holder() -> None:
                    app._dispatch_guard.acquire()
                    held.set()
                    release.wait(5)
                    app._dispatch_guard.release()

                keeper = threading.Thread(target=holder)
                keeper.start()
                assert held.wait(5), "precondition: the guard is held"

                # Act -- main asks for a guard it cannot afford to wait on
                t0 = time.monotonic()
                result = app._dispatch_single("/print main")
                elapsed = time.monotonic() - t0
                release.set()
                keeper.join(5)

                # Assert
                assert result.success is False, "main is refused, never queued"
                assert elapsed < app._DISPATCH_WAIT_S / 2, (
                    "the main thread must be refused immediately, not after "
                    f"the off-main wait; took {elapsed:.2f}s"
                )

        _run(scenario)


class TestMarshalling:
    """Every worker-thread path onto a widget must go through the main thread."""

    def test_the_app_owns_its_main_thread_id(self, app_factory):
        """D4: the marshalling decision no longer reads Textual private API.

        ``App._thread_id`` is private, set by ``_thread_init`` and zeroed
        after shutdown; four output helpers branch on it.
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Assert -- on_mount stamped the loop thread, which is the
                # thread this scenario runs on.
                actual = app._main_thread_id
                expected = threading.get_ident()
                assert actual == expected, (
                    "on_mount runs on the event-loop thread, so that is the "
                    f"id to compare against; got {actual} vs {expected}"
                )

        _run(scenario)

    def test_status_from_a_worker_reaches_the_screen(self, app_factory):
        """The id is only useful if it still routes a worker's output."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- exactly what a dispatch worker does
                done = threading.Event()

                def worker() -> None:
                    app._status("from a worker", "green")
                    done.set()

                threading.Thread(target=worker, daemon=True).start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while not done.is_set() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                await pilot.pause()

                # Assert
                assert done.is_set(), "the worker's status call must not park"
                text = app._get_screen_text()
                assert "from a worker" in text, (
                    f"off-main status must marshal onto #output, got {text!r}"
                )

        _run(scenario)

    def test_find_bar_updates_are_marshalled(self, app_factory):
        """T12: the one internal-handle slot wired without marshalling.

        ``/find`` runs on a dispatch worker and mutates widgets, while
        ``_write_batch`` auto-dismisses the overlay from the main thread.
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                internal = app.repl.ctx.internal

                # Assert -- the slot is a marshalling wrapper, not the bare
                # method.  This is the defect itself: every sibling slot
                # forwards through _on_main or call_later.
                assert internal.update_find_bar != app._update_find_bar, (
                    "update_find_bar must be wired through the main-thread "
                    "forwarder, not bound straight to the widget mutator"
                )

                # Act -- drive it from a worker, as /find does
                done = threading.Event()
                threading.Thread(
                    target=lambda: (internal.update_find_bar(None), done.set()),
                    daemon=True,
                ).start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while not done.is_set() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                await pilot.pause()

                # Assert
                assert done.is_set(), "the worker's find-bar update must not park"
                from textual.widgets import RichLog
                assert app.query_one("#output", RichLog).display is True, (
                    "a None state hides the frozen view and restores #output"
                )

        _run(scenario)


class TestConfirmIsStopAware:
    """T14: ``_confirm`` parked on an unbounded ``event.wait()``.

    The dialog's own bindings cover the normal path, but a stop or a
    shutdown with the modal up left the worker waiting forever -- and
    teardown joins the threads workers run on.
    """

    def test_a_script_stop_releases_a_waiting_confirm(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a worker parked on the dialog, as /confirm is
                box: list = []
                worker = threading.Thread(
                    target=lambda: box.append(app._confirm("Proceed?")),
                    daemon=True,
                )
                worker.start()
                await asyncio.sleep(0.3)
                assert box == [], "precondition: the confirm is still waiting"

                # Act -- the user stops the script while the modal is up
                app.repl._script_stop.set()
                deadline = asyncio.get_running_loop().time() + 5.0
                while not box and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.05)
                worker.join(2)

                # Assert
                assert box, (
                    "a stopped run must release the confirm; an unbounded "
                    "wait here is a worker teardown can never join"
                )
                assert box[0] is False, "an abandoned confirm fails closed"
                app.repl._script_stop.clear()

        _run(scenario)


class TestBatchedOutputIsEquivalent:
    """E1-E3 traded per-line work for per-batch work.

    The speed is measured out of band (a per-line RichLog.write ran
    605-669 us; one combined write for a 50-line batch ran ~9 ms).  What
    these pin is the part a benchmark cannot: that the batch renders,
    logs and captures exactly what the per-line version did.
    """

    def test_every_line_of_a_batch_reaches_the_screen_in_order(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                app._write_batch([f"line {i}" for i in range(50)])
                await pilot.pause()

                # Assert
                text = app._get_screen_text()
                first = text.find("line 0")
                last = text.find("line 49")
                assert first != -1 and last != -1, "every line of the batch renders"
                assert first < last, "one combined write must preserve line order"

        _run(scenario)

    def test_escapes_are_stripped_when_color_is_off(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                app.repl.ctx.ns("flags")["color"] = False

                # Act -- device SGR color arriving with color rendering off
                app._write_batch(["\x1b[31mRED-TEXT\x1b[0m"])
                await pilot.pause()

                # Assert
                text = app._get_screen_text()
                assert "RED-TEXT" in text, "the text still shows"
                assert "[31m" not in text, (
                    "with color off the escapes are stripped, not leaked as "
                    f"literal characters. Screen was: {text!r}"
                )

        _run(scenario)

    def test_the_batch_logs_one_entry_per_line(self, app_factory):
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                app._open_log()

                # Act
                app._write_batch(["alpha", "beta", "gamma"])
                await pilot.pause()

                # Assert -- one write and one flush, but still one line each
                logged = Path(app._log_path()).read_text(encoding="utf-8")
                rx = [ln for ln in logged.splitlines() if ln.endswith(("alpha", "beta", "gamma"))]
                actual = len(rx)
                assert actual == 3, f"one log line per received line, got {actual}"
                assert all(" < " in ln for ln in rx), "each keeps the RX prefix"

        _run(scenario)

    def test_a_text_capture_receives_the_stripped_lines(self, app_factory):
        """The capture tap now reuses the strip the render already did."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                target = Path(app.config_path).parent / "cap" / "out.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                started = app._capture.start(
                    path=target, file_mode="w", mode="text", duration=30.0
                )
                assert started is True, "precondition: capture running"

                # Act
                app._write_batch(["\x1b[32mplain\x1b[0m", "second"])
                await pilot.pause()
                app._capture.stop()

                # Assert
                actual = target.read_text(encoding="utf-8")
                assert actual == "plain\nsecond\n", (
                    f"captured text is ANSI-stripped and complete, got {actual!r}"
                )

        _run(scenario)


class TestDelayHook:
    """``/delay`` of a second or more animates the bottom-bar progress bar.

    ``_run_progress_bar`` became a free function in ``capture_view`` when
    the capture view was extracted, but ``app_hooks._hook_delay`` kept
    calling it as an app method -- every TUI ``/delay 1s`` crashed with
    ``'SerialTerminal' object has no attribute '_run_progress_bar'`` and
    nothing drove the path, so nothing noticed.  This does.
    """

    @pytest.mark.slow  # real 1 s wait: the progress path is the point
    def test_one_second_delay_runs_the_progress_bar_to_done(self, app_factory):
        async def scenario():
            from termapy.app_hooks import _hook_delay
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                outcome: dict = {}

                def run_hook():
                    # The hook is written for a dispatch worker: it blocks
                    # and marshals label writes via _on_main, so it must
                    # not run on the event loop's thread.
                    try:
                        outcome["result"] = _hook_delay(app, app.repl.ctx, "1s")
                    except Exception as exc:  # noqa: BLE001 - the assertion is the type
                        outcome["error"] = exc

                # Act
                worker = threading.Thread(target=run_hook, daemon=True)
                worker.start()
                deadline = time.monotonic() + 5.0
                while worker.is_alive() and time.monotonic() < deadline:
                    await asyncio.sleep(0.1)  # keep the loop serving _on_main

                # Assert
                assert not worker.is_alive(), "the delay finished within the deadline"
                assert "error" not in outcome, (
                    f"the progress-bar path raised: {outcome.get('error')!r}"
                )
                assert outcome["result"].success, "/delay 1s reports success"
                assert outcome["result"].value == "1.0", "value is the seconds waited"

        _run(scenario)
