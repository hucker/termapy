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
