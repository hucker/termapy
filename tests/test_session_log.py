"""Tests for termapy.session_log: the one session-log writer, its line format and markers, and the old MCP log's move."""

from __future__ import annotations

import json
import re

from termapy.config import mcp_log_path, prepare_cfg_folder
from termapy.defaults import default_cfg
from termapy.session_log import SESSION_END, SESSION_START, SessionLog, entry_text

_STAMPED = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\] ")


def test_every_line_is_stamped_and_marked(tmp_path):
    """A session reads [stamp] marker text, framed by Session Start and Session End: the TUI's log and MCP's alike."""
    # Arrange
    log = SessionLog()
    path = tmp_path / "dev.mcp.log"

    # Act
    log.open(path)
    log.write("$", ["/ver  (normal)"])
    log.write("<", ["one", "two"])
    log.end()

    # Assert
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(_STAMPED.match(line) for line in lines), f"every line stamped: {lines}"
    actual = [line.partition("] ")[2] for line in lines]
    expected = [f"# {SESSION_START}", "$ /ver  (normal)", "< one", "< two", f"# {SESSION_END}"]
    assert actual == expected, "the markers and the text, in order"
    assert [entry_text(line) for line in lines][0] == SESSION_START, (
        "entry_text drops stamp and marker"
    )


def test_a_batch_shares_one_stamp(tmp_path):
    """Lines written together arrived together, so they carry one timestamp."""
    # Arrange
    log = SessionLog()
    path = tmp_path / "s.log"
    log.open(path)

    # Act
    log.write("<", ["a", "b", "c"])

    # Assert
    stamps = {line.partition("] ")[0] for line in path.read_text(encoding="utf-8").splitlines()[1:]}
    assert len(stamps) == 1


def test_a_closed_log_drops_writes_but_still_tees(tmp_path):
    """Before a config opens the log nothing is written, and the --mcp-verbose copy still sees every line."""
    # Arrange
    log = SessionLog()
    teed: list[str] = []
    log.tee = teed.append

    # Act
    log.write("#", ["before any config"])

    # Assert
    assert log.fh is None and log.path is None, "nothing open"
    assert [entry_text(line) for line in teed] == ["before any config"], "the tee is not the file's"


def test_writing_twice_and_reopening_appends(tmp_path):
    """A second session on the same config appends after the first: a log is the config's history."""
    # Arrange
    path = tmp_path / "s.log"
    for word in ("first", "second"):
        log = SessionLog()
        log.open(path)
        log.write("#", [word])
        log.end()

    # Act
    actual = [entry_text(line) for line in path.read_text(encoding="utf-8").splitlines()]

    # Assert
    expected = [SESSION_START, "first", SESSION_END, SESSION_START, "second", SESSION_END]
    assert actual == expected


def _cfg_at(tmp_path, name: str = "dev"):
    """A config file at <tmp>/<name>/<name>.cfg."""
    path = tmp_path / name / f"{name}.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(default_cfg()), encoding="utf-8")
    return path


def test_the_old_mcp_log_moves_beside_the_cfg(tmp_path):
    """A load moves mcp/session.log to <cfg>.mcp.log, and the emptied mcp/ folder goes with it."""
    # Arrange
    cfg_path = _cfg_at(tmp_path)
    old = cfg_path.parent / "mcp" / "session.log"
    old.parent.mkdir()
    old.write_text("earlier sessions\n", encoding="utf-8")

    # Act
    prepare_cfg_folder(str(cfg_path))

    # Assert
    new = mcp_log_path(str(cfg_path))
    assert new.name == "dev.mcp.log", "named after the cfg, beside it"
    assert new.read_text(encoding="utf-8") == "earlier sessions\n", "the history moved, not lost"
    assert not old.parent.exists(), "an emptied mcp/ is removed"


def test_the_move_never_overwrites_a_log_already_there(tmp_path):
    """When <cfg>.mcp.log exists, the old file stays where it is, and so does its folder."""
    # Arrange
    cfg_path = _cfg_at(tmp_path)
    new = mcp_log_path(str(cfg_path))
    new.write_text("current\n", encoding="utf-8")
    old = cfg_path.parent / "mcp" / "session.log"
    old.parent.mkdir()
    old.write_text("old\n", encoding="utf-8")

    # Act
    prepare_cfg_folder(str(cfg_path))

    # Assert
    assert new.read_text(encoding="utf-8") == "current\n", "the newer log is untouched"
    assert old.read_text(encoding="utf-8") == "old\n", "and the old one is left for the user"
