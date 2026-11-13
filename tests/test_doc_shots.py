"""The documentation screenshots show the app that ships, not an older one.

Ten of the 24 images in ``src/termapy/help/img`` went unchanged from
2026-04-03 to 2026-10-28, through a toolbar change that renamed the
``?`` button to ``Help``: the docs showed a build that no longer
existed, and nothing said so.  Taking them needed a person at a terminal
with the demo device connected, so "re-shoot the docs" was a chore
someone had to remember.

``scripts/doc_shots.py`` makes 17 of them one command, and the guards
below make a stale one a red test rather than something a reader
notices.  The signal is the toolbar: it is in every full-window shot and
its labels come from ``app.py``, so an image whose buttons disagree with
the app's was taken by a different build.

A shot that is deliberately not regenerated (the pickers, the editors,
the palette) is still checked: those are the ones most likely to rot,
because nothing regenerates them at all.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

IMG_DIR = Path(__file__).resolve().parent.parent / "src" / "termapy" / "help" / "img"

# How many text runs from the top of an SVG to search for the toolbar.
# The toolbar is the first row of the frame, so its labels are the first
# handful of runs; the window title precedes them.
_TOOLBAR_RUNS = 12


def _text_runs(path: Path) -> list[str]:
    """Every text run in an SVG screenshot, in document order, unescaped."""
    source = path.read_text(encoding="utf-8")
    return [
        html.unescape(run).replace("\xa0", " ").strip()
        for run in re.findall(r">([^<]{1,})</text>", source)
    ]


def _shots() -> list[Path]:
    """Every committed documentation screenshot."""
    return sorted(IMG_DIR.glob("doc_*.svg"))


def _toolbar_labels() -> set[str]:
    """The button labels the app builds today, read from its own source.

    Read from ``app.py`` rather than by booting the app: the guard is
    about what the CODE says the toolbar is, and a shot is compared
    against that.  Reading the source keeps the test fast and free of a
    headless app boot.
    """
    source = (Path(__file__).resolve().parent.parent / "src" / "termapy" / "app.py").read_text(
        encoding="utf-8"
    )
    return set(re.findall(r'Button\(\s*"([^"]+)"', source))


@pytest.mark.rule("doc-shots-current")
def test_shots_exist():
    """The doc set is present: a guard that silently checks nothing is worse than none."""
    # Arrange / Act
    shots = _shots()

    # Assert
    assert len(shots) >= 20, f"only {len(shots)} screenshots found in {IMG_DIR}; the doc set is ~24"


@pytest.mark.rule("doc-shots-current")
@pytest.mark.parametrize("shot", _shots(), ids=lambda p: p.stem)
def test_a_shot_shows_the_current_toolbar(shot: Path):
    """A shot whose toolbar is not the app's was taken by an older build: re-shoot it.

    Fixed by running ``uv run python scripts/doc_shots.py <name>``, never
    by widening this test: the image is the thing that is wrong.
    """
    # Arrange
    labels = _toolbar_labels()
    runs = _text_runs(shot)
    head = runs[:_TOOLBAR_RUNS]

    # Act -- a full-window shot carries the toolbar; a modal or full-screen
    # view covers it, and those are checked by the staleness guard instead.
    shows_toolbar = any(label in head for label in labels)

    # Assert
    if shows_toolbar:
        assert "?" not in head, (
            f"{shot.name} shows the retired '?' toolbar button; the app builds "
            f"{sorted(labels)}. Re-shoot: uv run python scripts/doc_shots.py {shot.stem}"
        )


@pytest.mark.rule("doc-shots-current")
@pytest.mark.parametrize("shot", _shots(), ids=lambda p: p.stem)
def test_a_shot_carries_no_ones_home_folder(shot: Path):
    """A published image shows a neutral path, never the username of whoever took it."""
    # Arrange
    text = " ".join(_text_runs(shot))

    # Act -- the two spellings a home folder reaches a frame by
    leaks = [
        pattern
        for pattern in (r"[Cc]:\\Users\\(?!<)", r"/home/(?!<)", r"/Users/(?!<)")
        if re.search(pattern, text)
    ]

    # Assert
    assert leaks == [], (
        f"{shot.name} shows a home folder, which ships in the docs. "
        f"scripts/doc_shots.py stages the config at a neutral path; re-shoot "
        f"with it, or stage the hand-taken shot the same way."
    )
