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

import pytest
from pilot_wait import steady
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


async def _settled_width(pilot, bar: StatusBar) -> int:
    """The bar's width once it has stopped changing.

    One pause is not always enough under a loaded run: a layout pass from
    mounting, or the one that follows a resize, can land a frame later and
    would be read as the bar's own.  A layout the bar asks for still shows
    within the wait, so this hides nothing the tests are about.
    """
    return await steady(pilot, lambda: bar.size.width)


def test_show_makes_it_visible_with_text():
    """show() both renders the text and adds the visible class that unhides the bar."""

    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("Saved config", timeout=99)
            assert "Saved config" in str(bar.content), "shows the text"
            assert bar.has_class("visible"), "becomes visible when it has text"

    _run(scenario)


@pytest.mark.rule("widget-text-literal")
def test_status_text_renders_as_typed():
    """Plugin text on the status bar is shown as written, brackets and all."""

    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)

            # Act
            bar.show("[ok] saved to [Rec]", timeout=99)

            # Assert
            assert str(bar.render()) == "[ok] saved to [Rec]"

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


@pytest.mark.rule("ui-timer-stale-fire")
def test_a_stale_auto_clear_leaves_a_newer_message():
    """A stopped timer can still fire once (Textual's Windows sleep): the first message's clear leaves the second alone."""

    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("first", timeout=99)
            stale = bar._shown
            bar.show("second", timeout=99)

            # Act -- the first message's auto-clear fires after its stop
            bar._clear_if_current(stale)

            # Assert
            assert bar.has_class("visible") and "second" in str(bar.content), (
                "a stale auto-clear hid the newer message"
            )

    _run(scenario)


def test_progress_outlives_a_transient_message_timer():
    """Progress text is persistent: a transient message's auto-clear set before it never hides it."""

    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("saved", timeout=0.05)
            bar.set_progress("Capturing -> cap.txt")
            await pilot.pause(0.3)

            # Assert
            assert bar.has_class("visible") and "Capturing" in str(bar.content), (
                "the transient message's timer cleared the progress text"
            )

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


def test_a_counter_never_narrows_the_bar_while_it_shows():
    """A shorter text keeps the bar's width (no layout pass), a wider one grows it; text always updates.

    A layout pass repaints the bottom row and the output pane, so a plugin
    refreshing a counter twice a second made the screen flicker.  The width
    holding is the visible sign that no layout ran.
    """

    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("TX:1,000  RX:2,000", timeout=99)
            wide = await _settled_width(pilot, bar)

            # Act
            bar.show("TX:9  RX:9", timeout=99)
            after_shorter = await _settled_width(pilot, bar)
            shown = str(bar.render()).strip()
            bar.show("TX:10,000  RX:20,000", timeout=99)
            after_wider = await _settled_width(pilot, bar)

            # Assert
            assert shown == "TX:9  RX:9", "the shorter text is what shows"
            assert after_shorter == wide, "a shorter text keeps the width: no layout"
            assert after_wider > wide, "a wider text grows the bar"

    _run(scenario)


def test_hiding_the_bar_lets_the_next_text_size_it_afresh():
    """After the bar hides, a short text gets a short bar again, not the old widest."""

    async def scenario():
        async with _Host().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            bar.show("a long status message for a moment", timeout=99)
            wide = await _settled_width(pilot, bar)

            # Act
            bar.clear_status()
            await pilot.pause()
            bar.show("ok", timeout=99)
            actual = await _settled_width(pilot, bar)

            # Assert
            assert actual < wide, "sized for the new text"

    _run(scenario)
