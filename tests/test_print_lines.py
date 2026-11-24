"""A file's text printed a person can stop: waiting.print_lines, behind /cfg.file and the .dump family."""

from __future__ import annotations

import threading

import pytest

from termapy.plugins import PluginContext
from termapy.plugins.handles.internal import InternalHandle
from termapy.plugins.handles.io import IOHandle
from termapy.plugins.waiting import print_lines

LINES = ["one", "two", "three", "four", "five"]


def _ctx(stop: threading.Event, printed: list[str], *, in_script: bool = False, stop_at: int = 0):
    """A context whose writer records each line and, at line ``stop_at``, presses Escape."""

    def write(text, color=None):
        printed.append(text)
        if stop_at and len(printed) == stop_at:
            stop.set()

    internal = InternalHandle(
        in_script=lambda: in_script, script_stop=stop.set, script_stop_event=stop
    )
    return PluginContext(internal=internal, io=IOHandle(_write=write))


def test_escape_stops_the_print_and_says_where():
    """A stop asked while printing ends it, with a line saying how far it got."""
    # Arrange
    stop = threading.Event()
    printed: list[str] = []

    # Act
    count = print_lines(_ctx(stop, printed, stop_at=2), LINES)

    # Assert
    assert (count, printed) == (2, ["one", "two", "Stopped after 2 of 5 lines."])


@pytest.mark.rule("plugin-never-clears-stop")
def test_at_the_prompt_a_stale_stop_is_cleared():
    """Typed at the prompt, a stop an earlier Escape left set does not end this print."""
    # Arrange
    stop = threading.Event()
    stop.set()
    printed: list[str] = []

    # Act
    count = print_lines(_ctx(stop, printed), LINES)

    # Assert
    assert (count, printed) == (5, LINES), "every line, no stopped line"


@pytest.mark.rule("plugin-never-clears-stop")
def test_in_a_script_the_stop_is_the_scripts():
    """Inside a script a stop already asked is honored, never cleared."""
    # Arrange
    stop = threading.Event()
    stop.set()
    printed: list[str] = []

    # Act
    count = print_lines(_ctx(stop, printed, in_script=True), LINES)

    # Assert
    assert (count, printed, stop.is_set()) == (0, ["Stopped after 0 of 5 lines."], True)
