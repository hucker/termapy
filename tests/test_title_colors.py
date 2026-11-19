"""The title bar's colors: the one reader of a configured color, and the shade that keeps a button apart from border_color.

Pure functions over Textual's ``Color``; the app-level painting is driven
with Pilot in ``tests/test_app_pilot.py``.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.color import Color

from termapy.title_bar import (
    BLEND_DISTANCE,
    CONNECTION_COLORS,
    TITLE_BUTTON_COLORS,
    color_distance,
    distinct_from,
    named_color,
    parse_color,
)


def _title_colors() -> list[Color]:
    """Every color a title-bar button wears, the theme's resolved."""
    variables = App().get_css_variables()
    names = [*TITLE_BUTTON_COLORS.values(), *CONNECTION_COLORS.values()]
    return [named_color(name, variables) for name in names]


@pytest.mark.rule("cfg-colors-drawable")
class TestParseColor:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("blue", "#0000FF"),
            ("DodgerBlue", "#1E90FF"),
            ("  red ", "#FF0000"),
            ("#ff6600", "#FF6600"),
            ("darkorange", "#FF8C00"),
            ("lightcoral", "#F08080"),
            ("lightgray", "#D3D3D3"),
        ],
    )
    def test_a_css_name_or_hex_reads_in_any_case(self, text, expected):
        """A CSS name or a hex reads as the TUI draws it, in any case or spacing, darkorange and lightcoral included."""
        # Act
        actual = parse_color(text)

        # Assert
        assert actual is not None and actual.hex == expected, f"{text!r} reads as {expected}"

    @pytest.mark.parametrize("text", ["steel", "dark_orange", "light_blue", ""])
    def test_a_name_for_no_color_is_none(self, text):
        """A word that names no CSS color, or a Rich-style name, is no color, so the title bar warns rather than failing to draw."""
        assert parse_color(text) is None, f"{text!r} names no color the TUI can draw"


@pytest.mark.rule("title-bar-stands-apart")
class TestDistinctFrom:
    def test_the_default_bar_keeps_every_button_its_own_color(self):
        """On the default blue bar no title-bar color is shaded, so the default look is unchanged."""
        # Arrange
        bar = parse_color("blue")
        assert bar is not None, "blue is a color"
        expected = _title_colors()

        # Act
        actual = [distinct_from(color, bar) for color in expected]

        # Assert
        assert actual == expected, "every title-bar color already stands apart from blue"

    def test_every_button_stands_apart_on_a_bar_of_its_own_color(self):
        """Whichever title-bar color the user picks for border_color, the button wearing it is shaded clear of the bar."""
        # Arrange
        colors = _title_colors()

        # Act
        distances = [color_distance(distinct_from(color, color), color) for color in colors]

        # Assert
        assert min(distances) >= BLEND_DISTANCE, (
            f"each button stands at least {BLEND_DISTANCE} from a bar of its own color: {distances}"
        )

    def test_the_shade_moves_away_from_the_bar(self):
        """Darker on a bar as light, lighter on a darker one: red on red deepens, crimson on dark red lightens, both still red."""
        # Arrange
        red, crimson, dark_red = (Color.parse(name) for name in ("red", "crimson", "darkred"))

        # Act
        on_red = distinct_from(red, red)
        on_dark_red = distinct_from(crimson, dark_red)

        # Assert
        assert on_red.brightness < red.brightness, "a button as light as its bar darkens"
        assert on_dark_red.brightness > crimson.brightness, "a button lighter than its bar lightens"
        assert on_red.r > on_red.g + on_red.b, "the deeper shade is still red"
        assert on_dark_red.r > on_dark_red.g + on_dark_red.b, "the lighter shade is still red"
