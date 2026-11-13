"""LinkLog -- the output pane, whose links say on hover what a click does.

A link in the pane (registry ``links-are-data``) carries its id in its
style's meta, beside the ``@click`` action.  Moving the mouse over one
asks ``describe(link_id)`` for a line saying what a click does -- the
command it runs, or the one it puts in the input line -- and shows it as
the pane's tooltip; anywhere else the tooltip is cleared.  The text is
asked for at hover time, so it reads the live prefix and says so when
the link has expired.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.style import Style
from rich.text import Span, Text
from textual import events
from textual.message import Message

from termapy.widgets.selectable_log import SelectableRichLog

# The style-meta key holding a link's id (``app._write_link_segments``).
LINK_META = "link"


def _plain_style(style: str | Style) -> Style:
    """``style`` with its colors dropped and its click meta kept.

    Rich's ``Style.without_color`` also drops ``meta``, which is where a
    link's click action lives, so the meta goes back on.
    """
    parsed = Style.parse(style) if isinstance(style, str) else style
    dropped = parsed.without_color
    return dropped + Style(meta=parsed.meta) if parsed.meta else dropped


def without_color(text: Text) -> Text:
    """``text`` with every color dropped and everything else kept: bold, italic, underline, links and their click actions.

    What the output pane writes when ``/term.color`` is off, so termapy's
    own markup goes plain the way the CLI console goes plain under
    ``--no-color``, and a help page's links still click.
    """
    plain = Text(
        text.plain,
        style=_plain_style(text.style),
        justify=text.justify,
        overflow=text.overflow,
        no_wrap=text.no_wrap,
        end=text.end,
        tab_size=text.tab_size,
    )
    plain.spans = [Span(span.start, span.end, _plain_style(span.style)) for span in text.spans]
    return plain


class LinkLog(SelectableRichLog):
    """A selectable RichLog whose links explain themselves on hover.

    Args:
        describe: Returns the tooltip for a link id: a ``Text``, never a
            markup string, because a command can hold [brackets].  Omitted,
            the pane shows no tooltips.
        **kwargs: Passed to ``RichLog``.
    """

    class Width(Message):
        """The pane's row width changed: how many columns a table may take.

        Posted on every resize, so the app can hold the width for the
        dispatch workers, which must not read a widget (registry
        ``commands-off-main``).  ``Resize`` itself does not bubble.

        Attributes:
            columns: The usable width in cells, the scrollbar's gutter
                reserved.
        """

        def __init__(self, columns: int) -> None:
            super().__init__()
            self.columns = columns

    def __init__(self, *, describe: Callable[[int], Text] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._describe = describe
        self._shown: Text | None = None

    def on_resize(self, event: events.Resize) -> None:
        """Tell the app the width a table row may take now, after RichLog's own resize work."""
        super().on_resize(event)
        # content_size includes the vertical scrollbar's gutter; reserve it
        # whether or not the scrollbar shows (as the port picker does).
        width = self.content_size.width - self.styles.scrollbar_size_vertical
        if width > 0:
            self.post_message(self.Width(width))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Show what the link under the pointer does, or nothing off a link."""
        link_id = event.style.meta.get(LINK_META) if event.style else None
        shown = (
            self._describe(link_id) if link_id is not None and self._describe is not None else None
        )
        if shown == self._shown:
            return  # unchanged: re-setting would restart the tooltip's delay
        self._shown = shown
        self.tooltip = shown
