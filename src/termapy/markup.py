"""Rich markup: the one home for turning text into markup and back.

Two kinds of string reach termapy's output: **text** (a value, a path, a
device reply, a help sentence) and **markup** (text carrying Rich style tags,
``[green]ok[/]``).  Rich reads ``[word]`` as a tag whenever ``word`` starts
with a lowercase letter, ``#``, ``/`` or ``@``, and drops a tag it does not
know without a word of complaint, so text pasted into markup loses ``[ok]``,
``[path]`` and ``[[test]]`` silently.  The rule (registry ``markup-escape``):
text never reaches a markup parser unescaped.  A value interpolated into
markup goes through :func:`escape_markup`; a name or a function whose name
ends in ``_markup`` holds markup already and is interpolated as it is.

This is Rich's grammar, the one the output pane (a ``RichLog``) and the CLI
console parse.  Textual's widgets (``Static``, ``Label``, ``Button``,
``notify``) parse Textual's own markup, which has no clean escape: a trailing
backslash in a value still swallows the close tag after it.  A widget showing
text is therefore given ``markup=False`` or a ``rich.text.Text``, never an
escaped string (registry ``widget-text-literal``).
"""

from __future__ import annotations

from rich.errors import MarkupError
from rich.markup import escape as _rich_escape
from rich.text import Text


def escape_markup(value: object) -> str:
    """Return ``value`` as markup that renders as exactly its text.

    Accepts any value and converts it with ``str()`` first, so a number
    needs no special case.

    Rich's ``escape`` alone is right only where a tag follows: Rich reads
    backslashes specially only before a ``[``, so the trailing backslash it
    doubles (to keep a Windows path from escaping the close tag after it)
    renders as two at the end of a line, and a trailing pair it leaves
    alone collapses to one before a tag.  Trailing backslashes are
    therefore doubled here and followed by an empty no-op span
    (``[none][/none]``), which makes them render as themselves wherever
    the value lands.

    Args:
        value: The text (or any value) to embed in a markup string.

    Returns:
        The escaped markup.
    """
    text = str(value)
    body = text.rstrip("\\")
    trailing = len(text) - len(body)
    escaped = _rich_escape(body)
    if trailing:
        escaped += "\\" * (2 * trailing) + "[none][/none]"
    return escaped


def markup_to_plain(markup: str) -> str:
    """Flatten Rich markup to the text it renders.

    Rich tags are display syntax, not data: a consumer that is not a
    terminal (the ``--json`` envelope, an MCP agent, the session log) should
    see ``PORT`` where the terminal saw ``[cyan]PORT[/]``.  Malformed markup
    comes back unchanged -- a decorated line beats a dropped one.

    Args:
        markup: A Rich markup string.

    Returns:
        The rendered text without style.
    """
    try:
        return Text.from_markup(markup).plain
    except MarkupError:
        return markup
