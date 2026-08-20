"""Unit tests for CLITerminal - hooks, helpers, output, and dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from termapy.cli import CLITerminal, PlainFileHistory
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import CapabilitySet, UsageError

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
    caps = cli.ctx.capabilities.union(CapabilitySet(gui_apps=True))
    cli.ctx.capabilities = caps
    cli.ctx.fs.capabilities = caps


# -- Output methods ----------------------------------------------------------


class TestOutput:
    def test_write_plain(self, cli, capsys):
        # Act
        cli.write("hello world")

        # Assert
        actual = capsys.readouterr().out
        assert "hello world" in actual, "text appears in stdout"

    def test_write_with_color(self, cli, capsys):
        # Act
        cli.write("error msg", "red")

        # Assert
        actual = capsys.readouterr().out
        assert "error msg" in actual, "text appears even with color arg"

    def test_status_indented(self, cli, capsys):
        # Act
        cli.status("info line")

        # Assert
        actual = capsys.readouterr().out
        assert "info line" in actual, "status text appears"

    def test_status_with_color(self, cli, capsys):
        # Act
        cli.status("warning", "yellow")

        # Assert
        actual = capsys.readouterr().out
        assert "warning" in actual, "colored status appears"

    def test_raw_output(self, cli, capsys):
        # Act
        cli._raw("raw text")

        # Assert
        actual = capsys.readouterr().out
        assert "raw text" in actual, "raw text bypasses Rich"

    def test_err_output(self, cli, capsys):
        # Act
        cli._err("error text")

        # Assert
        actual = capsys.readouterr().err
        assert "error text" in actual, "error goes to stderr"


# -- Hook: delay -------------------------------------------------------------


class TestHookDelay:
    def test_short_delay(self, cli):
        # Act
        result = cli._hook_delay(cli.ctx, "10ms")

        # Assert
        assert result.success, "short delay completes ok"

    def test_invalid_duration(self, cli):
        # Act
        result = cli._hook_delay(cli.ctx, "xyz")

        # Assert
        assert not result.success, "invalid duration fails"

    def test_delay_quiet(self, cli):
        # Act
        result = cli._hook_delay_quiet(cli.ctx, "10ms")

        # Assert
        assert result.success, "quiet delay completes ok"

    def test_delay_quiet_invalid(self, cli):
        # Act
        result = cli._hook_delay_quiet(cli.ctx, "bad")

        # Assert
        assert not result.success, "invalid duration fails"


# -- /term.color (portable device-color toggle) -----------------------------


class TestTermColor:
    """/term.color is now a portable builtin backed by flags['color']."""

    def test_color_on(self, cli):
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = False

        # Act
        result = cli.repl.dispatch("term.color on")

        # Assert
        assert result.success, "command succeeds"
        assert cli.repl.ctx.ns("flags")["color"] is True, "color enabled"

    def test_color_off(self, cli):
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act
        result = cli.repl.dispatch("term.color off")

        # Assert
        assert result.success, "command succeeds"
        assert cli.repl.ctx.ns("flags")["color"] is False, "color disabled"

    def test_color_bare_queries(self, cli):
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act -- bare invocation QUERIES; it must NOT mutate
        cli.repl.dispatch("term.color")

        # Assert
        assert cli.repl.ctx.ns("flags")["color"] is True, "bare query leaves state"

    def test_color_toggle_verb(self, cli):
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act
        cli.repl.dispatch("term.color toggle")

        # Assert
        assert cli.repl.ctx.ns("flags")["color"] is False, "toggle verb flips"

    def test_color_invalid_arg_errors(self, cli):
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act
        result = cli.repl.dispatch("term.color 2")

        # Assert -- invalid arg errors, never a silent flip
        assert result.success is False, "invalid arg fails"
        assert result.error == "Invalid value: 2 (use on/off/toggle)", "clear msg"
        assert cli.repl.ctx.ns("flags")["color"] is True, "state unchanged"

    def test_legacy_color_alias(self, cli):
        # Arrange
        cli.repl.ctx.ns("flags")["color"] = True

        # Act -- /color forwards to /term.color
        cli.repl.dispatch("color off")

        # Assert
        assert cli.repl.ctx.ns("flags")["color"] is False, "/color forwards to /term.color"


# -- Hook: raw ---------------------------------------------------------------


class TestHookRaw:
    def test_raw_not_connected(self, cli):
        # Arrange -- fixture is disconnected by default.
        # Act
        result = cli._hook_raw(cli.ctx, "hello")

        # Assert
        assert not result.success, "fails when disconnected"

    def test_raw_no_args(self, cli):
        # Arrange -- connect to DEMO so the "no args" branch is reached.
        cli._connect()

        # Act / Assert -- bad arity raises; the dispatcher renders the
        # usage line from the declaration (see test_usage_error.py).
        with pytest.raises(UsageError):
            cli._hook_raw(cli.ctx, "")

    def test_raw_sends_data(self, cli):
        # Arrange -- real connect to DEMO.  FakeSerial records bytes
        # in ``_input_buf`` once ``_process_input`` consumes them, so
        # we send something DEMO will NOT auto-consume by picking a
        # no-line-ending AT prefix and inspecting the buffer.
        cli._connect()

        # Act
        result = cli._hook_raw(cli.ctx, "AT")

        # Assert -- FakeSerial processes input when a line ending is
        # seen; /raw sends no line ending, so the bytes sit in the
        # input buffer where we can verify them.
        assert result.success, "command succeeds"
        actual = bytes(cli.engine.port_obj._input_buf)
        assert actual == b"AT", f"bytes sent verbatim, got {actual!r}"


# -- Hook: run ---------------------------------------------------------------


class TestHookRun:
    def test_run_bare_shows_help(self, cli, capsys):
        # Arrange -- bare /run in CLI shows /help run for parity with
        # /cfg, /proto, /port (TUI opens the Run picker; CLI has no
        # picker so the builtin's open_picker fallback prints help).
        # Act
        result = cli.repl.dispatch("run")

        # Assert
        assert result.success, "bare /run succeeds"
        actual = capsys.readouterr().out
        assert "/run" in actual, "help output mentions /run"
        assert "SUBCOMMANDS" in actual or "Synopsis" in actual or "SYNOPSIS" in actual, (
            "help output has the man-page shape"
        )

    def test_run_list_enumerates_scripts(self, cli, capsys):
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
        # Act -- /run <nonexistent> goes through the builtin handler,
        # which delegates to engine.start_script for resolution.
        result = cli.repl.dispatch("run nonexistent.run")

        # Assert
        assert not result.success, "missing script fails"


# -- /edit.cfg in CLI (audit item #3) ---------------------------------------


class TestEditCfgCallableInCli:
    """``/edit.cfg`` is registered as a sub_command of the ``edit``
    built-in (see ``builtins/commands/edit.py``), so CLI gets it for
    free -- TUI overrides with a modal config editor, but the builtin
    handler in CLI spawns the system editor on the cfg file.  MCP
    correctly hides the command via ``needs.gui_apps``.

    The CLI/MCP parity audit (notes/cli-mcp-parity-audit.md, item 3)
    initially flagged this as a gap; verification showed it already
    works.  These tests pin the behavior so a refactor of the
    ``edit`` builtin cannot silently regress CLI access.
    """

    def test_edit_cfg_registered_in_cli(self, cli):
        # Arrange / Act
        plugin = cli.repl._plugins.get("edit.cfg")

        # Assert
        assert plugin is not None, "edit.cfg present in CLI plugin set"
        assert plugin.needs.gui_apps, (
            "edit.cfg needs gui_apps so headless CLI / MCP correctly gate it"
        )

    def test_edit_cfg_opens_config_file_when_gui_apps_available(self, cli):
        # Arrange -- force the gui_apps gate open and capture the impl
        # call without spawning a real editor.
        from termapy.plugins import CapabilitySet

        cli.ctx.capabilities = CapabilitySet(interactive=True, gui_apps=True)
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


# -- Hook: run.profile.* (CLI parity with TUI) -------------------------------


class TestHookRunProfile:
    """``/run.profile.*`` subcommands installed by
    ``run_profile_hooks.register_run_profile_hooks``.  CLI gained
    these in chore/run-profile-cli-parity; tests guard the parity
    so future CLI refactors don't silently drop them again.
    """

    def test_list_empty_prof_dir(self, cli, capsys):
        # Act
        result = cli.repl.dispatch("run.profile.list")

        # Assert
        actual = capsys.readouterr().out
        assert result.success, "empty prof/ is a success, not an error"
        assert "no profile files" in actual, "reports empty state"

    def test_list_enumerates_csvs(self, cli, capsys):
        # Arrange
        prof_dir = cli._prof_dir()
        assert prof_dir is not None, "fixture cfg gives prof_dir"
        prof_dir.mkdir(exist_ok=True)
        (prof_dir / "first.csv").write_text("")
        (prof_dir / "second.csv").write_text("")

        # Act
        cli.repl.dispatch("run.profile.list")

        # Assert
        actual = capsys.readouterr().out
        assert "first.csv" in actual, "first profile listed"
        assert "second.csv" in actual, "second profile listed"

    def test_dump_empty_prof_dir_fails(self, cli):
        # Act
        result = cli.repl.dispatch("run.profile.dump")

        # Assert
        assert not result.success, "no profile to dump"

    def test_dump_prints_newest_csv(self, cli, capsys):
        # Arrange
        prof_dir = cli._prof_dir()
        assert prof_dir is not None, "fixture cfg gives prof_dir"
        prof_dir.mkdir(exist_ok=True)
        prof_path = prof_dir / "demo.csv"
        prof_path.write_text("step,dt_ms\n0,1.5\n")

        # Act
        cli.repl.dispatch("run.profile.dump")

        # Assert
        actual = capsys.readouterr().out
        assert "step,dt_ms" in actual, "header line printed"
        assert "0,1.5" in actual, "data line printed"

    def test_dump_named_file_not_found(self, cli):
        # Act
        result = cli.repl.dispatch("run.profile.dump missing.csv")

        # Assert
        assert not result.success, "named file that doesn't exist fails"

    def test_show_no_profiles_fails(self, cli):
        # Act
        result = cli.repl.dispatch("run.profile.show")

        # Assert
        assert not result.success, "nothing to show with empty prof/"

    def test_show_opens_newest(self, cli):
        # Arrange -- explicitly stagger mtimes via os.utime; Windows
        # NTFS mtime resolution is coarse enough that two write_text()
        # calls in the same tick land on the same timestamp, and the
        # sort by mtime falls back to alphabetical (where 'newer' <
        # 'older'), causing the wrong file to be picked as "newest."
        import os

        prof_dir = cli._prof_dir()
        assert prof_dir is not None, "fixture cfg gives prof_dir"
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
        # Arrange
        prof_dir = cli._prof_dir()
        assert prof_dir is not None, "fixture cfg gives prof_dir"
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
        assert opened == [str(prof_dir)], "opened prof/ folder via ctx.fs.open_file"

    def test_cmd_empty_fails_with_usage(self, cli, capsys):
        # Act
        result = cli.repl.dispatch("run.profile.cmd")

        # Assert
        assert not result.success, "empty arg fails"
        actual = capsys.readouterr().out
        assert "Usage:" in actual, "usage message shown"


# -- Hook: log.clear ---------------------------------------------------------


class TestHookLogClear:
    def test_no_log_file(self, cli):
        # Act
        result = cli._hook_log_delete(cli.ctx, "")

        # Assert
        assert not result.success, "no log file to delete"

    def test_delete_log(self, cli):
        # Arrange -- write the log file at the path the real
        # cfg_log_path() would compute, so the test exercises the
        # real resolution logic instead of a patched return value.
        from termapy.config import cfg_log_path

        log_path = Path(cfg_log_path(cli.config_path))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log data")

        # Act
        result = cli._hook_log_delete(cli.ctx, "")

        # Assert
        assert result.success, "deletion succeeds"
        assert not log_path.exists(), "file removed"


# -- Connect / Disconnect ---------------------------------------------------


class TestConnect:
    def test_connect_already_connected(self, cli, capsys):
        # Arrange -- connect once for real, then try again.
        cli._connect()
        capsys.readouterr()  # drain the first-connect banner

        # Act
        cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Already connected" in actual, "warns already connected"

    def test_connect_success(self, cli, capsys):
        # Act -- real DEMO connect, no mocks.
        cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Connected" in actual, "reports connection"
        assert cli.engine.is_connected, "engine reports connected state"

    def test_connect_failure(self, cli, capsys):
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
        # Act -- override with another port name (still DEMO) so the
        # cfg-update branch runs.
        cli._connect(port="DEMO")

        # Assert
        assert cli.cfg["serial"]["port"] == "DEMO", (
            "port updated in config at migrated location"
        )
        assert cli.engine.is_connected, "engine connected after override"

    def test_port_connect_command_reports_failure(self, cli):
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
        # Act -- the happy path still returns ok.
        result = cli.repl.dispatch("port.connect DEMO")

        # Assert
        assert result.success, "connect to DEMO reports success"
        assert cli.engine.is_connected, "engine connected after a successful connect"

    def test_disconnect_not_connected(self, cli, capsys):
        # Arrange -- fixture is disconnected by default.
        # Act
        cli._disconnect()

        # Assert
        actual = capsys.readouterr().out
        assert "Not connected" in actual, "warns not connected"

    def test_disconnect_success(self, cli, capsys):
        # Arrange -- real connect, then real disconnect.
        cli._connect()
        capsys.readouterr()  # drain connect banner

        # Act
        cli._disconnect()

        # Assert
        assert not cli.engine.is_connected, "engine reports disconnected"
        actual = capsys.readouterr().out
        assert "Disconnected" in actual, "reports disconnection"


# -- serial_write_raw --------------------------------------------------------


class TestSerialWriteRaw:
    def test_not_connected(self, cli, capsys):
        # Arrange -- fixture is disconnected by default.
        # Act
        cli._serial_write_raw("AT")

        # Assert
        actual = capsys.readouterr().out
        assert "Not connected" in actual, "warns not connected"

    def test_sends_with_line_ending(self, cli, capsys):
        # Arrange -- real DEMO connect; DEMO responds to AT<CRLF> with
        # OK<CRLF>.  Seeing "OK" in stdout proves that (a) the bytes
        # were sent, (b) DEMO recognized them as a complete AT command,
        # which means the line ending was applied correctly.
        cli._connect()
        cli.cfg["eol"] = "\r\n"

        # Act
        cli._serial_write_raw("AT")

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
        # Arrange
        effects = {"cfg_update": {"baud_rate": 9600}}

        # Act
        cli._apply_port_effects(effects)

        # Assert
        assert cli.repl._cfg_data["serial"]["baud_rate"] == 9600, (
            "config updated at migrated location (cfg['serial']['baud_rate'])"
        )

    def test_empty_effects(self, cli):
        # Act / Assert - no exception on empty effects
        cli._apply_port_effects({})


# -- Confirm -----------------------------------------------------------------


class TestConfirm:
    def test_confirm_yes(self, cli):
        # Act
        with patch("builtins.input", return_value="y"):
            actual = cli._confirm("Continue?")

        # Assert
        assert actual is True, "y means yes"

    def test_confirm_no(self, cli):
        # Act
        with patch("builtins.input", return_value="n"):
            actual = cli._confirm("Continue?")

        # Assert
        assert actual is False, "n means no"

    def test_confirm_empty(self, cli):
        # Act
        with patch("builtins.input", return_value=""):
            actual = cli._confirm("Continue?")

        # Assert
        assert actual is False, "empty defaults to no"

    def test_confirm_eof(self, cli):
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
        completer = _TermapyCompleter(cli.repl, cli.prefix, cli.config_path)

        # Assert
        assert completer._prefix == "/", "prefix wired"
        assert completer._repl is cli.repl, "repl wired"


# -- Hook registration ------------------------------------------------------


class TestHookRegistration:
    def test_hooks_registered(self, cli):
        """All expected CLI hooks are registered."""
        # Arrange
        expected = {
            "delay", "delay.silent",
            # term.color / color moved to a portable builtin + legacy alias
            # (see TestTermColor); no longer CLI-registered hooks.
            "run", "run.profile",
            # /run.profile.* subcommands installed by
            # run_profile_hooks.register_run_profile_hooks().  CLI gained
            # these in chore/run-profile-cli-parity for TUI/CLI parity.
            "run.profile.cmd", "run.profile.dump",
            "run.profile.list", "run.profile.show", "run.profile.explore",
            "demo", "demo.force", "clr", "raw", "help.open",
            "log.clear", "tui", "cli",
        }

        # Assert
        for name in expected:
            assert name in cli.repl._plugins, f"hook {name} not registered"


# -- --version / --help CLI flags ------------------------------------------


class TestVersionFlag:
    """``termapy --version`` is argparse's built-in version action.

    Cheap regression guard: the entry parser keeps the ``--version``
    flag wired to a real version string, not "unknown" or a crash.
    """

    def test_version_exits_zero_and_prints_termapy_version(
        self, capsys, monkeypatch,
    ):
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

    def test_load_yields_newest_first(self, tmp_path):
        # Arrange -- a TUI-written file (plain lines, oldest first)
        path = tmp_path / "dev.history"
        path.write_text("first\nsecond\nthird\n", encoding="utf-8")

        # Act
        actual = list(PlainFileHistory(str(path)).load_history_strings())

        # Assert -- prompt_toolkit contract: newest first
        expected = ["third", "second", "first"]
        assert actual == expected, "reversed for prompt_toolkit"

    def test_legacy_prompt_toolkit_file_cleans_up(self, tmp_path):
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
        # Act
        actual = list(
            PlainFileHistory(str(tmp_path / "absent.history")).load_history_strings()
        )

        # Assert
        assert actual == [], "no file, no history, no error"

    def test_multiline_input_flattened(self, tmp_path):
        # Arrange
        path = tmp_path / "dev.history"
        hist = PlainFileHistory(str(path))

        # Act -- plain-lines format cannot hold embedded newlines
        hist.store_string("line1\nline2")

        # Assert
        actual = path.read_text(encoding="utf-8")
        assert actual == "line1 line2\n", "newlines flattened to spaces"

    def test_tui_reads_cli_written_file_as_lines(self, tmp_path):
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
