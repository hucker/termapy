"""Waiting on the app in Pilot tests: poll a condition instead of trusting one pause.

A single ``pilot.pause()`` lets the event loop run once, which is enough on
an idle machine and not always under a loaded full suite: a RichLog, for
one, renders only after layout has given it a size.  Poll the state the
test is about, then assert it, so a slow run waits instead of failing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


async def until(pilot: Any, predicate: Callable[[], object], seconds: float = 5.0) -> bool:
    """Poll ``predicate`` every 20 ms until it is truthy or ``seconds`` pass; return the last answer.

    The caller asserts afterward, so a condition that never holds still
    fails with the test's own message, not a timeout from here.
    """
    for _ in range(int(seconds * 50)):
        if predicate():
            return True
        await pilot.pause(0.02)
    return bool(predicate())


async def steady(pilot: Any, read: Callable[[], object], polls: int = 3, seconds: float = 5.0):
    """Return ``read()`` once it has held for ``polls`` checks in a row, 20 ms apart.

    For a value that settles rather than turns true: a pane still growing,
    a widget still being laid out.  After ``seconds`` it returns the last
    reading, and the caller's assert decides.
    """
    previous, held = object(), 0
    for _ in range(int(seconds * 50)):
        value = read()
        held = held + 1 if value == previous else 0
        if held >= polls:
            return value
        previous = value
        await pilot.pause(0.02)
    return read()


def has_link(app: Any, command: str) -> bool:
    """True once the output pane has registered a link that runs ``command``.

    A page arrives in pieces, so its text can show before the link a test
    wants to read or click: wait on this, not on the text.
    """
    return any(link.command == command for link in app._links.values())
