"""The config schema is one table, and one validator answers for the file, the editor and /cfg (registry cfg-schema-one-table).

``defaults.CFG_KEYS`` holds each key once; ``DEFAULT_CFG``, ``CFG_HELP``,
``CFG_CHOICES`` and ``MANAGED_CFG_KEYS`` are views of it.
``config.check_cfg_value`` reads the kinds, and a load, the config editor
and ``/cfg`` all ask it.  The guard that each surface does is in
``test_architecture.py``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.app import App

from termapy import port_control
from termapy.cli import CLITerminal
from termapy.config import (
    BOOL_CFG_PATHS,
    CFG_CHECKS,
    cfg_choice,
    cfg_value_text,
    check_cfg_value,
    validate_config,
)
from termapy.defaults import (
    CFG_CHOICES,
    CFG_HELP,
    CFG_KEYS,
    CUSTOM_BUTTON_FIELDS,
    DEFAULT_CFG,
    MANAGED_CFG_KEYS,
    STANDARD_BAUD_RATES,
    cfg_key,
    cfg_key_paths,
    cfg_value,
)
from termapy.dialogs.config_editor import ConfigEditor

LEAVES = [record.path.rpartition(".")[2] for record in CFG_KEYS]

# (key, a value the file might hold, the same value as typed at /cfg)
BAD_VALUES = [
    ("max_lines", 0, "0"),
    ("max_grep_lines", 0, "0"),
    ("proto_frame_gap_ms", 0, "0"),
    ("default_response_timeout_ms", 0, "0"),
    ("cmd_delay_ms", -1, "-1"),
    ("tx_char_delay_ms", -1, "-1"),
    ("encoding", "nope", "nope"),
    ("request_err_pattern", "(", "("),
    ("eol", "abc", "abc"),
    ("eol_rx", "nope", "nope"),
    ("protocol", "nope", "nope"),
    ("parity", "X", "X"),
    ("byte_size", 9, "9"),
]


@pytest.fixture
def cli(tmp_path) -> CLITerminal:
    """A CLI host over a throwaway config, every built-in registered."""
    serial = DEFAULT_CFG["serial"]
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}}
    path = tmp_path / "c" / "c.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(path), no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path  # keep the checkout's plugin/ out of the boot
    return terminal


def _editor_answer(tmp_path, key: str, raw: str) -> tuple[str, str]:
    """What the config editor's live check says about ``raw`` on ``key``'s line."""
    serial = DEFAULT_CFG["serial"]
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}}
    path = tmp_path / "e" / "e.cfg"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(cfg))

    async def scenario() -> tuple[str, str]:
        async with App[None]().run_test() as pilot:
            pilot.app.push_screen(ConfigEditor(cfg, str(path)))
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, ConfigEditor), "the editor is on top"
            return screen._validate_value(key, raw)

    return asyncio.run(scenario())


class TestTheTable:
    def test_the_defaults_are_a_view_of_the_rows_in_their_order(self):
        """DEFAULT_CFG nests each row's default by path, in table order: the table is the layout of a config file."""
        top_level = [record.path for record in CFG_KEYS if "." not in record.path]
        assert list(DEFAULT_CFG) == top_level, "a config file is laid out in the table's order"
        for record in CFG_KEYS:
            if record.kind != "section":
                assert cfg_value({}, record.path) == record.default, record.path

    def test_a_sections_leaves_follow_it(self):
        """A section's row comes first and its leaves directly after, so a nested view can be built in order."""
        seen: set[str] = set()
        for record in CFG_KEYS:
            section = record.path.rpartition(".")[0]
            assert not section or section in seen, f"{record.path} comes before its section"
            if record.kind == "section":
                seen.add(record.path)
        assert cfg_key_paths() == tuple(record.path for record in CFG_KEYS)

    def test_a_leaf_names_one_key(self):
        """A key's name in the file is its leaf, and CFG_HELP is by leaf, so no two rows may share one."""
        assert len(set(LEAVES)) == len(LEAVES), "two rows share a leaf"

    def test_every_row_has_help_and_a_kind_with_a_check(self):
        """A row names a kind config.check_cfg_value knows and says what it does, so no key is unchecked or undocumented."""
        for record in (*CFG_KEYS, *CUSTOM_BUTTON_FIELDS):
            assert record.kind in CFG_CHECKS, f"{record.path}: unknown kind {record.kind}"
            assert record.help, f"{record.path} has no help line"

    def test_the_bounds_fit_the_kind(self):
        """Only an enum has choices, and it must; only an int has a min."""
        for record in CFG_KEYS:
            assert bool(record.choices) == (record.kind == "enum"), record.path
            assert record.min is None or record.kind == "int", record.path

    def test_the_views_come_from_the_rows(self):
        """Choices, managed keys, bool paths and help rows are all read from the table."""
        assert set(CFG_CHOICES) == {
            record.path.rpartition(".")[2] for record in CFG_KEYS if record.kind == "enum"
        }
        assert MANAGED_CFG_KEYS == {"config_version"}, "the one key termapy writes itself"
        assert BOOL_CFG_PATHS == {
            tuple(record.path.split(".")) for record in CFG_KEYS if record.kind == "bool"
        }
        expected = {*LEAVES, *(record.path for record in CUSTOM_BUTTON_FIELDS)}
        assert set(CFG_HELP) == expected, "every key has its help row, and only keys do"

    def test_every_default_passes_its_own_check(self):
        """The table never ships a default its own validator refuses; an empty port is the deliberate exception, a config has to name one."""
        bad = [
            record.path
            for record in CFG_KEYS
            if record.kind != "section"
            and check_cfg_value(record.path, record.default, DEFAULT_CFG) is not None
        ]
        assert bad == ["serial.port"], "only the port has no usable default"

    def test_a_name_finds_its_row(self):
        """A path, a unique leaf and a button field all find a record, and nothing else does."""
        assert cfg_key("serial.baud_rate") is cfg_key("baud_rate"), "a leaf is its path"
        record = cfg_key("enabled")
        assert record is not None and record.kind == "bool", "a button field has a row"
        assert cfg_key("nope") is None

    def test_the_port_commands_set_only_keys_the_table_has(self):
        """port_control's pyserial map is keyed by leaf: every key it sets is a serial row, so the two cannot drift."""
        serial_leaves = {
            record.path.rpartition(".")[2]
            for record in CFG_KEYS
            if record.path.startswith("serial.")
        }
        assert set(port_control.PORT_PROPS) <= serial_leaves
        assert set(port_control.SERIAL_KEYS) <= serial_leaves


class TestOneAnswer:
    @pytest.mark.parametrize(("key", "value", "typed"), BAD_VALUES)
    def test_the_same_bad_value_is_warned_red_and_refused(self, tmp_path, cli, key, value, typed):
        """The file warns, the editor shows red and /cfg.auto refuses, all with the validator's one reason."""
        # Arrange
        record = cfg_key(key)
        assert record is not None, f"{key} has a row"
        finding = check_cfg_value(key, value, DEFAULT_CFG)
        assert finding is not None, f"{value!r} is wrong for {key}"
        section, _, leaf = record.path.rpartition(".")
        in_file = {section: {leaf: value}} if section else {leaf: value}
        before = cfg_value(cli.repl.cfg, record.path)

        # Act
        warnings = validate_config(in_file)
        status, error = _editor_answer(tmp_path, leaf, json.dumps(value))
        result = cli.repl.dispatch(f"cfg.auto {leaf} {typed}")

        # Assert
        assert any(warning.startswith(f"{record.path}: ") for warning in warnings), warnings
        assert status.startswith("[red]"), "red in the editor"
        assert finding.reason.split(":")[0] in error, "the editor gives the same reason"
        assert not result.success, "/cfg.auto refuses it"
        assert f"Invalid {leaf}: {typed} (" in result.error
        assert cfg_value(cli.repl.cfg, record.path) == before, "the value is unchanged"

    @pytest.mark.parametrize("rate", [110, 600, 14400, 28800])
    def test_every_standard_rate_is_green_in_the_editor(self, tmp_path, rate):
        """H1: the editor kept its own baud list, missing four rates a load accepted."""
        assert rate in STANDARD_BAUD_RATES
        status, error = _editor_answer(tmp_path, "baud_rate", str(rate))
        assert (status.startswith("[green]"), error) == (True, "")

    def test_a_non_standard_rate_needs_custom_baud(self, tmp_path, cli):
        """baud-standard-unless-custom: off the list, a rate warns at load, shows red and is refused, until custom_baud is true."""
        # Act
        warnings = validate_config({"serial": {"baud_rate": 250000}})
        status, _ = _editor_answer(tmp_path, "baud_rate", "250000")
        refused = cli.repl.dispatch("cfg.auto baud_rate 250000")
        cli.repl.dispatch("cfg.auto custom_baud true")
        taken = cli.repl.dispatch("cfg.auto baud_rate 250000")

        # Assert
        expected = "not a standard rate: set custom_baud to true to allow it"
        assert any(expected in warning for warning in warnings), warnings
        assert status.startswith("[red]"), "red, as any refused value is"
        assert refused.success is False and expected in refused.error, refused.error
        assert taken.success, taken.error
        assert cfg_value(cli.repl.cfg, "serial.baud_rate") == 250000, "custom_baud let it in"


class TestSetting:
    @pytest.mark.parametrize("line", ["cfg.auto config_version 99", "cfg config_version 99"])
    def test_a_managed_key_is_refused(self, cli, line):
        """config_version is termapy's to write: /cfg and /cfg.auto refuse it by name and leave it."""
        before = cli.repl.cfg["config_version"]
        result = cli.repl.dispatch(line)
        assert result.error == "Managed key: config_version (termapy writes it)"
        assert cli.repl.cfg["config_version"] == before

    def test_a_managed_key_can_still_be_read(self, cli):
        """Refusing the write leaves the query: bare /cfg config_version answers."""
        result = cli.repl.dispatch("cfg config_version")
        assert result.value == cli.repl.cfg["config_version"]

    def test_a_numeric_choice_takes_a_fraction_whatever_the_key_holds(self, cli):
        """/cfg stop_bits 1.5 used to fail: the type came from the 1 the key held, so 1.5 was not an int."""
        result = cli.repl.dispatch("cfg.auto stop_bits 1.5")
        assert result.success, result.error
        assert cli.repl.cfg["serial"]["stop_bits"] == 1.5

    def test_the_type_comes_from_the_table_not_the_file(self, cli):
        """A key the file holds as the wrong type can be put right with /cfg: its type is the row's."""
        cli.repl._cfg_data["max_lines"] = "lots"
        result = cli.repl.dispatch("cfg.auto max_lines 500")
        assert result.success, result.error
        assert cli.repl.cfg["max_lines"] == 500

    @pytest.mark.parametrize("key", ["custom_buttons", "serial"])
    def test_a_container_cannot_be_set_to_text(self, cli, key):
        """A list or section stays what it is: typed text used to replace it, and break what reads it."""
        before = cli.repl.cfg[key]
        result = cli.repl.dispatch(f"cfg.auto {key} oops")
        assert not result.success, "text is not a list or a section"
        assert cli.repl.cfg[key] == before

    @pytest.mark.parametrize("typed", [r"\r", "cr"])
    def test_a_line_ending_typed_as_text_is_pointed_at_its_setter(self, cli, typed):
        """/cfg takes text as typed, so a backslash-r is two characters: refused, naming the command that sets it by name."""
        before = cli.repl.cfg["eol"]
        result = cli.repl.dispatch(f"cfg.auto eol {typed}")
        assert not result.success, "two characters would go out after every line"
        assert "/term.eol sets it by name" in result.error
        assert cli.repl.cfg["eol"] == before

    def test_a_good_value_still_applies(self, cli):
        """The check refuses only what is wrong: an ordinary edit goes through."""
        result = cli.repl.dispatch("cfg.auto max_lines 500")
        assert (result.success, cli.repl.cfg["max_lines"]) == (True, 500)

    def test_a_change_reports_the_value_as_termapy_spells_it(self, cli, capsys):
        """The session line says echo = off, as /term.echo answers, never Python's False."""
        # Arrange
        capsys.readouterr()

        # Act
        cli.repl.dispatch("cfg.auto echo off")
        query = cli.repl.dispatch("cfg echo")

        # Assert
        assert "echo = off  (session)" in capsys.readouterr().out, "the change line"
        assert query.value is False, "the query answers the same spelling"


class TestValueText:
    """config.cfg_value_text: one spelling for a config value wherever termapy shows one."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, "on"),
            (False, "off"),
            ("right", "right"),
            ("", '""'),
            (4, "4"),
            (1.5, "1.5"),
            (None, "null"),
            ({"port": "DEMO"}, '{"port": "DEMO"}'),
            (["a", 1], '["a", 1]'),
        ],
    )
    def test_each_kind_reads_as_termapy_writes_it(self, value, expected):
        """A switch is on/off, a container compact JSON, an empty string quoted, anything else as written."""
        assert cfg_value_text(value) == expected


class TestTheCheck:
    @pytest.mark.parametrize(
        ("key", "typed", "expected"),
        [("stop_bits", "1.5", 1.5), ("byte_size", "7", 7), ("stop_bits", "2.0", 2)],
    )
    def test_a_numeric_choice_matches_as_a_number(self, key, typed, expected):
        """Typed text matches a number by value and comes back as the table's number."""
        assert cfg_choice(key, typed) == expected

    def test_a_number_that_is_not_a_choice_is_refused(self):
        """A number outside the set names the choices, as text outside a set does."""
        with pytest.raises(ValueError, match="use 5/6/7/8"):
            cfg_choice("byte_size", "9")

    @pytest.mark.parametrize("value", [True, [1], {"a": 1}])
    def test_a_value_of_another_shape_is_not_a_choice(self, value):
        """true is not the number 1, and a list or object neither matches nor raises."""
        assert check_cfg_value("stop_bits", value) is not None

    def test_a_key_the_schema_does_not_hold_has_no_rule(self):
        """A name with no row is the unknown-key warning's business, not this check's."""
        assert check_cfg_value("nope", 1) is None

    def test_a_bool_is_not_an_int(self):
        """A JSON true in a count is a type mistake, though Python calls it an int."""
        assert check_cfg_value("max_lines", True) is not None

    @pytest.mark.parametrize("pattern", ["", r"^ERROR\b"])
    def test_a_pattern_that_compiles_passes(self, pattern):
        """An empty pattern means off, and a good one compiles."""
        assert check_cfg_value("request_err_pattern", pattern) is None


class TestTheLoadWarnings:
    def test_a_warning_names_the_key_the_rule_and_the_value(self):
        """One shape for every key: the path, what it must be, what it holds."""
        expected = "max_grep_lines: must be a positive integer, got 0"
        assert validate_config({"max_grep_lines": 0}) == [expected]

    def test_a_section_of_the_wrong_shape_warns_and_does_not_crash(self):
        """A hand-edited serial that is not an object used to raise; it is a warning like any other."""
        assert validate_config({"serial": 5}) == ["serial: must be a JSON object, got 5"]

    def test_a_long_value_is_cut(self):
        """A warning stays one readable line whatever the file holds."""
        warning = validate_config({"max_lines": "x" * 500})[0]
        assert len(warning) < 120, warning
        assert warning.endswith("..."), warning

    def test_a_key_the_file_lacks_is_not_a_finding(self):
        """--check and a load read the file as written: a missing key is the backfill's job."""
        assert validate_config({}) == []
