"""``termapy.matching``: the one name-pattern reader every name filter shares."""

from __future__ import annotations

import pytest

from termapy.matching import is_glob, matches


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("pic32", False),
        ("pic32*", True),
        ("U?MODE", True),
        ("U[12]MODE", True),
        ("", False),
    ],
)
def test_a_wildcard_makes_a_glob(pattern, expected):
    """name-one-matcher: any of * ? [ makes the pattern a glob, and nothing else does."""
    assert is_glob(pattern) is expected


@pytest.mark.parametrize(
    ("name", "pattern", "expected"),
    [
        ("PIC32MX795F512L", "pic32", True),  # plain text: contains, case ignored
        ("ATSAME54P20A", "pic32", False),
        ("crc16-modbus", "*MODBUS*", True),  # a glob ignores case on every OS
        ("crc16-modbus", "modbus*", False),  # a glob is anchored at both ends
        ("crc16-modbus", "crc16*", True),
        ("U1MODE", "U?MODE", True),
        ("U3MODE", "U[12]MODE", False),
        ("anything", "", True),  # an empty pattern lists everything
    ],
)
def test_plain_text_contains_and_a_glob_is_anchored(name, pattern, expected):
    """name-one-matcher: plain text is a case-insensitive contains; a glob is anchored, case ignored."""
    assert matches(name, pattern) is expected
