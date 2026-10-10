"""Behavioral tests for the StatusBar widget.

This is the payoff of extracting the status line from ``SerialTerminal``:
the show / auto-clear / progress logic is now exercisable by mounting
JUST the widget in a throwaway host app -- no serial engine, no plugins,
no real terminal.  When the same logic lived on ``SerialTerminal`` it
could only be reached by standing up the whole application, which is why
app.py has ~zero unit coverage and tests like test_palette.py could only
assert "the method exists," not "it does the right thing."
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from termapy.widgets import StatusBar


class _Host(App):
    """Minimal host: mounts one StatusBar and nothing else."""

    def compose(self) -> ComposeResult:
        yield StatusBar()


def _run(scenario) -> None:
    """Run an async Pilot scenario (matches the asyncio.run pattern in
    test_palette.py -- no pytest-asyncio needed)."""
    asyncio.run(scenario())


def test_show_makes_it_visible_with_text():
    """show() both renders the text and adds the visible class that unhides the bar."""
    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("Saved config", timeout=99)
            assert "Saved config" in str(bar.content), "shows the text"
            assert bar.has_class("visible"), "becomes visible when it has text"

    _run(scenario)


def test_empty_text_clears():
    """Showing an empty string hides the bar instead of leaving an empty visible strip."""
    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("something", timeout=99)
            bar.show("")
            assert not bar.has_class("visible"), "empty text hides the bar"

    _run(scenario)


def test_newer_message_replaces_older():
    """A second show() replaces the first message; messages do not queue or stack."""
    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("first", timeout=99)
            bar.show("second", timeout=99)
            assert "second" in str(bar.content), "newest message wins"

    _run(scenario)


def test_progress_is_persistent_and_hides_on_empty():
    """Progress text shows without a timeout and is hidden only by an empty set_progress()."""
    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.set_progress("50%")
            assert bar.has_class("visible"), "progress text shows"
            assert "50%" in str(bar.content), "progress text is correct"
            bar.set_progress("")
            assert not bar.has_class("visible"), "empty progress hides the bar"

    _run(scenario)


def test_transient_message_auto_clears_after_timeout():
    """A message with a short timeout hides itself once the timer fires, with no caller action."""
    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("bye", timeout=0.05)
            assert bar.has_class("visible"), "visible right after show"
            await pilot.pause(0.2)  # let the auto-clear timer fire
            assert not bar.has_class("visible"), "auto-cleared after timeout"

    _run(scenario)
