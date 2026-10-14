"""list-one-format: every list command renders through ``listing``, one shape for prose and data."""

from __future__ import annotations

import json

from termapy.config import config_summary
from termapy.folder_ops import FILE_COLUMNS
from termapy.listing import Column, counted, emit_listing, render_listing
from termapy.repl import ReplEngine

_COLUMNS = (
    Column("NAME", lambda record: record["name"]),
    Column("BAUD", lambda record: str(record["baud"]), right=True),
    Column("NOTE", lambda record: record["note"]),
)
_RECORDS = [
    {"name": "bench", "baud": 115200, "note": "the long board"},
    {"name": "lab", "baud": 9600, "note": ""},
]


def test_a_listing_is_title_header_dashes_rows():
    """list-one-format: title with count, then header, dashes and one row per record, columns two apart."""
    # Act
    actual = [text for text, _ in render_listing("Boards", "board", _COLUMNS, _RECORDS)]

    # Assert
    expected = [
        "Boards - 2 boards",
        "NAME   BAUD    NOTE",
        "-----  ------  --------------",
        "bench  115200  the long board",
        "lab      9600",
    ]
    assert actual == expected, "the one listing shape"


def test_headers_sit_left_and_chrome_is_dim():
    """The header and dashes are dim and the rows plain, so the data stands out in every frontend."""
    lines = render_listing("Boards", "board", _COLUMNS, _RECORDS)
    actual = [color for _, color in lines]
    assert actual == ["", "dim", "dim", "", ""], "title plain, header and dashes dim, rows plain"


def test_an_empty_listing_is_its_title_alone():
    """No records: one line saying so, no header over nothing."""
    actual = render_listing("run/", "script", _COLUMNS, [])
    assert actual == [("run/ - empty", "")], "empty listing is the title line"


def test_the_count_reads_in_the_singular_for_one():
    """One record is '1 script', not '1 scripts'."""
    actual = (counted(1, "script"), counted(0, "script"), counted(7, "script"))
    assert actual == ("1 script", "0 scripts", "7 scripts"), "naive plural, singular for one"


def test_a_vanished_file_shows_question_marks():
    """A file that vanished between glob and stat lists with '?' for size and age instead of failing."""
    record = {"name": "gone.run", "bytes": 0, "mtime": None, "age_s": None}
    actual = [column.cell(record) for column in FILE_COLUMNS]
    assert actual == ["gone.run", "?", "?"], "size and age unknown, name kept"


def _engine() -> tuple[ReplEngine, list[str]]:
    """A bare engine whose output lines are captured."""
    output: list[str] = []
    eng = ReplEngine({}, "", lambda text, color=None: output.append(text))
    return eng, output


def test_emit_listing_prints_prose_and_returns_the_value():
    """Without a data request the table prints and the value is the scriptable scalar."""
    # Arrange
    eng, output = _engine()

    # Act
    result = emit_listing(eng.ctx, "Boards", "board", _COLUMNS, _RECORDS, value="bench\nlab")

    # Assert
    assert result.value == "bench\nlab", "value is what the caller made it"
    assert result.data is None, "no doubled payload when prose was printed"
    assert output[0] == "Boards - 2 boards", "the prose table was printed"


def test_emit_listing_hands_a_data_request_the_records():
    """When data is wanted (--json, /term.request, MCP) the records come back raw and nothing prints."""
    # Arrange
    eng, output = _engine()
    eng.ctx.wants_data = True

    # Act
    result = emit_listing(eng.ctx, "Boards", "board", _COLUMNS, _RECORDS, value="bench\nlab")

    # Assert
    assert result.data == _RECORDS, "the records, numbers still numbers"
    assert output == [], "no prose alongside the data"


def _cfg(tmp_path, name: str, cfg: dict):
    """Write ``<name>/<name>.cfg`` holding ``cfg``."""
    path = tmp_path / name / f"{name}.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_config_summary_reads_port_baud_and_title(tmp_path):
    """/cfg.list and the Cfg picker show one config's port, baud and title from config_summary."""
    path = _cfg(tmp_path, "bench", {"title": "Bench board", "serial": {"port": "COM9", "baud_rate": 9600}})
    actual = config_summary(path)
    assert actual == {"port": "COM9", "baud_rate": 9600, "title": "Bench board"}, "the listing facts"


def test_config_summary_drops_a_title_that_repeats_the_name(tmp_path):
    """New configs are written titled with their own name; that reads as no title."""
    path = _cfg(tmp_path, "lab", {"title": "lab"})
    assert config_summary(path)["title"] == "", "a title equal to the name is no title"


def test_config_summary_of_a_broken_file_is_empty(tmp_path):
    """A cfg that will not load still gets a row: empty facts, not a crash."""
    path = tmp_path / "bad" / "bad.cfg"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    actual = config_summary(path)
    assert actual == {"port": "", "baud_rate": None, "title": ""}, "empty facts for a broken cfg"
