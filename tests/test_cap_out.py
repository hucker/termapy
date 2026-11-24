"""/cap.out and the > / >> redirect: a termapy command's output to a file in cap/, and who owns its flags."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.mcp.server import MCPHost
from termapy.scripting import split_redirect


def _config(tmp_path: Path) -> Path:
    """A config file of its own under a fresh termapy_cfg/."""
    path = tmp_path / "termapy_cfg" / "c" / "c.cfg"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(DEFAULT_CFG), encoding="utf-8")
    return path


@pytest.fixture
def cli(tmp_path) -> CLITerminal:
    """A CLI session on a config of its own, no port open."""
    path = _config(tmp_path)
    made = CLITerminal(dict(DEFAULT_CFG), str(path), no_color=True, term_width=120)
    made.repl.global_root = tmp_path / "termapy_cfg"
    return made


@pytest.fixture
def host(tmp_path) -> Iterator[MCPHost]:
    """An MCP host on a config of its own."""
    path = _config(tmp_path)
    made = MCPHost(dict(DEFAULT_CFG), str(path), verbose=False)
    made.repl.global_root = tmp_path / "termapy_cfg"
    made._fire_app_start()
    yield made


def _cap(session: CLITerminal | MCPHost) -> Path:
    """The session's cap/ folder."""
    return Path(session.ctx.config_path).parent / "cap"


def _call(host: MCPHost, line: str) -> dict:
    """One ``run_command`` call, as the tool makes it."""
    return asyncio.run(host.run_command_async(line, "normal", 8.0))


class TestSplitRedirect:
    @pytest.mark.rule("redirect-is-cap-out")
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("/cfg.list > configs.txt", ("/cfg.list", False, "configs.txt")),
            ("/term.info --json >> info.jsonl", ("/term.info --json", True, "info.jsonl")),
            ('/print "a" > out.txt', ('/print "a"', False, "out.txt")),
            ("/print a > b > c.txt", ("/print a > b", False, "c.txt")),
        ],
    )
    def test_a_trailing_operator_is_a_redirect(self, line, expected):
        """A blank-separated > or >> with one name after it ends the line it redirects."""
        assert split_redirect(line) == expected

    @pytest.mark.rule("redirect-is-cap-out")
    @pytest.mark.parametrize(
        "line",
        ['/print "a > b"', "/print 'a > b'", "/grep a>b", "/expect >", "/print >>> x", "> x.txt"],
    )
    def test_an_operator_that_is_text_is_no_redirect(self, line):
        """Quoted, inside a word, at the very end, tripled or with nothing before it, > is text."""
        assert split_redirect(line) is None


class TestRedirect:
    @pytest.mark.rule("redirect-is-cap-out")
    @pytest.mark.rule("writes-in-cfg-folder")
    def test_a_redirect_writes_the_output_to_cap(self, cli):
        """What /print shows lands in cap/out.txt, and the answer is the file's full path."""
        # Act
        result = cli.repl.dispatch_full("/print hello > out.txt")

        # Assert
        path = _cap(cli) / "out.txt"
        assert result.success is True, result.error
        assert path.read_text(encoding="utf-8") == "hello\n"
        assert result.value == str(path), "the value names the file in full"
        assert Path(result.value).is_absolute()

    @pytest.mark.rule("redirect-is-cap-out")
    def test_a_second_redirect_replaces_the_first(self, cli):
        """> starts the file over each time, so the second run's output is all there is."""
        # Act
        cli.repl.dispatch_full("/print first > out.txt")
        cli.repl.dispatch_full("/print second > out.txt")

        # Assert
        assert (_cap(cli) / "out.txt").read_text(encoding="utf-8") == "second\n"

    @pytest.mark.rule("redirect-is-cap-out")
    def test_append_adds_after_what_is_there(self, cli):
        """>> twice keeps both lines, in order."""
        # Act
        cli.repl.dispatch_full("/print first >> out.txt")
        cli.repl.dispatch_full("/print second >> out.txt")

        # Assert
        assert (_cap(cli) / "out.txt").read_text(encoding="utf-8") == "first\nsecond\n"

    @pytest.mark.rule("redirect-is-cap-out")
    @pytest.mark.rule("command-arg-keeps-its-flags")
    def test_json_appends_one_envelope_a_line(self, cli):
        """The line's own --json writes its envelope, one per run, so the file is JSON Lines."""
        # Act
        cli.repl.dispatch_full("/print one --json >> out.jsonl")
        cli.repl.dispatch_full("/print two --json >> out.jsonl")

        # Assert
        lines = (_cap(cli) / "out.jsonl").read_text(encoding="utf-8").splitlines()
        actual = [(envelope["cmd"], envelope["value"]) for envelope in map(json.loads, lines)]
        expected = [("/print one", "one"), ("/print two", "two")]
        assert actual == expected, "each run's envelope is its own line, naming the line it ran"

    @pytest.mark.rule("redirect-is-cap-out")
    def test_a_quoted_operator_is_text(self, cli):
        """/print "a > b" prints its text and writes no file."""
        # Act
        result = cli.repl.dispatch_full('/print "a > b"')

        # Assert
        assert result.value == '"a > b"', "the whole text is /print's"
        assert not _cap(cli).exists(), "and nothing was written"

    @pytest.mark.rule("redirect-is-cap-out")
    def test_a_raw_args_command_keeps_its_operator(self, cli):
        """/search takes its text as written, so a > in it is part of what it searches for."""
        # Act
        cli.repl.dispatch_full("/search kettle > out.txt")

        # Assert
        assert not _cap(cli).exists(), "the line was a search, not a redirect"

    @pytest.mark.rule("redirect-is-cap-out")
    def test_a_device_line_keeps_its_operator(self, cli):
        """A line for the device is the device's, shell redirect and all."""
        # Act
        result = cli.repl.dispatch_full("ls > out.txt")

        # Assert
        assert result.success is False, "no port is open to send it on"
        assert not _cap(cli).exists(), "and no file was written"

    @pytest.mark.rule("redirect-is-cap-out")
    @pytest.mark.rule("failure-reported-once")
    def test_a_failing_command_leaves_the_file(self, cli, capsys):
        """A mistyped command printed nothing, so the file keeps what it had and the error shows once."""
        # Arrange
        cli.repl.dispatch_full("/print hello > out.txt")
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/nosuch > out.txt")

        # Assert
        assert result.success is False, "the failure is the answer"
        assert (_cap(cli) / "out.txt").read_text(encoding="utf-8") == "hello\n"
        assert capsys.readouterr().out.count("Unknown command: nosuch") == 1

    @pytest.mark.rule("redirect-is-cap-out")
    def test_a_page_lands_flush_and_help_keeps_its_indents(self, cli):
        """The file is the text less termapy's screen gutter: a status page's rows flush, a help page's body indented under its headings."""
        # Act
        cli.repl.dispatch_full("/term.json_fmt > page.txt")
        cli.repl.dispatch_full("/help capture > help.txt")

        # Assert
        page = (_cap(cli) / "page.txt").read_text(encoding="utf-8").splitlines()
        help_lines = (_cap(cli) / "help.txt").read_text(encoding="utf-8").splitlines()
        assert [row.split(":")[0] for row in page] == ["highlight", "indent   "], "rows flush"
        assert help_lines[:2] == ["NAME", help_lines[1]] and help_lines[1].startswith(
            "  capture"
        ), "the heading flush, its body two spaces in, as on screen"

    @pytest.mark.rule("redirect-is-cap-out")
    def test_a_script_line_redirects(self, cli):
        """A .run line is a typed line, so its redirect writes the file too."""
        # Arrange
        run = Path(cli.ctx.config_path).parent / "run"
        run.mkdir()
        (run / "s.run").write_text("/print from a script > s.txt\n", encoding="utf-8")

        # Act
        cli.repl.dispatch_full("/run s")

        # Assert
        assert (_cap(cli) / "s.txt").read_text(encoding="utf-8") == "from a script\n"


class TestCapOut:
    @pytest.mark.rule("command-arg-keeps-its-flags")
    def test_silent_quiets_only_its_own_reply(self, cli, capsys):
        """--silent before cmd= is /cap.out's: its reply goes, and the line still writes the file."""
        # Act
        cli.repl.dispatch_full("/cap.out --silent q.txt cmd=/print kept")

        # Assert
        assert (_cap(cli) / "q.txt").read_text(encoding="utf-8") == "kept\n"
        assert "line to" not in capsys.readouterr().out, "no reply was printed"

    def test_its_own_json_answers_with_the_file(self, cli, capsys):
        """/cap.out --json answers as one envelope: the path as value, the count in data."""
        # Arrange
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full("/cap.out --json out.txt cmd=/print hi")

        # Assert
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        path = str(_cap(cli) / "out.txt")
        actual = (envelope["cmd"].split()[0], envelope["value"], envelope["data"]["lines"])
        expected = ("/cap.out", path, 1)
        assert actual == expected

    def test_a_device_line_is_refused(self, cli):
        """/cap.out runs termapy commands; a device's reply is /cap.text's, and the refusal says so."""
        # Act
        result = cli.repl.dispatch_full("/cap.out out.txt cmd=AT")

        # Assert
        assert result.success is False
        assert "cap.text cmd=" in result.error, (
            "the refusal names the command that captures a reply"
        )
        assert not _cap(cli).exists(), "and nothing was written"

    @pytest.mark.rule("command-arg-keeps-its-flags")
    def test_a_flag_after_cmd_is_not_the_commands(self, cli, capsys):
        """/repeat's --json after cmd= is the repeated line's, so /repeat answers in prose."""
        # Arrange
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full("/repeat count=1 cmd=/print hi --json")
        after = capsys.readouterr().out
        cli.repl.dispatch_full("/repeat --json count=1 cmd=/print hi")
        before = capsys.readouterr().out

        # Assert
        assert '"cmd": "/repeat' not in after, "after cmd= the flag is not /repeat's"
        assert '"cmd": "/repeat' in before, "before cmd= it is"


class TestUnderMcp:
    @pytest.mark.rule("redirect-is-cap-out")
    def test_an_agent_gets_the_person_view(self, host):
        """Under MCP the file holds what a person would see, though the session wants data."""
        # Act
        reply = _call(host, "/app.ver > v.txt")

        # Assert
        assert reply["success"] is True, reply["error"]
        assert (_cap(host) / "v.txt").read_text(encoding="utf-8").startswith("termapy v")

    @pytest.mark.rule("mcp-no-code-writes")
    def test_an_agent_stays_in_cap(self, host):
        """A name that leaves cap/ is refused, naming the variable that would allow it."""
        # Act
        reply = _call(host, "/app.ver > ../out.txt")

        # Assert
        assert reply["success"] is False
        assert "TERMAPY_MCP_FS_UNCONFINED" in reply["error"], reply["error"]
        assert not (_cap(host).parent / "out.txt").exists(), "and nothing was written"

    @pytest.mark.rule("mcp-no-code-writes")
    def test_an_agent_never_appends_outside_the_folder(self, host, tmp_path):
        """With host-wide paths on, >> onto a file outside the config's folder is refused."""
        # Arrange
        host.ctx.capabilities = replace(host.ctx.capabilities, filesystem_unconfined=True)
        host.ctx.sync_capabilities()
        victim = tmp_path / "victim.txt"
        victim.write_text("mine\n", encoding="utf-8")

        # Act
        reply = _call(host, f"/print planted >> {victim}")

        # Assert
        assert "File exists: victim.txt" in reply["error"], reply["error"]
        assert victim.read_text(encoding="utf-8") == "mine\n", "the file is untouched"

    @pytest.mark.rule("mcp-no-auto-run")
    def test_an_agent_cannot_redirect_into_a_config(self, host):
        """A config file is never a redirect's target under MCP."""
        # Act
        reply = _call(host, "/print planted > x.cfg")

        # Assert
        assert "would be a config file" in reply["error"], reply["error"]
        assert not (_cap(host) / "x.cfg").exists(), "and nothing was written"
