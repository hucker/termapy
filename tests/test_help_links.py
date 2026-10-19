"""links-are-data for /help: names link to pages, a page tries its command, and nothing else changes.

The rows of ``/help``, SUBCOMMANDS, candidates and ``/help.plugin`` and
the SEE ALSO line are built from pieces.  In the TUI a command's name is
a link to ``/help <name>``; on its own page, NAME and SYNOPSIS fill the
input line with it, and the bare line runs the bare form (the picker, as
its button opens it).  Everywhere else the pieces are the markup the
line always was.  These tests pin both halves: what a click may run
(never more than a page or a bare form), and the same characters in the
same styles, brackets kept literal, every link pointing at a command
that exists.  The clicking itself is
``tests/test_app_pilot_targets.py::TestClickableOutput``.
"""

from __future__ import annotations

import json

from rich.console import Console
from rich.text import Text

from termapy.builtins.commands.help import _args_pieces, _landscape_row, _pad_markup
from termapy.markup import markup_to_plain
from termapy.plugins import CmdResult, PluginInfo, escape_markup, interpolate_help
from termapy.plugins.views import Link, Piece, pieces_markup


def _styles(markup: str) -> list[tuple[str, str]]:
    """Each character of rendered ``markup`` with the style it is drawn in."""
    console = Console()
    text = Text.from_markup(markup)
    return [
        (char, str(text.get_style_at_offset(console, index)))
        for index, char in enumerate(text.plain)
    ]


def _engine(tmp_path):
    """A real CLI host's engine over a throwaway config, with every built-in registered."""
    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    config_path = tmp_path / "c" / "c.cfg"
    config_path.parent.mkdir()
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "serial is a section"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": ""}}
    config_path.write_text(json.dumps(cfg))
    return CLITerminal(cfg, str(config_path), no_color=True, term_width=120).repl


def _links_of(eng, command: str) -> list[Link]:
    """The links a clicking frontend receives while ``command`` runs."""
    links: list[Link] = []
    eng.ctx.io._write_segments = lambda pieces, color: links.extend(
        piece.link for piece in pieces if piece.link is not None
    )
    eng.dispatch(command)
    return links


def test_a_row_draws_the_same_characters_in_the_same_styles():
    """A command row's markup renders exactly as before: cyan name, padding, plain help."""
    # Arrange
    plugin = PluginInfo(name="cfg.load", args="", help="Load a config.", handler=lambda c, a: None)
    # The row as the old builder wrote it: its own padding formula.
    before = _pad_markup("  [cyan]/cfg.load[/]", 14 + 2) + "  Load a config."

    # Act
    after = pieces_markup(_landscape_row("/", "cfg.load", plugin, 14))

    # Assert
    assert _styles(after) == _styles(before), "the CLI and MCP see what they always saw"


def test_a_search_row_keeps_its_match_underlines():
    """In /help <term> candidates, only the matched part is underlined, name and help alike."""
    # Arrange
    plugin = PluginInfo(name="cfg.load", args="", help="Load a config.", handler=lambda c, a: None)
    before = _pad_markup("  [cyan]/cfg.[u]load[/u][/]", 14 + 2) + "  [u]Load[/u] a config."

    # Act
    after = pieces_markup(_landscape_row("/", "cfg.load", plugin, 14, needles=["load"]))

    # Assert
    assert _styles(after) == _styles(before), "the why-it-matched underline survives the links"


def test_brackets_in_help_text_stay_text(tmp_path):
    """A plugin whose help says [red]x[/] shows those characters, not a red x."""
    # Arrange
    eng = _engine(tmp_path)
    eng.register_plugin(
        PluginInfo(
            name="bracketed",
            args="",
            help="Shows [red]x[/] literally.",
            handler=lambda ctx, args: CmdResult.ok(value=""),
        )
    )
    shown: list[str] = []
    eng.ctx.io._write_markup = lambda markup: shown.append(markup_to_plain(markup))

    # Act
    eng.dispatch("help")

    # Assert
    row = next(line for line in shown if "/bracketed" in line)
    assert "Shows [red]x[/] literally." in row, "the brackets are text"


def _lines_of(eng, command: str) -> list[list[Piece]]:
    """The lines of pieces a clicking frontend receives while ``command`` runs."""
    lines: list[list[Piece]] = []
    eng.ctx.io._write_segments = lambda pieces, color: lines.append(list(pieces))
    eng.dispatch(command)
    return lines


def _line_with(lines: list[list[Piece]], text: str) -> list[Piece]:
    """The first line whose text contains ``text``."""
    return next(line for line in lines if text in "".join(piece.text for piece in line))


def _named(link: Link) -> str:
    """The command a link opens the page of, or runs, or fills in."""
    words = link.command.split()
    return words[1] if words[0] == "help" and len(words) > 1 else words[0]


def test_every_help_link_names_a_command_that_exists(tmp_path):
    """Each link /help and a man page hand out opens, runs or fills a registered command."""
    # Arrange
    eng = _engine(tmp_path)

    # Act
    links = (
        _links_of(eng, "help")
        + _links_of(eng, "help cfg")
        + _links_of(eng, "help cfg.load")
        + _links_of(eng, "help run")
    )

    # Assert
    assert links, "the pages carry links"
    bad = [link for link in links if _named(link) not in eng._plugins]
    assert bad == [], "no link points at a command that is not there"


def test_a_page_runs_nothing_but_pages_and_bare_forms(tmp_path):
    """On every command's page a click runs only /help <name>, a declared bare form or its list's own rows."""
    # Arrange
    eng = _engine(tmp_path)
    plugins = eng._plugins
    bare_forms = {name for name, plugin in plugins.items() if plugin.picker} | {
        f"{name}.{plugin.bare_sub}" for name, plugin in plugins.items() if plugin.bare_sub
    }
    # A help_list's rows do what they do in the list (load a config, run a
    # script, open a screenshot), and its "more" line runs the list.
    lists = {f"{name}.{plugin.help_list}" for name, plugin in plugins.items() if plugin.help_list}
    list_rows = lists | {link.command for name in lists for link in _links_of(eng, name)}

    # Act
    runs = {link.command for name in plugins for link in _links_of(eng, f"help {name}") if link.run}

    # Assert
    bad = sorted(
        command
        for command in runs
        if not command.startswith("help ") and command not in bare_forms | list_rows
    )
    assert bad == [], "one click on /help cap.clear must never delete the captures"


def test_a_bare_form_only_opens_or_shows(tmp_path):
    """A declared bare_sub is a list, a folder, a viewer or an info page: /help's link runs it."""
    # Arrange
    eng = _engine(tmp_path)

    # Act
    actual = {plugin.bare_sub for plugin in eng._plugins.values() if plugin.bare_sub}

    # Assert
    assert actual <= {"list", "explore", "show", "info"}, (
        "a new bare form is run by a click on its page: review that it changes nothing, then add it"
    )


def test_a_command_name_on_its_page_fills_the_input_line(tmp_path):
    """NAME and SYNOPSIS put the command in the input line; one that takes arguments gets a space for them."""
    # Arrange
    eng = _engine(tmp_path)

    # Act
    loaded = _line_with(_lines_of(eng, "help cfg.loaded"), "/cfg.loaded - ")
    run_lines = _lines_of(eng, "help run")
    run_name = _line_with(run_lines, "/run - ")
    run_synopsis = _line_with(run_lines, "/run {")

    # Assert
    assert loaded[1].link == Link("cfg.loaded", run=False), "Enter runs it, nothing runs on its own"
    assert run_name[1].link == Link("run ", run=False), "ready for the file name"
    assert run_synopsis[1].link == Link("run ", run=False), "the synopsis line does the same"


def test_the_bare_line_opens_the_picker_and_runs_the_list(tmp_path):
    """/help run's bare line reads as before; its picker name runs bare /run and /run.list runs itself."""
    # Arrange
    eng = _engine(tmp_path)

    # Act
    line = _line_with(_lines_of(eng, "help run"), "-- bare:")

    # Assert
    actual_text = "".join(piece.text for piece in line)
    expected_text = "  /run -- bare: opens the Run picker in the TUI; elsewhere runs /run.list"
    assert actual_text == expected_text
    actual_links = {piece.text: piece.link for piece in line if piece.link is not None}
    expected_links = {
        "/run": Link("run ", run=False),
        "Run picker": Link("run"),
        "/run.list": Link("run.list"),
    }
    assert actual_links == expected_links, "the picker as its button opens it, the list as typed"


def test_a_synopsis_draws_the_same_characters_in_the_same_styles():
    """A synopsis renders as the old nested tags did: a <required> inside {optional} is yellow in green."""
    # Arrange
    args = "<file> {timeout=<dur>} {a|b} [x]"
    before = "[yellow]<file>[/] [green]{timeout=[yellow]<dur>[/]}[/] [green]{a|b}[/] \\[x]"

    # Act
    after = pieces_markup(_args_pieces(args))

    # Assert
    assert _styles(after) == _styles(before), "the CLI and MCP see what they always saw"


def test_the_page_lines_draw_the_same_characters_in_the_same_styles(tmp_path):
    """NAME, SYNOPSIS and the bare line of /help run render as their old markup did."""
    # Arrange
    eng = _engine(tmp_path)
    help_markup = escape_markup(interpolate_help(eng._plugins["run"].help, "/"))
    before = [
        f"  [cyan]/run[/] - {help_markup}",
        "  [cyan]/run[/] [green]{filename}[/]",
        "  [cyan]/run[/] -- bare: opens the Run picker in the TUI; elsewhere runs /run.list",
    ]

    # Act
    lines = _lines_of(eng, "help run")
    after = [pieces_markup(_line_with(lines, start)) for start in ("/run - ", "/run {", "-- bare:")]

    # Assert
    assert [_styles(line) for line in after] == [_styles(line) for line in before], (
        "only the TUI sees a difference: the links"
    )


def test_help_cfg_ends_with_the_configs_indented_each_loading_on_a_click(tmp_path, monkeypatch):
    """/help cfg, and /cfg.help alike, end with /cfg.list's table 2 spaces in, each name loading its config."""
    # Arrange
    cfg_root = tmp_path / "cfg_root"
    (cfg_root / "bench").mkdir(parents=True)
    (cfg_root / "bench" / "bench.cfg").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TERMAPY_CFG_DIR", str(cfg_root))
    eng = _engine(tmp_path)

    # Act
    value = eng.dispatch("cfg.help").value
    help_lines = _lines_of(eng, "help cfg")
    cfg_help_lines = _lines_of(eng, "cfg.help")

    # Assert
    texts = ["".join(piece.text for piece in line) for line in help_lines]
    assert texts[-4].strip() == "cfg_root/ - 1 config", "the list's own title closes the page"
    assert all(text.startswith("  ") for text in texts[-4:]), "indented like the rows above it"
    row_links = [piece.link for piece in help_lines[-1] if piece.link is not None]
    assert row_links == [Link("cfg.load bench")], "the listed config loads on a click"
    assert cfg_help_lines == help_lines, "/cfg.help is the same page"
    assert value == "cfg", "the resolved name, so a script can confirm the match"


def test_the_list_is_left_off_when_it_cannot_run_or_is_empty(tmp_path):
    """/help run shows SCRIPTS only with a config loaded and a script in its folder."""
    # Arrange
    from termapy.defaults import DEFAULT_CFG
    from termapy.repl import ReplEngine

    eng = _engine(tmp_path)
    shown: list[str] = []
    eng.ctx.io._write_markup = lambda markup: shown.append(markup_to_plain(markup))
    no_config = ReplEngine(dict(DEFAULT_CFG), "", lambda text, color=None: None)
    unconfigured: list[str] = []
    no_config.ctx.io._write_markup = lambda markup: unconfigured.append(markup_to_plain(markup))

    # Act
    eng.dispatch("help run")
    empty = "SCRIPTS" in shown
    folder = tmp_path / "c" / "run"
    folder.mkdir()
    (folder / "hello.run").write_text("/print hi\n", encoding="utf-8")
    eng.dispatch("help run")
    listed = "SCRIPTS" in shown
    no_config.dispatch("help run")

    # Assert
    assert not empty, "an empty folder adds nothing to the page"
    assert listed, "a script in the folder is listed"
    assert "SCRIPTS" not in unconfigured, "with no config the list cannot run, and is left off"


def test_help_plugin_links_each_command_to_its_page(tmp_path):
    """/help.plugin's rows link each listed command to /help <name>, one link per row."""
    # Arrange
    eng = _engine(tmp_path)

    # Act
    commands = {link.command for link in _links_of(eng, "help.plugin")}

    # Assert
    assert "help cfg" in commands, "a built-in's row opens its page"
    assert all(command.removeprefix("help ") in eng._plugins for command in commands), (
        "every row points at a registered command"
    )


def test_see_also_links_the_siblings_and_the_parent(tmp_path):
    """A subcommand's SEE ALSO links each sibling and the parent."""
    # Arrange
    eng = _engine(tmp_path)

    # Act
    commands = [link.command for link in _links_of(eng, "help cfg.load")]

    # Assert
    assert "help cfg.list" in commands, "a sibling"
    assert commands[-1] == "help cfg", "the parent closes the SEE ALSO line"


def _mcp_host(tmp_path):
    """A started MCP host over a throwaway config: unlike the CLI, it keeps a session log."""
    from termapy.defaults import default_cfg
    from termapy.mcp.server import MCPHost

    cfg = default_cfg()
    cfg["serial"]["port"] = ""
    config_path = tmp_path / "m" / "m.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    host = MCPHost(cfg, str(config_path), verbose=False)
    host.repl.global_root = tmp_path
    host._fire_app_start()
    return host


def test_help_log_ends_with_the_session_log_opening_on_a_click(tmp_path):
    """/help log ends with /log.list's one row, the session log, and a click on it runs /log.show."""
    # Arrange -- MCP sessions default to quiet; ask at normal, as a call can.
    eng = _mcp_host(tmp_path).repl
    eng.ctx.ns("flags")["output_level"] = "normal"

    # Act
    lines = _lines_of(eng, "help log")
    eng.ctx.wants_data = True
    listed = eng.dispatch("log.list")

    # Assert
    row = lines[-1]
    assert "session.log" in "".join(piece.text for piece in row), "the log the frontend keeps"
    actual_links = [piece.link for piece in row if piece.link is not None]
    assert actual_links == [Link("log.show")], "a click opens it, as the Log button does"
    assert [record["name"] for record in listed.data] == ["session.log"], "one record, as data"


def test_a_long_list_shows_its_newest_ten_and_links_the_rest(tmp_path):
    """/help run with twelve scripts shows ten rows, a title counting twelve, and a link to /run.list."""
    # Arrange
    eng = _engine(tmp_path)
    folder = tmp_path / "c" / "run"
    folder.mkdir()
    for index in range(12):
        (folder / f"s{index:02}.run").write_text("/print hi\n", encoding="utf-8")

    # Act
    lines = _lines_of(eng, "help run")

    # Assert
    texts = ["".join(piece.text for piece in line) for line in lines]
    start = next(i for i, text in enumerate(texts) if text.strip().startswith("run/ - "))
    assert texts[start].strip() == "run/ - 12 scripts", "the title counts them all"
    rows = texts[start + 3 : -1]
    assert len(rows) == 10, "the newest ten, so a big folder does not bury the page"
    assert texts[-1].strip() == "... 2 more: /run.list", "the rest are one click away"
    assert lines[-1][-1].link == Link("run.list"), "that click runs the whole list"


def test_mcp_gets_the_help_text_plain(tmp_path):
    """Over MCP a help page's lines are the plain text, no tags and no links."""
    # Arrange
    import asyncio

    host = _mcp_host(tmp_path)

    # Act
    result = asyncio.run(host.run_command_async("/help cfg", "verbose", 5.0))

    # Assert
    lines = [
        entry["text"] if isinstance(entry, dict) else entry for entry in result["output_lines"]
    ]
    row = next(line for line in lines if "/cfg.load" in line)
    assert "[" not in row, "no markup reaches the agent"
    assert row.split()[0] == "/cfg.load", "the row reads as it always did"
