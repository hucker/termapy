"""The global idioms: what reads the same on every command, one row each.

Any command takes ``--json`` and ``--quiet``, and every boolean setting reads
the same words, so no command declares them and no command's page explains
them.  This table does, for every surface: ``/help cycle``, ``/help.idioms``, and
the links and tooltips in a synopsis (registry ``idioms-one-table``).  A fact
another module owns is read from it, never typed here.  A new idiom is a row.
"""

from __future__ import annotations

from dataclasses import dataclass

from termapy.defaults import OUTPUT_LEVELS
from termapy.plugins.output_levels import LEVEL_FLAGS
from termapy.scripting import BOOL_TOKENS_HELP

_FLAGS_TEXT = " ".join(LEVEL_FLAGS)


@dataclass(frozen=True)
class Idiom:
    """One idiom.

    Attributes:
        name: What ``/help`` takes, and the row's name in ``/help.idioms``.
        summary: One line, shown in its tooltip and in ``/help.idioms``.
        page: The page text, prose with ``{prefix}``.
        aliases: Other words that open the same page.
        tokens: The synopsis words it explains (``{on|off|toggle}``), which
            ``/help`` links to the page, and lists the commands taking them.
        flags: The flags it explains, which any command takes.
    """

    name: str
    summary: str
    page: str
    aliases: tuple[str, ...] = ()
    tokens: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


IDIOMS: tuple[Idiom, ...] = (
    Idiom(
        name="boolean",
        aliases=("bool",),
        tokens=("on", "off"),
        summary=f"A yes or no, in any of these words: {BOOL_TOKENS_HELP}.",
        page=f"""\
Wherever termapy reads a yes or no from text, it reads the same words, in any
case: {BOOL_TOKENS_HELP}.

That holds for a setting, a key=value keyword, a script and an environment
variable. Any other word is an error that names these, never a silent off.""",
    ),
    Idiom(
        name="toggle",
        tokens=("toggle",),
        summary="Flip a yes or no setting to its other value; made for a custom button.",
        page="""\
{prefix}term.hex toggle turns hex display on if it is off, and off if it is on.

It is most useful on a custom button, where one click switches a mode you change
often. Give the button this command ({prefix}help custom_buttons has the rest):

  "command": "{prefix}term.hex toggle"

On, off or any other boolean word sets the value outright, and with no argument
the setting only shows it, so nothing flips by accident.""",
    ),
    Idiom(
        name="cycle",
        tokens=("cycle",),
        summary="Move a setting to its next value, wrapping at the end; made for a custom button.",
        page="""\
{prefix}term.output cycle moves the output level to its next value, in the order the
synopsis lists them, and the last value wraps back to the first.

It is most useful on a custom button, where each click steps to the next value.
Give the button this command ({prefix}help custom_buttons has the rest):

  "command": "{prefix}term.output cycle"

A value sets the setting outright, and with no argument it only shows it.""",
    ),
    Idiom(
        name="bare",
        summary="A setting with no argument shows its value and changes nothing.",
        page="""\
{prefix}term.eol answers with the line ending; {prefix}term.eol lf sets it. Asking is
always safe, so a script can read a setting without moving it.

A command that is not a setting has its own bare form, and its page says what
that is: {prefix}run opens the script picker, or lists the scripts where nothing
can pick.""",
    ),
    Idiom(
        name="json",
        flags=("--json",),
        summary="Answer this one call as a JSON object, for a script or an agent.",
        page="""\
Any command takes --json, and so does a line for the device. That one call
answers as one JSON object: cmd, success, error, value, data, output_lines.

  {prefix}port.list --json
  AT+INFO --json

{prefix}term.request on does the same for the whole session, and {prefix}term.json sets
how the JSON prints. The flag is taken off the line wherever it appears, so a
command's text cannot carry it. Typed as {prefix}help --json it means help as JSON,
which is why this page is {prefix}help json.""",
    ),
    Idiom(
        name="level",
        aliases=OUTPUT_LEVELS,
        flags=tuple(LEVEL_FLAGS),
        summary="How much one call prints, from nothing to all its detail.",
        page=f"""\
Any command takes one of these, for that one call: {_FLAGS_TEXT}.
A .quiet suffix on the name says the same thing.

  {{prefix}}port.list --quiet
  {{prefix}}port.list.quiet

silent prints nothing, quiet only the result, normal adds the command's other
output, and verbose adds its detail: on a help page, the whole page. {{prefix}}term.output sets the level for the
session. The flag is taken off the line wherever it appears, so a command's
text cannot carry it.""",
    ),
)

# The flags any command takes, as the names they are typed with.
ANY_COMMAND_FLAGS: tuple[str, ...] = tuple(flag for idiom in IDIOMS for flag in idiom.flags)

_BY_WORD: dict[str, Idiom] = {
    word: idiom for idiom in IDIOMS for word in (idiom.name, *idiom.aliases, *idiom.tokens)
}

#: Every word that opens a page, for a did-you-mean.
IDIOM_WORDS: tuple[str, ...] = tuple(_BY_WORD)

#: A synopsis word and the idiom explaining it.
IDIOM_TOKENS: dict[str, Idiom] = {token: idiom for idiom in IDIOMS for token in idiom.tokens}


def idiom_for(word: str) -> Idiom | None:
    """The idiom ``word`` names, with or without its dashes, or None.

    Args:
        word: What the user typed (``cycle``, ``--quiet``).

    Returns:
        The row, or None when the word names no idiom.
    """
    return _BY_WORD.get(word.lstrip("-").lower())


__all__ = [
    "ANY_COMMAND_FLAGS",
    "IDIOMS",
    "IDIOM_TOKENS",
    "IDIOM_WORDS",
    "Idiom",
    "idiom_for",
]
