"""Tests for typed_lines -- the lines typed at the prompt, per config, that /run.last replays."""

from __future__ import annotations

import pytest

from termapy.typed_lines import TypedLine, TypedLines

pytestmark = pytest.mark.rule("typed-lines-one-feed")


def test_lines_come_back_in_the_order_typed():
    """A config's lines are oldest first, repeats kept: the history file dedupes, this list does not."""
    # Arrange
    typed = TypedLines()

    # Act
    for text in ("A", "B", "A"):
        typed.add("one.cfg", TypedLine(text))

    # Assert
    actual = [line.text for line in typed.lines("one.cfg")]
    assert actual == ["A", "B", "A"], "every line, in the order typed"


def test_a_failed_line_keeps_its_error():
    """A failed line is kept with its error, so the list can show why it will not replay."""
    # Arrange
    typed = TypedLines()

    # Act
    typed.add("one.cfg", TypedLine("/nosuch", success=False, error="Unknown command: nosuch"))

    # Assert
    actual = typed.lines("one.cfg")
    expected = [TypedLine("/nosuch", False, "Unknown command: nosuch")]
    assert actual == expected


def test_each_config_keeps_its_own_lines():
    """Lines typed in one config never show in another, and come back on returning to the first."""
    # Arrange
    typed = TypedLines()

    # Act
    typed.add("one.cfg", TypedLine("A"))
    typed.add("two.cfg", TypedLine("B"))
    typed.add("one.cfg", TypedLine("C"))

    # Assert
    actual_one = [line.text for line in typed.lines("one.cfg")]
    actual_two = [line.text for line in typed.lines("two.cfg")]
    assert actual_one == ["A", "C"], "one.cfg's lines only"
    assert actual_two == ["B"], "two.cfg's lines only"


def test_a_config_with_nothing_typed_is_empty():
    """A config nobody typed in reads as an empty list, not an error."""
    # Act
    actual = TypedLines().lines("never.cfg")

    # Assert
    assert actual == []


def test_reading_twice_returns_the_same_lines():
    """A read consumes nothing: a second read, as a list then a replay make, sees the same lines."""
    # Arrange
    typed = TypedLines()
    typed.add("one.cfg", TypedLine("A"))

    # Act
    first = typed.lines("one.cfg")
    second = typed.lines("one.cfg")

    # Assert
    assert second == first, "the second read sees what the first did"


def test_a_read_is_a_copy():
    """Changing a list a read returned leaves the kept lines alone."""
    # Arrange
    typed = TypedLines()
    typed.add("one.cfg", TypedLine("A"))

    # Act
    typed.lines("one.cfg").clear()

    # Assert
    actual = [line.text for line in typed.lines("one.cfg")]
    assert actual == ["A"], "the kept line survives a caller clearing its copy"


def test_a_long_session_keeps_only_the_newest():
    """Typing past the limit drops the oldest lines, so a long session's list stays bounded."""
    # Arrange
    typed = TypedLines(limit=5)

    # Act
    for i in range(1000):
        typed.add("one.cfg", TypedLine(f"line {i}"))

    # Assert
    actual = [line.text for line in typed.lines("one.cfg")]
    expected = [f"line {i}" for i in range(995, 1000)]
    assert actual == expected, "the newest five, oldest first"
