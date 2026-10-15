"""list-one-format: a list answer is records plus a view, and dispatch renders the view once.

``CmdResult.table`` carries the records as ``data`` and a ``TableView``;
the handler renders nothing.  Dispatch's exit prints the view unless the
caller wants data, and strips it either way.  A plain ``ReplEngine`` with
captured output stands in for the host.
"""

from __future__ import annotations

import json

from termapy.config import config_summary
from termapy.folder_ops import FILE_COLUMNS
from termapy.plugins import CmdResult, Column, PluginInfo, TableView
from termapy.plugins.views import counted
from termapy.repl import ReplEngine

_COLUMNS = (
    Column("NAME", lambda record: record["name"]),
    Column("BAUD", lambda record: str(record["baud"]), right=True),
    Column("NOTE", lambda record: record["note"]),
)
_RECORDS = (
    {"name": "bench", "baud": 115200, "note": "the long board"},
    {"name": "lab", "baud": 9600, "note": ""},
)
_TITLE = "Boards - 2 boards"


def _view(records=_RECORDS) -> TableView:
    return TableView("Boards", "board", _COLUMNS, tuple(records))


# -- the view: one shape ------------------------------------------------------


def test_a_table_is_title_header_dashes_rows():
    """list-one-format: title with count, then header, dashes and one row per record, columns two apart."""
    # Act
    actual = [text for text, _ in _view().lines()]

    # Assert
    expected = [
        _TITLE,
        "NAME   BAUD    NOTE",
        "-----  ------  --------------",
        "bench  115200  the long board",
        "lab      9600",
    ]
    assert actual == expected, "the one table shape"


def test_header_and_dashes_are_dim_rows_plain():
    """The header and dashes are dim and the rows plain, so the data stands out in every frontend."""
    actual = [color for _, color in _view().lines()]
    assert actual == ["", "dim", "dim", "", ""], "title plain, header and dashes dim, rows plain"


def test_an_empty_table_is_its_title_alone():
    """No records: one line saying so, no header over nothing."""
    assert _view(records=()).lines() == [("Boards - empty", "")], "empty table is the title line"


def test_the_count_reads_in_the_singular_for_one():
    """One record is '1 script', not '1 scripts'."""
    actual = (counted(1, "script"), counted(0, "script"), counted(7, "script"))
    assert actual == ("1 script", "0 scripts", "7 scripts"), "naive plural, singular for one"


def test_a_vanished_file_shows_question_marks():
    """A file that vanished between glob and stat lists with '?' for size and age instead of failing."""
    record = {"name": "gone.run", "bytes": 0, "mtime": None, "age_s": None}
    actual = [column.cell(record) for column in FILE_COLUMNS]
    assert actual == ["gone.run", "?", "?"], "size and age unknown, name kept"


def test_cmdresult_table_carries_records_and_view():
    """CmdResult.table puts the records in data and their table in view; it renders nothing."""
    result = CmdResult.table("Boards", "board", _COLUMNS, _RECORDS, value="bench\nlab")
    assert result.data == list(_RECORDS), "the records, numbers still numbers"
    assert result.view == _view(), "the view describes the same records"


# -- dispatch: renders once, or not at all ------------------------------------


def _engine() -> tuple[ReplEngine, list[str]]:
    """A bare engine with a /boards table command and its output captured."""
    output: list[str] = []
    eng = ReplEngine({}, "", lambda text, color=None: output.append(text))

    def _handler(ctx, args: str) -> CmdResult:
        return CmdResult.table("Boards", "board", _COLUMNS, _RECORDS, value="bench\nlab")

    eng.register_plugin(PluginInfo(name="boards", args="", help="h", handler=_handler))
    return eng, output


def test_dispatch_prints_the_table_and_strips_the_view():
    """In prose mode the table prints once and the returned result carries no view: a view never leaves dispatch."""
    # Arrange
    eng, output = _engine()

    # Act
    result = eng.dispatch("boards")

    # Assert
    assert output.count(_TITLE) == 1, "the table printed once"
    assert result.view is None, "the view was consumed at dispatch's exit"
    assert result.data == list(_RECORDS), "the records stay on the result"


def test_two_dispatches_print_two_tables():
    """Each dispatch renders its own table: nothing is cached or swallowed between calls."""
    eng, output = _engine()
    eng.dispatch("boards")
    eng.dispatch("boards")
    assert output.count(_TITLE) == 2, "one table per dispatch"


def test_json_gets_the_records_and_no_table():
    """--json answers with the records in the envelope and prints no table beside it."""
    # Arrange
    eng, output = _engine()

    # Act
    eng.dispatch("boards --json")

    # Assert
    assert _TITLE not in output, "no prose table in JSON mode"
    envelope = json.loads(output[-1])
    assert envelope["data"] == list(_RECORDS), "the envelope carries the records"
    assert "view" not in envelope, "a view never reaches JSON"


def test_a_session_that_wants_data_prints_nothing():
    """An MCP-style session (wants_data) gets the records with no prose to pay for."""
    # Arrange
    eng, output = _engine()
    eng.ctx.wants_data = True

    # Act
    result = eng.dispatch("boards")

    # Assert
    assert output == [], "nothing printed"
    assert result.data == list(_RECORDS), "the records came back"


def test_silent_suppresses_the_table():
    """A per-call .silent re-applies at the exit, so the table stays quiet."""
    eng, output = _engine()
    eng.dispatch("boards.silent")
    assert _TITLE not in output, ".silent prints no table"


def test_a_forwarded_table_prints_once():
    """A bare call that runs a table subcommand (bare_sub) prints the table once, not once per dispatch level."""
    # Arrange
    eng, output = _engine()
    eng.register_plugin(PluginInfo(
        name="rack", args="", help="h", handler=lambda ctx, args: CmdResult.ok(value=""),
        bare_sub="boards",
    ))
    eng.register_plugin(PluginInfo(
        name="rack.boards", args="", help="h",
        handler=lambda ctx, args: CmdResult.table("Boards", "board", _COLUMNS, _RECORDS, value=""),
    ))

    # Act
    eng.dispatch("rack")

    # Assert
    assert output.count(_TITLE) == 1, "the inner dispatch printed it; the outer one did not again"


# -- config_summary: the Cfg picker's and /cfg.list's facts -------------------


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


def test_config_summary_never_writes_the_file(tmp_path):
    """Listing a config is a read: a cfg missing newer default keys is left byte-for-byte as it was."""
    # Arrange -- an old, sparse cfg that load_config would backfill and rewrite
    path = _cfg(tmp_path, "old", {"serial": {"port": "COM5"}})
    before = path.read_bytes()

    # Act
    summary = config_summary(path)

    # Assert
    assert summary["port"] == "COM5", "the facts were read"
    assert path.read_bytes() == before, "the file was not rewritten"


def test_config_summary_reads_a_pre_migration_layout(tmp_path):
    """An old flat cfg (port at the top level) is migrated in memory, so its port still shows."""
    path = _cfg(tmp_path, "flat", {"port": "COM6", "baud_rate": 57600})
    actual = config_summary(path)
    assert (actual["port"], actual["baud_rate"]) == ("COM6", 57600), "migrated before reading, not after"
