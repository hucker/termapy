"""Regression tests for /search and cli output rendering markup-safety.

Bug: when /search's `_highlight()` cuts a snippet that includes part
of a long_help's own Rich markup (e.g. the closing ``[/]`` of a
``state_line()`` output), the resulting string had an orphan tag that
broke Rich's parser.  The MarkupError then cascaded through
``cli.status``, which wrapped the error message in ``[red]...[/]``
without escaping the literal ``[/]`` in the message -- second
MarkupError, visible to the user as a traceback.

These tests pin both fixes: ``_highlight`` escapes user text before
splicing, and ``cli.write``/``cli.status`` escape user text before
wrapping in their color tag.
"""

from __future__ import annotations

import io

from rich.console import Console
from rich.errors import MarkupError

from termapy.builtins.commands.search import _highlight


class TestHighlightEscapesUserMarkup:
    """``_highlight()`` must produce parseable Rich markup even when
    the source text contains its own ``[...]`` tags."""

    def test_snippet_with_orphan_closing_tag_renders(self):
        # Arrange -- text mimics what `state_line("baud rate", 115200)` plus
        # a follow-up description produces.  The match is far enough into
        # the text that the context window slices through the closing
        # ``[/]`` without grabbing the matching ``[green]``.
        text = (
            "[green]Current baud rate = 115200[/]\n\n"
            "Combined form for baud + mode triple..."
        )
        start = text.index("baud", text.index("Combined"))
        end = start + len("baud")

        # Act
        snippet = _highlight(text, (start, end))

        # Assert -- Rich must be able to parse the result without
        # MarkupError.  The exact byte-for-byte snippet content isn't
        # the point; the contract is "valid Rich markup."
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        try:
            console.print(snippet)
        except MarkupError as e:
            raise AssertionError(
                f"_highlight produced unparseable markup: {snippet!r}\n"
                f"Rich error: {e}"
            ) from e

    def test_snippet_with_literal_bracket_pair_renders(self):
        # Arrange -- text contains a literal "[/]" that isn't a valid
        # markup pair, plus a match elsewhere.  Without escaping, the
        # snippet would carry the bare "[/]" into Rich.
        text = "before stuff [/] middle marker target end stuff"
        start = text.index("target")
        end = start + len("target")

        # Act
        snippet = _highlight(text, (start, end))

        # Assert
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        try:
            console.print(snippet)
        except MarkupError as e:
            raise AssertionError(
                f"_highlight produced unparseable markup: {snippet!r}\n"
                f"Rich error: {e}"
            ) from e

    def test_match_itself_wrapped_in_yellow(self):
        # Arrange -- a clean text with a clear match.
        text = "the quick brown fox jumps"
        start = text.index("brown")
        end = start + len("brown")

        # Act
        snippet = _highlight(text, (start, end))

        # Assert -- the wrapper around the matched token is still there
        # (escaping doesn't touch the wrapper tags _highlight itself adds).
        assert "[yellow]brown[/]" in snippet, (
            f"matched token should be wrapped in yellow; got {snippet!r}"
        )


class TestCliStatusEscapesUserText:
    """``cli.status()`` and ``cli.write()`` must not let user-supplied
    text break the color-tag wrapper.  Regression for the cascade
    where ``err_msg`` contained literal ``[/]`` (a Rich MarkupError
    serialized into a message) and the wrapper broke when rendering it.
    """

    def _make_cli(self):
        # Build the minimum CLITerminal surface we need for write/status,
        # bypassing the full constructor (which wants a real cfg + repl).
        from termapy.cli import CLITerminal

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        instance = CLITerminal.__new__(CLITerminal)
        instance.console = console
        return instance, buf

    def test_status_with_literal_closing_tag_in_text(self):
        # Arrange -- text mimics the cascade-failure case: an error
        # message that mentions "[/]" literally.
        cli, buf = self._make_cli()
        text = "closing tag '[/]' at position 42 has nothing to close"

        # Act -- must not raise MarkupError.
        try:
            cli.status(text, "red")
        except MarkupError as e:
            raise AssertionError(
                f"cli.status raised MarkupError on text with literal '[/]'; "
                f"got {e}"
            ) from e

        # Assert -- some output was produced.
        out = buf.getvalue()
        assert "closing tag" in out, (
            f"the message text reached the console; got {out!r}"
        )

    def test_write_with_literal_closing_tag_in_text(self):
        # Arrange / Act -- same scenario but via write().
        cli, buf = self._make_cli()
        text = "value contains [/] inside"
        try:
            cli.write(text, "yellow")
        except MarkupError as e:
            raise AssertionError(
                f"cli.write raised MarkupError on text with literal '[/]'; "
                f"got {e}"
            ) from e

        # Assert
        out = buf.getvalue()
        assert "value contains" in out, (
            f"the message text reached the console; got {out!r}"
        )

    def test_write_uncolored_still_renders_markup(self):
        # Arrange / Act -- uncolored path still parses markup (callers
        # rely on this for rich content; markup-aware callers use
        # write_markup explicitly, but plain write() with no color
        # historically does parse).
        cli, buf = self._make_cli()
        cli.write("[bold]hi[/]")

        # Assert -- escaping is NOT applied on the no-color branch, so
        # ``[bold]hi[/]`` parses and renders just the visible "hi".
        out = buf.getvalue()
        assert "hi" in out and "[bold]" not in out, (
            f"uncolored write() still parses markup; got {out!r}"
        )
