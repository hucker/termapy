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

import re

# Rich is imported inside each function: ``termapy.plugins`` imports this
# module, and the print-and-exit flags (``--ports``) reach ``plugins``
# through ``port_control`` -- they must load no UI framework, Rich included
# (``cli_flags``' contract), and only these functions ever need it.


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
    from rich.markup import escape

    text = str(value)
    body = text.rstrip("\\")
    trailing = len(text) - len(body)
    escaped = escape(body)
    if trailing:
        escaped += "\\" * (2 * trailing) + "[none][/none]"
    return escaped


def json_markup(data: object, indent: int | None = 2, *, ensure_ascii: bool = True) -> str:
    """``data`` as highlighted JSON in Rich markup (``json_fmt.highlight``).

    Rich's JSON highlighter escapes what it emits, so a ``[`` inside a
    string value stays literal and ``markup_to_plain`` of the result is
    exactly the JSON text.

    Args:
        data: A JSON-shaped value; anything else is ``str()``-ed.
        indent: Spaces per level; ``None`` for one line.
        ensure_ascii: Escape non-ASCII characters, as ``json.dumps`` does
            by default, so the colored form reads the same characters as
            termapy's plain one.

    Returns:
        The markup.
    """
    from rich.json import JSON

    return JSON.from_data(data, indent=indent, ensure_ascii=ensure_ascii, default=str).text.markup


def received_json_markup(line: str, indent: int, highlight: bool) -> str | None:
    """A received line that is a JSON object or array, laid out for the screen.

    A device that answers in JSON reads like termapy's own JSON: ``indent``
    spaces per level (0 keeps one line), highlighted when ``highlight`` is
    on.  Display only: the session log, a capture and MCP keep the line as
    received.  Characters are shown as the device sent them, not as ``\\u``
    escapes.

    Args:
        line: One received line, ANSI already stripped.
        indent: Spaces per level, ``json_fmt.indent``; 0 for one line.
        highlight: Whether to highlight it (``json_fmt.highlight`` with color on).

    Returns:
        The markup to show in place of the line, or None to show the line as
        received: with no indent and no highlight, and for any line that is
        not a whole JSON object or array.
    """
    if not indent and not highlight:
        return None
    text = line.strip()
    if not text.startswith(("{", "[")):
        return None
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return None
    spaces = indent or None
    if highlight:
        return json_markup(data, indent=spaces, ensure_ascii=False)
    return escape_markup(json.dumps(data, indent=spaces, ensure_ascii=False))


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
    from rich.errors import MarkupError
    from rich.text import Text

    try:
        return Text.from_markup(markup).plain
    except MarkupError:
        return markup


def match_parts(text: str, needles: str | list[str] | None) -> list[tuple[str, bool]]:
    """``text`` cut into ``(part, matched)`` around every case-insensitive needle.

    The split behind :func:`underline_markup` and the clickable help rows,
    which style their matched parts themselves.  No needle, or no text,
    gives the text as one unmatched part.

    Args:
        text: The text to cut.
        needles: One needle or several; empty ones are ignored.

    Returns:
        The parts in order, each with whether a needle matched it.
    """
    if isinstance(needles, str):
        needles = [needles]
    needles = [needle for needle in needles or [] if needle]
    if not needles or not text:
        return [(text, False)] if text else []
    pattern = re.compile("|".join(re.escape(needle) for needle in needles), re.IGNORECASE)
    parts: list[tuple[str, bool]] = []
    last = 0
    for match in pattern.finditer(text):
        if match.start() > last:
            parts.append((text[last : match.start()], False))
        parts.append((match.group(), True))
        last = match.end()
    if last < len(text):
        parts.append((text[last:], False))
    return parts


def underline_markup(text: str, needles: str | list[str]) -> str:
    """``text`` escaped, every case-insensitive occurrence of a needle in ``[u]...[/u]``.

    How ``/help``'s candidate list and ``/search``'s hits say why a row
    matched.

    Args:
        text: The text to show.
        needles: The search terms to underline.

    Returns:
        The markup.
    """
    return "".join(
        f"[u]{escape_markup(part)}[/u]" if matched else escape_markup(part)
        for part, matched in match_parts(text, needles)
    )
