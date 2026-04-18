"""Contract tests for the SHUTDOWN_RACE exception tuple.

SHUTDOWN_RACE is the tuple of exception types we allow reader-thread
callbacks, timers, and event handlers to swallow silently during app
teardown.  Anything not in this tuple should propagate so real bugs
get noticed.

These tests pin that contract so a future refactor can't silently
widen or narrow what's caught.
"""

from __future__ import annotations

from textual.css.query import NoMatches

from termapy.app import SHUTDOWN_RACE


class TestShutdownRaceMembership:
    def test_contains_no_matches(self):
        # NoMatches is what query_one raises when a widget is gone -- the
        # most common shutdown-race exception in termapy.
        assert NoMatches in SHUTDOWN_RACE, \
            "NoMatches must be in SHUTDOWN_RACE so query_one failures " \
            "during teardown are quietly swallowed"

    def test_contains_runtime_error(self):
        # RuntimeError is raised by call_from_thread when the event
        # loop is shutting down.
        assert RuntimeError in SHUTDOWN_RACE, \
            "RuntimeError must be in SHUTDOWN_RACE so call_from_thread " \
            "failures during teardown are quietly swallowed"

    def test_does_not_contain_generic_exception(self):
        # The whole point of this tuple is to avoid swallowing arbitrary
        # exceptions -- real bugs should propagate.  If Exception ever
        # ends up in the tuple (e.g. someone 'cleaning up' the types),
        # we've regressed back to the bug we were trying to fix.
        assert Exception not in SHUTDOWN_RACE, \
            "Generic Exception must NOT be in SHUTDOWN_RACE or we'd be " \
            "silencing real bugs again"


class TestShutdownRaceBehavior:
    def test_swallows_no_matches(self):
        # Arrange / Act / Assert -- should not raise
        try:
            raise NoMatches("gone")
        except SHUTDOWN_RACE:
            pass  # expected

    def test_swallows_runtime_error(self):
        # Arrange / Act / Assert
        try:
            raise RuntimeError("loop closed")
        except SHUTDOWN_RACE:
            pass  # expected

    def test_lets_value_error_propagate(self):
        # Arrange / Act / Assert -- real bugs still bubble up.
        import pytest

        with pytest.raises(ValueError, match="real bug"):
            try:
                raise ValueError("real bug")
            except SHUTDOWN_RACE:
                pass  # this branch must NOT catch ValueError

    def test_lets_type_error_propagate(self):
        # Arrange / Act / Assert -- belt and suspenders.
        import pytest

        with pytest.raises(TypeError):
            try:
                raise TypeError("also a real bug")
            except SHUTDOWN_RACE:
                pass
