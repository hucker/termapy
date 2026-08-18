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

import pytest

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
