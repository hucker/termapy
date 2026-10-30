"""``termapy.matching``: the one name-pattern reader every name filter shares."""

# cspell:ignore hlep lsit -- the typos these tests make on purpose

from __future__ import annotations

import pytest

from termapy.matching import closest, did_you_mean, edit_distance, is_glob, matches


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


# -- did-you-mean-one ----------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("hello", "hello", 0),
        ("cat", "cats", 1),  # insert
        ("cats", "cat", 1),  # delete
        ("cat", "car", 1),  # substitute
        ("ab", "ba", 1),  # a swap of neighbors is one edit, not two
        ("", "", 0),
        ("abc", "", 3),
        ("abc", "xyz", 3),
    ],
)
def test_each_kind_of_edit_counts_once(a, b, expected):
    """did-you-mean-one: insert, delete, substitute and a neighbor swap each cost one edit."""
    assert edit_distance(a, b) == expected


@pytest.mark.parametrize(
    ("typed", "names", "expected"),
    [
        ("hlep", ["help", "log", "port"], ["help"]),
        (
            "pic32cm51641e00100",
            ["pic32cm5164le00100", "pic32cm2532le00064"],
            ["pic32cm5164le00100"],
        ),
        ("u1_mode", ["U1_MODE", "U1_STA"], ["U1_MODE"]),
        (
            "SERCOM_2USART_INT_CTRLA",
            ["SERCOM2_USART_INT_CTRLA", "SERCOM5_USART_INT_CTRLA"],
            ["SERCOM2_USART_INT_CTRLA"],
        ),
    ],
)
def test_the_name_meant_comes_first(typed, names, expected):
    """did-you-mean-one: a swap, a one-for-L, a case slip and a moved digit each find the name meant."""
    assert closest(typed, names)[: len(expected)] == expected


@pytest.mark.parametrize("word", ["xyz", "foo", "quit"])
def test_a_short_word_is_not_a_typo_of_everything(word):
    """did-you-mean-one: the reach grows with the name, so three letters reach one edit, not two."""
    commands = ["sym", "log", "os", "edit", "exit", "tui", "cls", "ss"]
    assert closest(word, commands) == [], f"{word} is not a typo of any of them"


def test_nearest_first_at_most_the_limit_and_never_itself():
    """did-you-mean-one: closer names lead, the limit caps the answer, and the typed name never returns."""
    names = ["dev.list", "dev.lib", "dev.use", "dev.lsit"]
    assert closest("dev.lsit", names, limit=1) == ["dev.list"], "one swap beats two edits"
    assert "dev.lsit" not in closest("dev.lsit", names), "a name is not its own suggestion"


def test_did_you_mean_is_one_phrasing():
    """did-you-mean-one: one or more names read the same way everywhere, and none reads as nothing."""
    assert did_you_mean(["help"], prefix="/") == " -- did you mean /help?"
    assert did_you_mean(["a", "b"]) == " -- did you mean a, b?"
    assert did_you_mean([]) == "", "no suggestion adds nothing to the error"
