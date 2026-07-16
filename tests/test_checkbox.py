"""Behavioral tests for StrongCheckbox -- the glyph flips with the value.

Same pattern as test_status_bar: mount just the widget, drive it, assert.
StrongCheckbox exists solely to flip its glyph between ✗ (off) and ✓ (on)
-- the default Checkbox reuses one glyph -- and that contract had no
coverage when the widget lived only inside dialogs/modals.
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


def test_glyph_is_cross_when_off():
    async def scenario():
        async with _Host(value=False).run_test() as pilot:
            cb = pilot.app.query_one(StrongCheckbox)
            assert cb.BUTTON_INNER == "✗", "off shows the cross glyph"

    _run(scenario)


def test_glyph_is_check_when_on():
    async def scenario():
        async with _Host(value=True).run_test() as pilot:
            cb = pilot.app.query_one(StrongCheckbox)
            assert cb.BUTTON_INNER == "✓", "on shows the check glyph"

    _run(scenario)


def test_glyph_flips_when_value_changes():
    async def scenario():
        async with _Host(value=False).run_test() as pilot:
            cb = pilot.app.query_one(StrongCheckbox)
            cb.value = True
            await pilot.pause()
            assert cb.BUTTON_INNER == "✓", "flips to check when turned on"
            cb.value = False
            await pilot.pause()
            assert cb.BUTTON_INNER == "✗", "flips back to cross when turned off"

    _run(scenario)
