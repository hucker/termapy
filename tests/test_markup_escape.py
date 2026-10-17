"""markup-escape: text reaches the terminal as typed, through every markup path.

Rich drops a ``[word]`` it does not know without complaint, so these tests
render real output through Rich, as the output pane and the CLI console do,
and look for the text, rather than inspecting the markup strings a handler
builds.  ``Text.from_markup`` raising is a failure too: malformed markup is
the other half of the same bug.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from rich.text import Text

from termapy.help_dynamic import green, is_state_line
from termapy.markup import escape_markup, markup_to_plain
from termapy.plugins import (
    CapabilitySet,
    InternalHandle,
    IOHandle,
    PluginContext,
    format_kv_lines,
    interpolate_help,
    resolve_long_help,
)
from termapy.repl import ReplEngine
from termapy.variables import set_context_var, unset_user_var

# Each bites a different way: a lowercase tag, a TOML table header, a stray
# close tag, a Windows path whose trailing backslash would escape the close
# tag after it, a trailing pair Rich's own escape collapses, real markup that
# must stay text, an escaped-looking bracket, and a number.
TRICKY = [
    "[ok] boot done",
    "[[test]] sections",
    "a [/] b",
    "C:\\dir\\",
    "x\\\\",
    "[bold]x[/]",
    "x\\[y]",
    42,
]


@dataclass
class _Screen:
    """What a terminal shows: markup rendered by Rich, text as written."""

    lines: list[str] = field(default_factory=list)

    def write(self, text: str, color: str | None = None) -> None:
        self.lines.append(str(text))

    def write_markup(self, markup: str) -> None:
        self.lines.append(Text.from_markup(markup).plain)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def session(tmp_path):
    """An engine on a throwaway config whose output lands on a rendered _Screen."""
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    cfg = {"echo": False}
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    screen = _Screen()
    # global_root: the checkout has a real termapy_cfg/plugin/; keep it out.
    engine = ReplEngine(cfg, str(config_path), screen.write, global_root=tmp_path)
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        internal=InternalHandle(plugins=engine._plugins, dispatch=engine.dispatch),
        io=IOHandle(_write=screen.write, _write_markup=screen.write_markup),
        capabilities=CapabilitySet(interactive=True, filesystem_unconfined=True),
        dispatch=engine.dispatch,
    )
    engine.set_context(ctx)
    return engine, ctx, screen


@pytest.fixture
def tag_var():
    """A user-variable name, unset afterwards: user variables are process-global."""
    yield "MARKUP_TAG"
    unset_user_var("MARKUP_TAG")


@pytest.mark.parametrize("value", TRICKY)
def test_escaped_text_renders_as_itself(value):
    """markup-escape: escape_markup(x) renders exactly str(x), before a close tag and at the end of a line."""
    # Act
    tagged = Text.from_markup(f"[green]{escape_markup(value)}[/]").plain
    line_end = Text.from_markup(f"[green]x[/] {escape_markup(value)}").plain

    # Assert
    assert tagged == str(value), "before a close tag"
    assert line_end == f"x {value}", (
        "at the end of a line, where Rich's own escape shows a doubled backslash"
    )


def test_markup_to_plain_strips_tags_and_keeps_escaped_text():
    """markup_to_plain gives the text a terminal shows, and returns malformed markup unchanged."""
    # Arrange
    markup = f"[cyan]PORT[/] = {escape_markup('[ok]')}"

    # Act / Assert
    assert markup_to_plain(markup) == "PORT = [ok]", "tags go, escaped text stays"
    assert markup_to_plain("a [/] b") == "a [/] b", "a stray close tag is returned, not raised"


def test_kv_lines_escape_labels_and_values_and_color_by_row():
    """format_kv_lines escapes every label and value; a third row element colors the value."""
    # Arrange
    rows: list[tuple[str, str] | tuple[str, str, str]] = [
        ("Path", "C:\\dir\\"),
        ("State", "[ok]", "red"),
    ]

    # Act
    rendered = [Text.from_markup(line) for line in format_kv_lines(rows)]

    # Assert
    assert [text.plain for text in rendered] == ["  Path : C:\\dir\\", "  State: [ok]"]
    value_styles = {str(span.style) for span in rendered[1].spans if span.start >= len("  State: ")}
    assert value_styles == {"red"}, "the third element colors the value only"


def test_state_line_escapes_its_text_and_is_recognized():
    """help_dynamic.green escapes its text, and /help knows the result as a state line."""
    # Act
    line = green("Connected: COM[3]")

    # Assert
    assert Text.from_markup(line).plain == "Connected: COM[3]"
    assert is_state_line(line), "the whole-line green shape is what /help passes through"
    assert not is_state_line("prose with [green]a word[/] colored"), "only a whole line counts"


def test_a_bracketed_variable_value_lists_as_set(session, tag_var):
    """The original victim: /var.set TAG [ok] boot done once listed as `$(TAG) =  boot done`."""
    # Arrange
    engine, _, screen = session
    engine.dispatch(f"var.set {tag_var} [ok] boot done")
    screen.lines.clear()

    # Act
    engine.dispatch("var")
    listing = screen.text
    screen.lines.clear()
    engine.dispatch(f"var {tag_var}")
    single = screen.text

    # Assert
    expected = f"$({tag_var}) = [ok] boot done"
    assert expected in listing, "the listing shows the value as stored"
    assert expected in single, "so does the one-name form"


def test_an_echoed_command_keeps_its_brackets(session):
    """echo_repl shows a typed command with its brackets: `/print [ok]` echoes as typed."""
    # Arrange
    engine, ctx, _ = session
    ctx.ns("flags")["echo_repl"] = True
    echoed: list[str] = []

    # Act
    engine.dispatch_full(
        "/print [ok]", echo_markup=lambda markup: echoed.append(markup_to_plain(markup))
    )

    # Assert
    assert any(line.endswith("/print [ok]") for line in echoed), (
        f"echo shows the command as typed; got {echoed}"
    )


def test_the_echo_line_expands_vars_marks_the_eol_and_escapes_the_command(tmp_path):
    """format_echo_markup: echo_fmt's $(CFG) expands, an EOL marker follows a device line, and [ok] survives."""
    # Arrange
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    cfg = {"echo_fmt": "[purple]$(CFG)> {cmd}[/]", "eol_markers": True, "eol": "\r"}
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    engine = ReplEngine(cfg, str(config_path), lambda *a, **k: None, global_root=tmp_path)
    # $(CFG) is a frontend's to register (cli.py, app.py, mcp/server.py); a bare engine has none.
    set_context_var("CFG", lambda: config_path.stem)

    # Act
    slash = Text.from_markup(engine.format_echo_markup("/print [ok]")).plain
    device = Text.from_markup(engine.format_echo_markup("AT[ok]", lambda eol: "<CR>")).plain

    # Assert
    assert slash == "rig> /print [ok]", "a slash command echoes with no line-ending marker"
    assert device == "rig> AT[ok]<CR>", "a device line shows the line ending it is sent with"


def _page_text(plugin, ctx: PluginContext) -> list[str]:
    """Every line of prose a command's man page must show: one-liner, synopsis, flags, long help."""
    lines = [interpolate_help(plugin.help, ctx.prefix), plugin.args or ""]
    flags = plugin.flags or {}
    lines += [desc for desc in flags.values() if not (desc.startswith("-") and desc in flags)]
    for line in resolve_long_help(plugin, ctx).splitlines():
        lines.append(markup_to_plain(line) if is_state_line(line) else line)
    return [line.strip() for line in lines if line.strip()]


def test_every_help_page_shows_its_own_text(session):
    """markup-escape: /help <cmd> shows every line of every command's help, so no bracketed word is lost.

    Until 2026-10-13 the page passed help text to Rich as markup: /proto
    said "[] sections" and /port.connect lost its whole Syntax line.
    """
    # Arrange
    engine, ctx, screen = session
    missing: list[str] = []

    # Act
    for name, plugin in sorted(engine._plugins.items()):
        screen.lines.clear()
        engine.dispatch(f"help {name}")
        shown = screen.text
        missing += [f"/{name}: {line}" for line in _page_text(plugin, ctx) if line not in shown]

    # Assert
    assert missing == [], "help text missing from its rendered page:\n  " + "\n  ".join(missing)
