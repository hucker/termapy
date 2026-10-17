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
from termapy.plugins import CmdResult, Column, PluginInfo, RecordView, TableView
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
    eng.register_plugin(
        PluginInfo(
            name="rack",
            args="",
            help="h",
            handler=lambda ctx, args: CmdResult.ok(value=""),
            bare_sub="boards",
        )
    )
    eng.register_plugin(
        PluginInfo(
            name="rack.boards",
            args="",
            help="h",
            handler=lambda ctx, args: CmdResult.table(
                "Boards", "board", _COLUMNS, _RECORDS, value=""
            ),
        )
    )

    # Act
    eng.dispatch("rack")

    # Assert
    assert output.count(_TITLE) == 1, "the inner dispatch printed it; the outer one did not again"


# -- record pages: one record, its rows rendered by dispatch ------------------

_STATUS = {"port": "COM4", "open": True, "note": None}
_STATUS_ROWS = (("port", "COM4"), ("open", "yes", "green"), None, ("note", "[none]"))


def test_a_record_page_aligns_each_block_and_escapes_every_value():
    """record-one-format: title, cyan rows aligned per block, a blank line between blocks, notes; text is escaped."""
    # Arrange
    result = CmdResult.record(
        _STATUS,
        _STATUS_ROWS,
        value="COM4",
        title=(("Status", "bold"),),
        notes=(("hint [x]", "dim"),),
    )

    # Act
    actual = result.view.lines()

    # Assert
    expected = [
        "  [bold]Status[/]",
        "  [cyan]port[/]: COM4",
        "  [cyan]open[/]: [green]yes[/]",
        "",
        "  [cyan]note[/]: \\[none]",
        "  [dim]hint \\[x][/]",
    ]
    assert actual == expected, "one page shape; a value that looks like a tag stays text"


def test_cmdresult_record_carries_the_facts_as_data():
    """CmdResult.record keeps the facts' real types in data and renders nothing itself."""
    result = CmdResult.record(_STATUS, _STATUS_ROWS, value="COM4")
    assert (result.data, result.value) == (_STATUS, "COM4"), "bools and None stay themselves"
    assert isinstance(result.view, RecordView), "the rows ride in the view"


def _record_engine() -> tuple[ReplEngine, list[str]]:
    """A bare engine with a /status record command and its output, markup included, captured."""
    output: list[str] = []
    eng = ReplEngine({}, "", lambda text, color=None: output.append(text))
    # A bare engine drops markup; the rows are markup, so route them to the capture.
    eng.ctx.io._write_markup = output.append

    def _handler(ctx, args: str) -> CmdResult:
        return CmdResult.record(_STATUS, _STATUS_ROWS, value="COM4")

    eng.register_plugin(PluginInfo(name="status", args="", help="h", handler=_handler))
    return eng, output


def test_dispatch_prints_the_record_page_once_per_call():
    """In prose mode each dispatch prints its rows once and strips the view; a second call prints them again."""
    # Arrange
    eng, output = _record_engine()

    # Act
    first = eng.dispatch("status")
    eng.dispatch("status")

    # Assert
    rows = [line for line in output if "port" in line]
    assert len(rows) == 2, "one port row per dispatch, nothing cached or doubled"
    assert first.view is None, "the view was consumed at dispatch's exit"


def test_json_gets_the_record_and_no_rows():
    """--json answers with the record in the envelope and no rows of display text beside it."""
    # Arrange
    eng, output = _record_engine()

    # Act
    eng.dispatch("status --json")

    # Assert
    envelope = json.loads(output[-1])
    assert envelope["data"] == _STATUS, "the facts with their real types"
    assert envelope["output_lines"] == [], "the facts ride once"
    assert len(output) == 1, "nothing printed above the envelope"


def test_a_record_session_that_wants_data_prints_nothing():
    """An MCP-style session gets the record with no rows to pay for."""
    eng, output = _record_engine()
    eng.ctx.wants_data = True
    result = eng.dispatch("status")
    assert (output, result.data) == ([], _STATUS), "nothing printed, the record returned"


# -- config_summary: the Cfg picker's and /cfg.list's facts -------------------


def _cfg(tmp_path, name: str, cfg: dict):
    """Write ``<name>/<name>.cfg`` holding ``cfg``."""
    path = tmp_path / name / f"{name}.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_config_summary_reads_port_baud_and_title(tmp_path):
    """/cfg.list and the Cfg picker show one config's port, baud and title from config_summary."""
    path = _cfg(
        tmp_path, "bench", {"title": "Bench board", "serial": {"port": "COM9", "baud_rate": 9600}}
    )
    actual = config_summary(path)
    assert actual == {"port": "COM9", "baud_rate": 9600, "title": "Bench board"}, (
        "the listing facts"
    )


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
    assert (actual["port"], actual["baud_rate"]) == ("COM6", 57600), (
        "migrated before reading, not after"
    )


# -- every list command answers with records ----------------------------------


def _cli_engine(tmp_path):
    """A real CLI host over a throwaway config, so every built-in list command is registered."""
    import json as _json

    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    config_path = tmp_path / "c" / "c.cfg"
    config_path.parent.mkdir()
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "serial is a section"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "", "baud_rate": 115200}}
    config_path.write_text(_json.dumps(cfg))
    eng = CLITerminal(cfg, str(config_path), no_color=True, term_width=80).repl
    # Declare a desktop so a GUI-gated list (/cfg.icon.list) runs on a
    # headless CI runner too: the guard is about shape, not the host.
    from dataclasses import replace

    eng.ctx.capabilities = replace(eng.ctx.capabilities, gui_apps=True)
    return eng


# List commands whose items are each one value under a unique name answer
# with a mapping, so a consumer looks a name up instead of searching.
_MAPPINGS: dict[str, str] = {
    "env.list": "variable name -> value",
    "var.list": "one dict per variable namespace (user, launch, ...)",
}


def _lists_without_records(eng: ReplEngine) -> list[str]:
    """Every .list / .lib command whose answer to a data caller has the wrong shape.

    A mapping command must answer with a dict; every other one with a list of dicts.
    """
    eng.ctx.wants_data = True
    offenders = []
    for name in sorted(name for name in eng._plugins if name.endswith((".list", ".lib"))):
        result = eng.dispatch(name)
        if name in _MAPPINGS:
            right_shape = isinstance(result.data, dict)
        else:
            records = result.data if isinstance(result.data, list) else None
            right_shape = records is not None and all(
                isinstance(record, dict) for record in records
            )
        if not result.success or not right_shape:
            offenders.append(f"{name}: success={result.success} data={type(result.data).__name__}")
    return offenders


def test_every_list_command_answers_with_records(tmp_path):
    """list-one-format: every .list and .lib command answers a data caller with a list of dicts, never lines of text."""
    # Arrange
    eng = _cli_engine(tmp_path)

    # Act
    offenders = _lists_without_records(eng)

    # Assert
    assert offenders == [], (
        "a list command returns CmdResult.table(title, noun, columns, records, value=...):\n  "
        + "\n  ".join(offenders)
    )


def test_list_guard_fires_on_a_probe(tmp_path):
    """The list guard flags a .list command that prints its rows and answers with no records."""
    # Arrange
    eng = _cli_engine(tmp_path)

    def _handler(ctx, args: str) -> CmdResult:
        ctx.io.output("  bench  115200")
        return CmdResult.ok(value="bench")

    eng.register_plugin(PluginInfo(name="probe.list", args="", help="h", handler=_handler))

    # Act
    offenders = _lists_without_records(eng)

    # Assert
    assert offenders == ["probe.list: success=True data=NoneType"], (
        "the printing list is the one flagged"
    )
