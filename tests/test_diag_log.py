"""Tests for termapy.diag_log: where the diagnostic log goes, what a session writes there, and what a broad catch records."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from termapy import diag_log
from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG

pytestmark = pytest.mark.rule("diag-log-folder")


def _run(code: str, tmp_path: Path, **env: str) -> list[str]:
    """Run ``code`` in a fresh interpreter in ``tmp_path``; return its stdout lines.

    A fresh process, because the log is the process's: an earlier test's
    open handler or ``--cfg-dir`` would otherwise decide the answer.  The
    run starts with no ``TERMAPY_DIAG_DIR``, ``TERMAPY_CFG_DIR`` or
    ``TERMAPY_STATE_DIR`` unless ``env`` sets one.
    """
    names = (diag_log.DIAG_DIR_ENV, "TERMAPY_CFG_DIR", "TERMAPY_STATE_DIR")
    base = {key: value for key, value in os.environ.items() if key not in names}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"the probe failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout.splitlines()


_PRINT_FOLDER = "from termapy import diag_log; print(diag_log.log_folder())"


@pytest.mark.rule("diag-log-folder")
@pytest.mark.slow
class TestLogFolder:
    """The override, else the config root's log/, else the app state folder's; never a new config root."""

    def test_the_override_wins(self, tmp_path):
        """TERMAPY_DIAG_DIR names the folder, whatever else is set."""
        # Arrange
        target = tmp_path / "elsewhere"
        cfg_root = tmp_path / "cfg"
        cfg_root.mkdir()

        # Act
        actual = _run(
            _PRINT_FOLDER,
            tmp_path,
            TERMAPY_DIAG_DIR=str(target),
            TERMAPY_CFG_DIR=str(cfg_root),
        )

        # Assert
        assert actual == [str(target)]

    def test_an_existing_config_root_holds_it(self, tmp_path):
        """With a config root, the log goes in its log/ folder, beside the configs."""
        # Arrange
        cfg_root = tmp_path / "cfg"
        cfg_root.mkdir()

        # Act
        actual = _run(_PRINT_FOLDER, tmp_path, TERMAPY_CFG_DIR=str(cfg_root))

        # Assert
        assert actual == [str(cfg_root / diag_log.LOG_FOLDER)]

    def test_a_config_root_that_does_not_exist_is_never_made(self, tmp_path):
        """An MCP server naming where a config root would be logs to the app state folder and creates nothing there."""
        # Arrange -- what run_mcp_stdio sets for a project with no termapy_cfg/
        absent = tmp_path / "project" / "termapy_cfg"
        state = tmp_path / "state"
        code = (
            "import termapy.config as config\n"
            f"config.CFG_DIR = {str(absent)!r}\n"
            "config.CFG_DIR_MAY_BE_ABSENT = True\n"
            "from termapy import diag_log\n"
            "print(diag_log.log_folder())\n"
            "print(diag_log.start('mcp').parent)\n"
        )

        # Act
        actual = _run(code, tmp_path, TERMAPY_STATE_DIR=str(state))

        # Assert
        expected = str(state / diag_log.LOG_FOLDER)
        assert actual == [expected, expected], "the app state folder takes it"
        assert not absent.exists(), "a log never creates a config root"


@pytest.mark.rule("diag-log-per-process")
@pytest.mark.slow
def test_a_session_records_its_start_failures_and_exit(tmp_path):
    """A process start, each frontend, the version before the first problem, a traceback, and the exit."""
    # Arrange
    code = (
        "from termapy import diag_log\n"
        "print(repr(diag_log.traceback_note()))\n"
        "path = diag_log.start('cli')\n"
        "diag_log.start('tui')\n"
        "diag_log.info('an event')\n"
        "try:\n"
        "    {}['port']\n"
        "except KeyError:\n"
        "    diag_log.exception('a failure')\n"
        "print(repr(diag_log.traceback_note()))\n"
        "print(path)\n"
    )

    # Act
    before, after, path_line = _run(code, tmp_path, TERMAPY_DIAG_DIR=str(tmp_path / "log"))
    path = Path(path_line)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Assert
    assert before == "''", "no note before the log is open"
    assert after == repr(f" (traceback in {path})"), "after it, the note names the file"
    assert path.parent.glob("*.log").__next__() == path, "one file in the folder"
    assert path.stem.rpartition("-")[2].isdigit(), "named <stamp>-<pid>.log"
    assert "process start: pid" in lines[0], "the first line is the process"
    assert "session start: cli" in text and "session start: tui" in text, "each frontend"
    version_at = next(i for i, line in enumerate(lines) if "INFO" in line and " termapy " in line)
    failure_at = next(i for i, line in enumerate(lines) if "a failure" in line)
    assert version_at < failure_at, "the version is written before the first problem"
    assert "Traceback (most recent call last)" in text, "the traceback is kept"
    assert "KeyError: 'port'" in text, "with the exception that ended it"
    assert lines[-1].endswith("process exit"), "a normal exit is the last line"


@pytest.mark.rule("diag-log-per-process")
def test_prune_keeps_the_newest_and_any_running_process(tmp_path):
    """A start leaves KEEP_FILES logs, and never deletes one whose process is running."""
    # Arrange -- pids this large are no running process on any platform
    dead = [
        tmp_path / f"20260101_{i:06d}-{3_999_999_000 + i}.log"
        for i in range(diag_log.KEEP_FILES + 5)
    ]
    live = tmp_path / f"20250101_000000-{os.getpid()}.log"
    for path in [*dead, live]:
        path.write_text("x", encoding="utf-8")

    # Act
    diag_log._prune(tmp_path, keep=dead[-1])

    # Assert
    actual = sorted(path.name for path in tmp_path.glob("*.log"))
    expected = sorted([live.name, *(path.name for path in dead[-diag_log.KEEP_FILES :])])
    assert actual == expected, "the newest stay, and so does the running process's oldest file"


def _test_logger(name: str, handler: logging.Handler) -> logging.Logger:
    """A logger outside termapy's tree, so a test record never reaches this run's own log."""
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    return logger


def test_a_full_file_takes_no_more_records(tmp_path):
    """Past max_bytes a file notes the cap once and drops the rest, so a repeating failure cannot fill the disk."""
    # Arrange
    path = tmp_path / "capped.log"
    handler = diag_log._handler_class()(path, max_bytes=200)  # ty: ignore[unknown-argument] -- the class it builds takes max_bytes
    logger = _test_logger("diag-test-cap", handler)

    # Act
    for i in range(50):
        logger.info("record %d of a failure that repeats", i)
    handler.close()

    # Assert
    text = path.read_text(encoding="utf-8")
    assert text.count("later records dropped") == 1, "the cap is noted once"
    assert len(text) < 400, "records stop near the cap"


def test_a_failed_write_never_reaches_the_screen(tmp_path, capsys):
    """A record the handler cannot write raises nothing and prints nothing."""
    # Arrange
    handler = diag_log._handler_class()(tmp_path / "quiet.log")
    logger = _test_logger("diag-test-quiet", handler)

    # Act -- a format that cannot fill: logging's handleError prints a traceback by default
    logger.info("%d", "not a number")
    handler.close()

    # Assert
    assert capsys.readouterr().err == "", "no '--- Logging error ---' on stderr"


def test_once_writes_a_repeating_failure_one_time(caplog):
    """Records sharing a once= key are written the first time only."""
    # Arrange
    key = object()

    # Act
    with caplog.at_level(logging.WARNING, logger=diag_log.LOGGER_NAME):
        diag_log.warning("an observer failed", once=key)
        diag_log.warning("an observer failed", once=key)

    # Assert
    actual = [record for record in caplog.records if record.getMessage() == "an observer failed"]
    assert len(actual) == 1


def test_a_record_names_the_caller_and_carries_the_traceback(caplog):
    """An exception record is the caller's file and line, with the exception being handled."""
    # Act
    with caplog.at_level(logging.ERROR, logger=diag_log.LOGGER_NAME):
        try:
            raise ValueError("bad value")
        except ValueError:
            diag_log.exception("caught in a test")

    # Assert
    record = next(record for record in caplog.records if record.getMessage() == "caught in a test")
    assert record.filename == Path(__file__).name, "the caller's file, not diag_log.py"
    assert record.exc_info is not None, "the traceback rides on the record"
    assert record.exc_info[0] is ValueError


@pytest.mark.parametrize(
    ("name", "level", "expected"),
    [
        ("termapy", logging.INFO, True),
        ("termapy.child", logging.INFO, True),
        ("xmodem.XMODEM", logging.INFO, False),
        ("xmodem.XMODEM", logging.WARNING, True),
        ("pySerial.loop", logging.ERROR, True),
    ],
)
def test_the_file_takes_termapy_events_and_vendored_warnings(name, level, expected):
    """termapy's records from INFO up; a vendored library's from WARNING up."""
    # Arrange
    record = logging.LogRecord(name, level, __file__, 1, "message", None, None)

    # Act
    actual = diag_log._keep_record(record)

    # Assert
    assert actual is expected


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


@pytest.mark.rule("broad-catch-logged")
def test_a_raising_command_names_its_exception_and_logs_its_traceback(cli, caplog):
    """Dispatch answers a handler's exception with its type and the log file, and records the traceback."""

    # Arrange
    def broken(ctx, args):
        """Fail the way a plugin bug fails."""
        return {}["port"]

    cli.repl.register_hook("thing", "", "A thing.", broken)

    # Act
    with caplog.at_level(logging.ERROR, logger=diag_log.LOGGER_NAME):
        result = cli.repl.dispatch("thing", report=False)

    # Assert
    assert result.success is False
    assert result.error == f"Plugin error (thing): KeyError: 'port'{diag_log.traceback_note()}", (
        "the type, not just str(e), which for a KeyError is only the key"
    )
    record = next(
        record for record in caplog.records if "command raised: thing" in record.getMessage()
    )
    assert record.exc_info is not None and record.exc_info[0] is KeyError, (
        "the traceback is recorded"
    )


class TestLogDiagCommands:
    """/log.diag.*: this run's diagnostic log, printed, listed and opened, in a frontend with no session log."""

    def test_dump_prints_this_runs_log(self, cli, capsys):
        """/log.diag.dump N prints the last N lines of the file this process is writing."""
        # Arrange
        path = diag_log.start("cli")
        assert path is not None, "the run's log is open"
        diag_log.info("a line to find")
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch("log.diag.dump 1", report=False)

        # Assert
        assert result.success is True
        assert "a line to find" in capsys.readouterr().out, "the newest line is the one printed"

    def test_list_names_this_runs_file(self, cli):
        """/log.diag.list is the folder's files, newest first, this run's among them."""
        # Arrange
        path = diag_log.start("cli")
        assert path is not None, "the run's log is open"

        # Act
        result = cli.repl.dispatch("log.diag.list", report=False)

        # Assert
        names = [record["name"] for record in result.data]
        assert path.name in names, "this run's file is listed"
        assert names == sorted(names, reverse=True), "newest first"

    def test_show_refuses_a_name_outside_the_folder(self, cli):
        """/log.diag.show opens only a file in log/: a climb out is refused, not opened."""
        # Act
        result = cli.repl.dispatch("log.diag.show ../demo/demo.cfg", report=False)

        # Assert
        assert result.success is False
        assert result.error == "Invalid log name: ../demo/demo.cfg"

    def test_show_names_a_missing_file(self, cli):
        """/log.diag.show with a name no file has says which path it looked for."""
        # Arrange
        diag_log.start("cli")

        # Act
        result = cli.repl.dispatch("log.diag.show 19990101_000000-1", report=False)

        # Assert
        expected = f"Log file not found: {diag_log.log_folder() / '19990101_000000-1'}"
        assert result.error == expected, "the name as typed, since no .log file exists either"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {
                "kind": "plugin",
                "name": "traffic",
                "layer": "global",
                "file": "plugin/traffic.py",
                "count": 2,
                "detail": {"commands": ["traffic", "traffic.reset"]},
                "error": "",
            },
            "plugin traffic (global plugin/traffic.py): 2, commands traffic, traffic.reset",
        ),
        (
            {
                "kind": "plugin",
                "name": "broken",
                "layer": "demo",
                "file": "plugin/broken.py",
                "count": 0,
                "detail": {},
                "error": "RuntimeError: boom",
            },
            "plugin broken (demo plugin/broken.py): error: RuntimeError: boom",
        ),
    ],
)
def test_a_load_row_is_one_log_line(record, expected):
    """A load-report row reads as one line: kind, name, layer and file, then the count and note or the error."""
    # Arrange
    from termapy import load_report

    # Act
    actual = load_report.diag_line(record)

    # Assert
    assert actual == expected


def test_a_config_load_records_its_folder_plugins(cli, tmp_path, caplog):
    """A config load records each plugin it loaded from the config's folders: what code the session runs."""
    # Arrange
    plugin = tmp_path / "c" / "plugin" / "hi.py"
    plugin.parent.mkdir()
    plugin.write_text(
        "from termapy.plugins import CmdResult, Command\n"
        "def _handler(ctx, args):\n"
        '    """Say hi."""\n'
        "    return CmdResult.ok()\n"
        'COMMAND = Command(name="hi", help="Say hi.", handler=_handler)\n',
        encoding="utf-8",
    )

    # Act
    with caplog.at_level(logging.INFO, logger=diag_log.LOGGER_NAME):
        cli.repl.fire_lifecycle("on_app_start")

    # Assert
    loaded = [
        record.getMessage() for record in caplog.records if "plugin/hi.py" in record.getMessage()
    ]
    assert loaded == ["  loaded plugin hi (c plugin/hi.py): 1, commands hi"], (
        "one line per file, its commands named"
    )
    headers = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("config load:")
    ]
    assert headers == [f"config load: {cli.repl.config_path}"], "under a header naming the load"


def test_an_on_connect_command_is_recorded_with_its_key(caplog):
    """A command a config runs at connect is recorded with its key, so a script it starts reads as the config's."""
    # Arrange
    from termapy.config import on_connect_commands

    cfg = {"on_connect_cmd": "/print hi", "tui_on_connect_cmd": "/run welcome"}

    # Act
    with caplog.at_level(logging.INFO, logger=diag_log.LOGGER_NAME):
        on_connect_commands(cfg, "tui_on_connect_cmd")

    # Assert
    actual = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("on connect:")
    ]
    expected = [
        "on connect: on_connect_cmd runs /print hi",
        "on connect: tui_on_connect_cmd runs /run welcome",
    ]
    assert actual == expected, "every frontend's field first, then the frontend's own"
