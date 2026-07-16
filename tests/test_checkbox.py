"""Behavioral tests for StrongCheckbox -- the glyph flips with the value.

Same pattern as test_status_bar: mount just the widget, drive it, assert.
StrongCheckbox exists solely to flip its glyph between blank (off) and X
(on) -- the default Checkbox reuses one glyph -- and that contract had no
coverage when the widget lived only inside dialogs/modals.  ASCII glyphs
on purpose: the old ✓/✗ were EAW-ambiguous and clipped on 2-cell fonts.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from termapy.widgets import StrongCheckbox


class _Host(App):
    """Minimal host: mounts one StrongCheckbox at a given initial value."""

    def __init__(self, value: bool = False) -> None:
        super().__init__()
        self._value = value

    def compose(self) -> ComposeResult:
        yield StrongCheckbox(value=self._value)


def _run(scenario) -> None:
    asyncio.run(scenario())


def test_glyph_is_blank_when_off():
    async def scenario():
        async with _Host(value=False).run_test() as pilot:
            cb = pilot.app.query_one(StrongCheckbox)
            assert cb.BUTTON_INNER == " ", "off shows a blank (classic [ ])"

    _run(scenario)


def test_glyph_is_x_when_on():
    async def scenario():
        async with _Host(value=True).run_test() as pilot:
            cb = pilot.app.query_one(StrongCheckbox)
            assert cb.BUTTON_INNER == "X", "on shows the X (classic [X])"

    _run(scenario)


def test_glyph_flips_when_value_changes():
    async def scenario():
        async with _Host(value=False).run_test() as pilot:
            cb = pilot.app.query_one(StrongCheckbox)
            cb.value = True
            await pilot.pause()
            assert cb.BUTTON_INNER == "X", "flips to X when turned on"
            cb.value = False
            await pilot.pause()
            assert cb.BUTTON_INNER == " ", "flips back to blank when turned off"

    _run(scenario)
