"""breadcrumbs: the file each running termapy leaves, and how --kill reads, picks and stops them."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from termapy import breadcrumbs
from termapy.breadcrumbs import Breadcrumb, Crumb


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """A private app state folder, so each test sees only its own breadcrumbs."""
    folder = tmp_path / "state"
    monkeypatch.setenv("TERMAPY_STATE_DIR", str(folder))
    return folder


@contextmanager
def _child_session(state_dir, *, config: str = "rig.cfg", open_port: str = "COM9"):
    """A child process that leaves a breadcrumb and waits; yields it and its breadcrumb.

    The breadcrumb is found by what it holds: on Windows a venv's python.exe
    is a launcher whose own child runs the code, so the breadcrumb's PID is
    not ``child.pid``.  Ending the interpreter ends its launcher too, which is
    why the cleanup stops the breadcrumb's process rather than ``child``.
    """
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time\n"
            "from termapy.breadcrumbs import Breadcrumb\n"
            f"Breadcrumb().write('cli', config={config!r}, open_port={open_port!r})\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "TERMAPY_STATE_DIR": str(state_dir)},
    )
    crumb = None
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready", "the child wrote its breadcrumb"
        crumb = next(crumb for crumb in breadcrumbs.read_running() if crumb.open_port == open_port)
        yield child, crumb
    finally:
        if crumb is not None:
            breadcrumbs.stop(crumb)
        if child.poll() is None:
            child.kill()
        if child.stdout is not None:
            child.stdout.close()


def _crumb(**fields) -> Crumb:
    """A breadcrumb for this test process, with any field replaced."""
    base = {
        "pid": os.getpid(),
        "token": breadcrumbs.process_token(os.getpid()) or "",
        "started": "2026-10-31T09:00:00",
        "frontend": "cli",
        "config": "C:/proj/termapy_cfg/rig/rig.cfg",
        "port": "COM7",
        "open_port": "COM7",
    }
    base.update(fields)
    return Crumb(**base)


class TestProcessToken:
    """A process is identified by its PID and its start time together, since a PID is reused."""

    def test_a_running_process_has_a_stable_token(self):
        """The same live process gives the same token on every call."""
        # Act
        first = breadcrumbs.process_token(os.getpid())
        second = breadcrumbs.process_token(os.getpid())

        # Assert
        assert first, "a running process has a token"
        assert first == second, "the token names the process, so it does not change"

    def test_a_process_that_has_exited_has_none(self):
        """Once a process exits its PID has no token, so its breadcrumb reads as stale."""
        # Arrange
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)

        # Act
        actual = breadcrumbs.process_token(child.pid)

        # Assert
        assert actual is None, "an exited process is not running"


class TestBreadcrumb:
    """This process's breadcrumb: written, rewritten, removed."""

    def test_a_write_names_the_session(self, state_dir):
        """The file holds the frontend, config, ports and the token that proves the PID."""
        # Arrange
        crumb = Breadcrumb()

        # Act
        crumb.write("mcp", config="rig.cfg", port="SN:BG03U7VTA", open_port="COM7")

        # Assert
        actual = json.loads((state_dir / "running" / f"{os.getpid()}.json").read_text("utf-8"))
        expected = {
            "pid": os.getpid(),
            "token": breadcrumbs.process_token(os.getpid()),
            "frontend": "mcp",
            "config": "rig.cfg",
            "port": "SN:BG03U7VTA",
            "open_port": "COM7",
        }
        assert {key: actual[key] for key in expected} == expected

    def test_a_second_write_replaces_the_first(self, state_dir):
        """Twice in a row: a disconnect rewrites the one file, so --kill sees the port released."""
        # Arrange
        crumb = Breadcrumb()
        crumb.write("cli", config="rig.cfg", port="COM7", open_port="COM7")

        # Act
        crumb.write("cli", config="rig.cfg", port="COM7", open_port="")

        # Assert
        files = list((state_dir / "running").glob("*.json"))
        assert len(files) == 1, "one process, one breadcrumb"
        assert breadcrumbs.read_running()[0].open_port == "", "the latest write is what is read"

    def test_remove_deletes_it(self, state_dir):
        """At exit the breadcrumb goes, so a cleanly closed session leaves nothing behind."""
        # Arrange
        crumb = Breadcrumb()
        crumb.write("cli")

        # Act
        crumb.remove()

        # Assert
        assert breadcrumbs.read_running() == [], "nothing left to find"


class TestReadRunning:
    """Only breadcrumbs whose process is still running count, and reading deletes nothing."""

    def _plant(self, state_dir, crumb: Crumb) -> None:
        """Write ``crumb`` as a file, the way a session would have."""
        folder = state_dir / "running"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{crumb.pid}.json").write_text(json.dumps(vars(crumb)), encoding="utf-8")

    def test_a_dead_process_is_skipped_and_its_file_kept(self, state_dir):
        """A crashed session's breadcrumb is not listed, and a read leaves the file for prune_stale."""
        # Arrange
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        self._plant(state_dir, _crumb(pid=child.pid, token="1234"))

        # Act
        actual = breadcrumbs.read_running()

        # Assert
        assert actual == [], "a process that is gone is not running"
        assert (state_dir / "running" / f"{child.pid}.json").exists(), "a read never writes"

    def test_a_reused_pid_is_not_the_same_process(self, state_dir):
        """kill-verifies-identity: a live PID with another start token is another program, never listed."""
        # Arrange -- this process's PID with a token from some earlier process
        self._plant(state_dir, _crumb(token="not-this-process"))

        # Act
        actual = breadcrumbs.read_running()

        # Assert
        assert actual == [], "the PID is alive but belongs to a different process"

    def test_an_unreadable_file_is_skipped(self, state_dir):
        """A half-written or foreign file in the folder is ignored, not an error."""
        # Arrange
        folder = state_dir / "running"
        folder.mkdir(parents=True)
        (folder / "123.json").write_text("{not json", encoding="utf-8")

        # Act
        actual = breadcrumbs.read_running()

        # Assert
        assert actual == []

    def test_prune_stale_removes_only_the_dead(self, state_dir):
        """prune_stale deletes a dead session's breadcrumb and keeps a live one."""
        # Arrange
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        self._plant(state_dir, _crumb(pid=child.pid, token="1234"))
        Breadcrumb().write("cli")

        # Act
        pruned = breadcrumbs.prune_stale()

        # Assert
        remaining = sorted(path.name for path in (state_dir / "running").glob("*.json"))
        assert (pruned, remaining) == (1, [f"{os.getpid()}.json"]), "only the live one stays"


class TestSelect:
    """--kill's target: all, a port, or a config name."""

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("all", [1, 2, 3]),
            ("ALL", [1, 2, 3]),
            ("others", [1, 2, 3]),
            ("COM7", [1]),
            ("COM4", [3]),
            ("rig", [1]),
            ("RIG", [1]),
            ("rig*", [1, 2]),
            ("nothing", []),
        ],
        ids=[
            "all",
            "all, any case",
            "others: all, until a session drops itself",
            "the port open now",
            "the config's port, not open",
            "a config name",
            "a config name, any case",
            "a glob picks several",
            "no match",
        ],
    )
    def test_the_target_picks(self, target, expected):
        """A port matches the open port or the config's setting; a name matches the config's stem."""
        # Arrange
        crumbs = [
            _crumb(pid=1, config="a/rig.cfg", port="COM7", open_port="COM7"),
            _crumb(pid=2, config="b/rig2.cfg", port="COM8", open_port="COM8"),
            _crumb(pid=3, config="c/other.cfg", port="COM4", open_port=""),
        ]

        # Act
        actual = [crumb.pid for crumb in breadcrumbs.select(crumbs, target)]

        # Assert
        assert actual == expected


class TestStop:
    """Stopping ends the named process, and only that process."""

    def test_a_reused_pid_is_never_signalled(self, state_dir):
        """kill-verifies-identity: a breadcrumb whose PID now belongs to another process signals nothing.

        The PID here is this test's own, so a signal would end the test run.
        """
        # Arrange
        crumb = _crumb(token="not-this-process")

        # Act
        actual = breadcrumbs.stop(crumb)

        # Assert -- reaching this line is the proof nothing was signalled
        assert actual is True, "the process the breadcrumb named is not running"

    @pytest.mark.slow
    def test_stop_ends_a_session_and_removes_its_breadcrumb(self, state_dir):
        """A live session's process ends and its breadcrumb is deleted."""
        with _child_session(state_dir) as (child, crumb):
            # Act
            actual = breadcrumbs.stop(crumb)

            # Assert
            assert actual is True, "stop reports the process gone"
            assert child.wait(timeout=10) is not None, "the process and its launcher have exited"
            assert not (state_dir / "running" / f"{crumb.pid}.json").exists(), (
                "its breadcrumb is gone"
            )


class TestKillFlag:
    """termapy --kill: bare lists, a target asks and stops."""

    @staticmethod
    def _run(target: str, *, answer: bool = False) -> int:
        """Run --kill with ``target``, answering the question with ``answer``; return the exit code."""
        from termapy.cli_flags import run_kill

        with pytest.raises(SystemExit) as exit_info:
            run_kill(argparse.Namespace(kill=target), confirm=lambda question: answer)
        return int(exit_info.value.code or 0)

    def test_nothing_running_says_so(self, state_dir, capsys):
        """With no session running, --kill says so and succeeds."""
        # Act
        code = self._run("")

        # Assert
        assert (code, capsys.readouterr().out.strip()) == (0, "No termapy is running.")

    def test_bare_lists_and_stops_nothing(self, state_dir, capsys):
        """Bare --kill is a query: it lists the running sessions and leaves them running."""
        # Arrange
        Breadcrumb().write("mcp", config="a/rig.cfg", port="COM7", open_port="COM7")

        # Act
        code = self._run("")

        # Assert
        out = capsys.readouterr().out
        assert code == 0
        assert str(os.getpid()) in out and "rig" in out and "COM7" in out, out
        assert breadcrumbs.read_running(), "still running"

    def test_a_target_that_picks_nothing_suggests_one(self, state_dir, capsys):
        """A mistyped config name fails and names the one it most likely meant."""
        # Arrange
        Breadcrumb().write("cli", config="a/rig.cfg", port="COM7", open_port="COM7")

        # Act
        code = self._run("rgi")

        # Assert
        assert code == 1
        assert "no running termapy matches rgi -- did you mean rig?" in capsys.readouterr().err

    def test_no_answer_stops_nothing(self, state_dir, capsys):
        """Answering no leaves every session running and says so."""
        # Arrange
        Breadcrumb().write("cli", config="a/rig.cfg", port="COM7", open_port="COM7")

        # Act
        code = self._run("all", answer=False)

        # Assert
        assert code == 1, "the request was not carried out"
        assert "Nothing stopped." in capsys.readouterr().out
        assert breadcrumbs.read_running(), "still running"


class TestSessionTrail:
    """A session's breadcrumb follows it: start, connect, disconnect."""

    def test_the_breadcrumb_follows_connect_and_disconnect(self, cli_demo, state_dir):
        """Twice in a row: the open port appears on connect and clears on disconnect, each time."""
        # Arrange
        own = state_dir / "running" / f"{os.getpid()}.json"

        def open_port() -> str:
            return json.loads(own.read_text(encoding="utf-8"))["open_port"]

        # Act
        cli_demo._fire_app_start()
        at_start = open_port()
        steps = []
        for _ in range(2):
            cli_demo._connect()
            steps.append(open_port())
            cli_demo._disconnect()
            steps.append(open_port())

        # Assert
        record = json.loads(own.read_text(encoding="utf-8"))
        assert (record["frontend"], record["port"]) == ("cli", "DEMO")
        assert at_start == "", "nothing is open at start"
        assert steps == ["DEMO", "", "DEMO", ""], "connect names the port, disconnect clears it"


class TestAppCommands:
    """/app.running and /app.kill: the same breadcrumbs, from inside a session."""

    def test_running_lists_this_session_marked(self, cli_demo, state_dir):
        """/app.running answers a record per session and marks the one that ran it."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        result = cli_demo.repl.dispatch_full("/app.running")

        # Assert
        assert result.success, result.error
        actual = [
            (record["pid"], record["frontend"], record["config"], record["this_session"])
            for record in result.data
        ]
        assert actual == [(os.getpid(), "cli", "rig", True)]

    def test_kill_ends_this_session_through_exit(self, cli_demo, state_dir):
        """Picked by its own target, the session is not killed: after a yes it leaves through /exit."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        with patch("builtins.input", return_value="y"):
            result = cli_demo.repl.dispatch_full("/app.kill rig")

        # Assert
        assert result.success, result.error
        assert cli_demo._exit_requested is True, "the session asked to exit, the clean way"

    def test_all_includes_this_session(self, cli_demo, state_dir):
        """all means every session, this one too, which leaves last through /exit (tmux kill-server)."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        with patch("builtins.input", return_value="y"):
            result = cli_demo.repl.dispatch_full("/app.kill all")

        # Assert
        assert result.success, result.error
        assert cli_demo._exit_requested is True, "this session was part of all"

    def test_others_never_includes_this_session(self, cli_demo, state_dir):
        """others means every session but this one (tmux kill-session -a); alone, there is none."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        result = cli_demo.repl.dispatch_full("/app.kill others")

        # Assert
        assert (result.success, result.error) == (False, "No other termapy is running.")
        assert cli_demo._exit_requested is False, "this session keeps running"

    def test_a_no_keeps_this_session_open(self, cli_demo, state_dir):
        """Answering no to a target that picks this session leaves it running."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        with patch("builtins.input", return_value="n"):
            result = cli_demo.repl.dispatch_full("/app.kill rig")

        # Assert
        assert (result.success, result.error) == (False, "Nothing stopped.")
        assert cli_demo._exit_requested is False, "no exit was asked for"

    def test_kill_is_out_of_an_agents_reach(self, cli_demo):
        """/app.kill needs a person and a yes, which MCP has neither of, so an agent cannot end sessions."""
        # Act
        plugin = cli_demo.repl.plugin_for("app.kill")

        # Assert
        assert plugin is not None, "/app.kill is registered"
        assert (plugin.needs.interactive, plugin.needs.confirm_dialog) == (True, True)

    @pytest.mark.slow
    def test_kill_suggests_then_asks_then_stops(self, cli_demo, state_dir):
        """Twice in a row: a typo gets a suggestion, a no keeps the other session, then others stops it alone."""
        # Arrange
        cli_demo._fire_app_start()
        with _child_session(state_dir, config="probe.cfg", open_port="COM9") as (child, _):
            # Act
            typo = cli_demo.repl.dispatch_full("/app.kill prbe")
            with patch("builtins.input", return_value="n"):
                declined = cli_demo.repl.dispatch_full("/app.kill probe")
            still_running = child.poll() is None
            with patch("builtins.input", return_value="y"):
                stopped = cli_demo.repl.dispatch_full("/app.kill others")

            # Assert
            assert typo.error == "No running termapy matches prbe -- did you mean probe?"
            assert (declined.success, declined.error) == (False, "Nothing stopped.")
            assert still_running, "a no left the other session running"
            assert stopped.success, stopped.error
            assert child.wait(timeout=10) is not None, "the other session's process has exited"
            assert cli_demo._exit_requested is False, "others left this session running"


@pytest.fixture
def cli_demo(tmp_path):
    """A CLI session on the DEMO simulator, its config on disk."""
    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict)
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "auto_connect": False}
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    cli = CLITerminal(cfg, str(config_path))
    yield cli
    if cli.engine.is_connected:
        cli._disconnect()
