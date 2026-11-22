"""Syntax highlighting for the editors through Pygments: text and a lexer name become Textual's per-line highlight map, in UTF-8 byte offsets.

Textual's ``TextArea`` colors a line from ``(start, end, name)`` spans: byte
offsets into the line's UTF-8 (``None`` runs to the line's end) and one of its
theme's highlight names (``string``, ``number``, ``json.label``, ...).  Textual
fills that map from a tree-sitter query; termapy fills it here from a Pygments
lexer, so the editors need no compiled parser (decision 2026-11-21).  This
module is Textual-free: it knows the map's shape, not the widget.

Three lexers: ``json`` for a config, ``toml`` for a ``.pro`` protocol script
and ``run`` for a ``.run`` script, the last a small lexer of termapy's own
grammar (a ``#`` comment line, a ``/command``, a ``$(NAME)`` variable, the
``<-`` capture, quoted text and numbers; a device line stays plain).
"""

from __future__ import annotations

from collections import defaultdict
from functools import cache

from pygments.lexer import Lexer, RegexLexer, bygroups

# The defining modules, not the ``pygments.lexers`` facade, which resolves names at run time
# and so hides them from the type checker.
from pygments.lexers.configs import TOMLLexer
from pygments.lexers.data import JsonLexer
from pygments.token import (
    Comment,
    Keyword,
    Literal,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    _TokenType,
)

Span = tuple[int, int | None, str]
"""One colored run of a line: its UTF-8 byte start, byte end (``None`` to the line's end) and highlight name."""


class RunLexer(RegexLexer):
    """termapy's ``.run`` script grammar, enough of it to color an editor.

    A line that starts with ``#`` is a comment (the first one is the file's
    title); a line that starts with ``/`` is a REPL command, as is the command
    after a ``<-`` capture; ``$(NAME)`` is a variable unless the ``$`` is
    escaped; quoted text and numbers (``300ms`` included) are what they look
    like.  Everything else, a device line above all, stays plain.
    """

    name = "termapy run script"
    aliases = ["run"]
    tokens = {
        "root": [
            (r"^[ \t]*#.*$", Comment.Single),
            (r"\\\$", Text),
            (r"\$\([^)\n]*\)", Name.Variable),
            (r"(<-)([ \t]*)(/[\w.]+)", bygroups(Operator, Text, Name.Function)),
            (r"^[ \t]*/[\w.]+", Name.Function),
            (r'"[^"\n]*"', String.Double),
            (r"\b\d+(?:\.\d+)?(?:ms|s|m|h)?\b", Number),
            (r"\s+", Text),
            (r'[^\s$"<#\\]+', Text),
            (r".", Text),
        ],
    }


_LEXERS: dict[str, type[Lexer]] = {"json": JsonLexer, "toml": TOMLLexer, "run": RunLexer}

# The literal words JSON and TOML spell as keyword constants, and the highlight name of each.
_LITERALS = {"null": "json.null", "true": "boolean.true", "false": "boolean.false"}


@cache
def _lexer(name: str) -> Lexer:
    """The one instance of the named lexer; ``KeyError`` names an unknown one."""
    return _LEXERS[name]()


def _name(lexer: str, ttype: _TokenType, value: str) -> str | None:
    """The Textual highlight name for a Pygments token, or ``None`` for plain text.

    The names are the ones Textual's own tree-sitter queries assigned (a JSON
    key is ``json.label``, a TOML key ``toml.type``, ``null`` is ``json.null``),
    except that ``true`` and ``false`` are ``boolean.true`` and ``boolean.false``:
    Rich's console colors them apart, and the editors' theme follows Rich.
    """
    if ttype in Comment:
        return "comment"
    if ttype in String:
        return "string"
    if ttype in Number.Float:
        return "float"
    if ttype in Number:
        return "number"
    if ttype in Literal.Date:
        return "toml.datetime"
    if ttype in Keyword.Constant:
        return _LITERALS.get(value, "keyword")
    if ttype in Keyword:
        return "keyword"
    if ttype in Name.Tag:
        return "json.label"
    if ttype in Name.Variable:
        return "variable"
    if ttype in Name.Function:
        return "function"
    if ttype in Name and lexer == "toml":
        return "toml.type"
    if ttype in Operator:
        return "operator"
    if ttype in Punctuation:
        return "punctuation.bracket" if value.strip("[]{}()") == "" else "punctuation.delimiter"
    return None


def highlights(text: str, lexer: str) -> dict[int, list[Span]]:
    """Textual's highlight map for ``text`` under ``lexer``: row -> its colored spans, in UTF-8 bytes.

    A token that spans lines becomes one span per row, the first running to
    its line's end and the middle ones whole; whitespace is never a span.

    Args:
        text: The document, lines joined by ``\\n``.
        lexer: ``json``, ``toml`` or ``run``.

    Raises:
        KeyError: ``lexer`` is not one of the three.
    """
    rows: defaultdict[int, list[Span]] = defaultdict(list)
    row = 0
    column = 0
    for _, ttype, value in _lexer(lexer).get_tokens_unprocessed(text):
        name = _name(lexer, ttype, value)
        pieces = value.split("\n")
        for index, piece in enumerate(pieces):
            last = index == len(pieces) - 1
            width = len(piece.encode("utf-8"))
            if name is not None and piece.strip():
                rows[row].append((column, column + width if last else None, name))
            if last:
                column += width
            else:
                row += 1
                column = 0
    return dict(rows)
