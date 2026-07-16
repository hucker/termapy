"""Unit tests for HistoryNavigator -- the REPL Up/Down browsing cursor."""

from __future__ import annotations

from termapy.history_nav import HistoryNavigator


def test_starts_not_browsing():
    nav = HistoryNavigator()

    # Assert
    assert nav.browsing is False, "a fresh navigator holds the live draft, not history"


def test_up_on_empty_history_is_noop():
    nav = HistoryNavigator()

    # Act
    value = nav.up([], "draft")

    # Assert
    assert value is None, "no history means nothing to recall -- input untouched"
    assert nav.browsing is False, "an empty-history Up does not enter browsing"


def test_first_up_jumps_to_newest_and_stashes_draft():
    nav = HistoryNavigator()
    history = ["one", "two", "three"]

    # Act
    value = nav.up(history, "half-typed")

    # Assert
    assert value == "three", "first Up recalls the newest entry"
    assert nav.browsing is True, "recalling an entry enters browsing"


def test_successive_up_walks_toward_older_and_floors():
    nav = HistoryNavigator()
    history = ["one", "two", "three"]

    # Act
    steps = [nav.up(history, "draft"), nav.up(history, "draft"), nav.up(history, "draft")]
    floored = nav.up(history, "draft")  # already at oldest

    # Assert
    assert steps == ["three", "two", "one"], "Up walks newest -> oldest"
    assert floored == "one", "Up at the oldest entry stays put"


def test_down_when_not_browsing_is_noop():
    nav = HistoryNavigator()

    # Act
    value = nav.down(["one", "two"])

    # Assert
    assert value is None, "Down without an active browse leaves the input alone"


def test_down_walks_toward_newer():
    nav = HistoryNavigator()
    history = ["one", "two", "three"]
    nav.up(history, "draft")  # -> three
    nav.up(history, "draft")  # -> two

    # Act
    value = nav.down(history)  # back toward newer

    # Assert
    assert value == "three", "Down steps from an older entry toward newer ones"


def test_down_off_the_newest_restores_stashed_draft():
    nav = HistoryNavigator()
    history = ["one", "two"]
    nav.up(history, "half-typed")  # stashes the draft, recalls "two"

    # Act
    value = nav.down(history)  # walk down past the newest entry

    # Assert
    assert value == "half-typed", "walking down off the newest entry restores the draft"
    assert nav.browsing is False, "restoring the draft returns to not-browsing"


def test_empty_draft_is_restored_as_empty_string_not_none():
    nav = HistoryNavigator()
    history = ["one"]
    nav.up(history, "")  # started browsing from an empty input

    # Act
    value = nav.down(history)

    # Assert
    assert value == "", "an empty draft round-trips as '' (distinct from the None no-op)"
    assert nav.browsing is False, "still returns to not-browsing"


def test_reset_stops_browsing():
    nav = HistoryNavigator()
    nav.up(["one", "two"], "draft")

    # Act
    nav.reset()

    # Assert
    assert nav.browsing is False, "reset abandons the browse (submit / config switch / Escape)"


def test_up_after_reset_restashes_current_draft():
    nav = HistoryNavigator()
    history = ["one", "two"]
    nav.up(history, "first-draft")
    nav.reset()

    # Act -- a new browse should stash the NEW draft, not the stale one
    nav.up(history, "second-draft")
    restored = nav.down(history)

    # Assert
    assert restored == "second-draft", "a fresh browse stashes the current draft"
