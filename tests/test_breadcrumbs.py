"""breadcrumbs: the file each running termapy leaves, how --alive lists them, and how --kill picks and stops them."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import threading
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


def _end(crumb: Crumb) -> None:
    """End a test's own child session, if its process is still the one the breadcrumb names."""
    import signal

    if breadcrumbs._is_live(crumb):
        try:
            os.kill(crumb.pid, signal.SIGTERM)
        except OSError:
            pass


# A child that also answers a request to leave: it watches, and once asked it
# returns, so the interpreter exits normally (exit code 0, breadcrumb removed).
_ANSWERS = (
    "import threading\n"
    "from termapy.breadcrumbs import StopWatch\n"
    "asked = threading.Event()\n"
    "watch = StopWatch(crumb.pid, crumb.token, poll_s=0.05)\n"
    "watch.set_handler(lambda by: asked.set())\n"
    "watch.start()\n"
)


@contextmanager
def _child_session(
    state_dir, *, config: str = "rig.cfg", open_port: str = "COM9", answers: bool = False
):
    """A child process that leaves a breadcrumb and waits; yields it and its breadcrumb.

    With ``answers`` it also watches for a request to leave and exits
    normally when asked; without, only a signal ends it, as an older
    termapy that never answers one.

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
            "crumb = Breadcrumb()\n"
            f"crumb.write('cli', config={config!r}, open_port={open_port!r})\n"
            + (_ANSWERS if answers else "")
            + "print('ready', flush=True)\n"
            + ("asked.wait(60)\n" if answers else "time.sleep(60)\n"),
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
            _end(crumb)
        if child.poll() is None:
            child.kill()
        if child.stdout is not None:
            child.stdout.close()


def _crumb(**fields) -> Crumb:
    """A breadcrumb for this test process, with any field replaced."""
    base = Crumb(
        pid=os.getpid(),
        token=breadcrumbs.process_token(os.getpid()) or "",
        started="2026-10-31T09:00:00",
        frontend="cli",
        config="C:/proj/termapy_cfg/rig/rig.cfg",
        port="COM7",
        open_port="COM7",
    )
    return dataclasses.replace(base, **fields)


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


# The chain ``uv run termapy`` builds on Windows: the shell starts uv, uv the
# termapy console script, that the venv's python launcher, and that the
# interpreter running termapy.
_UV_CHAIN = {
    17604: (900, "powershell.exe"),
    15876: (17604, "uv.exe"),
    27136: (15876, "termapy.exe"),
    18488: (27136, "python.exe"),
    22056: (18488, "python.exe"),
}


class TestWhoStartedIt:
    """The program that started a termapy is its first ancestor that is not python, termapy or uv."""

    def test_the_starter_is_past_the_launchers(self):
        """uv run termapy on Windows: python, python, termapy.exe and uv.exe are skipped to reach the shell."""
        assert breadcrumbs.starting_process(22056, _UV_CHAIN) == (17604, "powershell.exe")

    def test_a_starter_that_is_no_launcher_is_the_parent(self):
        """An MCP server an editor starts directly names the editor."""
        table = {50: (1, "Code.exe"), 60: (50, "termapy")}
        assert breadcrumbs.starting_process(60, table) == (50, "Code.exe")

    @pytest.mark.parametrize(
        "table",
        [
            {10: (11, "python.exe"), 11: (10, "uv.exe")},
            {10: (11, "python.exe"), 11: (99, "uv.exe")},
            {10: (0, "python.exe")},
            {},
        ],
        ids=["a loop", "a parent that has exited", "no parent", "not in the table"],
    )
    def test_no_starter_found_is_none(self, table):
        """A walk that loops or runs out of processes gives None, never a launcher."""
        assert breadcrumbs.starting_process(10, table) is None

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("python.exe", True),
            ("Python.EXE", True),
            ("pythonw.exe", True),
            ("python3", True),
            ("python3.14", True),
            ("/usr/bin/python3.14", True),
            ("termapy.exe", True),
            ("uv", True),
            ("uvx.exe", True),
            ("powershell.exe", False),
            ("bash", False),
            ("Code.exe", False),
            ("python3-config", False),
            ("termapy-web", False),
        ],
    )
    def test_launcher_names(self, name, expected):
        """Python by pattern, termapy and uv by name, case and .exe ignored; anything else is a starter."""
        assert breadcrumbs.is_launcher(name) is expected

    def test_the_process_table_holds_this_process(self):
        """The real snapshot finds this process under the parent the OS reports for it."""
        # Act
        table = breadcrumbs.process_table()

        # Assert
        assert table[os.getpid()][0] == os.getppid(), "one snapshot, the OS's own parent link"


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
            "config": os.path.abspath("rig.cfg"),
            "port": "SN:BG03U7VTA",
            "open_port": "COM7",
        }
        assert {key: actual[key] for key in expected} == expected

    def test_every_path_is_absolute(self, state_dir):
        """A relative config or log is written absolute, so a reader in another folder finds the right file."""
        # Act
        Breadcrumb().write("cli", config="rig/rig.cfg", log="rig/log/rig.log")

        # Assert
        actual = breadcrumbs.read_running()[0]
        paths = (actual.config, actual.log, actual.python, actual.cwd)
        assert [os.path.isabs(path) for path in paths] == [True] * 4, paths
        assert actual.config == os.path.join(os.getcwd(), "rig", "rig.cfg"), (
            "against its own folder"
        )

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

    def test_a_plain_write_reads_nothing_costly(self, state_dir):
        """Without detailed, no version and no starter, so a one-shot stays fast; the free facts are there."""
        # Act
        Breadcrumb().write("cli")

        # Assert
        actual = breadcrumbs.read_running()[0]
        assert (actual.version, actual.parent_pid, actual.parent_name) == ("", 0, "")
        assert (actual.python, actual.cwd) == (os.path.abspath(sys.executable), os.getcwd())

    def test_a_detailed_write_records_the_version_and_starter(self, state_dir):
        """A long-lived session's breadcrumb names the termapy version and the program that started it."""
        # Arrange
        expected_starter = breadcrumbs.starting_process(os.getpid(), breadcrumbs.process_table())

        # Act
        Breadcrumb().write("tui", detailed=True)

        # Assert
        actual = breadcrumbs.read_running()[0]
        assert actual.version == breadcrumbs.installed_version() != "", "the installed version"
        assert (actual.parent_pid, actual.parent_name) == (expected_starter or (0, ""))

    def test_the_port_details_round_trip(self, state_dir):
        """Settings, the port's serial number and the session log are read back as written."""
        # Arrange
        log = str(state_dir / "rig.mcp.log")

        # Act
        Breadcrumb().write(
            "mcp", open_port="COM7", settings="115200 8N1", port_serial="BG03U7VTA", log=log
        )

        # Assert
        actual = breadcrumbs.read_running()[0]
        assert (actual.settings, actual.port_serial, actual.log) == ("115200 8N1", "BG03U7VTA", log)

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

    @pytest.mark.rule("kill-verifies-identity")
    def test_a_reused_pid_is_not_the_same_process(self, state_dir):
        """A live PID with another start token is another program, never listed."""
        # Arrange -- this process's PID with a token from some earlier process
        self._plant(state_dir, _crumb(token="not-this-process"))

        # Act
        actual = breadcrumbs.read_running()

        # Assert
        assert actual == [], "the PID is alive but belongs to a different process"

    def test_an_older_breadcrumb_still_reads(self, state_dir):
        """A session started by an older termapy, with none of the newer fields, is listed with them blank."""
        # Arrange
        old_keys = ("pid", "token", "started", "frontend", "config", "port", "open_port")
        old = {key: getattr(_crumb(), key) for key in old_keys}
        folder = state_dir / "running"
        folder.mkdir(parents=True)
        (folder / f"{os.getpid()}.json").write_text(json.dumps(old), encoding="utf-8")

        # Act
        actual = breadcrumbs.read_running()

        # Assert
        assert [crumb.open_port for crumb in actual] == ["COM7"], "still listed"
        assert (actual[0].version, actual[0].parent_pid, actual[0].settings) == ("", 0, "")

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
    """--kill's target: all, a PID, a port, or a config name."""

    @pytest.mark.parametrize(("target", "expected"), [("other", "others"), ("al", "all")])
    def test_a_mistyped_target_word_suggests_it(self, target, expected):
        """The target words are offered like names: other is answered with others."""
        # Arrange
        crumbs = [_crumb(pid=22056, config="a/rig.cfg")]

        # Act
        actual = breadcrumbs.suggest(crumbs, target)

        # Assert
        assert actual == f" -- did you mean {expected}?"

    def test_a_mistyped_pid_suggests_the_running_one(self):
        """A PID one digit off is answered with the PID that is running."""
        # Arrange
        crumbs = [_crumb(pid=22056, config="a/rig.cfg")]

        # Act
        actual = breadcrumbs.suggest(crumbs, "22057")

        # Assert
        assert actual == " -- did you mean 22056?"

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
            ("2", [2]),
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
            "a PID",
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


_ASKED_PID = 4242  # a PID the tests' requests name; no process is behind it


def _asked(token: str = "t") -> Crumb:
    """The breadcrumb of a session at ``_ASKED_PID`` with start ``token``."""
    return _crumb(pid=_ASKED_PID, token=token)


@pytest.mark.rule("kill-asks-first")
class TestStopRequest:
    """A request to leave names the session by PID and start token, and says who asked."""

    def test_a_request_names_the_session_and_who_asked(self, state_dir):
        """The request file holds the session's token and the asking PID, read back by the session."""
        # Act
        written = breadcrumbs.request_stop(_asked(), by=99)

        # Assert
        actual = json.loads((state_dir / "running" / f"{_ASKED_PID}.stop").read_text("utf-8"))
        assert (written, actual) == (True, {"token": "t", "by": 99})
        assert breadcrumbs._read_request(_ASKED_PID, "t") == 99, "the session reads who asked"

    @pytest.mark.rule("kill-verifies-identity")
    def test_another_process_under_the_pid_ignores_it(self, state_dir):
        """A request carrying an earlier process's token is never acted on by the PID's new owner."""
        # Arrange
        breadcrumbs.request_stop(_asked(token="earlier-process"), by=99)

        # Act
        actual = breadcrumbs._read_request(_ASKED_PID, "this-process")

        # Assert
        assert actual is None, "the token is the session's, not the PID's"

    def test_prune_drops_a_request_no_session_carries(self, state_dir):
        """A request whose session is gone is deleted, so a crashed asker leaves nothing armed."""
        # Arrange
        breadcrumbs.request_stop(_asked(), by=99)

        # Act
        breadcrumbs.prune_stale()

        # Assert
        assert not (state_dir / "running" / f"{_ASKED_PID}.stop").exists()


@pytest.mark.rule("kill-asks-first")
class TestStopWatch:
    """A session's watch answers its own request once, through the handler its frontend registered."""

    def test_a_request_is_answered_once(self, state_dir):
        """The handler gets the asking PID, the request is deleted, and a second look finds nothing."""
        # Arrange
        calls: list[int] = []
        watch = breadcrumbs.StopWatch(_ASKED_PID, "t")
        watch.set_handler(calls.append)
        breadcrumbs.request_stop(_asked(), by=99)

        # Act
        first = watch.check()
        second = watch.check()

        # Assert
        assert (first, second, calls) == (True, False, [99]), "answered once"
        assert not (state_dir / "running" / f"{_ASKED_PID}.stop").exists(), "request consumed"

    def test_twice_in_a_row_each_is_answered(self, state_dir):
        """Two requests one after the other each reach the handler: the watch keeps watching."""
        # Arrange
        calls: list[int] = []
        watch = breadcrumbs.StopWatch(_ASKED_PID, "t")
        watch.set_handler(calls.append)

        # Act
        for asker in (98, 99):
            breadcrumbs.request_stop(_asked(), by=asker)
            watch.check()

        # Assert
        assert calls == [98, 99]

    def test_with_no_handler_the_request_waits(self, state_dir):
        """Between two frontends of one process a request stays on disk for the next one."""
        # Arrange
        calls: list[int] = []
        watch = breadcrumbs.StopWatch(_ASKED_PID, "t")
        breadcrumbs.request_stop(_asked(), by=99)

        # Act
        unanswered = watch.check()
        watch.set_handler(calls.append)
        answered = watch.check()

        # Assert
        assert (unanswered, answered, calls) == (False, True, [99]), "held, then answered"

    def test_an_older_frontend_never_clears_a_newer_handler(self, state_dir):
        """A frontend that ends releases only its own handler; one registered after it stays."""
        # Arrange
        calls: list[str] = []

        def older(by: int) -> None:
            calls.append("older")

        def newer(by: int) -> None:
            calls.append("newer")

        watch = breadcrumbs.StopWatch(_ASKED_PID, "t")
        watch.set_handler(older)
        watch.set_handler(newer)

        # Act
        watch.clear_handler(older)
        breadcrumbs.request_stop(_asked(), by=99)
        watch.check()

        # Assert
        assert calls == ["newer"]

    def test_a_handler_that_raises_does_not_stop_the_watch(self, state_dir):
        """A failing handler is recorded and the watch answers the next request."""
        # Arrange
        calls: list[int] = []

        def handler(by: int) -> None:
            calls.append(by)
            raise RuntimeError("frontend already gone")

        watch = breadcrumbs.StopWatch(_ASKED_PID, "t")
        watch.set_handler(handler)

        # Act
        breadcrumbs.request_stop(_asked(), by=98)
        watch.check()
        breadcrumbs.request_stop(_asked(), by=99)
        watch.check()

        # Assert
        assert calls == [98, 99], "both requests reached the handler"

    def test_the_thread_answers_and_cancel_ends_it(self, state_dir):
        """Started, the watch finds a request by itself; cancelled, its thread ends."""
        # Arrange
        asked = threading.Event()
        watch = breadcrumbs.StopWatch(_ASKED_PID, "t", poll_s=0.02)
        watch.set_handler(lambda by: asked.set())
        watch.start()

        # Act
        breadcrumbs.request_stop(_asked(), by=99)
        answered = asked.wait(10)
        watch.cancel(timeout=10)

        # Assert
        assert answered, "the watch thread answered the request"
        assert not watch._thread.is_alive(), "cancel ended the thread"


@pytest.mark.rule("kill-asks-first")
class TestStopSessions:
    """A stop asks every session to leave and never ends one: what stays is reported, not killed."""

    def test_a_session_already_gone_is_left_alone(self, state_dir):
        """A session that exited is counted gone: nothing asked, nothing signalled."""
        # Arrange
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        crumb = _crumb(pid=child.pid, token="1234")

        # Act
        actual = breadcrumbs.stop_sessions([crumb], grace_s=0)

        # Assert
        assert (actual.left, actual.refused) == ((crumb,), ())

    def test_a_session_still_running_is_named_with_the_command_that_ends_it(self):
        """A session that did not leave is reported with the OS command, which termapy never runs."""
        # Arrange
        left = _crumb(pid=1)
        stayed = _crumb(pid=2)
        expected_command = "taskkill -PID 2 -F" if sys.platform == "win32" else "kill -9 2"

        # Act
        outcome = breadcrumbs.StopOutcome(left=(left,), refused=(stayed,))

        # Assert
        assert outcome.stopped_lines() == [f"Stopped 1 ({left.summary})"]
        assert outcome.refused_lines() == [
            f"Could not shut down process 2 ({stayed.summary}). "
            f'Please kill it using "{expected_command}".'
        ], "it is the person's call, with the command in hand"

    @pytest.mark.slow
    def test_a_session_that_answers_leaves_without_a_signal(self, state_dir):
        """Asked, a watching session exits on its own: exit code 0, not the code of a killed process."""
        with _child_session(state_dir, answers=True) as (child, crumb):
            # Act
            actual = breadcrumbs.stop_sessions([crumb], grace_s=30)

            # Assert
            assert (actual.left, actual.refused) == ((crumb,), ()), "it left when asked"
            assert child.wait(timeout=10) == 0, "it exited normally, nothing ended it"

    @pytest.mark.slow
    def test_a_session_that_never_answers_is_reported_and_left_running(self, state_dir):
        """A session with no watch, as an older termapy, keeps running and keeps its breadcrumb."""
        with _child_session(state_dir) as (child, crumb):
            # Act
            actual = breadcrumbs.stop_sessions([crumb], grace_s=0.5)

            # Assert
            assert (actual.left, actual.refused) == ((), (crumb,)), "reported, not stopped"
            assert child.poll() is None, "termapy signalled nothing"
            assert breadcrumbs.read_running() == [crumb], "still listed as running"
            assert not (state_dir / "running" / f"{crumb.pid}.stop").exists(), "request withdrawn"


@contextmanager
def _termapy_session(tmp_path, state_dir, *args: str):
    """A real termapy started with ``args``; yields the process and its breadcrumb once written.

    Its diagnostic log goes to ``tmp_path / "diag"``.  Whatever is still
    running at the end is ended by the test.
    """
    import time

    cfg_root = tmp_path / "cfg"
    cfg_root.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "TERMAPY_STATE_DIR": str(state_dir),
        "TERMAPY_DIAG_DIR": str(tmp_path / "diag"),
    }
    env.pop("CLAUDE_PROJECT_DIR", None)
    child = subprocess.Popen(
        [sys.executable, "-m", "termapy", "--cfg-dir", str(cfg_root), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=tmp_path,
    )
    crumb = None
    try:
        deadline = time.monotonic() + 60
        while crumb is None and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.1)
            crumb = next(iter(breadcrumbs.read_running()), None)
        assert crumb is not None, "the session left its breadcrumb"
        yield child, crumb
    finally:
        if crumb is not None:
            _end(crumb)
        if child.poll() is None:
            child.kill()
        if child.stdin is not None:
            child.stdin.close()


@pytest.mark.slow  # real termapy processes
@pytest.mark.rule("kill-asks-first")
class TestFrontendsLeaveWhenAsked:
    """A real MCP server and a real CLI script leave through their own teardown when asked."""

    def test_an_mcp_server_leaves_with_its_client_still_attached(self, tmp_path, state_dir):
        """With stdin open, as under a client, the server tears down and exits 0 instead of being ended."""
        with _termapy_session(tmp_path, state_dir, "--mcp") as (child, crumb):
            # Act
            actual = breadcrumbs.stop_sessions([crumb], grace_s=30)

            # Assert
            assert (actual.left, actual.refused) == ((crumb,), ()), "it left when asked"
            assert child.wait(timeout=10) == 0, "it exited on its own"
        diag = "".join(path.read_text("utf-8") for path in (tmp_path / "diag").glob("*.log"))
        assert "process exit: stopped by termapy PID" in diag, "it ran its teardown first"

    def test_a_script_in_a_delay_stops_when_asked(self, tmp_path, state_dir):
        """A --run script waiting in /delay stops at once and the CLI exits 0, well before the delay ends."""
        # Arrange
        script = tmp_path / "wait.run"
        script.write_text("/delay 60s\n", encoding="utf-8")

        with _termapy_session(tmp_path, state_dir, "--cli", "--demo", "--run", str(script)) as (
            child,
            crumb,
        ):
            # Act
            actual = breadcrumbs.stop_sessions([crumb], grace_s=30)

            # Assert
            assert (actual.left, actual.refused) == ((crumb,), ()), "it left when asked"
            assert child.wait(timeout=10) == 0, "it exited on its own"


class TestAliveFlag:
    """termapy --alive: the sessions, in /app.alive's table, and nothing stopped."""

    def test_alive_prints_the_app_alive_table(self, state_dir, capsys):
        """--alive prints the table /app.alive answers with, columns and title alike, and exits 0."""
        # Arrange
        from termapy.cli_flags import run_alive
        from termapy.plugins.views import TableView

        Breadcrumb().write(
            "mcp", config="a/rig.cfg", port="COM7", open_port="COM7", settings="9600 8N1"
        )
        records = tuple(breadcrumbs.session_record(crumb) for crumb in breadcrumbs.read_running())
        view = TableView(
            breadcrumbs.SESSIONS_TITLE, "session", breadcrumbs.session_columns(), records
        )

        # Act
        with pytest.raises(SystemExit) as exit_info:
            run_alive()

        # Assert -- captured output is not a terminal, so the table gets its full width
        expected = [text for text, _color in view.lines(200)]
        assert (exit_info.value.code, capsys.readouterr().out.splitlines()) == (0, expected)
        assert breadcrumbs.read_running(), "a listing stops nothing"


class TestKillFlag:
    """termapy --kill: bare lists as --alive does, a target asks and stops."""

    @staticmethod
    def _run(target: str, *, answer: bool = False) -> int:
        """Run --kill with ``target``, answering the question with ``answer``; return the exit code."""
        from termapy.cli_flags import run_kill

        with pytest.raises(SystemExit) as exit_info:
            run_kill(argparse.Namespace(kill=target.split()), confirm=lambda question: answer)
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

    def test_me_is_refused_from_the_command_line(self, state_dir, capsys):
        """--kill me names no session (the command line is none), and says where me works."""
        # Arrange
        Breadcrumb().write("cli", config="a/rig.cfg", port="COM7", open_port="COM7")

        # Act
        code = self._run("me", answer=True)

        # Assert
        assert code == 1, "nothing to stop"
        assert "app.kill me in the session" in capsys.readouterr().err, "points to where me works"
        assert breadcrumbs.read_running(), "still running"

    @pytest.mark.slow  # a real termapy and a child session
    def test_yes_answers_for_you_with_no_terminal(self, state_dir, tmp_path):
        """-y stops the picked sessions from a pipe, where the answer would be no, and shows the assumed yes."""
        with _child_session(state_dir, answers=True) as (child, _):
            # Act
            result = subprocess.run(
                [sys.executable, "-m", "termapy", "--kill", "all", "-y"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmp_path,
                env={**os.environ, "TERMAPY_STATE_DIR": str(state_dir)},
            )

            # Assert
            assert result.returncode == 0, result.stderr
            assert "[y/N] y (--yes)" in result.stdout, "the log shows what was assumed"
            assert child.wait(timeout=10) == 0, "the session left when asked"

    def test_a_bad_timeout_is_refused(self, state_dir, capsys):
        """--kill all timeout=soon names the bad duration and stops nothing."""
        # Arrange
        Breadcrumb().write("cli", config="a/rig.cfg", port="COM7", open_port="COM7")

        # Act
        code = self._run("all timeout=soon", answer=True)

        # Assert
        assert (code, capsys.readouterr().err.strip()) == (1, "termapy: Invalid timeout: soon")
        assert breadcrumbs.read_running(), "still running"

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

    def test_the_breadcrumb_carries_the_open_ports_settings(self, cli_demo, state_dir):
        """Connect records the settings and serial number, a baud change rewrites them, disconnect clears them."""
        # Arrange
        own = state_dir / "running" / f"{os.getpid()}.json"

        def port_fields() -> tuple[str, str]:
            record = json.loads(own.read_text(encoding="utf-8"))
            return record["settings"], record["port_serial"]

        cli_demo._fire_app_start()

        # Act
        cli_demo._connect()
        connected = port_fields()
        changed = cli_demo.repl.dispatch_full("/port.baud_rate 9600")
        after_baud = port_fields()
        cli_demo._disconnect()
        disconnected = port_fields()

        # Assert
        assert changed.success, changed.error
        assert connected == ("115200 8N1", "TERMAPY-DEMO"), "the simulator's settings and serial"
        assert after_baud == ("9600 8N1", "TERMAPY-DEMO"), "a settings change is rewritten"
        assert disconnected == ("", ""), "no port, no settings"

    def test_an_interactive_session_records_its_version(self, cli_demo, state_dir):
        """A CLI at its prompt is long-lived, so its breadcrumb carries the version."""
        # Act
        cli_demo._fire_app_start()

        # Assert
        record = json.loads((state_dir / "running" / f"{os.getpid()}.json").read_text("utf-8"))
        assert record["version"] == breadcrumbs.installed_version()

    @pytest.mark.slow  # a fresh interpreter, so the breadcrumb is its own
    def test_a_one_shot_records_no_version_or_starter(self, tmp_path):
        """termapy -e answers fast: its breadcrumb skips the version and the process walk."""
        # Arrange
        cfg_root = tmp_path / "cfg"
        cfg_root.mkdir()

        # Act
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "termapy",
                "--cli",
                "--demo",
                "--cfg-dir",
                str(cfg_root),
                "-e",
                "/app.alive.json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=tmp_path,
            env={**os.environ, "TERMAPY_STATE_DIR": str(tmp_path / "state")},
        )

        # Assert
        envelope = json.loads(result.stdout)
        own = [record for record in envelope["data"] if record["this_session"]]
        assert [(record["version"], record["parent_pid"]) for record in own] == [("", None)]
        assert own[0]["settings"] == "115200 8N1", "the free facts are still there"


class TestAppCommands:
    """/app.alive and /app.kill: the same breadcrumbs, from inside a session."""

    def test_alive_lists_this_session_marked(self, cli_demo, state_dir):
        """/app.alive answers a record per session and marks the one that ran it."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        result = cli_demo.repl.dispatch_full("/app.alive")

        # Assert
        assert result.success, result.error
        actual = [
            (record["pid"], record["frontend"], record["config"], record["this_session"])
            for record in result.data
        ]
        assert actual == [(os.getpid(), "cli", "rig", True)]

    def test_alive_json_carries_what_the_table_leaves_out(self, cli_demo, state_dir):
        """The paths and the port's serial number are in the record; the table has no room for them."""
        # Arrange
        cli_demo._fire_app_start()
        cli_demo._connect()

        # Act
        result = cli_demo.repl.dispatch_full("/app.alive")

        # Assert
        record = result.data[0]
        actual = {key: record[key] for key in ("python", "cwd", "port_serial", "settings")}
        assert actual == {
            "python": os.path.abspath(sys.executable),
            "cwd": os.getcwd(),
            "port_serial": "TERMAPY-DEMO",
            "settings": "115200 8N1",
        }

    def test_alive_table_drops_the_starter_first(self):
        """A narrow frontend loses STARTED BY first; the session's identity columns never go."""
        # Arrange
        from termapy.plugins.views import TableView

        crumb = _crumb(
            settings="115200 8N1", version="0.80.0", parent_pid=17604, parent_name="pwsh.exe"
        )
        view = TableView(
            breadcrumbs.SESSIONS_TITLE,
            "session",
            breadcrumbs.session_columns(),
            (breadcrumbs.session_record(crumb),),
        )

        def headers(width: int) -> list[str]:
            return next(text for text, _ in view.lines(width) if text.startswith("PID")).split()

        # Act
        wide = headers(200)
        narrow = headers(70)

        # Assert
        assert wide == [
            "PID",
            "MODE",
            "CONFIG",
            "PORT",
            "SETTINGS",
            "VERSION",
            "STARTED",
            "STARTED",
            "BY",
        ], "every column at full width"
        assert narrow[:4] == ["PID", "MODE", "CONFIG", "PORT"], "identity stays"
        assert "BY" not in narrow, "the starter goes first"

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

    def test_me_is_exit(self, cli_demo, state_dir):
        """/app.kill me ends this session the way /exit does, asking nothing."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        with patch("builtins.input", side_effect=AssertionError("/exit asks nothing")):
            result = cli_demo.repl.dispatch_full("/app.kill ME")

        # Assert
        assert result.success, result.error
        assert cli_demo._exit_requested is True, "the session leaves through /exit"

    def test_a_pid_picks_that_session(self, cli_demo, state_dir):
        """This session's own PID picks it: after a yes it leaves through /exit."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        with patch("builtins.input", return_value="y"):
            result = cli_demo.repl.dispatch_full(f"/app.kill {os.getpid()}")

        # Assert
        assert result.success, result.error
        assert cli_demo._exit_requested is True, "the PID was this session's"

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

    def test_bare_kill_lists_and_stops_nothing(self, cli_demo, state_dir):
        """Bare /app.kill is a query, as bare --kill is: /app.alive's answer, no question, no exit."""
        # Arrange
        cli_demo._fire_app_start()

        # Act
        with patch("builtins.input", side_effect=AssertionError("nothing to confirm")):
            bare = cli_demo.repl.dispatch_full("/app.kill")
        alive = cli_demo.repl.dispatch_full("/app.alive")

        # Assert
        assert (bare.success, bare.data) == (True, alive.data), "the same answer as /app.alive"
        assert cli_demo._exit_requested is False, "a listing ends nothing"

    @pytest.mark.rule("kill-asks-first")
    def test_a_waiting_prompt_ends_as_ctrl_d_when_asked(self, cli_demo):
        """A request while the CLI prompt waits for a line ends the prompt, as Ctrl+D does."""
        # Arrange
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        with create_pipe_input() as pipe:
            cli_demo._session = PromptSession(input=pipe, output=DummyOutput())
            # Either order ends it: before the prompt starts, its pre_run does.
            asker = threading.Timer(0.2, cli_demo._leave_on_request, args=(99,))
            asker.start()

            # Act / Assert
            with pytest.raises(EOFError):
                cli_demo._session.prompt("> ", pre_run=cli_demo._end_prompt_if_asked)
            asker.join(10)

        # Assert
        assert cli_demo._exit_requested is True, "the loop ends instead of prompting again"

    @pytest.mark.rule("kill-asks-first")
    def test_a_request_before_the_script_starts_is_not_lost(self, cli_demo, tmp_path, capsys):
        """A request that came in while the session started stops its --run script before any line."""
        # Arrange
        script = tmp_path / "say.run"
        script.write_text("/echo the script ran\n", encoding="utf-8")
        cli_demo._run_script_mode(str(script))
        before = capsys.readouterr().out

        # Act
        cli_demo._leave_on_request(99)
        cli_demo._run_script_mode(str(script))
        after = capsys.readouterr().out

        # Assert
        assert "the script ran" in before, "with no request the script runs"
        assert "the script ran" not in after, "start_script's reset did not swallow the request"

    @pytest.mark.rule("kill-asks-first")
    def test_a_prompt_that_starts_after_the_request_ends_at_once(self, cli_demo):
        """A request already in when the prompt starts ends it at once, and a script stops too."""
        # Arrange
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        cli_demo._leave_on_request(99)
        with create_pipe_input() as pipe:
            cli_demo._session = PromptSession(input=pipe, output=DummyOutput())

            # Act / Assert
            with pytest.raises(EOFError):
                cli_demo._session.prompt("> ", pre_run=cli_demo._end_prompt_if_asked)

        # Assert
        assert cli_demo.repl._script_stop.is_set(), "a running script stops at its next line"

    @pytest.mark.slow  # a child process
    @pytest.mark.rule("kill-asks-first")
    def test_a_session_that_does_not_leave_fails_with_the_command(self, cli_demo, state_dir):
        """/app.kill all fails naming the session that stayed and its command, and this session stays."""
        # Arrange
        cli_demo._fire_app_start()
        with _child_session(state_dir, config="probe.cfg") as (child, crumb):
            # Act -- a short timeout= instead of the default wait
            with patch("builtins.input", return_value="y"):
                result = cli_demo.repl.dispatch_full("/app.kill all timeout=300ms")

            # Assert
            assert result.success is False, "a session is still running"
            assert f"Could not shut down process {crumb.pid}" in result.error, result.error
            assert breadcrumbs.kill_command(crumb.pid) in result.error, "the command to end it"
            assert child.poll() is None, "termapy ended nothing"
            assert cli_demo._exit_requested is False, "this session stays open to show why"

    def test_a_yes_session_answers_its_own_questions(self, tmp_path, state_dir, capsys):
        """A CLI started with -y answers /app.kill's question itself and leaves through /exit."""
        # Arrange
        from termapy.cli import CLITerminal
        from termapy.defaults import DEFAULT_CFG

        serial = DEFAULT_CFG["serial"]
        assert isinstance(serial, dict)
        cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "auto_connect": False}
        config_path = tmp_path / "rig" / "rig.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg), encoding="utf-8")
        cli = CLITerminal(cfg, str(config_path), assume_yes=True)
        cli._fire_app_start()

        # Act
        with patch("builtins.input", side_effect=AssertionError("--yes asks no one")):
            result = cli.repl.dispatch_full("/app.kill rig")

        # Assert
        assert result.success, result.error
        assert cli._exit_requested is True, "the yes was given, so the session leaves"
        assert "[y/N] y (--yes)" in capsys.readouterr().out, "the assumed answer is shown"

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
        with _child_session(state_dir, config="probe.cfg", open_port="COM9", answers=True) as (
            child,
            _,
        ):
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
