"""Unit tests for CLITerminal - hooks, helpers, output, and dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from termapy.cli import CLITerminal, PlainFileHistory
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import CapabilitySet, CmdResult
from termapy.plugins.capabilities import STATE_MESSAGES

pytestmark = pytest.mark.slow  # subprocess-spawning + CLITerminal end-to-end


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def cli(tmp_path):
    """Create a CLITerminal wired to the DEMO simulator.

    Uses a real SerialEngine with port="DEMO" so FakeSerial stands in
    for real hardware.  Tests that need a disconnected state can set
    ``cli.engine._is_connected = False`` (or call ``cli._disconnect()``).
    Tests that need a connected state can call ``cli._connect()``.
    """
    # Serial keys (port, baud_rate) live nested under cfg["serial"]
    # post-v22; the merge into DEFAULT_CFG must target the sub-dict
    # explicitly or those keys get silently created at the top level
    # where pyserial / open_serial won't find them.
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200},
        "eol": "\r",
    }
    config_path = tmp_path / "test_cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "proto", "cap", "prof"):
        (config_path.parent / sub).mkdir(exist_ok=True)

    return CLITerminal(cfg, str(config_path), no_color=True, term_width=80)


def _grant_gui_apps(cli):
    """Grant the gui_apps capability on the CLI context.

    /run.profile.show and .explore are gated on gui_apps in two places:
    the command's ``needs`` (read from ctx.capabilities by the dispatcher)
    and ``ctx.fs.open_file`` (read from ctx.fs.capabilities).  CapabilitySet
    is frozen, so union a fresh set and assign both snapshots -- mirroring
    what PluginContext.__post_init__ does at construction.  Lets these tests
    exercise the open behavior on a headless host (CI has no DISPLAY).
    """
    # A CLI with a desktop has both: its system editor is its file editor.
    caps = cli.ctx.capabilities.union(CapabilitySet(gui_apps=True, file_editor=True))
    cli.ctx.capabilities = caps
    cli.ctx.fs.capabilities = caps


# -- Output methods ----------------------------------------------------------


class TestOutput:
    def test_write_plain(self, cli, capsys):
        """Plain write output lands on stdout with the text intact."""
        # Act
        cli.write("hello world")

        # Assert
        actual = capsys.readouterr().out
        assert "hello world" in actual, "text appears in stdout"

    def test_write_with_color(self, cli, capsys):
        """A color argument styles the line but never drops its text."""
        # Act
        cli.write("error msg", "red")

        # Assert
        actual = capsys.readouterr().out
        assert "error msg" in actual, "text appears even with color arg"

    def test_status_indented(self, cli, capsys):
        """Status lines reach stdout, the same channel as plain output."""
        # Act
        cli.status("info line")

        # Assert
        actual = capsys.readouterr().out
        assert "info line" in actual, "status text appears"

    def test_status_with_color(self, cli, capsys):
        """A colored status line still carries its text through to stdout."""
        # Act
        cli.status("warning", "yellow")

        # Assert
        actual = capsys.readouterr().out
        assert "warning" in actual, "colored status appears"

    def test_raw_output(self, cli, capsys):
        """Raw output bypasses Rich and reaches stdout verbatim."""
        # Act
        cli._raw("raw text")

        # Assert
        actual = capsys.readouterr().out
        assert "raw text" in actual, "raw text bypasses Rich"

    def test_err_output(self, cli, capsys):
        """Error text goes to stderr, not stdout, so pipes stay clean."""
        # Act
        cli._err("error text")

        # Assert
        actual = capsys.readouterr().err
        assert "error text" in actual, "error goes to stderr"


# -- /delay: one builtin, the CLI only draws its bar -----------------------


class TestDelay:
    def test_short_delay(self, cli, capsys):
        """A valid duration like 10ms waits, reports success with the seconds, and says it is done."""
        # Act
        result = cli.repl.dispatch_full("/delay 10ms")

        # Assert
        assert (result.success, result.value) == (True, "0.01"), "short delay completes ok"
        assert "Delay 10ms done." in capsys.readouterr().out

    def test_invalid_duration(self, cli):
        """An unparseable duration fails through the duration reader instead of sleeping a default."""
        # Act
        result = cli.repl.dispatch_full("/delay xyz")

        # Assert
        assert not result.success, "invalid duration fails"
        assert "invalid duration: 'xyz'" in result.error

    def test_silent_waits_without_output(self, cli, capsys):
        """/delay.silent is the universal level suffix: it waits and prints nothing."""
        # Arrange
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full("/delay.silent 10ms")

        # Assert
        assert result.success, "silent delay completes ok"
        assert "Delay" not in capsys.readouterr().out, "no done line at silent"

    def test_silent_still_refuses_a_bad_duration(self, cli):
        """The silent variant still fails on a bad duration; silent is not lenient."""
        # Act
        result = cli.repl.dispatch_full("/delay.silent bad")

        # Assert
        assert not result.success, "invalid duration fails"


# -- /term.color (portable device-color toggle) -----------------------------


class TestTermColor:
    """/term.color is now a portable builtin backed by flags['color']."""

    def test_color_on(self, cli):
        """The on token sets flags[color] from off."""
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = False

        # Act
        result = cli.repl.dispatch("term.color on")

        # Assert
        assert result.success, "command succeeds"
        assert cli.repl.ctx.ns("flags")["color"] is True, "color enabled"

    def test_color_off(self, cli):
        """The off token clears flags[color] from on."""
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act
        result = cli.repl.dispatch("term.color off")

        # Assert
        assert result.success, "command succeeds"
        assert cli.repl.ctx.ns("flags")["color"] is False, "color disabled"

    def test_color_bare_queries(self, cli):
        """A bare /term.color only queries; it must not flip the setting."""
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act -- bare invocation QUERIES; it must NOT mutate
        cli.repl.dispatch("term.color")

        # Assert
        assert cli.repl.ctx.ns("flags")["color"] is True, "bare query leaves state"

    def test_color_toggle_verb(self, cli):
        """Only the explicit toggle verb flips the current color state."""
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act
        cli.repl.dispatch("term.color toggle")

        # Assert
        assert cli.repl.ctx.ns("flags")["color"] is False, "toggle verb flips"

    def test_color_invalid_arg_errors(self, cli):
        """An unknown token errors with the standard message and leaves state alone."""
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act
        result = cli.repl.dispatch("term.color 2")

        # Assert -- invalid arg errors, never a silent flip
        assert result.success is False, "invalid arg fails"
        assert result.error == "Invalid value: 2 (use on/off/true/false/yes/no/1/0/y/n/t/f or toggle)", "clear msg"
        assert cli.repl.ctx.ns("flags")["color"] is True, "state unchanged"

    def test_legacy_color_alias(self, cli):
        """The legacy /color name still forwards to /term.color."""
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act -- /color forwards to /term.color
        cli.repl.dispatch("color off")

        # Assert
        assert cli.repl.ctx.ns("flags")["color"] is False, "/color forwards to /term.color"


class TestTermHex:
    """/term.hex reaches the CLI's display; it used to be honored by the TUI alone."""

    def test_the_cli_prints_a_reply_as_its_bytes(self, cli, capsys):
        """hex-view-wire-bytes: with /term.hex on, the CLI prints the DEMO reply as its wire bytes while expect still sees the line."""
        # Arrange
        cli._connect()
        cli.repl.dispatch("term.hex on")
        capsys.readouterr()  # drop the connect output

        # Act -- DEMO answers AT with OK CR LF; the row prints before the
        # line reaches the watcher, so the match means the row is out
        cli._serial_send("AT")
        matched = cli.repl.wait_for_match(lambda line: line == "OK", timeout=2.0)
        cli._disconnect()
        printed = capsys.readouterr().out.splitlines()

        # Assert
        assert matched == "OK", "the decoded line reaches the expect watcher"
        assert "4F 4B 0D 0A" in printed, f"the CLI ignored /term.hex, printed {printed!r}"
        assert "OK" not in printed, "the decoded line is not printed as well"


# -- /raw --------------------------------------------------------------------


class TestRaw:
    @pytest.mark.parametrize(
        "route",
        [
            pytest.param(lambda cli: cli._dispatch("/raw hello"), id="typed"),
            pytest.param(lambda cli: cli.repl.dispatch("raw hello"), id="dispatched"),
        ],
    )
    def test_raw_not_connected(self, cli, route):
        """state-answered-by-gate: /raw with no open port is the gate's answer by either route, typed text included."""
        # Arrange -- fixture is disconnected by default.
        # Act
        result = route(cli)

        # Assert
        assert not result.success, "fails when disconnected"
        assert result.error == STATE_MESSAGES["serial_connected"], (
            "the gate answered; a typed /raw past it would reach the write and say 'Send error: ...'"
        )

    def test_raw_no_args(self, cli):
        """/raw with no payload renders the usage line instead of sending."""
        # Arrange -- connect to DEMO so the "no args" branch is reached.
        cli._connect()

        # Act
        result = cli._dispatch("/raw")

        # Assert
        assert not result.success, "a bare /raw is a usage error"
        assert "Usage:" in result.error, "the dispatcher renders the declaration"

    @pytest.mark.parametrize(
        "route",
        [
            pytest.param(lambda cli: cli._dispatch("/raw AT"), id="typed"),
            pytest.param(lambda cli: cli.repl.dispatch("raw AT"), id="dispatched"),
        ],
    )
    def test_raw_sends_data(self, cli, route):
        """/raw writes the payload byte-for-byte with no line ending, by either route."""
        # Arrange -- real connect to DEMO.  FakeSerial records bytes
        # in ``_input_buf`` until it sees a line ending, so with none
        # sent the bytes sit there where we can verify them.
        cli._connect()

        # Act
        result = route(cli)

        # Assert
        assert result.success, "command succeeds"
        actual = bytes(cli.engine.port_obj._input_buf)
        assert actual == b"AT", f"bytes sent verbatim, got {actual!r}"


# -- Hook: run ---------------------------------------------------------------


class TestHookRun:
    @pytest.mark.parametrize("command", ["run", "proto", "cfg"])
    def test_bare_picker_command_lists_without_a_picker(self, cli, command, tmp_path, monkeypatch):
        """bare-declared: with no picker in the CLI, bare /run, /proto and /cfg run their .list, the picker's contents."""
        # Arrange -- a script, a protocol file, and a config root of our own:
        # /cfg.list reads the config root, which must not be the checkout's.
        parent = Path(cli.config_path).parent
        (parent / "run" / "boot.run").write_text("/print hi\n", encoding="utf-8")
        (parent / "proto" / "probe.pro").write_text("", encoding="utf-8")
        cfg_root = tmp_path / "cfg_root"
        (cfg_root / "bench").mkdir(parents=True)
        (cfg_root / "bench" / "bench.cfg").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("TERMAPY_CFG_DIR", str(cfg_root))

        # Act
        bare = cli.repl.dispatch(command)
        listed = cli.repl.dispatch(f"{command}.list")

        # Assert
        assert bare.success, f"bare /{command}: {bare.error}"
        assert bare.value == listed.value, f"bare /{command} is /{command}.list"
        assert bare.value, f"/{command}.list found something to list"

    def test_run_list_enumerates_scripts(self, cli, capsys):
        """/run.list shows every .run file in the config's run/ folder."""
        # Arrange
        run_dir = Path(cli.config_path).parent / "run"
        (run_dir / "test1.run").write_text("/echo hello")
        (run_dir / "test2.run").write_text("/echo world")

        # Act
        cli.repl.dispatch("run.list")

        # Assert
        actual = capsys.readouterr().out
        assert "test1.run" in actual, "first script listed"
        assert "test2.run" in actual, "second script listed"

    def test_run_file_not_found(self, cli):
        """Running a script that does not exist fails instead of silently doing nothing."""
        # Act -- /run <nonexistent> goes through the builtin handler,
        # which delegates to engine.start_script for resolution.
        result = cli.repl.dispatch("run nonexistent.run")

        # Assert
        assert not result.success, "missing script fails"


# -- /edit.cfg in CLI (audit item #3) ---------------------------------------


class TestEditCfgCallableInCli:
    """``/edit.cfg`` is a sub_command of the ``edit`` built-in (see
    ``builtins/commands/edit.py``), the one /edit in every frontend: the
    TUI opens it in its config editor, the CLI in the system editor
    (``ctx.internal.edit_file``).  MCP hides it via ``needs.file_editor``.

    The CLI/MCP parity audit (notes/cli-mcp-parity-audit.md, item 3)
    initially flagged this as a gap; verification showed it already
    works.  These tests pin the behavior so a refactor of the
    ``edit`` builtin cannot silently regress CLI access.
    """

    def test_edit_cfg_registered_in_cli(self, cli):
        """The CLI exposes edit.cfg, gated on file_editor (a desktop, in the CLI)."""
        # Arrange / Act
        plugin = cli.repl._plugins.get("edit.cfg")

        # Assert
        assert plugin is not None, "edit.cfg present in CLI plugin set"
        assert plugin.needs.file_editor, (
            "edit.cfg needs file_editor so a CLI without a desktop and MCP correctly gate it"
        )

    def test_edit_cfg_opens_config_file_when_gui_apps_available(self, cli):
        """With gui_apps granted, edit.cfg opens the live config path exactly once."""
        # Arrange -- force the gui_apps gate open and capture the impl
        # call without spawning a real editor.
        from termapy.plugins import CapabilitySet

        cli.ctx.capabilities = CapabilitySet(interactive=True, gui_apps=True, file_editor=True)
        cli.ctx.fs.capabilities = cli.ctx.capabilities
        opened = []
        cli.ctx.fs._open_file_impl = lambda path: opened.append(path)

        # Act
        result = cli.repl.dispatch("edit.cfg")

        # Assert
        assert result.success, "edit.cfg dispatches cleanly"
        assert len(opened) == 1, "open_file called exactly once"
        assert Path(opened[0]) == Path(cli.config_path), (
            "opens the live config file, not a stale path"
        )

    def test_edit_needs_the_extension(self, cli):
        """file-name-default-ext: /edit foo bails even when run/foo.run exists; its folders are mixed, so it needs foo.run."""
        # Arrange
        from termapy.plugins import CapabilitySet

        cli.ctx.capabilities = CapabilitySet(interactive=True, gui_apps=True, file_editor=True)
        cli.ctx.fs.capabilities = cli.ctx.capabilities
        opened = []
        cli.ctx.fs._open_file_impl = lambda path: opened.append(path)
        scripts_dir = cli.ctx.fs.scripts_dir
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "foo.run").write_text("/print hi\n")

        # Act
        bare = cli.repl.dispatch("edit foo")
        named = cli.repl.dispatch("edit foo.run")

        # Assert
        assert bare.success is False, "a bare stem names no folder, so /edit bails"
        assert bare.error == "File not found: foo"
        assert named.success, named.error
        assert [Path(path).name for path in opened] == ["foo.run"], "only the named form opened a file"


# -- /run.profile.* ----------------------------------------------------------


class TestRunProfile:
    """``/run.profile`` and its ``prof/`` folder family, one builtin in every frontend.

    Until 2026-10-16 the TUI and the CLI each registered hooks with a
    hand-written copy of the folder family (review 2026-10-15, D8); the
    folder subcommands are now ``build_folder_subcommands("prof")``.
    """

    def test_list_empty_prof_dir(self, cli, capsys):
        """An empty prof/ folder lists as an empty state, not an error."""
        # Act
        result = cli.repl.dispatch("run.profile.list")

        # Assert
        actual = capsys.readouterr().out
        assert result.success, "empty prof/ is a success, not an error"
        assert "prof/ - empty" in actual, "reports the empty state in the one listing format"

    def test_list_enumerates_csvs(self, cli, capsys):
        """Every profile CSV in prof/ appears in the listing."""
        # Arrange
        prof_dir = cli.repl.prof_dir
        prof_dir.mkdir(exist_ok=True)
        (prof_dir / "first.csv").write_text("")
        (prof_dir / "second.csv").write_text("")

        # Act
        cli.repl.dispatch("run.profile.list")

        # Assert
        actual = capsys.readouterr().out
        assert "first.csv" in actual, "first profile listed"
        assert "second.csv" in actual, "second profile listed"

    def test_dump_empty_prof_dir_says_empty(self, cli, capsys):
        """Dumping with no profiles answers as every folder family does: an empty prof/, not an error."""
        # Act
        result = cli.repl.dispatch("run.profile.dump")

        # Assert
        assert (result.success, result.value) == (True, ""), "nothing to dump is an empty answer"
        assert "prof/ is empty." in capsys.readouterr().out, "and says so"

    def test_dump_prints_newest_csv(self, cli, capsys):
        """With no name given, dump prints the newest CSV's contents."""
        # Arrange
        prof_dir = cli.repl.prof_dir
        prof_dir.mkdir(exist_ok=True)
        prof_path = prof_dir / "demo.csv"
        prof_path.write_text("step,dt_ms\n0,1.5\n")

        # Act
        cli.repl.dispatch("run.profile.dump")

        # Assert
        actual = capsys.readouterr().out
        assert "step,dt_ms" in actual, "header line printed"
        assert "0,1.5" in actual, "data line printed"

    def test_dump_takes_a_name_without_its_extension(self, cli):
        """file-name-default-ext: /run.profile.dump boot finds prof/boot.csv."""
        # Arrange
        prof_dir = cli.repl.prof_dir
        prof_dir.mkdir(exist_ok=True)
        (prof_dir / "boot.csv").write_text("step,dt_ms\n")

        # Act
        result = cli.repl.dispatch("run.profile.dump boot")

        # Assert
        assert (result.success, result.value) == (True, "step,dt_ms\n"), result.error

    def test_cmd_is_one_command_never_split(self, cli):
        """A /run.profile.cmd argument is one command string: an escaped newline in it does not make two lines."""
        # Act
        result = cli.repl.dispatch(r"run.profile.cmd /print one\n/print two")

        # Assert
        assert result.success, result.error
        scripts = list(cli.repl.scripts_dir.glob("_profile_tmp_*.run"))
        actual = [script.read_text(encoding="utf-8").splitlines() for script in scripts]
        assert actual == [[r"/print one\n/print two"]], "the temporary script holds the one line"

    def test_dump_named_file_not_found(self, cli):
        """A named profile that is missing fails rather than dumping another."""
        # Act
        result = cli.repl.dispatch("run.profile.dump missing.csv")

        # Assert
        assert not result.success, "named file that doesn't exist fails"

    def test_show_no_profiles_says_empty(self, cli, capsys):
        """Show with nothing in prof/ answers as every folder family does and opens nothing."""
        # Arrange
        _grant_gui_apps(cli)
        opened: list[str] = []
        cli.ctx.fs._open_file_impl = opened.append

        # Act
        result = cli.repl.dispatch("run.profile.show")

        # Assert
        assert (result.success, opened) == (True, []), "an empty prof/ opens nothing"
        assert "prof/ is empty." in capsys.readouterr().out, "and says so"

    def test_show_opens_newest(self, cli):
        """Show opens the newest CSV by mtime, not by name order."""
        # Arrange -- explicitly stagger mtimes via os.utime; Windows
        # NTFS mtime resolution is coarse enough that two write_text()
        # calls in the same tick land on the same timestamp, and the
        # sort by mtime falls back to alphabetical (where 'newer' <
        # 'older'), causing the wrong file to be picked as "newest."
        import os

        prof_dir = cli.repl.prof_dir
        prof_dir.mkdir(exist_ok=True)
        older = prof_dir / "older.csv"
        older.write_text("")
        os.utime(older, (1_000_000.0, 1_000_000.0))
        newest = prof_dir / "newer.csv"
        newest.write_text("")
        os.utime(newest, (2_000_000.0, 2_000_000.0))
        _grant_gui_apps(cli)
        # Record what gets opened through the real ctx.fs.open_file seam:
        # the host wires _open_file_impl to open_with_system; swap in a real
        # recorder so the handler runs end-to-end but nothing launches.  No
        # mock -- opened is a plain list, _open_file_impl is the seam field.
        opened: list[str] = []
        cli.ctx.fs._open_file_impl = opened.append

        # Act
        result = cli.repl.dispatch("run.profile.show")

        # Assert
        actual = Path(result.value)
        expected = newest.resolve()
        assert result.success, "show succeeds when prof/ has files"
        assert actual == expected, f"returns newest .csv path, got {actual}"
        assert opened == [str(newest)], "opened the newest .csv via ctx.fs.open_file"

    def test_explore_opens_prof_dir(self, cli):
        """Explore opens the prof/ folder itself and returns its path."""
        # Arrange
        prof_dir = cli.repl.prof_dir
        _grant_gui_apps(cli)
        opened: list[str] = []
        cli.ctx.fs._open_file_impl = opened.append

        # Act
        result = cli.repl.dispatch("run.profile.explore")

        # Assert
        actual = Path(result.value)
        expected = prof_dir.resolve()
        assert result.success, "explore succeeds"
        assert actual == expected, f"returns prof/ dir path, got {actual}"
        assert [Path(path) for path in opened] == [prof_dir], "opened prof/ folder via ctx.fs.open_file"

    def test_cmd_empty_fails_with_usage(self, cli, capsys):
        """Profiling with no command fails and prints the usage line."""
        # Act
        result = cli.repl.dispatch("run.profile.cmd")

        # Assert
        assert not result.success, "empty arg fails"
        actual = capsys.readouterr().out
        assert "Usage:" in actual, "usage message shown"


# -- Hook: log.clear ---------------------------------------------------------


# -- Connect / Disconnect ---------------------------------------------------


class TestConnect:
    def test_connect_already_connected(self, cli, capsys):
        """A second connect warns instead of reopening the port."""
        # Arrange -- connect once for real, then try again.
        cli._connect()
        capsys.readouterr()  # drain the first-connect banner

        # Act
        cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Already connected" in actual, "warns already connected"

    def test_connect_success(self, cli, capsys):
        """Connecting to DEMO announces it and leaves the engine connected."""
        # Act -- real DEMO connect, no mocks.
        cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Connected" in actual, "reports connection"
        assert cli.engine.is_connected, "engine reports connected state"

    def test_connect_failure(self, cli, capsys):
        """A port that cannot open reports the failure and stays disconnected."""
        # Arrange -- switch the port to DEMO_FAIL, the reserved name
        # open_serial() treats as a simulated open failure.  Exercises
        # the real connect-failure path without needing broken hardware
        # or a mocked open_fn.
        # Port lives at cfg["serial"]["port"] post-v22.
        cli.cfg["serial"]["port"] = "DEMO_FAIL"
        cli.repl._cfg_data["serial"]["port"] = "DEMO_FAIL"

        # Act
        cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Cannot open" in actual, "reports failure"
        assert not cli.engine.is_connected, "engine remains disconnected"

    def test_connect_with_port(self, cli):
        """An explicit port argument is written back into the serial cfg section."""
        # Act -- override with another port name (still DEMO) so the
        # cfg-update branch runs.
        cli._connect(port="DEMO")

        # Assert
        assert cli.cfg["serial"]["port"] == "DEMO", (
            "port updated in config at migrated location"
        )
        assert cli.engine.is_connected, "engine connected after override"

    def test_port_connect_command_reports_failure(self, cli):
        """/port.connect returns a failing result on open failure, not a false ok."""
        # R2607-02: the /port.connect COMMAND must return a failing
        # CmdResult when the port can't open.  It previously returned ok()
        # unconditionally, so scripts and MCP saw a false success.
        # DEMO_FAIL is the reserved simulated-open-failure port.
        # Arrange
        cli.cfg["serial"]["port"] = "DEMO_FAIL"
        cli.repl._cfg_data["serial"]["port"] = "DEMO_FAIL"

        # Act
        result = cli.repl.dispatch("port.connect")

        # Assert
        assert not result.success, (
            "connect must report failure when the port cannot open"
        )
        assert not cli.engine.is_connected, "engine remains disconnected on failure"

    def test_port_connect_command_reports_success(self, cli):
        """/port.connect returns ok and connects when the port opens."""
        # Act -- the happy path still returns ok.
        result = cli.repl.dispatch("port.connect DEMO")

        # Assert
        assert result.success, "connect to DEMO reports success"
        assert cli.engine.is_connected, "engine connected after a successful connect"

    def test_disconnect_not_connected(self, cli, capsys):
        """Disconnecting while closed warns rather than failing silently."""
        # Arrange -- fixture is disconnected by default.
        # Act
        cli._disconnect()

        # Assert
        actual = capsys.readouterr().out
        assert STATE_MESSAGES["serial_connected"] in actual, "warns not connected"

    def test_disconnect_success(self, cli, capsys):
        """Disconnect closes the engine and announces it."""
        # Arrange -- real connect, then real disconnect.
        cli._connect()
        capsys.readouterr()  # drain connect banner

        # Act
        cli._disconnect()

        # Assert
        assert not cli.engine.is_connected, "engine reports disconnected"
        actual = capsys.readouterr().out
        assert "Disconnected" in actual, "reports disconnection"


# -- typed device line -------------------------------------------------------


class TestTypedLine:
    def test_not_connected(self, cli, capsys):
        """A line typed while disconnected warns instead of being dropped silently."""
        # Arrange -- fixture is disconnected by default.
        # Act
        cli._dispatch("AT")

        # Assert
        actual = capsys.readouterr().out
        assert STATE_MESSAGES["serial_connected"] in actual, "warns not connected"

    def test_sends_with_line_ending(self, cli, capsys):
        """Typed lines get the cfg eol appended, proven by DEMO answering OK."""
        # Arrange -- real DEMO connect; DEMO responds to AT<CRLF> with
        # OK<CRLF>.  Seeing "OK" in stdout proves that (a) the bytes
        # were sent, (b) DEMO recognized them as a complete AT command,
        # which means the line ending was applied correctly.
        cli._connect()
        cli.cfg["eol"] = "\r\n"

        # Act
        cli._dispatch("AT")

        # Assert -- reader thread drains DEMO's response into stdout.
        # Poll briefly since the background reader runs on its own
        # thread.
        import time
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            actual = capsys.readouterr().out
            if "OK" in actual:
                return
            time.sleep(0.02)
        pytest.fail(f"expected OK in stdout, got {actual!r}")


# -- Capture helpers ---------------------------------------------------------


class TestCapture:
    def test_start_capture_already_active(self, cli, tmp_path):
        """A second capture is refused while one is still running."""
        # Arrange -- start a real capture against tmp_path, then try
        # to start a second one.  The second call must be rejected.
        first = tmp_path / "first.txt"
        started = cli._start_capture(
            path=first, file_mode="w", mode="text", duration=1.0
        )
        assert started is True, "first capture starts cleanly"

        # Act -- try to start another while the first is still active.
        second = tmp_path / "second.txt"
        actual = cli._start_capture(
            path=second, file_mode="w", mode="text", duration=1.0
        )

        # Assert
        assert actual is False, "returns False when already active"
        cli._stop_capture()

    def test_start_capture_success(self, cli, tmp_path):
        """Starting a capture creates the file and marks the engine active."""
        # Arrange
        cap_path = tmp_path / "cap.txt"

        # Act
        actual = cli._start_capture(
            path=cap_path, file_mode="w", mode="text", duration=1.0
        )

        # Assert -- real file was created, real engine reports active.
        assert actual is True, "returns True on success"
        assert cli.capture.active, "engine reports active capture"
        assert cap_path.exists(), "capture file created on disk"
        cli._stop_capture()

    def test_stop_capture(self, cli, tmp_path, capsys):
        """Stop flushes the file and prints Capture complete exactly once."""
        # Arrange -- real text capture of three bytes.
        cap_path = tmp_path / "cap.txt"
        cli._start_capture(
            path=cap_path, file_mode="w", mode="text", duration=1.0
        )
        cli.capture.feed_text(["abc"])
        capsys.readouterr()  # drain "Capture started" status

        # Act
        cli._stop_capture()

        # Assert -- real engine closed, file has expected contents,
        # user-facing status line announces completion.
        assert not cli.capture.active, "engine reports inactive after stop"
        assert cap_path.read_text() == "abc\n", "captured line written"
        actual = capsys.readouterr().out
        # Exactly once: the message is owned by the CaptureEngine on_complete
        # callback.  _stop_capture used to ALSO print it -> a visible double.
        assert actual.count("Capture complete") == 1, (
            "reports completion exactly once (no double-print)"
        )


# -- apply_port_effects ------------------------------------------------------


class TestApplyPortEffects:
    def test_cfg_update(self, cli):
        """A cfg_update effect lands in the serial cfg section, not the top level."""
        # Arrange
        effects = {"cfg_update": {"baud_rate": 9600}}

        # Act
        cli._apply_port_effects(effects)

        # Assert
        assert cli.repl._cfg_data["serial"]["baud_rate"] == 9600, (
            "config updated at migrated location (cfg['serial']['baud_rate'])"
        )

    def test_empty_effects(self, cli):
        """An empty effects dict is a harmless no-op."""
        # Act / Assert - no exception on empty effects
        cli._apply_port_effects({})


# -- Confirm -----------------------------------------------------------------


class TestConfirm:
    def test_confirm_yes(self, cli):
        """Answering y at the prompt confirms."""
        # Act
        with patch("builtins.input", return_value="y"):
            actual = cli._confirm("Continue?")

        # Assert
        assert actual is True, "y means yes"

    def test_confirm_no(self, cli):
        """Answering n at the prompt declines."""
        # Act
        with patch("builtins.input", return_value="n"):
            actual = cli._confirm("Continue?")

        # Assert
        assert actual is False, "n means no"

    def test_confirm_empty(self, cli):
        """A bare Enter defaults to no, so destructive actions need a real yes."""
        # Act
        with patch("builtins.input", return_value=""):
            actual = cli._confirm("Continue?")

        # Assert
        assert actual is False, "empty defaults to no"

    def test_confirm_eof(self, cli):
        """EOF on stdin declines instead of raising, so piped input fails closed."""
        # Act
        with patch("builtins.input", side_effect=EOFError):
            actual = cli._confirm("Continue?")

        # Assert
        assert actual is False, "EOF returns False"


# -- History / prompt session ------------------------------------------------


class TestHistory:
    def test_history_path(self, cli):
        """History path is derived from config path."""
        # Act
        actual = cli._history_path()

        # Assert
        assert actual.endswith("test.history"), "history file uses config stem"

    def test_build_session_creates_completer(self, cli):
        """Completer class is wired with repl and prefix."""
        from termapy.cli import _TermapyCompleter

        # Act
        completer = _TermapyCompleter(cli.repl, cli.prefix)

        # Assert
        assert completer._prefix == "/", "prefix wired"
        assert completer._repl is cli.repl, "repl wired"


class TestFileCompletion:
    """The CLI's Tab completer reads ``Command.files``, not a table of its own."""

    def test_proto_run_completes_from_proto(self, cli):
        """/proto.run Tab-completes .pro names, so the completer is not wired to run/ alone."""
        from prompt_toolkit.document import Document

        from termapy.cli import _TermapyCompleter

        # Arrange
        proto_dir = Path(cli.config_path).parent / "proto"
        (proto_dir / "alpha.pro").write_text("", encoding="utf-8")
        (proto_dir / "beta.pro").write_text("", encoding="utf-8")
        completer = _TermapyCompleter(cli.repl, cli.prefix)

        # Act
        completions = completer.get_completions(Document("/proto.run a"), None)
        actual = [(completion.text, completion.start_position) for completion in completions]

        # Assert
        expected = [("alpha.pro", -1)]
        assert actual == expected, "only the matching .pro, replacing the typed partial"

    def test_profile_hooks_keep_their_declarations(self, cli):
        """/run.profile and /run.profile.dump declare the folders their file argument completes from."""
        profile = cli.repl.plugin_for("run.profile")
        dump = cli.repl.plugin_for("run.profile.dump")
        assert profile is not None and dump is not None, "both are registered"
        assert profile.files == "run", "/run.profile completes run/ scripts"
        assert dump.files == "prof", "/run.profile.dump completes prof/ results"


class TestEditTwins:
    """The noun-first edit commands are the ``/edit.<folder>`` commands under a second name."""

    @pytest.mark.parametrize(
        ("twin", "original", "file"),
        [
            ("run.edit", "edit.run", "run/probe.run"),
            ("proto.edit", "edit.proto", "proto/probe.pro"),
            ("plugin.edit", "edit.plugin", "plugin/probe.py"),
        ],
    )
    def test_both_spellings_open_the_same_file(self, cli, twin, original, file):
        """/<folder>.edit and /edit.<folder> hand the same existing file to the editor."""
        # Arrange
        path = Path(cli.config_path).parent / file
        path.write_text("", encoding="utf-8")
        _grant_gui_apps(cli)
        opened: list[str] = []
        cli.ctx.fs._open_file_impl = lambda opened_path: opened.append(str(opened_path))

        # Act
        for command in (twin, original):
            cli.repl.dispatch(f"{command} probe")

        # Assert
        assert opened == [str(path), str(path)], "both spellings open the existing file"

    def test_a_missing_name_fails_the_same_under_both_spellings(self, cli):
        """/run.edit opens existing scripts only: a new name is File not found, as with /edit.run."""
        # Arrange
        _grant_gui_apps(cli)

        # Act
        twin = cli.repl.dispatch("run.edit nope")
        original = cli.repl.dispatch("edit.run nope")

        # Assert
        assert twin.error == "File not found: nope", "/run.edit no longer creates a script"
        assert original.error == twin.error, "both spellings fail alike"

    def test_bare_lists_the_folder_under_both_spellings(self, cli):
        """With no picker in the CLI, bare /proto.edit lists proto/ exactly as /edit.proto does."""
        # Arrange
        (Path(cli.config_path).parent / "proto" / "a.pro").write_text("", encoding="utf-8")
        _grant_gui_apps(cli)

        # Act
        twin = cli.repl.dispatch("proto.edit")
        original = cli.repl.dispatch("edit.proto")

        # Assert
        assert twin.value == "a.pro", "bare /proto.edit lists the folder"
        assert original.value == twin.value, "both spellings list alike"

    def test_cfg_edit_opens_the_current_config(self, cli):
        """/cfg.edit opens the loaded config, the file /edit.cfg opens."""
        # Arrange
        _grant_gui_apps(cli)
        opened: list[str] = []
        cli.ctx.fs._open_file_impl = lambda opened_path: opened.append(str(opened_path))

        # Act
        for command in ("cfg.edit", "edit.cfg"):
            cli.repl.dispatch(command)

        # Assert
        expected = [str(Path(cli.config_path))] * 2
        assert opened == expected, "both spellings open the current config"


class TestBareForms:
    """bare-declared: a bare command's behavior is declared, and /help states it in one wording."""

    @pytest.mark.parametrize(
        ("command", "line"),
        [
            ("run", "/run -- bare: opens the Run picker in the TUI; elsewhere runs /run.list"),
            ("proto", "/proto -- bare: opens the Proto picker in the TUI; elsewhere runs /proto.list"),
            ("cfg", "/cfg -- bare: opens the Cfg picker in the TUI; elsewhere runs /cfg.list"),
            ("port", "/port -- bare: opens the Port picker in the TUI; elsewhere runs /port.list"),
            ("proto.run", "/proto.run -- bare: opens the Proto picker in the TUI"),
            ("run.edit", "/run.edit -- bare: opens the Run picker in the TUI"),
            ("ss", "/ss -- bare: runs /ss.explore"),
            ("cap", "/cap -- bare: runs /cap.explore"),
            ("log", "/log -- bare: runs /log.show"),
            ("mem", "/mem -- bare: runs /mem.info"),
        ],
    )
    def test_help_states_the_bare_form(self, cli, capsys, command, line):
        """bare-declared: /help prints a declared bare form as a SYNOPSIS line, the same way for every command."""
        cli.repl.dispatch(f"help {command}")
        assert line in capsys.readouterr().out, f"/help {command} states what bare /{command} does"

    def test_a_command_without_a_bare_form_prints_no_bare_line(self, cli, capsys):
        """A command that declares no bare form gets no bare line."""
        cli.repl.dispatch("help print")
        assert "-- bare:" not in capsys.readouterr().out, "no declaration, no line"

    @pytest.mark.parametrize("folder", ["ss", "cap"])
    def test_bare_ss_and_cap_open_their_folders(self, cli, folder):
        """Bare /ss and /cap do what the SS and Cap buttons do: open the folder."""
        # Arrange
        _grant_gui_apps(cli)
        opened: list[str] = []
        cli.ctx.fs._open_file_impl = lambda opened_path: opened.append(str(opened_path))

        # Act
        result = cli.repl.dispatch(folder)

        # Assert
        expected = [str(Path(cli.config_path).parent / folder)]
        assert result.success, result.error
        assert opened == expected, f"bare /{folder} opened {folder}/"

    def test_bare_log_is_log_show(self, cli, tmp_path):
        """Bare /log does what the Log button does, /log.show: in the CLI, which keeps no log, the gate refuses both alike."""
        # Arrange -- the CLI has no session log, so no system viewer is launched
        _grant_gui_apps(cli)

        # Act
        bare = cli.repl.dispatch("log")
        shown = cli.repl.dispatch("log.show")

        # Assert
        assert "session_log" in bare.error, f"bare /log ran /log.show: {bare.error}"
        assert bare.error.replace("/log ", "/log.show ") == shown.error, "bare /log and /log.show are one behavior"


# -- Hook registration ------------------------------------------------------


class TestNoSessionLog:
    """D3: the CLI keeps no session log, and every /log command and $(CFG.LOG_FILE) agrees."""

    @pytest.mark.parametrize("command", ["log.dump", "log.delete", "log.fingerprint", "log.show", "edit.log"])
    def test_the_cli_keeps_no_session_log(self, cli, command):
        """Every command on the session log is refused by the gate, for want of session_log.

        Until 2026-10-16 they read the TUI's <cfg>.log, so a CLI could dump
        or delete another frontend's log (review 2026-10-15, D3); then each
        answered "No log file." itself while /help called them available.
        """
        # Arrange -- grant what else they need, so session_log is the gap
        _grant_gui_apps(cli)

        # Act
        result = cli.repl.dispatch_full(f"/{command}")

        # Assert
        assert cli.session_log_path() is None, "the CLI writes no session log"
        expected = f"/{command} requires: session_log (not available in this environment)"
        assert (result.success, result.error) == (False, expected)

    def test_cfg_log_file_is_empty_in_the_cli(self, cli):
        """$(CFG.LOG_FILE) names nothing in the CLI, which keeps no session log."""
        from termapy.variables import expand_vars

        assert expand_vars("$(CFG.LOG_FILE)") == "", "nothing to name"


class TestExitFromAScript:
    """/exit and /tui end a CLI session from a script, not only typed at the prompt.

    Until 2026-10-16 the prompt loop matched the typed words, and dispatched
    from a ``.run`` file or ``-e`` ``/exit`` was a no-op, so the script went
    on (review 2026-10-15, D10).
    """

    @staticmethod
    def _run_script(cli, body: str) -> CmdResult:
        """Write ``body`` to run/bye.run and run it through /run."""
        (Path(cli.config_path).parent / "run" / "bye.run").write_text(body, encoding="utf-8")
        return cli.repl.dispatch("run bye.run")

    def test_exit_stops_the_script_and_ends_the_session(self, cli, capsys):
        """The line after /exit never runs, and the session is asked to end."""
        # Act
        self._run_script(cli, "/print before\n/exit\n/print after\n")

        # Assert
        out = capsys.readouterr().out
        assert "before" in out, "the line before /exit ran"
        assert "after" not in out, "the script stopped at /exit"
        assert (cli._exit_requested, cli.switch_to) == (True, None), "a plain exit, no mode switch"

    def test_tui_in_a_script_switches_to_the_tui(self, cli, capsys):
        """/tui in a script stops it and hands the mode loop the TUI."""
        # Act
        self._run_script(cli, "/tui\n/print after\n")

        # Assert
        assert "after" not in capsys.readouterr().out, "the script stopped at /tui"
        assert (cli._exit_requested, cli.switch_to) == (True, "tui")


class TestConfirmInAScript:
    """/confirm in a CLI script asks on stdin and a No stops the script.

    Until 2026-10-16 the script runner called ``ctx.ui.confirm`` itself, the
    CLI did not declare ``confirm_dialog``, and the run died with "Script
    error: ctx.ui.confirm requires confirm_dialog capability".  The CLI's
    y/n prompt is its confirmation now, and /confirm is the one plugin.
    """

    @staticmethod
    def _run(tmp_path, answer: str) -> str:
        """Run a script that confirms between two prints, feeding ``answer`` on stdin."""
        import subprocess
        import sys as _sys

        script = tmp_path / "ask.run"
        script.write_text("/print before\n/confirm go?\n/print reached-the-end\n", encoding="utf-8")
        cfg_root = tmp_path / "cfg"
        cfg_root.mkdir()
        result = subprocess.run(
            [_sys.executable, "-c",
             "import sys; "
             f"sys.argv = ['termapy', '--cli', '--demo', '--run', {str(script)!r}, "
             f"'--no-color', '--cfg-dir', {str(cfg_root)!r}]; "
             "from termapy.entry import main; main()"],
            input=answer,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout + result.stderr

    def test_yes_continues_the_script(self, tmp_path):
        """A y on stdin lets the line after /confirm run."""
        out = self._run(tmp_path, "y\n")
        assert "reached-the-end" in out, out
        assert "Script error" not in out, "the prompt is the CLI's confirmation"

    def test_no_stops_the_script(self, tmp_path):
        """An n on stdin stops the script before the next line."""
        out = self._run(tmp_path, "n\n")
        assert "Canceled." in out, out
        assert "reached-the-end" not in out, "a declined confirm stops the run"
        assert "Script error" not in out, "the prompt is the CLI's confirmation"


class TestHookRegistration:
    def test_hooks_registered(self, cli):
        """All expected CLI hooks are registered."""
        # Arrange
        expected = {
            # /delay is a builtin since 2026-10-16 (D2), not a CLI hook.
            # term.color / color moved to a portable builtin + legacy alias
            # (see TestTermColor); no longer CLI-registered hooks.
            "run", "run.profile",
            # /run.profile.* is the run builtin's since 2026-10-16 (D8).
            "run.profile.cmd", "run.profile.dump",
            "run.profile.list", "run.profile.show", "run.profile.explore",
            "demo", "demo.force", "clr", "raw", "help.open",
            "log.clear", "tui", "cli",
        }

        # Assert
        for name in expected:
            assert name in cli.repl._plugins, f"hook {name} not registered"


# -- --version / --help CLI flags ------------------------------------------


class TestCliCompletionHook:
    """``/cli.completion`` is a setting command: bare queries, a vocabulary token sets, toggle flips, anything else fails."""

    def test_bare_queries_without_changing_anything(self, cli):
        """A bare /cli.completion reports the cfg state and leaves it alone."""
        cli.cfg["cli_completion"] = True
        result = cli.repl.dispatch("cli.completion")
        assert (result.success, result.value) == (True, "on")
        assert cli.cfg["cli_completion"] is True, "a query never mutates"

    def test_off_disables_and_drops_the_session(self, cli):
        """off records the cfg value and drops the prompt session so the next prompt is plain."""
        cli.cfg["cli_completion"] = True
        result = cli.repl.dispatch("cli.completion off")
        assert (result.value, cli.cfg["cli_completion"], cli._session) == ("off", False, None)

    def test_toggle_flips(self, cli):
        """toggle flips the cfg value from off to on."""
        cli.cfg["cli_completion"] = False
        result = cli.repl.dispatch("cli.completion toggle")
        assert (result.value, cli.cfg["cli_completion"]) == ("on", True)

    def test_an_unknown_token_fails_instead_of_querying(self, cli):
        """A typo fails with the setting message; it used to read as a bare query and return ok."""
        cli.cfg["cli_completion"] = True
        result = cli.repl.dispatch("cli.completion maybe")
        assert result.success is False, "a token outside the vocabulary is refused"
        assert result.error == "Invalid value: maybe (use on/off/true/false/yes/no/1/0/y/n/t/f or toggle)"
        assert cli.cfg["cli_completion"] is True, "state unchanged on a refused token"


class TestVersionFlag:
    """``termapy --version`` is argparse's built-in version action.

    Cheap regression guard: the entry parser keeps the ``--version``
    flag wired to a real version string, not "unknown" or a crash.
    """

    def test_version_exits_zero_and_prints_termapy_version(
        self, capsys, monkeypatch,
    ):
        """--version exits 0 and prints termapy plus a non-empty version token."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--version"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        assert exc.value.code == 0, "--version exits 0"
        assert out.startswith("termapy "), (
            f"--version output starts with 'termapy ', got: {out!r}"
        )
        # The version token should be at least two dot-separated
        # numerics (or 'unknown' if running from a checkout without
        # installed metadata).  Either is acceptable -- a totally
        # empty version field is the regression we're guarding.
        version_token = out.strip().split(None, 1)[1]
        assert version_token, "version token is non-empty"


class TestHelpFlag:
    """``termapy --help`` is argparse's built-in help action.

    Catches regressions where someone deletes a flag definition or
    breaks the argparse setup.  We assert structural shape (many
    lines, key flag names present) rather than exact text so help
    string tweaks don't break the test.
    """

    def test_help_exits_zero_and_produces_substantive_output(
        self, capsys, monkeypatch,
    ):
        """--help exits 0 with a multi-line block, not a stub."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--help"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert exc.value.code == 0, "--help exits 0"
        assert len(lines) >= 40, (
            f"--help should produce a substantive multi-line block "
            f"(>= 40 lines); got {len(lines)} lines"
        )

    def test_help_documents_every_major_flag(self, capsys, monkeypatch):
        """Every user-facing flag in the curated set appears in --help."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--help"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit):
            main()

        # Assert -- the curated set is the user-facing surface area:
        # if any of these disappear from --help, somebody removed or
        # broke the argparse wiring.
        out = capsys.readouterr().out
        for flag in (
            "--version",
            "--cfg-dir",
            "--demo",
            "--cli",
            "--run",
            "--exec",
            "--ports",
            "--chips",
            "--info",
            "--watch",
            "--validate-profile",
            "--mcp",
        ):
            assert flag in out, (
                f"--help should document {flag!r}; missing from output"
            )

    def test_help_mentions_program_description(self, capsys, monkeypatch):
        """The parser description saying what termapy is survives in --help."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--help"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit):
            main()

        # Assert -- argparse renders the parser's description string
        # near the top.  Catches "someone wiped the description" too.
        out = capsys.readouterr().out
        assert "serial terminal" in out.lower(), (
            "--help should mention what termapy is"
        )


# -- --info CLI flag ---------------------------------------------------------


class TestInfoFlag:
    """Tests for the --info one-shot diagnostic flag in app.main().

    The --info flag prints serial-port chip identification to stdout
    and exits without launching the TUI or CLI interactive modes.  See
    plan: --info CLI flag for one-shot serial port chip diagnostics.
    """

    def test_info_no_args_exits_cleanly(self, capsys, monkeypatch):
        """Bare --info exits 0 or 1 with output whatever ports the machine has."""
        # Arrange - bare --info should query all ports.  We can't
        # assume the test machine has any serial ports, so we just
        # confirm the call exits with 0 or 1 (both are valid: 0 if
        # any port is connected, 1 if none) and produces some output.
        monkeypatch.setattr("sys.argv", ["termapy", "--info"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        actual_code = exc.value.code
        assert actual_code in (0, 1), f"--info exited cleanly, got {actual_code}"
        assert out, "--info produced some stdout output"

    def test_info_with_unknown_port_exits_nonzero(self, capsys, monkeypatch):
        """--info on an unknown port exits 1 with a not-found message."""
        # Arrange - a port name that almost certainly doesn't exist
        # on any test runner.
        monkeypatch.setattr(
            "sys.argv", ["termapy", "--info=DEFINITELY_NOT_A_PORT_999"]
        )
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        actual_code = exc.value.code
        expected_code = 1
        assert actual_code == expected_code, "unknown port exits with status 1"
        assert "No port matching" in out, "prints the not-found message"

    def test_info_with_equals_syntax(self, capsys, monkeypatch):
        """The --info=NAME form passes NAME through to the lookup."""
        # Arrange - --info=NAME should parse the same as --info NAME.
        monkeypatch.setattr(
            "sys.argv", ["termapy", "--info=COM_DOES_NOT_EXIST"]
        )
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit):
            main()

        # Assert
        out = capsys.readouterr().out
        assert "COM_DOES_NOT_EXIST" in out, "named port appears in output"

    def test_info_with_space_syntax(self, capsys, monkeypatch):
        """The --info NAME form parses the same as --info=NAME."""
        # Arrange - --info NAME should parse the same as --info=NAME.
        monkeypatch.setattr(
            "sys.argv", ["termapy", "--info", "ANOTHER_FAKE_PORT"]
        )
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit):
            main()

        # Assert
        out = capsys.readouterr().out
        assert "ANOTHER_FAKE_PORT" in out, "named port appears in output"

    def test_info_help_text_appears(self, capsys, monkeypatch):
        """--help documents the --info flag."""
        # Arrange - --help output should mention the new --info flag.
        monkeypatch.setattr("sys.argv", ["termapy", "--help"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        actual_code = exc.value.code
        expected_code = 0
        assert actual_code == expected_code, "--help exits with status 0"
        assert "--info" in out, "--info flag is documented in --help"


# -- --ports CLI flag (one-line-per-port table) ------------------------------


def _synthetic_facts(*entries):
    """Build a list[ChipFacts] from (device, manufacturer, model, vid_pid, serial) tuples."""
    from termapy.port_control import ChipFacts

    out = []
    for device, manufacturer, model, vid_pid, serial in entries:
        out.append(ChipFacts(
            device=device,
            description=f"USB Serial Port ({device})",
            manufacturer=manufacturer,
            model=model,
            vid_pid=vid_pid,
            serial=serial,
            usb_speed="USB Full-Speed (1 ms min latency)",
        ))
    return out


class TestPortsFlag:
    """Tests for --ports: print a one-line-per-port table and exit.

    These drive ``main()`` with a patched ``sys.argv``, so they cover the
    argparse-to-handler wiring rather than the listing behavior, and they
    take their fleet from ``TERMAPY_DEMO_FLEET`` (COM3 FTDI, COM4 Silicon
    Labs, COM7 Microsoft).  That is the right lever *here*: injection
    cannot reach through a process entry point, which is the whole reason
    the environment layer exists -- see ``port_control.resolve_port_source``.
    Tests about what the listing does hand a fleet in through ``source=``
    instead; see ``tests/test_cli_improvements.py``.
    """

    def test_ports_prints_header_and_one_row_per_port(self, capsys, monkeypatch):
        """--ports prints the header and a row per fleet port with aliased vendors."""
        # Arrange -- DEMO_FLEET gives a deterministic 3-port roster.
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        monkeypatch.setattr("sys.argv", ["termapy", "--ports"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        actual_code = exc.value.code
        assert actual_code == 0, "--ports with ports found exits 0"
        assert "PORT" in out and "MFG" in out and "VID:PID" in out, \
            "header row printed"
        assert "COM3" in out and "COM4" in out and "COM7" in out, \
            "every demo-fleet port appears"
        assert "MSFT" in out, "Microsoft manufacturer gets aliased to MSFT"
        assert "FTDI FT232R" in out, "chip model rendered"

    def test_ports_no_ports_exits_nonzero(self, capsys):
        """An empty fleet prints the no-ports marker and exits 1."""
        # Arrange -- an empty fleet.  The env hook can't express this
        # case (it always returns three ports), so this one drives the
        # handler directly and hands the emptiness in.
        import argparse

        from termapy import cli_flags

        args = argparse.Namespace(
            ports="*", json=False, vid=None, pid=None, mfg=None, sn=None
        )

        # Act
        with pytest.raises(SystemExit) as exc:
            cli_flags.run_ports(args, source=lambda: [])

        # Assert
        out = capsys.readouterr().out
        assert exc.value.code == 1, "empty port list exits 1"
        assert "(no ports found)" in out, "prints the empty marker"

    def test_ports_filter_matches_one(self, capsys, monkeypatch):
        """A --ports=NAME filter emits only the matching port's row."""
        # Arrange -- --ports=COM4 on the DEMO_FLEET 3-port roster.
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        monkeypatch.setattr("sys.argv", ["termapy", "--ports=COM4"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        assert exc.value.code == 0, "filter hit exits 0"
        assert "COM4" in out, "matched port in output"
        # COM3 and COM7 should NOT appear as data rows.  The header
        # text "PORT" might accidentally match so we check the row.
        data_lines = [data_line for data_line in out.splitlines() if data_line.startswith("COM")]
        assert all("COM4" in data_line for data_line in data_lines), \
            f"only COM4 row emitted, got: {data_lines}"

    def test_ports_filter_unknown_exits_nonzero(self, capsys, monkeypatch):
        """A filter with no match exits 1 with an error on stderr."""
        # Arrange -- DEMO_FLEET ports exist but NOPE999 doesn't match.
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        monkeypatch.setattr("sys.argv", ["termapy", "--ports=NOPE999"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        err = capsys.readouterr().err
        assert exc.value.code == 1, "no match exits 1"
        assert "No port matching" in err, "helpful error message"

    def test_ports_help_mentions_flag(self, capsys, monkeypatch):
        """--help documents the --ports flag."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--help"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit):
            main()

        # Assert
        out = capsys.readouterr().out
        assert "--ports" in out, "--ports is in --help"


# -- --chips CLI flag --------------------------------------------------------


class TestChipsFlag:
    """Tests for --chips: print the USB_SERIAL_CHIPS lookup table."""

    def test_chips_unfiltered_dumps_table(self, capsys, monkeypatch):
        """Bare --chips dumps the whole table, ending in a Count= line."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--chips"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        assert exc.value.code == 0, "--chips exits 0"
        assert "VID:PID" in out and "CHIP MODEL" in out, "header emitted"
        assert "FT232R" in out, "at least one known chip present"
        assert out.rstrip().splitlines()[-1].startswith("Count="), \
            "final line is Count=<N>"

    def test_chips_filter_narrows(self, capsys, monkeypatch):
        """A --chips filter keeps matching vendors and drops the rest."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--chips=ftdi"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        out = capsys.readouterr().out
        assert exc.value.code == 0, "filter exits 0"
        data_lines = [
            data_line for data_line in out.splitlines()
            if ":" in data_line and "FTDI" in data_line
        ]
        assert data_lines, "FTDI rows present"
        assert "Silicon Labs" not in out, "non-matching vendor excluded"


# -- --watch CLI flag (tricky: loop + KeyboardInterrupt) ---------------------


class TestWatchFlag:
    """Tests for --watch: event-line monitor with Ctrl+C exit."""

    def test_the_watch_flag_reaches_the_handler(self, monkeypatch):
        """argparse routes --watch to run_watch exactly once."""
        # Arrange -- the wiring half: argparse to run_watch, and nothing
        # more.  A recorder stands in for the handler because the real
        # one loops until interrupted, and the only lever that could stop
        # it from out here is time.sleep -- which is the SHARED module
        # object, so patching it reaches every thread in the process.  A
        # reader thread in a parallel test slept on it and took the
        # KeyboardInterrupt meant for this loop.
        from termapy import cli_flags

        monkeypatch.setattr("sys.argv", ["termapy", "--watch"])
        seen = []

        def _record(args, **kwargs):
            seen.append(args)
            raise SystemExit(0)  # the real handler always exits

        monkeypatch.setattr(cli_flags, "run_watch", _record)
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        assert exc.value.code == 0, "--watch exits 0"
        assert len(seen) == 1, f"--watch dispatches to run_watch once; got {seen}"

    def test_watch_exits_cleanly_on_keyboard_interrupt(self, capsys, monkeypatch):
        """Ctrl+C ends the watch loop with exit 0 after the baseline is shown."""
        # Arrange -- the behavior half: drive the handler directly with a
        # fleet that raises on the second look, standing in for the
        # user's Ctrl+C.  The poll interval is set to zero rather than
        # patching time.sleep, which would be global.
        import argparse

        from termapy import cli_flags

        monkeypatch.setattr(cli_flags, "_WATCH_INTERVAL_S", 0)
        baseline = _synthetic_facts(
            ("COM3", "FTDI", "FTDI FT232R", "0403:6001", "ABC"),
        )
        looks = [0]

        def _snapshots():
            looks[0] += 1
            if looks[0] == 1:
                return baseline
            raise KeyboardInterrupt

        # Act
        with pytest.raises(SystemExit) as exc:
            cli_flags.run_watch(argparse.Namespace(), source=_snapshots)

        # Assert
        out = capsys.readouterr().out
        assert exc.value.code == 0, "Ctrl+C exits 0"
        assert "monitoring 1 port" in out, "baseline banner printed"
        # Baseline rows have a blank event marker and the full state row.
        # State column shows "-" because fast-gather (used by --watch) skips
        # _check_in_use to keep the poll loop fast on multi-port systems.
        assert "COM3" in out, "baseline has COM3"
        assert "FTDI FT232R" in out, "baseline emits chip column"

    def test_watch_emits_add_and_remove_events(self, capsys, monkeypatch):
        """A changed snapshot emits a + line for the arrival and - for the loss."""
        # Arrange -- scripted sequence: baseline (1 port), then second
        # snapshot adds COM4 and removes COM3, then KeyboardInterrupt.
        import argparse

        from termapy import cli_flags

        baseline = _synthetic_facts(
            ("COM3", "FTDI", "FTDI FT232R", "0403:6001", "ABC"),
        )
        changed = _synthetic_facts(
            ("COM4", "FTDI", "FTDI FT230X", "0403:6015", "XYZ"),
        )
        call_count = [0]

        def _snapshots():
            call_count[0] += 1
            if call_count[0] == 1:
                return baseline
            if call_count[0] == 2:
                return changed
            raise KeyboardInterrupt

        # Zero the poll interval rather than patching time.sleep, which
        # is the shared module object and would reach other threads.
        monkeypatch.setattr(cli_flags, "_WATCH_INTERVAL_S", 0)

        # Act
        with pytest.raises(SystemExit):
            cli_flags.run_watch(argparse.Namespace(), source=_snapshots)

        # Assert -- '+' marker line for COM4 add, '-' marker line for
        # COM3 remove.  The marker is the first non-timestamp char
        # on the line.
        out = capsys.readouterr().out
        added_marker = [
            line for line in out.splitlines()
            if "] +" in line and "COM4" in line
        ]
        removed_marker = [
            line for line in out.splitlines()
            if "] -" in line and "COM3" in line
        ]
        assert added_marker, f"expected a '+ COM4' marker line, got: {out!r}"
        assert removed_marker, f"expected a '- COM3' marker line, got: {out!r}"

    def test_watch_help_mentions_flag(self, capsys, monkeypatch):
        """--help documents the --watch flag."""
        # Arrange
        monkeypatch.setattr("sys.argv", ["termapy", "--help"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit):
            main()

        # Assert
        out = capsys.readouterr().out
        assert "--watch" in out, "--watch in --help"


# -- Architectural guarantee: CLI flags do not import Textual ---------------


class TestCliFreeOfTextual:
    """Importing termapy.entry must not transitively import Textual.

    Guards the performance promise: ``termapy --ports`` should pay
    pyserial's import cost and nothing more.  Regressions here mean a
    CLI one-shot would suddenly take 300+ms and load 40MB of Textual.
    """

    def test_entry_import_does_not_load_textual(self):
        """The entry point, the root of every CLI one-shot, stays Textual-free."""
        # Arrange -- subprocess isolates the import graph.
        import subprocess
        import sys as _sys

        # Act
        result = subprocess.run(
            [_sys.executable, "-c",
             "import termapy.entry; "
             "import sys; "
             "assert 'textual' not in sys.modules, "
             "    list(m for m in sys.modules if m.startswith('textual'))"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Assert
        assert result.returncode == 0, (
            f"importing termapy.entry loaded textual.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_cli_flags_import_does_not_load_textual(self):
        """The one-shot flag handlers import without pulling in Textual."""
        # Arrange
        import subprocess
        import sys as _sys

        # Act
        result = subprocess.run(
            [_sys.executable, "-c",
             "import termapy.cli_flags; "
             "import sys; "
             "assert 'textual' not in sys.modules"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Assert
        assert result.returncode == 0, (
            f"importing termapy.cli_flags loaded textual.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_port_format_import_does_not_load_textual(self):
        """The port table formatter imports without pulling in Textual."""
        # Arrange
        import subprocess
        import sys as _sys

        # Act
        result = subprocess.run(
            [_sys.executable, "-c",
             "import termapy.port_format; "
             "import sys; "
             "assert 'textual' not in sys.modules"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Assert
        assert result.returncode == 0, (
            f"importing termapy.port_format loaded textual.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

class TestPlainFileHistory:
    """One history file per cfg, plain lines, shared by TUI and CLI."""

    def test_store_appends_plain_line(self, tmp_path):
        """Entries are stored as bare lines, the TUI's format, with no timestamps."""
        # Arrange
        path = tmp_path / "dev.history"
        hist = PlainFileHistory(str(path))

        # Act
        hist.store_string("/port.list")
        hist.store_string("/term.send hello")

        # Assert -- no timestamps, no + prefixes: the TUI's exact format
        actual = path.read_text(encoding="utf-8")
        expected = "/port.list\n/term.send hello\n"
        assert actual == expected, "plain lines only"

    def test_store_creates_a_missing_cfg_root(self, tmp_path):
        """With no config loaded the file sits in the cfg root, which the first write creates."""
        # Arrange -- the no-config history file in a root nothing has created
        path = tmp_path / "termapy" / ".cmd_history.txt"
        hist = PlainFileHistory(str(path))

        # Act
        hist.store_string("/ver")
        hist.store_string("/help")

        # Assert
        actual = path.read_text(encoding="utf-8")
        assert actual == "/ver\n/help\n", "both lines kept: the root was created, not lost"

    def test_load_yields_newest_first(self, tmp_path):
        """Loading reverses the file to newest first, as prompt_toolkit expects."""
        # Arrange -- a TUI-written file (plain lines, oldest first)
        path = tmp_path / "dev.history"
        path.write_text("first\nsecond\nthird\n", encoding="utf-8")

        # Act
        actual = list(PlainFileHistory(str(path)).load_history_strings())

        # Assert -- prompt_toolkit contract: newest first
        expected = ["third", "second", "first"]
        assert actual == expected, "reversed for prompt_toolkit"

    def test_legacy_prompt_toolkit_file_cleans_up(self, tmp_path):
        """Old FileHistory timestamps are skipped; leading + entries are kept as-is."""
        # Arrange -- a file written by the OLD FileHistory format
        path = tmp_path / "dev.history"
        path.write_text(
            "\n# 2026-07-14 09:00:00.000000\n+/port.list\n"
            "\n# 2026-07-14 09:00:05.000000\n+/ping\n",
            encoding="utf-8",
        )

        # Act
        actual = list(PlainFileHistory(str(path)).load_history_strings())

        # Assert -- timestamp comments skipped; "+cmd" entries kept verbatim
        # (a leading + is not stripped: "+++" is a legitimate command)
        expected = ["+/ping", "+/port.list"]
        assert actual == expected, "legacy timestamps dropped, entries survive"

    def test_missing_file_yields_nothing(self, tmp_path):
        """A missing history file loads as empty, not an error."""
        # Act
        actual = list(
            PlainFileHistory(str(tmp_path / "absent.history")).load_history_strings()
        )

        # Assert
        assert actual == [], "no file, no history, no error"

    def test_multiline_input_flattened(self, tmp_path):
        """Embedded newlines become spaces so one entry stays one line."""
        # Arrange
        path = tmp_path / "dev.history"
        hist = PlainFileHistory(str(path))

        # Act -- plain-lines format cannot hold embedded newlines
        hist.store_string("line1\nline2")

        # Assert
        actual = path.read_text(encoding="utf-8")
        assert actual == "line1 line2\n", "newlines flattened to spaces"

    def test_tui_reads_cli_written_file_as_lines(self, tmp_path):
        """A CLI-written history reads back in the TUI as exactly its commands."""
        # Arrange -- CLI writes via PlainFileHistory
        path = tmp_path / "dev.history"
        hist = PlainFileHistory(str(path))
        hist.store_string("/run at_demo")
        hist.store_string("/ping")

        # Act -- the TUI loader is a plain splitlines() read
        actual = path.read_text(encoding="utf-8").splitlines()

        # Assert -- byte-level interop: TUI sees exactly the commands
        expected = ["/run at_demo", "/ping"]
        assert actual == expected, "TUI plain-lines loader sees CLI entries"


class TestWatchPortWidth:
    """Port-column width: platform floor grown to the initial fleet."""

    def test_floor_wins_over_short_devices(self):
        """Short port names never shrink the column below the floor."""
        # Arrange / Act -- explicit floor so the test is platform-free
        from termapy.cli_flags import _watch_port_width
        actual = _watch_port_width(["COM3", "COM10"], floor=6)

        # Assert
        assert actual == 6, "short names never shrink below the floor"

    def test_longest_device_wins_over_floor(self):
        """A device name longer than the floor sets the column width."""
        # Arrange / Act
        from termapy.cli_flags import _watch_port_width
        actual = _watch_port_width(
            ["/dev/cu.Bluetooth-Incoming-Port", "/dev/ttyUSB0"], floor=6
        )

        # Assert
        expected = len("/dev/cu.Bluetooth-Incoming-Port")
        assert actual == expected, "fleet's longest name sets the width"

    def test_empty_fleet_gives_the_floor(self):
        """With no ports at start, the width is the floor so later rows pad."""
        # Arrange / Act -- watch can start with zero ports
        from termapy.cli_flags import _watch_port_width
        actual = _watch_port_width([], floor=32)

        # Assert
        assert actual == 32, "no ports -> floor, so later rows still pad"

    def test_mac_shaped_fleet_aligns_columns(self, capsys, monkeypatch):
        """Mixed-length macOS names pad to one width so later columns align."""
        # Arrange -- the reported defect: macOS device names vary wildly
        # in length (COMxxx never does), so the port column must be
        # padded to the fleet max or every later column drifts per row.
        import argparse

        from termapy import cli_flags

        fleet = _synthetic_facts(
            ("/dev/cu.Bluetooth-Incoming-Port", "-", "-", "0000:0000", "-"),
            ("/dev/cu.usbserial-BG03U7VT", "FTDI", "FTDI FT232R / FT245R",
             "0403:6001", "BG03U7VT"),
            ("/dev/cu.wlan-debug", "-", "-", "0000:0000", "-"),
        )
        monkeypatch.setattr(cli_flags, "_WATCH_INTERVAL_S", 0)
        looks = [0]

        def _snapshots():
            looks[0] += 1
            if looks[0] == 1:
                return fleet
            raise KeyboardInterrupt

        # Act
        with pytest.raises(SystemExit):
            cli_flags.run_watch(argparse.Namespace(), source=_snapshots)

        # Assert -- every state row's post-port columns start at the SAME
        # offset.  The state column value is deterministic here ("-" or
        # "closed"), but the alignment claim is about position, so anchor
        # on the vid_pid column value, unique per row and present in all.
        out = capsys.readouterr().out
        rows = [line for line in out.splitlines() if "/dev/cu." in line]
        assert len(rows) == 3, "one state row per fleet port"
        vid_offsets = {
            row.find("0403:6001") if "0403:6001" in row else row.find("0000:0000")
            for row in rows
        }
        assert len(vid_offsets) == 1, (
            f"vid_pid column at one offset across rows; got {sorted(vid_offsets)}"
        )

    def test_late_long_arrival_grows_the_column(self, capsys, monkeypatch):
        """A long late arrival grows the column for all later rows; it never shrinks."""
        # Arrange -- baseline is one short name; snapshot 2 adds a much
        # longer one; snapshot 3 re-emits an event on the SHORT port
        # (open-state flip).  Grow-only width means the snapshot-3 row
        # must align with the long row, not with the baseline.
        import argparse

        from termapy import cli_flags
        from termapy.port_control import ChipFacts

        short = _synthetic_facts(
            ("/dev/ttyUSB0", "FTDI", "FTDI FT232R", "0403:6001", "AAA"),
        )
        long_fleet = short + _synthetic_facts(
            ("/dev/cu.usbserial-VERYLONGNAME0123456789", "FTDI",
             "FTDI FT230X", "0403:6015", "BBB"),
        )
        flipped = [
            ChipFacts(**{**vars(facts), "in_use": "yes"})
            for facts in long_fleet
        ]
        monkeypatch.setattr(cli_flags, "_WATCH_INTERVAL_S", 0)
        snapshots = [short, long_fleet, flipped]
        looks = [0]

        def _feed():
            looks[0] += 1
            if looks[0] <= len(snapshots):
                return snapshots[looks[0] - 1]
            raise KeyboardInterrupt

        # Act
        with pytest.raises(SystemExit):
            cli_flags.run_watch(argparse.Namespace(), source=_feed)

        # Assert -- rows printed AFTER the long arrival share one
        # vid_pid offset (grown width); the baseline row keeps its
        # smaller offset (append-only: the seam is expected).
        out = capsys.readouterr().out
        state_rows = [
            line for line in out.splitlines()
            if "0403:" in line
        ]
        assert len(state_rows) >= 3, f"baseline + add + flip rows; got:\n{out}"
        baseline_offset = state_rows[0].index("0403:6001")
        grown_offsets = {
            row.index("0403:") for row in state_rows[1:]
        }
        assert len(grown_offsets) == 1, (
            f"post-growth rows share one offset; got {sorted(grown_offsets)}"
        )
        assert grown_offsets.pop() > baseline_offset, (
            "the seam: post-growth offset exceeds the baseline's"
        )
