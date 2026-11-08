"""A command declares that its bare form reports state, and /help shows the answer (registry state-read-immutable).

``Command.state`` is validated at construction and copied to ``PluginInfo``;
``ReplEngine.read_state`` runs the bare form silently and keeps its view;
``/help <cmd>`` renders it as CURRENT STATE, capped, with a link to the whole.
The guards that hold every tagged command to its promise are in
``test_architecture.py``; the click on the link is in
``test_app_pilot_targets.py``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from termapy import diag_log
from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import CmdResult, Column, Command, PluginInfo
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.plugins.params import ParamSpec


@pytest.fixture
def cli(tmp_path) -> CLITerminal:
    """A CLI host on the DEMO device, not connected, every built-in registered."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}}
    path = tmp_path / "c" / "c.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(path), no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path  # keep the checkout's plugin/ out of the boot
    return terminal


def _page(cli: CLITerminal, capsys, line: str) -> str:
    """What ``line`` prints."""
    capsys.readouterr()
    cli.repl.dispatch_full(line)
    return capsys.readouterr().out


def _state_lines(page: str) -> list[str]:
    """The lines of a page's CURRENT STATE section, up to the blank line that ends it."""
    lines = page.splitlines()
    rest = lines[lines.index("CURRENT STATE") + 1 :]
    return rest[: rest.index("")] if "" in rest else rest


def _tag(cli: CLITerminal, handler) -> None:
    """Register ``handler`` as the state command ``/thing``."""
    cli.repl.register_hook("thing", "", "A thing.", handler, state=True)


def _answering(result: CmdResult):
    """A handler that answers ``result``, for a shape test."""

    def handler(ctx, args):
        """Answer the prepared result."""
        return result

    return handler


def _unused(ctx, args):
    """A handler for a declaration test."""
    return None


class TestTheDeclaration:
    def test_it_defaults_off_and_reaches_the_plugin_info(self, cli):
        """The loader copies the tag: /term.eol carries it, a command that acts does not, and it is not shows."""
        plugins = cli.repl._plugins
        assert plugins["term.eol"].state is True, "a bare-query setting is tagged"
        assert plugins["term.send"].state is False, "a command that acts is not"
        assert plugins["term.eol"].shows is False, "bare it reports, with an argument it acts"

    def test_a_command_that_acts_cannot_declare_it(self):
        """state-read-immutable: a safety tier above safe says the bare form may change something."""
        with pytest.raises(ValueError, match="state= is for a command whose bare form"):
            Command(name="x", help="h", handler=_unused, state=True, safety="mutable")

    def test_a_picker_cannot_declare_it(self):
        """state-read-immutable: a bare form that opens a dialog cannot be run for a help page."""
        with pytest.raises(ValueError, match="this one opens a picker"):
            Command(name="x", help="h", handler=_unused, state=True, picker="run")

    @pytest.mark.parametrize(
        "declared",
        [{"args": "<name>"}, {"params": [ParamSpec("name", required=True)]}],
        ids=["args", "params"],
    )
    def test_a_required_argument_cannot_declare_it(self, declared):
        """state-read-immutable: a bare call is a usage error when an argument is required, however it is declared."""
        with pytest.raises(ValueError, match="this one needs an argument"):
            Command(name="x", help="h", handler=_unused, state=True, **declared)

    def test_an_optional_argument_can_declare_it(self):
        """A setting's {on|off|toggle} and a {<name>} group leave the bare form runnable."""
        actual = Command(name="x", help="h", args="{<name>}", handler=_unused, state=True)
        assert actual.state is True

    def test_a_hook_is_held_to_the_same_rule(self):
        """A host's command goes through PluginInfo, which refuses what Command refuses."""
        with pytest.raises(ValueError, match="this one needs an argument"):
            PluginInfo(name="x", args="<name>", help="h", handler=_unused, state=True)


class TestThePage:
    def test_the_section_shows_a_setting_as_it_is_now(self, cli, capsys):
        """/help term.eol shows cr, and lf after /term.eol lf: the page reads the session, twice."""
        # Act
        before = _page(cli, capsys, "/help term.eol")
        cli.repl.dispatch_full("/term.eol lf")
        after = _page(cli, capsys, "/help term.eol")

        # Assert
        assert _state_lines(before) == ["  cr"]
        assert _state_lines(after) == ["  lf"], "the page follows the session"
        assert before.index("CURRENT STATE") < before.index("SYNOPSIS"), "beside what it is"
        full = _page(cli, capsys, "/help term.eol --verbose")
        assert full.index("STATUS") < full.index("CURRENT STATE"), "and after a status it shows"

    def test_a_command_without_the_tag_has_no_section(self, cli, capsys):
        """A command that does not declare state shows no CURRENT STATE and is never run for its page."""
        assert "CURRENT STATE" not in _page(cli, capsys, "/help term.send")

    def test_the_developer_page_leaves_it_off(self, cli, capsys):
        """/help.dev is about the code: a tagged command's developer page has no section."""
        assert "CURRENT STATE" not in _page(cli, capsys, "/help.dev term.eol")

    def test_a_long_value_shows_its_start_and_a_link(self, cli, capsys):
        """A multi-line value stops at twenty lines; the line after it says how many more and names the command."""
        # Arrange
        value = "\n".join(f"line{i}" for i in range(25))
        _tag(cli, _answering(CmdResult.ok(value=value)))

        # Act
        actual = _state_lines(_page(cli, capsys, "/help thing"))

        # Assert
        assert actual[:2] == ["  line0", "  line1"]
        assert actual[19:] == ["  line19", "  ... 5 more: /thing"], "the rest is a click away"

    def test_a_very_wide_line_is_cut(self, cli, capsys):
        """One enormous line does not flood the page: it stops with an ellipsis."""
        # Arrange
        _tag(cli, _answering(CmdResult.ok(value="x" * 500)))

        # Act
        actual = _state_lines(_page(cli, capsys, "/help thing"))

        # Assert
        assert actual == ["  " + "x" * 200 + "..."]

    def test_a_record_shows_its_rows(self, cli, capsys):
        """A status page's aligned rows appear as they do when the command runs."""
        # Arrange
        rows = [("Name", "demo"), ("Longer Name", "2")]
        _tag(cli, _answering(CmdResult.record({"name": "demo"}, rows, value="demo")))

        # Act
        actual = _state_lines(_page(cli, capsys, "/help thing"))

        # Assert
        assert actual == ["  Name       : demo", "  Longer Name: 2"]

    def test_a_long_record_shows_its_start_and_a_link(self, cli, capsys):
        """A record past twenty rows shows twenty, then the link."""
        # Arrange
        rows = [(f"row{i}", "x") for i in range(23)]
        _tag(cli, _answering(CmdResult.record({}, rows, value="")))

        # Act
        actual = _state_lines(_page(cli, capsys, "/help thing"))

        # Assert
        assert len(actual) == 21, "twenty rows and the link"
        assert actual[-1] == "  ... 3 more: /thing"

    def test_a_long_table_counts_them_all_and_links_to_the_rest(self, cli, capsys):
        """A list past twenty rows keeps its whole count in the title, shows twenty, then the link."""
        # Arrange
        records = [{"name": f"row{i}"} for i in range(25)]
        columns = (Column("NAME", lambda record: record["name"]),)
        _tag(cli, _answering(CmdResult.table("things", "thing", columns, records)))

        # Act
        page = _page(cli, capsys, "/help thing")
        actual = _state_lines(page)

        # Assert
        assert "25" in actual[0], f"the title counts them all: {actual[0]}"
        assert "  row19" in [line.rstrip() for line in actual], "the twentieth row shows"
        assert "row20" not in page, "the twenty-first does not"
        assert actual[-1] == "  ... 5 more: /thing"

    @pytest.mark.parametrize(
        "result",
        [CmdResult.ok(value=""), CmdResult.ok(value=None)],
        ids=["empty", "none"],
    )
    def test_nothing_to_report_says_so(self, cli, capsys, result):
        """An empty answer reads (none), so the section never stands empty."""
        _tag(cli, _answering(result))
        assert _state_lines(_page(cli, capsys, "/help thing")) == ["  (none)"]

    def test_an_empty_table_is_its_own_empty_line(self, cli, capsys):
        """table-value-is-count: an empty list's value is 0, so the section shows the table's own empty line, never that."""
        _tag(cli, _answering(CmdResult.table("things", "thing", (Column("N", str),), [])))
        assert _state_lines(_page(cli, capsys, "/help thing")) == ["  things - empty"]

    def test_the_gates_answer_is_the_state(self, cli, capsys):
        """A closed port's signal line shows the gate's own words: not connected is the state."""
        actual = _state_lines(_page(cli, capsys, "/help port.dtr"))
        assert actual == [f"  {STATE_MESSAGES['serial_connected']}"]

    def test_a_handler_that_raises_leaves_the_page_whole(self, cli, capsys):
        """A broken state command shows its failure in the section, and the rest of the page still renders."""

        # Arrange
        def broken(ctx, args):
            """Fail the way a plugin bug fails."""
            raise RuntimeError("boom")

        _tag(cli, broken)

        # Act
        page = _page(cli, capsys, "/help thing --verbose")

        # Assert
        # The log's path is long and wraps, so compare without whitespace.
        actual = "".join(_state_lines(page)).replace(" ", "")
        expected = f"Plugin error (thing): RuntimeError: boom{diag_log.traceback_note()}"
        assert actual == expected.replace(" ", ""), "the failure names its type and its log"
        assert "AVAILABLE" in page, "the page goes on after a failed read"

    def test_brackets_in_a_value_stay_literal(self, cli, capsys):
        """markup-escape: a value holding [red] prints as written, never read as a style tag."""
        _tag(cli, _answering(CmdResult.ok(value="[red]x[/] [nope]")))
        assert _state_lines(_page(cli, capsys, "/help thing")) == ["  [red]x[/] [nope]"]

    def test_only_an_exact_page_runs_the_command(self, cli, capsys):
        """The /help landscape and /search never run a state command: only its own page does."""
        # Arrange
        runs: list[str] = []

        def counting(ctx, args):
            """Record the run."""
            runs.append(args)
            return CmdResult.ok(value="on")

        _tag(cli, counting)

        # Act
        _page(cli, capsys, "/help")
        _page(cli, capsys, "/search thing")
        before = len(runs)
        _page(cli, capsys, "/help thing")

        # Assert
        assert before == 0, "a listing never runs it"
        assert len(runs) == 1, "its own page runs it once"


class TestTheRead:
    def test_a_handlers_prose_goes_nowhere_and_the_value_comes_back(self, cli, capsys):
        """A read prints nothing the handler said on any channel and returns its answer."""

        # Arrange
        def chatty(ctx, args):
            """Talk on every channel, then answer."""
            ctx.io.result("result line")
            ctx.io.output("output line")
            ctx.io.output_markup("[green]markup line[/]")
            return CmdResult.ok(value="answer")

        _tag(cli, chatty)
        capsys.readouterr()

        # Act
        result = cli.repl.read_state("thing")

        # Assert
        assert capsys.readouterr().out == "", "a read prints nothing"
        assert result.value == "answer"

    def test_a_failure_is_not_printed(self, cli, capsys):
        """The reader answers with a failure and no red line: the page prints it, once."""
        _tag(cli, _answering(CmdResult.fail(msg="No thing.")))
        capsys.readouterr()

        result = cli.repl.read_state("thing")

        assert capsys.readouterr().out == "", "the caller prints it"
        assert (result.success, result.error) == (False, "No thing.")

    def test_a_table_keeps_its_view(self, cli):
        """Dispatch strips a view on its way out; a read keeps it, because the page renders it."""
        columns = (Column("NAME", lambda record: record["name"]),)
        answer = CmdResult.table("things", "thing", columns, [{"name": "a"}])
        _tag(cli, _answering(answer))
        assert cli.repl.read_state("thing").view is not None, "the page needs the rows"

    def test_two_reads_answer_the_same_and_restore_the_session(self, cli):
        """A read twice in a row gives one answer and leaves level, data preference and flags as found."""
        # Arrange
        ctx = cli.repl.ctx
        ctx.active_flags = {"--outer"}
        ctx.wants_data = True
        ctx._call_level = "quiet"

        # Act
        first = cli.repl.read_state("term.eol")
        second = cli.repl.read_state("term.eol")

        # Assert
        assert first.value == second.value == "cr"
        actual = (ctx.active_flags, ctx.wants_data, ctx._call_level, cli.repl._handler_depth)
        assert actual == ({"--outer"}, True, "quiet", 0), "the outer command is undisturbed"

    def test_a_bare_form_that_runs_help_terminates(self, cli, capsys):
        """A read inside a read is refused, so a tagged command whose bare form opens its own page cannot recurse."""

        # Arrange
        def forwards(ctx, args):
            """Run the help page, as a family root does."""
            return ctx.internal.dispatch("help thing")

        _tag(cli, forwards)

        # Act
        page = _page(cli, capsys, "/help thing")

        # Assert
        assert "CURRENT STATE" in page, "the page renders; the recursion stopped at one level"
        assert page.count("CURRENT STATE") == 1, "the inner page went nowhere"

    def test_the_prototype_note_waits_for_a_real_run(self, cli, capsys):
        """A help page is not a family's first use: the once-a-session note still shows when it runs."""
        # Arrange
        _tag(cli, _answering(CmdResult.ok(value="on")))
        cli.repl._plugins["thing"].maturity = "prototype"

        # Act
        page = _page(cli, capsys, "/help thing")
        run = _page(cli, capsys, "/thing")

        # Assert
        assert "is a prototype" not in page, "reading state says no note"
        assert "is a prototype" in run, "the first real run still does"

    def test_an_observer_sees_the_line_typed_and_not_the_read(self, cli, capsys):
        """A read is part of /help: /run.record and other observers see one line."""
        # Arrange
        seen: list[str] = []
        cli.repl.add_post_dispatch_observer(lambda line, result: seen.append(line))

        # Act
        _page(cli, capsys, "/help term.eol")

        # Assert
        assert seen == ["help term.eol"]

    def test_json_mode_answers_with_one_envelope(self, cli, capsys):
        """/help --json carries the section in output_lines, and the read adds no envelope of its own."""
        # Act
        out = _page(cli, capsys, "/help term.eol --json")

        # Assert
        envelope = json.loads(out)
        assert "CURRENT STATE" in envelope["output_lines"], "the page is in the envelope"
        assert "  cr" in envelope["output_lines"], "with the answer"
        assert out.count('"cmd"') == 1, "one envelope, the outermost command's"

    def test_request_mode_answers_with_one_envelope(self, cli, capsys):
        """Under /term.request on a page is one envelope too, even though it runs a command inside."""
        # Arrange
        cli.repl.dispatch_full("/term.request on")

        # Act
        out = _page(cli, capsys, "/help term.request")

        # Assert
        envelope = json.loads(out)
        assert "  on" in envelope["output_lines"], "the session's answer"
        assert out.count('"cmd"') == 1, "one envelope"


class TestThePortSettings:
    @pytest.mark.parametrize(
        "command", ["baud_rate", "byte_size", "parity", "stop_bits", "flow_control", "mode"]
    )
    def test_a_closed_port_shows_what_the_command_answers_and_says_it_once(
        self, cli, capsys, command
    ):
        """With no port open a setting's page is the command's own answer, from the config, and no second hand-written value."""
        # Arrange -- the command's answer, read as the page reads it: its view's
        # lines (baud_rate's record), else its value
        answer = cli.repl.read_state(f"port.{command}")
        expected = answer.view.lines() if answer.view is not None else [f"  {answer.value}"]

        # Act
        page = _page(cli, capsys, f"/help port.{command}")

        # Assert
        assert _state_lines(page) == expected, "the page and the command agree"
        assert "Current " not in page, "the value appears once, in CURRENT STATE"

    @pytest.mark.parametrize("line", ["dtr", "rts", "cts", "dsr", "ri", "cd"])
    def test_a_closed_ports_signal_line_says_so_and_says_it_once(self, cli, capsys, line):
        """A signal line with no port shows the gate's words, not a ? beside them."""
        page = _page(cli, capsys, f"/help port.{line}")
        assert _state_lines(page) == [f"  {STATE_MESSAGES['serial_connected']}"]
        assert "= ?" not in page, "no hand-written value disagrees with the section"

    def test_the_prose_a_setting_had_is_still_there(self, cli, capsys):
        """Losing the state line loses only that line: parity's values, flow control's warning and mode's forms stay."""
        assert "M - mark (always 1)" in _page(cli, capsys, "/help port.parity")
        assert "a mismatch will" in _page(cli, capsys, "/help port.flow_control")
        assert "115200 N81" in _page(cli, capsys, "/help port.mode")


class TestEveryFrontend:
    def test_connected_to_the_demo_the_page_shows_the_live_values(self, cli, capsys):
        """On an open port the frame settings and the signal lines come from the command, in the CLI."""
        # Arrange
        cli._connect()
        try:
            # Act
            baud = _state_lines(_page(cli, capsys, "/help port.baud_rate"))
            dtr = _state_lines(_page(cli, capsys, "/help port.dtr"))
            record = _state_lines(_page(cli, capsys, "/help term.info"))
        finally:
            cli._disconnect()

        # Assert
        assert (baud, dtr) == (["  115200"], ["  1"])
        assert len(record) == 16, "every row of /term.info: under the cap"

    def test_the_baud_rate_page_says_when_it_is_custom(self, cli, capsys):
        """baud-standard-unless-custom: in custom mode the page and the bare command say (custom); the value stays the number."""
        # Arrange
        cli._connect()
        try:
            cli.repl.dispatch("port.baud_rate 9460 custom=true")

            # Act
            state = _state_lines(_page(cli, capsys, "/help port.baud_rate"))
            bare = cli.repl.dispatch("port.baud_rate")
        finally:
            cli._disconnect()

        # Assert
        assert state == ["  9460 (custom)"], "CURRENT STATE names the mode"
        assert (bare.value, bare.data) == ("9460", {"baud": 9460, "custom": True}), (
            "a script still captures the rate alone"
        )

    def test_mcp_carries_the_section_in_its_output_lines(self, tmp_path):
        """An agent reading /help sees the same section, through the same read."""
        # Arrange
        pytest.importorskip("mcp")
        from termapy.mcp.server import MCPHost

        path = tmp_path / "m" / "m.cfg"
        path.parent.mkdir()
        cfg = {**DEFAULT_CFG}
        path.write_text(json.dumps(cfg))
        host = MCPHost(cfg, str(path), verbose=False)
        host.repl.global_root = tmp_path
        host._fire_app_start()

        # Act -- verbose: MCP's session default is quiet, and a page is normal output
        result = asyncio.run(host.run_command_async("/help term.eol", "verbose", 5.0))

        # Assert
        actual = [line["text"] for line in result["output_lines"]]
        assert "CURRENT STATE" in actual, "the section is there"
        assert "  cr" in actual, "with the session's value"
