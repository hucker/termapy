"""folder_ops.stamped_path: a timestamped file name never lands on one that exists.

The stamp is to the second, so two saves in one second (two lines of a
paste, a script) built the same name and the second replaced the first
without a word: found 2026-11-07, when ``/ss.svg x`` then ``/ss.svg.silent x``
left one screenshot.  Every timestamped save (screenshots, the screenshot
hotkeys, ``/run.profile``'s CSV) takes its name from here.
"""

from __future__ import annotations

from termapy.folder_ops import stamped_path

_STAMP = "20261107_163925"


def test_a_free_name_is_the_stamped_one(tmp_path):
    """With nothing there, the path is <base>_<stamp><ext>."""
    # Act
    actual = stamped_path(tmp_path, "shot", ".svg", stamp=_STAMP)

    # Assert
    assert actual == tmp_path / f"shot_{_STAMP}.svg"


def test_a_taken_name_gets_the_next_number(tmp_path):
    """The stamped name and its _2 taken, the third save is _3."""
    # Arrange
    (tmp_path / f"shot_{_STAMP}.svg").write_text("first", encoding="utf-8")
    (tmp_path / f"shot_{_STAMP}_2.svg").write_text("second", encoding="utf-8")

    # Act
    actual = stamped_path(tmp_path, "shot", ".svg", stamp=_STAMP)

    # Assert
    assert actual == tmp_path / f"shot_{_STAMP}_3.svg", "the next free number, nothing replaced"


def test_two_saves_in_a_row_never_share_a_name(tmp_path):
    """Saved back to back on the clock's stamp, the second name differs and is free, whatever the second."""
    # Arrange
    first = stamped_path(tmp_path, "shot", ".txt")
    first.write_text("first", encoding="utf-8")

    # Act
    second = stamped_path(tmp_path, "shot", ".txt")

    # Assert
    assert second != first, "a second save gets its own name"
    assert not second.exists(), "and the name is free"
    assert first.read_text(encoding="utf-8") == "first", "the first file is untouched"
