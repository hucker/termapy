"""The TUI's own buttons, menus and palette actions under a non-default REPL prefix.

The lines the TUI synthesizes itself (find bar, Record button, menu toggles,
palette entries, custom buttons) once spelled ``/`` in code, so with
``cmd_prefix: "!"`` each one went to the wire as device text.  These boot the
real app with a bang prefix and assert the action still reaches its command
(registry ``prefix-never-hardcoded``); ``tests/test_cli_prefix.py`` covers the
same contract for typed lines in the CLI.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from termapy.defaults import DEFAULT_CFG


def _run(scenario) -> None:
    asyncio.run(scenario())


def _write_cfg(tmp_path, **overrides) -> tuple[dict, str]:
    """A DEMO-port config on disk with a bang prefix and one placeholder button."""
    proj = tmp_path / "bang"
    proj.mkdir(parents=True, exist_ok=True)
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**serial, "port": "DEMO"},
        "auto_connect": False,
        "cmd_prefix": "!",
        "custom_buttons": [
            {
                "enabled": True,
                "name": "Lines",
                "command": "{prefix}term.line_no toggle",
                "tooltip": "toggle line numbers",
            },
        ],
        **overrides,
    }
    path = proj / "bang.cfg"
    path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    cfg = dict(cfg)
    cfg["_config_path"] = str(path)
    return cfg, str(path)


@pytest.fixture
def bang_app(tmp_path):
    """The real SerialTerminal on a config whose REPL prefix is ``!``."""
    from termapy.app import SerialTerminal

    cfg, path = _write_cfg(tmp_path)
    app = SerialTerminal(cfg, path)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the boot.
    app.repl.global_root = tmp_path
    return app


class TestSynthesizedLinesFollowThePrefix:
    """A TUI affordance dispatches a REPL command under any prefix, never device text."""

    def test_menu_toggle_reaches_its_command(self, bang_app):
        """The line-numbers menu action flips the flag under ``!``, so the synthesized line was a REPL command."""
        app = bang_app

        async def scenario():
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                before = app.ctx.ns("flags")["line_no"]

                # Act -- the palette action behind the toggle (a quiet, synchronous dispatch)
                app._palette_toggle_line_numbers()
                await pilot.pause()

                # Assert
                assert app.ctx.ns("flags")["line_no"] is (not before), (
                    "under cmd_prefix '!' the toggle still ran as !term.line_no toggle; "
                    "a typed '/term.line_no toggle' would have gone to the wire and changed nothing"
                )

        _run(scenario)

    def test_custom_button_placeholder_follows_the_prefix(self, bang_app):
        """A custom button whose command starts with {prefix} runs under ``!``, the shipped Info button's shape."""
        app = bang_app

        async def scenario():
            async with app.run_test() as pilot:
                await pilot.pause()
                # Arrange
                before = app.ctx.ns("flags")["line_no"]

                # Act -- the click handler's work, which dispatches on a worker thread
                app._run_custom_button("btn-custom-0")
                await app.workers.wait_for_complete()
                await pilot.pause()

                # Assert
                assert app.ctx.ns("flags")["line_no"] is (not before), (
                    "'{prefix}term.line_no toggle' dispatched as !term.line_no toggle"
                )

        _run(scenario)
