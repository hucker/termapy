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
from textual.widgets import Button, Input, RichLog, Static

from termapy.defaults import DEFAULT_CFG
from termapy.dialogs import ProtoPicker
from termapy.proto_debug import OPTIONS, ProtoDebugScreen
from termapy.widgets import SelectableRichLog, StrongSelectionList

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


class TestJsonFieldCheck:
    @pytest.mark.parametrize("bad", ["../up.json", "sub/x.json", "{oops}.json"])
    def test_a_bad_name_shows_as_it_is_typed(self, tmp_path, bad):
        """proto-results-name-only: the field turns red and the status says why, and a fixed name clears both, twice."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test(size=(140, 40)) as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                field = screen.query_one("#input-json-file", Input)
                status = screen.query_one("#proto-debug-status", Static)

                # Act -- bad, fixed, bad, fixed: the warning must come and go each time
                seen = []
                for value in (bad, "ok.json", bad, "ok.json"):
                    field.value = value
                    await pilot.pause()
                    said = "Invalid results file name" in str(status.render())
                    seen.append((field.has_class("-invalid"), said))

                # Assert
                expected = [(True, True), (False, False), (True, True), (False, False)]
                assert seen == expected, "(field red, status says why) after each edit"

        _run(scenario)

    def test_a_script_with_a_bad_name_opens_red(self, tmp_path):
        """A .pro whose json_file holds a folder shows it the moment the screen opens, before any Run."""

        async def scenario():
            app, script = _make_app(tmp_path, 'json_file = "../up.json"\n' + _ONE_AT_TEST)
            async with app.run_test(size=(140, 40)) as pilot:
                # Act
                screen = await _open_debug_screen(pilot, script)
                await pilot.pause()

                # Assert
                field = screen.query_one("#input-json-file", Input)
                assert field.value == "../up.json", "the script's own name is in the field"
                assert field.has_class("-invalid"), "and it shows as refused at once"

        _run(scenario)


class TestResultsCopy:
    def test_the_results_select_and_copy(self, tmp_path):
        """The results pane selects like the main output pane, and Ctrl+C puts that text on the clipboard."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test(size=(140, 40)) as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                stop = screen.query_one("#btn-dbg-stop", Button)
                await pilot.click("#btn-dbg-send")
                await _status_after(pilot, screen, "PASS", 5.0)
                await _disabled_after(pilot, stop, 5.0)
                detail = screen.query_one("#proto-debug-detail", SelectableRichLog)

                # Act
                detail.text_select_all()
                await pilot.pause()
                selected = screen.get_selected_text()
                await pilot.press("ctrl+c")
                await pilot.pause()

                # Assert
                assert selected is not None and "PASS" in selected, f"the run's text: {selected!r}"
                assert app.clipboard == selected, "Ctrl+C copies it inside the dialog too"

        _run(scenario)


class TestOptionTips:
    def test_each_option_shows_its_own_tip(self, tmp_path):
        """Hovering an Options row shows that row's help: the list is bordered, so row 0 starts below the border."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test(size=(140, 40)) as pilot:
                # Arrange -- wide enough that the Options box is on screen, as in a real terminal
                screen = await _open_debug_screen(pilot, script)
                options = screen.query_one("#sel-opts", StrongSelectionList)
                top = options.content_region.y - options.region.y
                left = options.content_region.x - options.region.x

                # Act -- the pointer on each row in turn, as a person moves down the list
                shown = []
                for row in range(len(OPTIONS)):
                    await pilot.hover("#sel-opts", offset=(left + 2, top + row))
                    await pilot.pause()
                    shown.append(options.tooltip)

                # Assert
                expected = [tip for _, _, tip in OPTIONS]
                assert shown == expected, "each row's own tip, none from the row above"

        _run(scenario)


class TestJsonField:
    @pytest.mark.parametrize(
        ("template", "lands", "says"),
        [
            ("out.json", True, "JSON: "),
            ("../up.json", False, "Failed to write JSON results: Invalid results file name"),
            ("{oops}.json", False, "Failed to write JSON results: Invalid results file name"),
        ],
    )
    def test_the_field_takes_a_name_never_a_folder(self, tmp_path, template, lands, says):
        """proto-results-name-only: a name lands in proto/test/, a folder writes nothing, and the screen says which."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                stop = screen.query_one("#btn-dbg-stop", Button)
                screen.query_one("#sel-opts", StrongSelectionList).select("json")
                screen.query_one("#input-json-file", Input).value = template
                await pilot.pause()

                # Act
                await pilot.click("#btn-dbg-send")
                text = await _status_after(pilot, screen, "PASS", 5.0)
                await _disabled_after(pilot, stop, 5.0)

                # Assert
                assert "1/1 PASS" in text, f"the run ran, got {text!r}"
                written = {path.name for path in tmp_path.rglob("*.json")}
                assert "up.json" not in written, "a folder in the field goes nowhere"
                assert ("out.json" in written) is lands, f"wrote {sorted(written)}"
                detail = screen.query_one("#proto-debug-detail", RichLog)
                shown = [strip.text.strip() for strip in detail.lines]
                assert any(says in line for line in shown), f"the pane says what happened: {shown}"

        _run(scenario)


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
                assert isinstance(pilot.app.screen, ProtoPicker), (
                    "bare offers the picker to choose a script"
                )
                assert missing.error.startswith("File not found: nope"), missing.error
                assert not_toml.error.startswith("Script error: TOML parse error"), (
                    "the debug screen is TOML-only"
                )
                assert no_tests.error == "Script has no tests."
                assert not isinstance(pilot.app.screen, ProtoDebugScreen), (
                    "no refusal opened the screen"
                )

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
                stop_dark = await _disabled_after(
                    pilot, screen.query_one("#btn-dbg-stop", Button), 5.0
                )

                # Assert
                assert "Stopping" in stopping, "the press is acknowledged at once"
                assert "0/1 PASS" in text and "stopped by user" in text, (
                    f"one test ran, then the stop landed; got {text!r}"
                )
                assert stop_dark is True, "Stop goes dark once the stopped run has finished"

        _run(scenario)


class TestSelectionGuards:
    """The three lists' rules: what must stay checked, and what Run does with none."""

    def test_the_visualizer_list_refills_itself_when_emptied(self, tmp_path):
        """Unchecking every visualizer re-checks one, by the option's OWN value.

        The guard selected ``self._visualizers[0].sort_order`` -- the
        display order the rows were sorted by (hex is 10) -- while the
        options carry their INDEX into that list (0, 1).  Selecting 10
        put a value in the selection that no option has, so the list
        stayed visibly empty and nothing was decoded.  Found 2026-10-28
        from a screenshot of an empty Visualizations list.
        """

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                viz = screen.query_one("#sel-viz", StrongSelectionList)

                # Act
                viz.deselect_all()
                await pilot.pause()

                # Assert
                values = {viz.get_option_at_index(i).value for i in range(viz.option_count)}
                assert viz.selected, "a visualizer is re-checked rather than left empty"
                assert set(viz.selected) <= values, (
                    f"the re-checked value {viz.selected!r} must be an option the list "
                    f"actually has, one of {sorted(values)!r} -- not a sort_order"
                )
                assert screen._get_active_visualizers(), (
                    "and the screen reads it back, so packets are still decoded"
                )

        _run(scenario)

    def test_run_with_no_tests_checked_asks_for_one(self, tmp_path):
        """Run with an empty Tests list says so instead of quietly running the cursor row.

        It used to fall back to the highlighted test, so unchecking
        everything and pressing Run still ran one test with nothing said
        -- which reads as the screen ignoring the checkboxes.
        """

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                tests = screen.query_one("#proto-debug-select", StrongSelectionList)

                # Act
                tests.deselect_all()
                await pilot.pause()
                await pilot.click("#btn-dbg-send")
                await pilot.pause()

                # Assert
                text = str(screen.query_one("#proto-debug-status", Static).render())
                assert "at least one test" in text, (
                    f"the refusal names what is missing; got {text!r}"
                )
                assert screen._run_active is False, "and nothing was run"

        _run(scenario)

    def test_the_options_are_read_from_the_one_list(self, tmp_path):
        """Each run option reads back off the Options list, and all four may be off."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                options = screen.query_one("#sel-opts", StrongSelectionList)

                # Act / Assert -- the screen opens with every option off
                assert not any(
                    (
                        screen._get_stop_on_error(),
                        screen._get_show_viz(),
                        screen._get_compact(),
                        screen._get_json_enabled(),
                    )
                ), "all four options start off, which is a legitimate state"

                # Act -- checking two reads back as exactly those two
                options.select("json")
                options.select("compact")
                await pilot.pause()

                # Assert
                assert screen._get_json_enabled() is True, "JSON reads back checked"
                assert screen._get_compact() is True, "Compact reads back checked"
                assert screen._get_stop_on_error() is False, "the others stay off"
                assert screen._get_show_viz() is False, "the others stay off"

        _run(scenario)

    def test_every_option_key_has_a_row(self, tmp_path):
        """The Options list is built from the OPTIONS table, so the two cannot drift."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                options = screen.query_one("#sel-opts", StrongSelectionList)

                # Act
                shown = {options.get_option_at_index(i).value for i in range(options.option_count)}

                # Assert
                assert shown == {key for key, _, _tip in OPTIONS}, (
                    "every option in the table is a row, and the list invents none"
                )

        _run(scenario)

    def test_checking_a_test_clears_the_complaint(self, tmp_path):
        """The "select at least one test" message goes once a test is checked.

        A refusal left on screen after it has been answered reads as a
        failure that is still current.  Unchecking again stays quiet:
        the screen asks when you press Run, it does not nag.
        """

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange -- ask for a test by running with none checked
                screen = await _open_debug_screen(pilot, script)
                tests = screen.query_one("#proto-debug-select", StrongSelectionList)
                status = screen.query_one("#proto-debug-status", Static)
                tests.deselect_all()
                await pilot.pause()
                await pilot.click("#btn-dbg-send")
                await pilot.pause()
                asked = str(status.render())

                # Act -- check one
                tests.select(tests.get_option_at_index(0).value)
                await pilot.pause()
                after_check = str(status.render())

                # Act -- and uncheck everything again
                tests.deselect_all()
                await pilot.pause()
                after_uncheck = str(status.render())

                # Assert
                assert "at least one test" in asked, "Run with none checked asks"
                assert after_check.strip() == "", (
                    f"checking a test answers the question, so the message goes; "
                    f"got {after_check!r}"
                )
                assert after_uncheck.strip() == "", (
                    "unchecking does not re-complain; the screen speaks when Run is pressed"
                )

        _run(scenario)

    def test_clearing_the_complaint_leaves_a_run_summary_alone(self, tmp_path):
        """Only the complaint is cleared -- a PASS summary survives a change of selection."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange -- a real run, so the status holds its summary
                screen = await _open_debug_screen(pilot, script)
                await pilot.click("#btn-dbg-send")
                summary = await _status_after(pilot, screen, "PASS", 6.0)
                await _disabled_after(pilot, screen.query_one("#btn-dbg-stop", Button), 5.0)

                # Act -- toggle the selection, which fires the same handler
                tests = screen.query_one("#proto-debug-select", StrongSelectionList)
                tests.deselect_all()
                await pilot.pause()
                tests.select(tests.get_option_at_index(0).value)
                await pilot.pause()

                # Assert
                after = str(screen.query_one("#proto-debug-status", Static).render())
                assert "PASS" in summary, "the run reported a summary"
                assert after == summary, (
                    f"the summary is still there; only the complaint clears. "
                    f"was {summary!r}, now {after!r}"
                )

        _run(scenario)


class TestNumberFields:
    """Repeat and Delay refuse what they cannot read, instead of quietly substituting."""

    @pytest.mark.parametrize(
        ("field", "text", "needle"),
        [
            ("#input-repeat", "abc", "Invalid repeat count: abc"),
            ("#input-repeat", "-5", "Invalid repeat count: -5"),
            ("#input-repeat", "0", "Invalid repeat count: 0"),
            ("#input-delay", "xyz", "Invalid delay: xyz"),
            ("#input-delay", "-1", "Invalid delay: -1"),
        ],
    )
    def test_run_refuses_a_field_it_cannot_read(self, tmp_path, field, text, needle):
        """A bad number stops the run and says which field and what was in it.

        Both fields swallowed every failure: ``abc`` ran once, ``-5``
        ran once, and a deliberate ``0`` repeat was rewritten to 1 by
        ``max(1, val)``.  That is the silent default on garbage the
        project rules forbid.
        """

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                screen.query_one(field, Input).value = text
                await pilot.pause()

                # Act
                await pilot.click("#btn-dbg-send")
                await pilot.pause()

                # Assert
                status = str(screen.query_one("#proto-debug-status", Static).render())
                assert needle in status, f"the refusal names the field and the text; got {status!r}"
                assert screen._run_active is False, "and nothing ran"

        _run(scenario)

    def test_a_blank_field_means_its_default(self, tmp_path):
        """Clearing a field is not an error -- it is the value the screen opened with."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                screen.query_one("#input-repeat", Input).value = ""
                screen.query_one("#input-delay", Input).value = ""
                await pilot.pause()

                # Act / Assert
                assert screen._get_repeat_count() == 1, "a blank repeat runs once"
                assert screen._get_start_delay() == 0.0, "a blank delay waits not at all"

        _run(scenario)

    def test_a_good_value_is_read_as_written(self, tmp_path):
        """The numbers a run uses are the ones in the fields, not clamped versions."""

        async def scenario():
            app, script = _make_app(tmp_path, _ONE_AT_TEST)
            async with app.run_test() as pilot:
                # Arrange
                screen = await _open_debug_screen(pilot, script)
                screen.query_one("#input-repeat", Input).value = "3"
                screen.query_one("#input-delay", Input).value = "250"
                await pilot.pause()

                # Act / Assert
                assert screen._get_repeat_count() == 3, "the repeat count is what was typed"
                assert screen._get_start_delay() == 0.25, "and the delay is ms, read as seconds"

        _run(scenario)
