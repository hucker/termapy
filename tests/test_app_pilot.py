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
import shutil
import threading
import time
from dataclasses import fields
from pathlib import Path

import pytest
import serial

from termapy.defaults import DEFAULT_CFG
from termapy.folders import SYMBOLS_SUFFIX
from termapy.plugins.command import CmdResult, LifecycleHook

DEMO_SYMBOLS = (
    Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo" / f"demo{SYMBOLS_SUFFIX}"
)
DEMO_COUNT = 13


def _age(path, seconds: float) -> None:
    """Push a file's mtime forward, past any filesystem timestamp coarseness.

    The sync check compares mtimes; writing twice within one clock tick can
    leave them equal on a coarse filesystem, which would make a passing
    test prove nothing.
    """
    import os

    when = time.time() + seconds
    os.utime(path, (when, when))


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
        app = SerialTerminal(cfg, path)
        # The checkout has a real termapy_cfg/plugin/; keep it out of the boot.
        app.repl.global_root = tmp_path
        return app, cfg, path
    return _make


class TestAppBoots:
    """Booting the real app is itself coverage: __init__ and compose run."""

    def test_mounts_its_widget_tree(self, app_factory):
        """The full app composes a real widget tree and keeps the config path it was given."""
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
        """With auto_connect off, boot leaves the port closed instead of chasing the device."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.is_connected is False, (
                    "auto_connect=False leaves the port closed"
                )

        _run(scenario)

    def test_repl_engine_is_wired_to_the_config(self, app_factory):
        """After boot the REPL engine points at the same config file as the app."""
        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.repl.config_path == path, (
                    "the REPL engine points at the same config as the app"
                )

        _run(scenario)


class TestCustomButtons:
    """Which configured custom buttons the bottom bar actually mounts."""

    def test_only_enabled_buttons_mount(self, app_factory):
        """Disabled buttons and ones with no enabled key stay off the bar, order kept."""
        async def scenario():
            # Arrange
            buttons = [
                {"enabled": True, "name": "A", "command": "/print a"},
                {"enabled": False, "name": "B", "command": "/print b"},
                {"name": "X", "command": "/print x"},
                {"enabled": True, "name": "C", "command": "/print c"},
            ]
            app, _, _ = app_factory(custom_buttons=buttons)
            async with app.run_test() as pilot:
                await pilot.pause()
                # Act
                actual = [str(button.label) for button in app.query(".custom-btn")]
                # Assert
                assert actual == ["A", "C"], (
                    "only enabled buttons mount; a missing enabled key means hidden"
                )

        _run(scenario)

    def test_a_bracketed_name_and_tooltip_render_as_typed(self, app_factory):
        """widget-text-literal: a button named [Rec] shows [Rec]; as markup it was an empty box (2026-05-22)."""
        async def scenario():
            from textual.widgets import Button

            # Arrange
            buttons = [{"enabled": True, "name": "[Rec]", "tooltip": "[ok] go", "command": "/print a"}]
            app, _, _ = app_factory(custom_buttons=buttons)
            async with app.run_test() as pilot:
                await pilot.pause()
                button = app.query_one(".custom-btn", Button)

                # Act
                label = str(button.label)
                tooltip = str(button.tooltip)

                # Assert
                assert (label, tooltip) == ("[Rec]", "[ok] go"), "cfg text reaches the button as written"

        _run(scenario)


class TestSwitchConfig:
    """``_switch_config`` is pure orchestration -- 36 lines of self.* wiring.

    It cannot be unit-tested because it IS the wiring; driving the app is
    the only way to observe that every collaborator got updated.
    """

    def test_swaps_every_piece_of_config_state(self, app_factory, tmp_path):
        """App, repl, ctx, flags, display and every ctx.fs folder follow the new config."""
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
                    app.repl.ctx.ns("flags")["line_no"],
                )
                expected = (second_path, second_path, second_path, True, True)
                assert actual == expected, (
                    "app, repl, ctx, flags and display state all follow the swap"
                )
                assert app.config_path != first_path, "the old config is gone"

                # Every directory the fs handle declares, read from the handle
                # itself rather than a list repeated here: a path added to
                # FilesystemHandle and forgotten in _switch_config would leave
                # plugins reading the previous config's folder, which is what
                # happened to prof_dir.
                new_dir = Path(second_path).parent
                stale = {
                    spec.name: getattr(app.repl.ctx.fs, spec.name)
                    for spec in fields(app.repl.ctx.fs)
                    if spec.name.endswith("_dir")
                    and Path(getattr(app.repl.ctx.fs, spec.name)).parent != new_dir
                }
                assert stale == {}, f"every ctx.fs path follows the swap; stale: {stale}"

        _run(scenario)

    def test_a_plugin_reads_the_new_configs_settings_not_the_old_ones(
        self, app_factory, tmp_path
    ):
        """A global plugin's ``on_config_load`` sees the config now loaded.

        One context serves every config in a TUI session, so a plugin
        config cached by plugin name alone handed each config the
        previous one's file: a global plugin with a saved path under one
        config went on auto-loading it under every config after that.
        """
        async def scenario():
            app, _, first_path = app_factory(name="first")
            # Arrange -- only the FIRST config saves a setting for this plugin
            plugin_dir = Path(first_path).parent / "plugin"
            plugin_dir.mkdir(parents=True, exist_ok=True)
            (plugin_dir / "mapper.cfg").write_text(
                '{"map_path": "first.map"}', encoding="utf-8",
            )
            seen: list[str] = []
            # Registered BEFORE the boot so the hook reads the setting under
            # the first config, as a global plugin's on_app_start does -- the
            # read that used to poison every config after it.  source="app":
            # the lifecycle pass drops folder-sourced hooks before firing.
            app.repl.register_lifecycle_hook(
                LifecycleHook(
                    name="on_app_start",
                    handler=lambda ctx: seen.append(
                        ctx.plugin_cfg("mapper").get("map_path", "")
                    ),
                    source="app",
                    plugin="mapper",
                ),
            )
            app.repl.register_lifecycle_hook(
                LifecycleHook(
                    name="on_config_load",
                    handler=lambda ctx: seen.append(
                        ctx.plugin_cfg("mapper").get("map_path", "")
                    ),
                    source="app",
                    plugin="mapper",
                ),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                assert seen == ["first.map"], (
                    f"precondition: the first config's setting was read at boot: {seen}"
                )
                second_cfg, second_path = _write_cfg(tmp_path, name="second")

                # Act
                app._switch_config(second_cfg, second_path)
                await pilot.pause()

                # Assert
                assert seen == ["first.map", ""], (
                    f"the second config reads its own (absent) setting: {seen}"
                )

        _run(scenario)

    def test_surfaces_config_warnings(self, app_factory, tmp_path):
        """Config warnings are popped once shown, so they cannot resurface on a later read."""
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
        """Both migration keys are consumed, so a later save cannot write them into the file."""
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
        """Steps with no _migrated_from marker (a hand-edited cfg) are dropped, not reported."""
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
        """Enter on a typed /command takes the submit path; the input clears after dispatch."""
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
        """A line submitted through the real input widget is recorded in app.history."""
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
        """With send_bare_enter off, a bare Enter adds nothing to history."""
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
        """A repeated command keeps one history entry; the earlier copy is removed."""
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

    def test_right_accepts_a_suggested_file_name(self, app_factory):
        """file-arg-declared: a started /run argument ghost-completes to the run/ file; Right takes it."""
        async def scenario():
            from textual.widgets import Input
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- saved after boot, so no startup cache can know it
                app.repl.scripts_dir.mkdir(parents=True, exist_ok=True)
                (app.repl.scripts_dir / "Welcome.run").write_text("/print hi\n")
                cmd_input = app.query_one("#cmd", Input)
                cmd_input.focus()
                cmd_input.value = "/run w"
                cmd_input.action_end()
                await pilot.pause()

                # Act
                await pilot.press("right")
                await pilot.pause()

                # Assert
                assert cmd_input.value == "/run Welcome.run", (
                    "Right accepts the matching file, spelled as on disk"
                )

        _run(scenario)

    def test_tui_hooks_keep_their_file_declarations(self, app_factory):
        """file-arg-declared: the commands the TUI re-registers still declare their folders."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                expected = {
                    "edit.run": "run",
                    "edit.proto": "proto",
                    "edit.plugin": "plugin",
                    "run.profile": "run",
                    "run.profile.dump": "prof",
                }
                for name, folder in expected.items():
                    plugin = app.repl.plugin_for(name)
                    assert plugin is not None, f"/{name} is registered"
                    assert plugin.files == folder, f"/{name} completes from {folder}/"

        _run(scenario)


class TestOutputBatching:
    """``_write_batch`` is the serial reader's path onto the screen."""

    def test_writes_lines_to_the_output_log(self, app_factory):
        """A batched write runs against the real #output widget without raising."""
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
        """An empty batch from the reader is a no-op, not an error."""
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
        """The local _state() snapshot has exactly find.current_state()'s keys, catching drift."""
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
        assert real is not None, "an active search has a state"
        actual, expected = set(self._state()), set(real)
        assert actual == expected, (
            f"fixture drifted from find.current_state(): "
            f"missing {expected - actual}, extra {actual - expected}"
        )

    def test_activating_shows_the_bar_and_freezes_output(self, app_factory):
        """A find state hides the live #output and shows the frozen #output-find copy."""
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
        """Every selector in _FIND_BAR_WIDGETS is displayed once find activates."""
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
        """The Record button hides while the find bar owns the button row."""
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
        """A None state (close find) shows live #output again and hides the frozen copy."""
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
        """A pattern with no matches does not freeze the output; there is nothing to show."""
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
        """The #find-status label shows a 1-based current/total, e.g. 2 of 3 matches."""
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

    def test_a_character_class_pattern_shows_as_typed(self, app_factory):
        """widget-text-literal: the status shows the regex [a-z]+x; as markup the class was a tag and vanished."""
        async def scenario():
            from textual.widgets import Label
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- a zero-match find echoes its pattern
                app._update_find_bar(self._state(pattern="[a-z]+x", total=0, matches=[], index=-1))
                await pilot.pause()

                # Assert
                actual = str(app.query_one("#find-status", Label).render())
                assert "'[a-z]+x'" in actual, f"the pattern is shown as typed, got {actual!r}"

        _run(scenario)

    def test_the_frozen_view_keeps_bracketed_text_on_unmatched_lines(self, app_factory):
        """markup-escape: an unmatched scrollback line reaches the frozen view as Text, so [ok] survives."""
        async def scenario():
            from textual.widgets import RichLog
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- line 1 holds the brackets and is not the match
                app._update_find_bar(self._state(
                    scrollback_text="[ok] boot done\nbeta line", matches=[(2, "beta line")], line_no=2,
                ))
                await pilot.pause()

                # Assert
                shown = [strip.text.rstrip() for strip in app.query_one("#output-find", RichLog).lines]
                assert "[ok] boot done" in shown, f"the unmatched line is shown as written, got {shown!r}"

        _run(scenario)


class TestSerialRxHandoff:
    """The reader hands RX to the main thread WITHOUT blocking on it.

    Blocking was the root of three defects: the reader stalled long enough for
    the driver to silently drop bytes, and ``disconnect()`` waited on the
    reader from the very thread the reader was waiting for, so teardown was
    guaranteed to time out (docs/review/2026-08-19-v0.74.0-opus-5.md, T2/T15).
    """

    def test_enqueue_never_blocks_and_posts_one_wakeup(self, app_factory):
        """Back-to-back RX events share one pending wake-up, then all drain and render."""
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

    def test_capture_echo_rides_the_rx_queue(self, app_factory):
        """A capture echo line is queued like RX, never a blocking call into main from the reader.

        ``on_echo`` was wired to ``_status``, whose off-main path is a
        blocking ``call_from_thread`` -- one round trip per record from
        ``_flush_bin`` on the reader, and a reader that teardown could not
        join (docs/review/2026-10-10-v0.76.0-fable-5.1.md, C2).
        """
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                on_echo = pilot.app._capture._on_echo
                assert on_echo is not None, "the TUI wires a capture echo"

                # Act -- what _flush_bin does on the reader thread, per record
                on_echo("echo line")

                # Assert -- queued, not written: the caller never waits on main
                assert ("echo", "echo line") in pilot.app._rx_events, "echo is an RX event"
                await pilot.pause()
                assert "echo line" in pilot.app._get_screen_text(), "drained and rendered on main"

        _run(scenario)

    def test_clear_does_not_overtake_the_lines_before_it(self, app_factory):
        """A queued clear wipes only the lines before it; all event kinds share one ordered list."""
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

    def test_a_hex_batch_renders_rows_and_feeds_lines(self, app_factory):
        """hex-view-wire-bytes: a toggle mid-drain keeps each batch in its view; hex-view lines are logged and fed, not shown."""
        async def scenario():
            from textual.widgets import RichLog
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- text, then a hex-view chunk, then text again,
                # all queued before main drains any of them
                app._rx_enqueue(("lines", ["TEXT-1"]))
                app._rx_enqueue(("hex", ["48 31"], ["HEX-1"]))
                app._rx_enqueue(("lines", ["TEXT-2"]))

                # Act
                await pilot.pause()

                # Assert
                shown = [strip.text.rstrip() for strip in app.query_one("#output", RichLog).lines]
                actual_tail = shown[-3:]
                expected_tail = ["TEXT-1", "48 31", "TEXT-2"]
                assert actual_tail == expected_tail, "each batch renders in its own view, in order"
                assert "HEX-1" not in shown, "a hex-view line is not also rendered as text"
                assert list(app.repl._recent_lines)[-3:] == ["TEXT-1", "HEX-1", "TEXT-2"], (
                    "the expect watcher gets every decoded line, whichever view showed it"
                )
                assert app.log_fh is not None, "a config on disk opens a log"
                log_text = Path(app.log_fh.name).read_text(encoding="utf-8")
                assert "< HEX-1" in log_text, "the session log keeps the decoded line"

        _run(scenario)

    def test_the_hex_view_shows_a_demo_reply_as_its_bytes(self, app_factory):
        """With /term.hex on, the real reader shows the DEMO reply as its wire bytes and the decoded line still reaches expect."""
        async def scenario():
            from textual.widgets import RichLog
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._connect() is True, "DEMO port should connect"
                log = app.query_one("#output", RichLog)
                app.repl.ctx.ns("flags")["hex"] = True

                # Act -- DEMO answers AT with OK CR LF
                app._serial_send("AT")
                for _ in range(250):
                    if "4F 4B 0D 0A" in app._get_screen_text():
                        break
                    await pilot.pause(0.02)

                # Assert
                shown = [strip.text.rstrip() for strip in log.lines]
                assert "4F 4B 0D 0A" in shown, f"the reply renders as its bytes, got {shown[-5:]!r}"
                assert "OK" not in shown, "the decoded line is not rendered as well"
                assert "OK" in list(app.repl._recent_lines), "the expect watcher still gets the line"

        _run(scenario)

    def test_a_flood_reaches_every_consumer_and_renders_only_max_lines(
        self, app_factory, tmp_path
    ):
        """rx-every-line-delivered: a flood is logged and captured whole; the pane renders only what it keeps.

        2100 events is past the 2000 the old cap held: it dropped the oldest
        before ``_write_batch``, so they missed the session log and the
        capture too, while the message said only the display fell behind.
        """
        async def scenario():
            from textual.widgets import RichLog
            app, _, _ = app_factory(max_lines=50)
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a text capture running, and the whole flood
                # queued before main drains any of it
                cap_path = tmp_path / "flood.txt"
                started = app._capture.start(
                    path=cap_path, file_mode="w", mode="text", duration=60.0
                )
                assert started is True, "the text capture starts"
                total = 2100
                for i in range(total):
                    app._rx_enqueue(("lines", [f"flood {i}"]))

                # Act -- read the pane before stopping: the capture's
                # completion status is itself a line, and would evict one
                await pilot.pause()
                shown = [
                    strip.text.rstrip()
                    for strip in app.query_one("#output", RichLog).lines
                    if strip.text.startswith("flood")
                ]
                app._capture.stop()

                # Assert
                expected = [f"flood {i}" for i in range(total)]
                actual_captured = cap_path.read_text(encoding="utf-8").splitlines()
                assert actual_captured == expected, "text capture gets every line, in order"
                assert app.log_fh is not None, "a config on disk opens a log"
                log_lines = Path(app.log_fh.name).read_text(encoding="utf-8").splitlines()
                actual_logged = [line.split("] < ", 1)[1] for line in log_lines if "] < " in line]
                assert actual_logged == expected, "the session log gets every line, in order"
                assert app.repl._recent_lines[-1] == "flood 2099", "the expect watcher was fed"
                assert app._line_counter == total, "line numbers count the lines not rendered"
                assert shown == expected[-50:], "the pane holds the newest max_lines"

        _run(scenario)

    def test_a_lost_port_stops_the_capture_and_resets_the_ui(self, app_factory, tmp_path):
        """port-lost-tears-down: a disconnect from the reader stops a running capture, frees its claim and resets DTR/RTS."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a binary capture holding the claim, DTR/RTS asserted
                started = app._cap_start(
                    path=tmp_path / "lost.bin", file_mode="wb", mode="bin", target_bytes=1000
                )
                assert started is True, "the binary capture starts"
                assert app._engine.serial_claimed is True, "a binary capture claims the port"
                app._btn_dtr.label = "DTR:1"
                app._btn_rts.label = "RTS:1"

                # Act -- what the reader enqueues when port.read raises
                app._rx_enqueue(("disconnect",))
                await pilot.pause()

                # Assert
                assert app._capture.active is False, "the capture stops with the port"
                assert app._engine.serial_claimed is False, (
                    "a claim left behind keeps the display suppressed after an "
                    "auto-reconnect"
                )
                actual_labels = (str(app._btn_dtr.label), str(app._btn_rts.label))
                expected_labels = ("DTR:0", "RTS:0")
                assert actual_labels == expected_labels, "the DTR/RTS labels reset"

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

    Shaped like ``SerialPort`` for the one member ``_serial_write`` touches.
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
        """tx-only-what-went-out: a send the port refuses is a failed command on screen, not an exception in the worker."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- connected, then the adapter fails the way an
                # unplugged one does
                assert app._connect() is True, "DEMO port should connect"
                port = _RaisingPort()
                app._engine._serial_port = port

                # Act -- the command every typed line is rewritten to
                result = app._dispatch_single("/term.send AT")
                await pilot.pause()

                # Assert
                assert port.writes == 1, "the write was attempted on the bound port"
                assert result.success is False, "a failed write is a failed command"
                text = app._get_screen_text()
                assert "Send error: simulated unplug" in text, (
                    f"a failed send must be reported. Screen was: {text!r}"
                )
                assert app.is_running, "a failed write must not kill the app"
                app._disconnect()

        _run(scenario)

    def test_missing_port_fails_the_send_and_logs_no_tx(self, app_factory):
        """tx-only-what-went-out: with the port nulled by teardown a send fails visibly and logs no TX line."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- the port object still open, the write wrapper
                # gone: exactly what the reader's finally leaves behind
                assert app._connect() is True, "DEMO port should connect"
                app._engine._serial_port = None
                app._open_log()

                # Act
                result = app._dispatch_single("/term.send AT")
                await pilot.pause()

                # Assert
                assert result.error == "Send error: Not connected.", (
                    "a send with no port is a failed command, not a silent drop"
                )
                logged = Path(app.session_log_path()).read_text(encoding="utf-8")
                tx_lines = [line for line in logged.splitlines() if line.endswith("> AT")]
                assert tx_lines == [], (
                    f"no TX line may claim bytes went out. Log was: {logged!r}"
                )
                app._disconnect()

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
        """An off-main dispatch blocks while the guard is held, then runs once it frees."""
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

    def test_connection_hooks_from_a_worker_run_on_main(self, tmp_path):
        """output-helpers-marshal: /port.connect and /port.disconnect run on a worker; their UI hooks run on main."""
        async def scenario():
            from termapy.app import SerialTerminal

            ran_on: dict[str, int] = {}

            class _Recording(SerialTerminal):
                """Records the thread each connection-hook body runs on, then runs it."""

                def _show_connected(self, message: str) -> None:
                    ran_on["connected"] = threading.get_ident()
                    super()._show_connected(message)

                def _show_disconnected(self) -> None:
                    ran_on["disconnected"] = threading.get_ident()
                    super()._show_disconnected()

            cfg, path = _write_cfg(tmp_path)
            app = _Recording(cfg, path)
            app.repl.global_root = tmp_path
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- connect then disconnect from a worker, as the commands do
                worker = threading.Thread(
                    target=lambda: (app._connect(), app._disconnect()), daemon=True
                )
                worker.start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while worker.is_alive() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                await pilot.pause()

                # Assert
                assert worker.is_alive() is False, "the worker's hooks must not park"
                expected = {
                    "connected": app._main_thread_id,
                    "disconnected": app._main_thread_id,
                }
                assert ran_on == expected, (
                    "the hooks touched widgets from the worker thread instead "
                    "of forwarding to main"
                )

        _run(scenario)


class TestAutoReconnect:
    """reconnect-survives-connect-attempts: each reconnect run owns its cancel token."""

    def test_a_failed_connect_while_retrying_leaves_it_retrying(self, app_factory):
        """A connect attempt that fails during auto-reconnect neither ends nor replaces the run."""
        async def scenario():
            app, _, _ = app_factory(auto_reconnect=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- auto-reconnect retrying a port that never opens
                app.repl._cfg_data["serial"]["port"] = "DEMO_FAIL"
                app._auto_reconnect()
                run = app._reconnect_stop
                assert app._reconnecting is True, "auto-reconnect is retrying"

                # Act -- a connect attempt on a worker, as /port.connect makes
                worker = threading.Thread(target=app._connect, daemon=True)
                worker.start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while worker.is_alive() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.5)  # longer than the loop's 0.25 s tick
                await pilot.pause()

                # Assert
                try:
                    assert app._reconnecting is True, (
                        "the failed connect ended the retrying: the reader's stop "
                        "event was the loop's too, and the restart was swallowed"
                    )
                    assert app._reconnect_stop is run, "the same run is still the live one"
                finally:
                    app._reconnect_stop.set()

        _run(scenario)

    def test_a_successful_connect_ends_the_retrying(self, app_factory):
        """A connect that succeeds while retrying ends the run, and no spinner frame paints over Connected."""
        async def scenario():
            from textual.widgets import Button
            app, _, _ = app_factory(auto_reconnect=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- retrying long enough for spinner frames to be in flight
                app.repl._cfg_data["serial"]["port"] = "DEMO_FAIL"
                app._auto_reconnect()
                await asyncio.sleep(0.3)

                # Act
                connected = app._connect("DEMO")
                await asyncio.sleep(0.6)
                await pilot.pause()

                # Assert
                assert connected is True, "the DEMO port opens"
                assert app._reconnecting is False, "a connect that succeeded ends the retrying"
                label = str(app.query_one("#title-right", Button).label).strip()
                assert label == "Connected", "no stale spinner frame paints over Connected"
                app._disconnect()

        _run(scenario)

    def test_a_live_run_finishes_the_connection_it_opened(self, app_factory):
        """reconnect-opens-once: the TUI finishes the port a run opened -- reader started, Connected shown -- without reopening it."""
        async def scenario():
            from textual.widgets import Button
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- what reconnect_loop leaves: the port open, no reader
                assert app._engine.connect() is True, "the DEMO port opens"
                port = app._engine.port_obj

                # Act
                app._finish_reconnect(threading.Event(), port)
                await pilot.pause()

                # Assert
                try:
                    assert app._engine.port_obj is port, "the run's port is kept, not reopened"
                    assert app._engine._reader_thread is not None, "the reader was started"
                    label = str(app.query_one("#title-right", Button).label).strip()
                    assert label == "Connected", "the TUI shows the reconnect"
                finally:
                    app._disconnect()

        _run(scenario)

    def test_a_cancelled_run_closes_only_the_port_it_opened(self, app_factory):
        """reconnect-opens-once: a run cancelled while its open was in flight closes its own port and never another."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a cancelled run (the title-bar button, a config switch)
                cancelled = threading.Event()
                cancelled.set()
                assert app._engine.connect() is True, "the DEMO port opens"

                # Act -- first a port some other connect opened since, then the run's own
                app._finish_reconnect(cancelled, object())
                kept = app.is_connected
                app._finish_reconnect(cancelled, app._engine.port_obj)

                # Assert
                assert kept is True, "a cancelled run closed a port it did not open"
                assert app.is_connected is False, (
                    "a cancelled run left its port open with no reader behind a Disconnected UI"
                )

        _run(scenario)


class TestSessionLog:
    """The session log is the transcript: every channel a command writes lands in it once."""

    def test_markup_output_is_logged_as_text_and_its_echo_is_not(self, app_factory):
        """A command's markup output reaches the log as rendered text under #; its echo stays out (the > line has it)."""
        from termapy.variables import unset_user_var

        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.repl.ctx.ns("flags")["echo_repl"] = True
                command = "/var.set MARKUP_TAG [ok] boot"

                # Act -- off the main thread, as every typed command runs
                done = threading.Event()
                threading.Thread(
                    target=lambda: (app._dispatch_single(command), done.set()), daemon=True,
                ).start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while not done.is_set() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                await pilot.pause()

                # Assert
                log = Path(app.session_log_path()).read_text(encoding="utf-8")
                assert "#   $(MARKUP_TAG) = [ok] boot" in log, f"markup output logged as its text:\n{log}"
                assert log.count(command) == 1, f"the command is logged once, as its > line:\n{log}"

        try:
            _run(scenario)
        finally:
            unset_user_var("MARKUP_TAG")  # user variables are process-global

    def test_log_commands_work_on_the_tuis_own_log(self, app_factory):
        """/log.dump reads the TUI's own log, and /log.delete closes the open file before removing it."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app._open_log()
                app._status("tui-was-here")
                log_path = app.session_log_path()
                assert log_path is not None and log_path.exists(), "the TUI keeps a log"

                # Act -- off the main thread, as every typed command runs
                results: dict = {}

                def run() -> None:
                    results["dump"] = app._dispatch_single("/log.dump")
                    results["delete"] = app._dispatch_single("/log.delete")

                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while worker.is_alive() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                await pilot.pause()

                # Assert
                assert results["dump"].success, results["dump"].error
                assert results["delete"].value == str(log_path), "the TUI's own log was deleted"
                assert not log_path.exists(), "the file is gone"
                assert app.log_fh is None, "the TUI let go of the file before the delete"

        _run(scenario)

    def test_multi_line_output_logs_every_line_with_its_prefix(self, app_factory):
        """A multi-line message, plain or markup, is one # entry per line; a blank line stays one empty entry."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- on the main thread, where both helpers log
                app._status("plain one\nplain two")
                app._write_output_markup("[cyan]tree[/]\n├── leaf", log=True)
                app._write_output_markup("", log=True)
                await pilot.pause()

                # Assert
                lines = Path(app.session_log_path()).read_text(encoding="utf-8").splitlines()
                logged = [line.split("] ", 1)[1] for line in lines[-5:]]
                assert logged == ["# plain one", "# plain two", "# tree", "# ├── leaf", "# "], (
                    f"every line carries the prefix, markup flattened:\n{lines[-5:]}"
                )

        _run(scenario)


class TestRawEcho:
    """/raw echoes through the engine's one echo builder."""

    def test_raw_echo_expands_the_echo_format(self, app_factory):
        """The /raw echo reads echo_fmt like every other echo: $(CFG) expands, [ok] stays text."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                app.repl.ctx.ns("flags")["echo_repl"] = True

                # Act -- not connected, so the line is echoed and refused, never sent
                app._dispatch_single("/raw AT[ok]")
                await pilot.pause()

                # Assert
                text = app._get_screen_text()
                assert "proj> /raw AT[ok]" in text, f"the echo expands $(CFG) and keeps the brackets:\n{text}"
                assert "$(CFG)" not in text, "the variable never shows unexpanded"

        _run(scenario)


class TestConfirmIsStopAware:
    """T14: ``_confirm`` parked on an unbounded ``event.wait()``.

    The dialog's own bindings cover the normal path, but a stop or a
    shutdown with the modal up left the worker waiting forever -- and
    teardown joins the threads workers run on.
    """

    def test_a_script_stop_releases_a_waiting_confirm(self, app_factory):
        """A script stop with the modal up releases the waiting worker, failing closed (False)."""
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
        """All 50 lines of one combined write render, with the first ahead of the last."""
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
        """With color off, device SGR escapes in a batch are stripped, not shown as literals."""
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
        """One batched log write still yields one RX-prefixed log line per received line."""
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
                logged = Path(app.session_log_path()).read_text(encoding="utf-8")
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


class TestPickersNeedAConfig:
    """With no config loaded, run/ and proto/ resolve to the current
    directory, so the Run and Proto buttons (and bare ``/run`` / ``/proto``,
    which route through the same methods) must refuse rather than list
    whatever folder the shell is in."""

    def test_run_and_proto_buttons_refuse_without_a_config(self, app_factory):
        """With config_path empty, the Run and Proto buttons open no picker on the cwd."""
        async def scenario():
            from textual.screen import ModalScreen
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- the state after deleting the active config
                app.config_path = ""

                # Act
                app._btn_scripts()
                await pilot.pause()
                app._btn_proto()
                await pilot.pause()

                # Assert
                assert not isinstance(app.screen, ModalScreen), (
                    "no picker opens when there is no config to list for"
                )

        _run(scenario)


class TestFailureReportedOnce:
    """failure-reported-once in the TUI, whose ``ctx.dispatch`` is ``_dispatch_single``.

    The TUI replaces the host's ``ctx.dispatch`` with its guarded
    ``_dispatch_single``.  When the capture began passing ``report=False``
    that override took no such argument, so every ``$(X) <- /cmd`` in the
    TUI failed with a TypeError (found by the stage A manual test,
    2026-10-16); the CLI-rig tests could not see it.
    """

    def test_a_failed_capture_answers_with_the_inner_error_once(self, app_factory):
        """$(B) <- a refused command fails with the inner error, shown once on screen."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._connect() is True, "DEMO port should connect"

                # Act
                result = app._dispatch_single("$(B) <- /port.baud_rate fast")
                await pilot.pause()

                # Assert
                assert (result.success, result.error) == (False, "Invalid baud rate: fast"), (
                    "the inner failure is the capture's answer"
                )
                text = app._get_screen_text()
                assert text.count("Invalid baud rate: fast") == 1, f"shown once. Screen was: {text!r}"
                app._disconnect()

        _run(scenario)


def _start_on_worker(app, line: str) -> tuple[threading.Thread, dict]:
    """Start dispatching ``line`` on a worker, as typed input runs; ``outcome`` fills when it ends."""
    outcome: dict = {}

    def run() -> None:
        t0 = time.monotonic()
        outcome["result"] = app._dispatch_single(line)
        outcome["seconds"] = time.monotonic() - t0

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, outcome


async def _await_until(condition, what: str) -> None:
    """Keep the event loop serving ``_on_main`` until ``condition()`` holds; fail after 5 s."""
    deadline = time.monotonic() + 5.0
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert condition(), f"{what} within the deadline"


async def _dispatch_on_worker(pilot, app, line: str) -> tuple[CmdResult, float]:
    """Dispatch ``line`` on a worker, as typed input runs; return its result and seconds taken."""
    worker, outcome = _start_on_worker(app, line)
    await _await_until(lambda: not worker.is_alive(), f"{line} finished")
    return outcome["result"], outcome["seconds"]


class TestLineNumbers:
    """``/term.line_no`` sets the one flag the pane renders from and ``/term.info`` reports.

    Until 2026-10-16 the setting lived in three places: the cfg key, an app
    attribute the TUI's own hook changed, and ``flags["line_no"]``, which
    ``/term.info`` read and nothing wrote, so it always said off (review
    2026-10-15, D4).
    """

    def test_on_numbers_the_pane_and_term_info_reports_it(self, app_factory):
        """/term.line_no on numbers the next received line, and /term.info's data says on."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                result, _ = await _dispatch_on_worker(pilot, app, "/term.line_no on")
                info, _ = await _dispatch_on_worker(pilot, app, "/term.info")
                app._write_batch(["numbered line"])
                await pilot.pause()

                # Assert
                assert result.value == "on", result.error
                assert info.data["line_no"] is True, "/term.info reads the flag the setter wrote"
                assert "| numbered line" in app._get_screen_text(), "the pane numbers the line"

        _run(scenario)


class TestDemoCommand:
    """``/demo`` is one builtin: it returns after the TUI has switched, not before.

    Until 2026-10-16 the TUI's own ``/demo`` started a worker and answered
    at once, so a script line after it ran against the old config (review
    2026-10-15, D6).
    """

    def test_demo_returns_after_the_switch(self, app_factory, tmp_path, monkeypatch):
        """The command's value is the demo config, and the app is already on it when the command returns."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- the demo is written under a config root of our own
                cfg_root = tmp_path / "cfg_root"
                cfg_root.mkdir()
                monkeypatch.setenv("TERMAPY_CFG_DIR", str(cfg_root))

                # Act
                result, _ = await _dispatch_on_worker(pilot, app, "/demo")
                switched_to = app.config_path
                app._disconnect()

                # Assert
                assert result.success, result.error
                assert Path(switched_to).name == "demo.cfg", "the app is on the demo when /demo returns"
                assert result.value == switched_to, "the value is the config switched to"

        _run(scenario)

    def test_an_argument_is_refused_not_ignored(self, app_factory):
        """/demo --force fails with usage instead of switching without the reset it used to mean."""
        async def scenario():
            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                result, _ = await _dispatch_on_worker(pilot, app, "/demo --force")

                # Assert
                assert result.error.startswith("Usage:"), result.error
                assert app.config_path == path, "no switch happened"

        _run(scenario)


class TestCfgWaitsForConfirm:
    """``/cfg <key> <value>`` in the TUI answers after its confirm dialog does.

    Until 2026-10-16 it returned the new value with the dialog still open,
    so a script holding that value went on although Cancel never applied it
    (review 2026-10-15, D7; decision 7: wait, Yes returns the value, Cancel
    fails).
    """

    @staticmethod
    async def _answer(pilot, app, line: str, key: str) -> CmdResult:
        """Run ``line`` on a worker, answer its dialog with ``key``, and return the result."""
        from termapy.dialogs import CfgConfirm

        worker, outcome = _start_on_worker(app, line)
        await _await_until(lambda: isinstance(app.screen, CfgConfirm), "the dialog opened")
        assert worker.is_alive(), "the command waits while the dialog is up"
        await pilot.press(key)
        await _await_until(lambda: not worker.is_alive(), f"{line} finished")
        return outcome["result"]

    def test_yes_applies_before_the_command_returns(self, app_factory):
        """Yes applies the change, and the command's value is the new setting."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                result = await self._answer(pilot, app, "/cfg eol_rx lf", "enter")

                # Assert
                assert (result.success, result.value) == (True, "lf"), result.error
                assert app.cfg["eol_rx"] == "lf", "applied when the command returned"

        _run(scenario)

    def test_an_escape_pressed_at_idle_does_not_cancel_a_later_dialog(self, app_factory):
        """A stop set outside a script stays set until a script starts; /cfg still waits for its own answer."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- Escape at idle sets the script stop event
                app.action_stop_script()

                # Act
                result = await self._answer(pilot, app, "/cfg eol_rx lf", "enter")

                # Assert
                assert (result.success, result.value) == (True, "lf"), result.error

        _run(scenario)

    def test_a_second_change_canceled_fails_and_keeps_the_first(self, app_factory):
        """Twice in a row: the second /cfg waits for its own dialog, and Escape fails it as Canceled."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                first = await self._answer(pilot, app, "/cfg eol_rx lf", "enter")
                second = await self._answer(pilot, app, "/cfg eol_rx cr", "escape")

                # Assert
                assert first.success, first.error
                assert (second.success, second.error) == (False, "Canceled."), "Cancel fails the command"
                assert app.cfg["eol_rx"] == "lf", "the canceled change was not applied"

        _run(scenario)


class TestDelayHook:
    """``/delay`` in the TUI is the builtin, drawn on the bottom bar.

    Until 2026-10-16 the TUI registered its own ``/delay`` and
    ``/delay.silent`` hooks.  The progress path once crashed on every
    ``/delay 1s`` with nothing driving it, and ``/delay.silent`` scheduled a
    no-op timer and returned at once, so a script line after it did not
    wait (review 2026-10-15, D2).  Both now run the one builtin.
    """

    @pytest.mark.slow  # real 1 s wait: the progress path is the point
    def test_one_second_delay_runs_the_progress_bar_to_done(self, app_factory):
        """A typed /delay 1s draws its bar, finishes, says so, and reports 1.0 as its value."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                result, _ = await _dispatch_on_worker(pilot, app, "/delay 1s")
                await pilot.pause()

                # Assert
                assert result.success, result.error
                assert result.value == "1.0", "value is the seconds waited"
                assert "Delay 1s done." in app._get_screen_text()

        _run(scenario)

    def test_silent_delay_waits(self, app_factory):
        """/delay.silent waits its full duration in the TUI and prints nothing."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                result, seconds = await _dispatch_on_worker(pilot, app, "/delay.silent 300ms")
                await pilot.pause()

                # Assert
                assert result.success, result.error
                assert seconds >= 0.3, f"the silent delay must wait; it took {seconds:.3f}s"
                assert "Delay" not in app._get_screen_text(), "silent prints nothing"

        _run(scenario)


class TestEditHooks:
    """The TUI's ``/edit`` family opens Textual editors through ``app_hooks``.

    ``18fac03`` moved ``_hook_edit_folder`` into ``app_hooks`` but left the
    registration calling it as an app method, so every TUI ``/edit.run
    <name>`` raised AttributeError -- the ``TestDelayHook`` defect again.
    """

    def test_edit_run_opens_the_script_editor_from_a_stem(self, app_factory):
        """file-name-default-ext: /edit.run foo opens run/foo.run in the script editor."""
        async def scenario():
            from termapy.dialogs import ScriptEditor
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                app.repl.scripts_dir.mkdir(parents=True, exist_ok=True)
                (app.repl.scripts_dir / "foo.run").write_text("/print hi\n")

                # Act
                result = app.repl.dispatch("edit.run foo")
                await pilot.pause()

                # Assert
                assert result.success, result.error
                assert Path(str(result.value)).name == "foo.run", "the stem named run/foo.run"
                assert isinstance(app.screen, ScriptEditor), "the script editor is open"

        _run(scenario)

    def test_the_textual_editors_need_no_desktop(self, app_factory):
        """With no desktop, as over SSH, /edit.run still opens termapy's own editor: it needs the TUI, not GUI apps."""
        async def scenario():
            from dataclasses import replace

            from termapy.dialogs import ScriptEditor
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a session with no desktop to hand a file to
                app.repl.ctx.capabilities = replace(app.repl.ctx.capabilities, gui_apps=False)
                app.repl.scripts_dir.mkdir(parents=True, exist_ok=True)
                (app.repl.scripts_dir / "foo.run").write_text("/print hi\n")

                # Act
                result = app.repl.dispatch("edit.run foo")
                await pilot.pause()

                # Assert
                assert result.success, result.error
                assert isinstance(app.screen, ScriptEditor), "termapy's editor opened without a desktop"

        _run(scenario)

    def test_bare_picker_commands_open_their_picker(self, app_factory):
        """bare-declared: every picker= command opens its button's picker when bare, and the TUI opens every PICKERS name."""
        async def scenario():
            from termapy.dialogs import ConfigPicker, ProtoPicker, ScriptPicker
            from termapy.plugins.command import PICKERS
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- /proto.run and /proto.debug are gated on a port.
                # The port picker skips its dialog when exactly one port
                # exists, so /port is checked by the picker it asked for.
                assert app._connect() is True, "DEMO port should connect"
                expected = {
                    "run": ScriptPicker,
                    "proto": ProtoPicker,
                    "cfg": ConfigPicker,
                    "port": None,
                    "run.edit": ScriptPicker,
                    "edit.run": ScriptPicker,
                    "proto.edit": ProtoPicker,
                    "edit.proto": ProtoPicker,
                    "proto.run": ProtoPicker,
                    "proto.debug": ProtoPicker,
                }
                asked: set[str] = set()
                for command, picker in expected.items():
                    # Act
                    result = app.repl.dispatch(command)
                    await pilot.pause()

                    # Assert
                    assert result.success, f"/{command}: {result.error}"
                    asked.add(str(result.value))
                    if picker is not None:
                        assert isinstance(app.screen, picker), f"bare /{command} opens {picker.__name__}"
                    while len(app.screen_stack) > 1:
                        app.pop_screen()
                    await pilot.pause()
                assert asked == set(PICKERS), "the TUI opens every picker a command may declare"

        _run(scenario)

    def test_noun_first_twins_open_the_textual_editors(self, app_factory):
        """/proto.edit <name> and /cfg.edit reach the same Textual editors as /edit.proto and /edit.cfg."""
        async def scenario():
            from termapy.dialogs import ConfigEditor, ProtoEditor
            app, _, _ = app_factory()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # Arrange
                app.repl.proto_dir.mkdir(parents=True, exist_ok=True)
                (app.repl.proto_dir / "probe.pro").write_text("send: 01\n")

                # Act / Assert
                result = app.repl.dispatch("proto.edit probe")
                await pilot.pause()
                assert result.success, result.error
                assert isinstance(app.screen, ProtoEditor), "/proto.edit opens the proto editor"
                app.pop_screen()
                await pilot.pause()

                for command in ("cfg.edit", "edit.cfg"):
                    result = app.repl.dispatch(command)
                    await pilot.pause()
                    assert result.success, result.error
                    assert isinstance(app.screen, ConfigEditor), f"/{command} opens the config editor"
                    app.pop_screen()
                    await pilot.pause()

        _run(scenario)

    def test_edit_info_without_a_report_fails_once(self, app_factory):
        """/edit.info reaches its hook (it raised AttributeError) and reports a missing report once."""
        async def scenario():
            from dataclasses import replace
            app, _, _ = app_factory()
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                # Arrange -- /edit.info hands the report to the system viewer,
                # so it needs a desktop; declare one rather than depend on the
                # host (a headless CI runner has none).
                app.repl.ctx.capabilities = replace(app.repl.ctx.capabilities, gui_apps=True)

                # Act
                result = app.repl.dispatch("edit.info")
                await pilot.pause()

                # Assert
                expected = "No info report yet. Run /cfg.info first."
                assert result.error == expected, "the hook ran and found no report"
                actual = app._get_screen_text().count("No info report yet")
                assert actual == 1, "the failure is reported once, by the dispatcher"

        _run(scenario)

    def test_edit_without_the_extension_bails_once(self, app_factory):
        """/edit welcome fails with one "File not found" line; the hook and the dispatcher both printed it."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                # Arrange -- the file exists, but /edit cannot know its folder
                app.repl.scripts_dir.mkdir(parents=True, exist_ok=True)
                (app.repl.scripts_dir / "welcome.run").write_text("/print hi\n")

                # Act
                result = app.repl.dispatch("edit welcome")
                await pilot.pause()

                # Assert
                assert result.error == "File not found: welcome"
                actual = app._get_screen_text().count("File not found: welcome")
                assert actual == 1, "the failure is reported once, by the dispatcher"

        _run(scenario)

    def test_cfg_load_resolves_a_config_folder(self, app_factory, tmp_path):
        """The TUI /cfg.load uses resolve_config: a config's folder loads <dir>/<dir>.cfg, as in the CLI and MCP."""
        async def scenario():
            app, _, _ = app_factory()
            _, second_path = _write_cfg(tmp_path, name="second")
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act -- the folder, not the .cfg inside it
                result = app.repl.dispatch(f"cfg.load {Path(second_path).parent}")
                await pilot.pause()

                # Assert
                assert result.success, result.error
                assert app.config_path == second_path, (
                    "the TUI's own chain read the folder as the cfg file and failed"
                )

        _run(scenario)

    @pytest.mark.parametrize(
        ("command", "screen_name"),
        [("/edit.run probe", "ScriptEditor"), ("/cfg.edit", "ConfigEditor")],
    )
    def test_a_typed_edit_opens_its_editor(self, app_factory, command, screen_name):
        """A typed /edit command, run on its dispatch worker, opens termapy's editor.

        Every typed /edit failed with "call_from_thread() got multiple
        values for argument 'callback'": push_screen's callback= was passed
        through _on_main.  The tests dispatched on main, where _on_main calls
        straight through, so none of them saw it (found by the B3 manual
        test, 2026-10-16).
        """
        async def scenario():
            from termapy import dialogs

            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.repl.scripts_dir.mkdir(parents=True, exist_ok=True)
                (app.repl.scripts_dir / "probe.run").write_text("/print hi\n")
                results: list = []

                # Act -- on a worker, as typed input runs
                worker = threading.Thread(
                    target=lambda: results.append(app._dispatch_single(command)), daemon=True,
                )
                worker.start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while worker.is_alive() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)
                await pilot.pause()

                # Assert
                assert results and results[0].success, results[0].error if results else "no result"
                assert isinstance(app.screen, getattr(dialogs, screen_name)), f"{screen_name} opened"

        _run(scenario)

    def test_cfg_load_switches_before_it_returns(self, app_factory, tmp_path):
        """one-handler-per-command: /cfg.load from a worker returns after the TUI's switch, not before it.

        The TUI's own /cfg.load scheduled the switch on main and returned at
        once, so a script line after it ran against the old config.
        """
        async def scenario():
            app, _, _ = app_factory()
            _, second_path = _write_cfg(tmp_path, name="second")
            async with app.run_test() as pilot:
                await pilot.pause()
                seen: dict = {}

                def run() -> None:
                    seen["result"] = app._dispatch_single(f"/cfg.load {second_path}")
                    seen["config_path"] = app.config_path  # read the moment it returns

                # Act -- on a worker, as a typed command or a script line runs
                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                deadline = asyncio.get_running_loop().time() + 5.0
                while worker.is_alive() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.02)

                # Assert
                assert seen["result"].success, seen["result"].error
                assert seen["config_path"] == second_path, "the switch was done when the command returned"

        _run(scenario)

    def test_cfg_load_of_an_unknown_name_answers_like_every_frontend(self, app_factory):
        """An unknown name gets the shared message (TerminalHost._load_config), the CLI's and MCP's too."""
        async def scenario():
            app, _, _ = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()

                # Act
                result = app.repl.dispatch("cfg.load no_such_config")

                # Assert
                assert result.success is False
                assert result.error.startswith("No config matching 'no_such_config'."), result.error

        _run(scenario)

    def test_edit_opens_a_plugin_file_in_the_system_editor(self, app_factory):
        """/edit foo.py finds plugin/foo.py in the TUI and hands it to the system editor, as in the CLI.

        The TUI's own /edit resolver knew only .run and .pro, so this
        failed in the TUI while it worked in the CLI (review 2026-10-15, D5).
        """
        async def scenario():
            from dataclasses import replace

            app, _, path = app_factory()
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange -- a desktop whose opener is a list, and a plugin file
                caps = replace(app.repl.ctx.capabilities, gui_apps=True)
                app.repl.ctx.capabilities = caps
                app.repl.ctx.fs.capabilities = caps
                opened: list[str] = []
                app.repl.ctx.fs._open_file_impl = lambda target: opened.append(str(target))
                plugin = Path(path).parent / "plugin" / "probe.py"
                plugin.parent.mkdir(exist_ok=True)
                plugin.write_text("# probe\n", encoding="utf-8")

                # Act
                result = app.repl.dispatch("edit probe.py")
                await pilot.pause()

                # Assert
                assert result.success, result.error
                assert opened == [str(plugin)], "the system editor got the plugin file"

        _run(scenario)


class TestSymbolsAutoload:
    """The symbol sidecar loads on mount, before the startup auto-connect.

    test_sym_commands.py proves the engine's auto-load; the CLI gold proves
    the CLI reaches it.  This is the TUI's wiring: ``on_mount`` fires
    ``on_app_start`` BEFORE ``_run_startup``, so an ``auto_connect`` config's
    ``on_connect`` hooks already see ``ctx.ns("symbols")``.
    """

    def test_sidecar_loads_on_mount_and_sym_resolves(self, app_factory):
        """A sym/ sidecar is fully installed by mount, and a typed /sym main renders its address."""
        async def scenario():
            from textual.widgets import Input, RichLog

            from termapy.symbols import get_table

            # Arrange -- the demo table in sym/, as /sym.import writes it
            app, _, path = app_factory()
            (Path(path).parent / "sym").mkdir(exist_ok=True)
            shutil.copyfile(DEMO_SYMBOLS, Path(path).parent / "sym" / f"proj{SYMBOLS_SUFFIX}")
            async with app.run_test() as pilot:
                await pilot.pause()

                # Assert -- mounted == loaded
                table = get_table(app.repl.ctx)
                assert table is not None, "the sidecar auto-loads on mount"
                assert len(table) == DEMO_COUNT, "the whole demo table is installed"

                # Act -- the user's path: type /sym and press enter
                cmd_input = app.query_one("#cmd", Input)
                cmd_input.value = "/sym main"
                cmd_input.focus()
                await pilot.pause()
                await pilot.press("enter")
                await app.workers.wait_for_complete()  # the dispatch worker
                await pilot.pause()  # the marshalled write onto the RichLog

                # Assert -- the lookup rendered in the output pane
                lines = [strip.text for strip in app.query_one("#output", RichLog).lines]
                assert any("0x00002000  main" in line for line in lines), (
                    f"/sym main renders the address in the TUI, got {lines[-5:]}"
                )

        _run(scenario)

    def test_on_connect_hook_sees_the_table_under_auto_connect(self, app_factory):
        """With auto_connect on, an on_connect hook already sees every demo symbol loaded."""
        async def scenario():
            from termapy.symbols import get_table

            # Arrange -- auto_connect on the DEMO port connects synchronously
            # inside _run_startup, so the hook fires during on_mount
            app, _, path = app_factory(auto_connect=True)
            (Path(path).parent / "sym").mkdir(exist_ok=True)
            shutil.copyfile(DEMO_SYMBOLS, Path(path).parent / "sym" / f"proj{SYMBOLS_SUFFIX}")
            seen: list[int] = []

            def hook(ctx):
                table = get_table(ctx)
                seen.append(len(table) if table is not None else -1)

            # source="app": a hook the host registers itself.  on_app_start
            # drops every folder-sourced hook before it fires, so a made-up
            # label would be swept away with them.
            app.repl.register_lifecycle_hook(
                LifecycleHook(name="on_connect", handler=hook, source="app", plugin="probe"),
            )

            # Act
            async with app.run_test() as pilot:
                await pilot.pause()

                # Assert
                assert app.is_connected is True, "auto_connect opened the DEMO port"
                assert seen == [DEMO_COUNT], (
                    "on_app_start (symbol load) precedes the startup auto-connect, "
                    "so on_connect already sees the table"
                )

        _run(scenario)


class TestHistoryFileSync:
    """The history file is shared state: external appends and other sessions.

    The reason this is Pilot-tested rather than unit-tested: the value is
    "press Up and see the line", which needs the real key handler, the real
    navigator and the real input widget together.
    """

    def test_a_line_appended_after_startup_is_recallable(self, app_factory, tmp_path):
        """The workflow this exists for: drop commands in, arrow to them."""
        async def scenario():
            from textual.widgets import Input

            # Seeded BEFORE the app: SerialTerminal.__init__ loads the file.
            history = tmp_path / "proj" / "proj.history"
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_text("/old\n", encoding="utf-8")
            app, _, _ = app_factory()

            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.history == ["/old"], "started with what the file held"

                # Arrange: something appends while the app runs
                history.write_text("/old\n/appended\n", encoding="utf-8")
                _age(history, 5)

                # Act
                app.query_one("#cmd", Input).focus()
                await pilot.pause()
                await pilot.press("up")
                await pilot.pause()

                # Assert
                assert app.query_one("#cmd", Input).value == "/appended", (
                    "the first Up shows the line added after startup"
                )

        _run(scenario)

    def test_unchanged_file_is_not_re_read(self, app_factory, tmp_path):
        """One stat per browse; an untouched file must not disturb the list."""
        async def scenario():
            from textual.widgets import Input

            # Seeded BEFORE the app: SerialTerminal.__init__ loads the
            # file, so a later write would test the sync path instead.
            history = tmp_path / "proj" / "proj.history"
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_text("/one\n/two\n", encoding="utf-8")
            app, _, _ = app_factory()

            async with app.run_test() as pilot:
                await pilot.pause()
                before = list(app.history)
                assert before == ["/one", "/two"], "loaded at startup"

                # Act
                app.query_one("#cmd", Input).focus()
                await pilot.pause()
                await pilot.press("up")
                await pilot.pause()

                # Assert
                assert app.history == before, "no change on disk, no change in memory"
                assert app.query_one("#cmd", Input).value == "/two", "newest first"

        _run(scenario)

    def test_a_typed_command_and_an_append_are_both_reachable(self, app_factory, tmp_path):
        """An append lands newest -- it is the latest thing the session learned.

        The typed command is not lost, just one Up further: recall order
        is by when this session saw a line, and the appended one arrived
        after the user pressed Enter.
        """
        async def scenario():
            from textual.widgets import Input

            history = tmp_path / "proj" / "proj.history"
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_text("/old\n", encoding="utf-8")
            app, _, _ = app_factory()

            async with app.run_test() as pilot:
                await pilot.pause()
                cmd_input = app.query_one("#cmd", Input)
                cmd_input.focus()
                cmd_input.value = "/print typed"
                await pilot.press("enter")
                await pilot.pause()

                history.write_text("/old\n/appended\n", encoding="utf-8")
                _age(history, 5)

                # Act
                await pilot.press("up")
                await pilot.pause()
                first = app.query_one("#cmd", Input).value
                await pilot.press("up")
                await pilot.pause()
                second = app.query_one("#cmd", Input).value

                # Assert
                assert first == "/appended", "the newly appended line is the first Up"
                assert second == "/print typed", "the typed command is one Up further"

        _run(scenario)

    def test_save_merges_instead_of_overwriting(self, app_factory):
        """Two sessions on one config: last exit no longer wins."""
        async def scenario():
            from textual.widgets import Input

            app, _, path = app_factory()
            history = Path(path).parent / "proj.history"

            async with app.run_test() as pilot:
                await pilot.pause()
                cmd_input = app.query_one("#cmd", Input)
                cmd_input.focus()
                cmd_input.value = "/print mine"
                await pilot.press("enter")
                await pilot.pause()

                # Arrange: another session exits while this one runs
                history.write_text("/theirs\n", encoding="utf-8")

                # Act
                app._save_history()

                # Assert
                lines = history.read_text(encoding="utf-8").splitlines()
                assert lines == ["/theirs", "/print mine"], (
                    "the other session's command survived this one's save"
                )

        _run(scenario)
