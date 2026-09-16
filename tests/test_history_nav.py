"""Unit tests for history_nav: the Up/Down cursor and the file merge."""

from __future__ import annotations

from termapy.history_nav import HistoryNavigator, merge_history, read_history


class TestReadHistory:
    """The file reader: never raises, trims, drops blanks."""

    def test_missing_file_is_empty(self, tmp_path):
        assert read_history(tmp_path / "nope.history", 30) == [], "no file, no history"

    def test_blank_lines_dropped_and_whitespace_trimmed(self, tmp_path):
        # Arrange
        path = tmp_path / "h"
        path.write_text("/one\n\n  /two  \n\n", encoding="utf-8")

        # Act / Assert
        assert read_history(path, 30) == ["/one", "/two"], "blanks out, ends trimmed"

    def test_limit_keeps_the_newest(self, tmp_path):
        # Arrange
        path = tmp_path / "h"
        path.write_text("\n".join(f"/cmd{i}" for i in range(10)), encoding="utf-8")

        # Act / Assert
        assert read_history(path, 3) == ["/cmd7", "/cmd8", "/cmd9"], "the tail is the newest"

    def test_undecodable_file_is_empty_not_fatal(self, tmp_path):
        # Arrange
        path = tmp_path / "h"
        path.write_bytes(b"\xff\xfe\x00binary")

        # Act / Assert
        assert read_history(path, 30) == [], "a corrupt file must not stop startup"


class TestMergeHistory:
    """Combining the file with a session's own list."""

    def test_a_line_appended_after_a_known_one_sorts_newest(self):
        """The recall case: the file gained a line after what we already had."""
        # Act
        actual = merge_history(["/old", "/appended"], ["/old"], 30)

        # Assert
        assert actual == ["/old", "/appended"], (
            "/appended arrived after this session last read the file, so Up reaches it"
        )

    def test_an_unrelated_file_sorts_before_the_session(self):
        """Nothing in common: those lines are history we never had, not news."""
        # Act
        actual = merge_history(["/from_file"], ["/mine"], 30)

        # Assert
        assert actual == ["/from_file", "/mine"], (
            "what this session typed must stay the first Up"
        )

    def test_a_shared_line_keeps_the_session_position(self):
        # Act
        actual = merge_history(["/a", "/b"], ["/b", "/c"], 30)

        # Assert
        assert actual == ["/a", "/b", "/c"], "/b is not duplicated, and keeps its newer slot"

    def test_lines_after_the_shared_point_sort_last(self):
        """Another session appended past a command we share."""
        # Act
        actual = merge_history(["/a", "/b", "/theirs"], ["/b", "/mine"], 30)

        # Assert
        assert actual == ["/a", "/b", "/mine", "/theirs"], (
            "/a precedes the shared /b; /theirs followed it, so it lands newest"
        )

    def test_limit_drops_the_oldest(self):
        # Act
        actual = merge_history(["/f1", "/f2"], ["/m1", "/m2"], 3)

        # Assert
        assert actual == ["/f2", "/m1", "/m2"], "the oldest file line falls off"

    def test_empty_sides(self):
        assert merge_history([], ["/mine"], 30) == ["/mine"], "no file yet"
        assert merge_history(["/theirs"], [], 30) == ["/theirs"], "nothing typed this session"
        assert merge_history([], [], 30) == [], "both empty"

    def test_two_sessions_are_additive(self):
        """The concurrent-exit case: neither session loses the other's commands."""
        # Arrange: session A exited first, writing its list
        a_wrote = ["/a1", "/a2"]

        # Act: session B, which started before A exited, now exits
        actual = merge_history(a_wrote, ["/b1", "/b2"], 30)

        # Assert
        assert actual == ["/a1", "/a2", "/b1", "/b2"], "last exit no longer wins"


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
