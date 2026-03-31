"""Tests for /import command — target device command help from JSON."""

import json
import time

import pytest

from termapy.demo import FakeSerial
from termapy.plugins import EngineAPI, PluginContext, TargetCommand
from termapy.repl import ReplEngine


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def dev() -> FakeSerial:
    """Create a FakeSerial instance."""
    return FakeSerial(baudrate=9600)


def _send_cmd(dev: FakeSerial, cmd: str) -> str:
    """Send an ASCII command and return the response as a string."""
    dev.write(cmd.encode() + b"\r")
    time.sleep(0.01)
    return dev.read(4096).decode()


@pytest.fixture
def engine(tmp_path):
    """Create a ReplEngine with device_json_cmd configured."""
    cfg = {
        "port": "DEMO",
        "baud_rate": 115200,
        "line_ending": "\r",
        "device_json_cmd": "AT+HELP.JSON",
    }
    config_path = tmp_path / "test" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
    engine_api = EngineAPI(
        plugins=eng._plugins,
        target_commands=eng._target_commands,
        set_target_commands=eng.set_target_commands,
        clear_target_commands=eng.clear_target_commands,
    )
    ctx = PluginContext(
        write=lambda t, c=None: output.append((t, c)),
        write_markup=lambda t: output.append((t, None)),
        cfg=cfg,
        config_path=str(config_path),
        engine=engine_api,
    )
    eng.set_context(ctx)
    return eng, output


# -- Demo device AT+HELP.JSON ------------------------------------------------


class TestDemoHelpJson:
    """Tests for the AT+HELP.JSON command on the demo device."""

    def test_returns_valid_json(self, dev: FakeSerial) -> None:
        # Act
        actual = _send_cmd(dev, "AT+HELP.JSON")
        # Assert
        data = json.loads(actual)
        assert isinstance(data, dict)  # response is a JSON object
        assert "commands" in data  # has commands wrapper

    def test_contains_at_commands(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert "AT" in cmds  # contains AT command
        assert "AT+INFO" in cmds  # contains AT+INFO
        assert "AT+TEMP" in cmds  # contains AT+TEMP
        assert "AT+STATUS" in cmds  # contains AT+STATUS

    def test_contains_non_at_commands(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert "mem" in cmds  # contains mem command

    def test_contains_gps_commands(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert "$GPGGA" in cmds  # NMEA position fix
        assert "$GPRMC" in cmds  # NMEA nav data
        assert "$GPGSA" in cmds  # NMEA DOP
        assert "$GPGSV" in cmds  # NMEA satellites in view

    def test_entries_have_help_field(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        for name, entry in cmds.items():
            assert "help" in entry, f"'{name}' missing help field"
            assert isinstance(entry["help"], str)  # help is a string

    def test_entries_have_args_field(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        for name, entry in cmds.items():
            assert "args" in entry, f"'{name}' missing args field"
            assert isinstance(entry["args"], str)  # args is a string

    def test_led_has_args(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert cmds["AT+LED"]["args"] != ""  # LED has required arg

    def test_at_has_empty_args(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert cmds["AT"]["args"] == ""  # AT takes no args

    def test_command_count(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert len(cmds) >= 10  # at least 10 commands exposed


# -- TargetCommand dataclass --------------------------------------------------


class TestTargetCommand:
    def test_create_with_args(self) -> None:
        # Act
        tc = TargetCommand(name="AT+LED", help="Control LED", args="<on|off>")
        # Assert
        assert tc.name == "AT+LED"  # name preserved
        assert tc.help == "Control LED"  # help preserved
        assert tc.args == "<on|off>"  # args preserved

    def test_create_without_args(self) -> None:
        # Act
        tc = TargetCommand(name="AT", help="Connection test")
        # Assert
        assert tc.args == ""  # args defaults to empty


# -- ReplEngine target command storage ----------------------------------------


class TestTargetCommandStorage:
    def test_initially_empty(self, engine) -> None:
        # Arrange
        eng, _ = engine
        # Assert
        assert eng._target_commands == {}  # starts empty

    def test_set_target_commands(self, engine) -> None:
        # Arrange
        eng, _ = engine
        commands = {
            "AT": TargetCommand(name="AT", help="Connection test"),
            "AT+INFO": TargetCommand(name="AT+INFO", help="Device info"),
        }
        # Act
        eng.set_target_commands(commands)
        # Assert
        assert len(eng._target_commands) == 2  # both stored
        assert "AT" in eng._target_commands  # AT present
        assert "AT+INFO" in eng._target_commands  # AT+INFO present

    def test_set_replaces_previous(self, engine) -> None:
        # Arrange
        eng, _ = engine
        eng.set_target_commands({
            "OLD": TargetCommand(name="OLD", help="old cmd"),
        })
        # Act
        eng.set_target_commands({
            "NEW": TargetCommand(name="NEW", help="new cmd"),
        })
        # Assert
        assert "OLD" not in eng._target_commands  # old entry removed
        assert "NEW" in eng._target_commands  # new entry present

    def test_clear_target_commands(self, engine) -> None:
        # Arrange
        eng, _ = engine
        eng.set_target_commands({
            "AT": TargetCommand(name="AT", help="Connection test"),
        })
        # Act
        eng.clear_target_commands()
        # Assert
        assert eng._target_commands == {}  # cleared


# -- JSON parsing (import_cmd helpers) ----------------------------------------


class TestReadJsonParsing:
    """Test the JSON parsing logic from import_cmd._read_json indirectly."""

    def test_parse_demo_response(self, dev: FakeSerial) -> None:
        """Verify the demo JSON response can build TargetCommands."""
        # Arrange
        raw = _send_cmd(dev, "AT+HELP.JSON")
        data = json.loads(raw)
        cmd_dict = data.get("commands", data)
        # Act
        commands = {}
        for name, entry in cmd_dict.items():
            if isinstance(entry, dict) and "help" in entry:
                commands[name] = TargetCommand(
                    name=name,
                    help=entry["help"],
                    args=entry.get("args", ""),
                )
        # Assert
        assert len(commands) >= 10  # built from all entries
        assert commands["AT+LED"].args == "<on|off>"  # args preserved

    def test_skip_entries_without_help(self) -> None:
        """Entries missing 'help' should be skipped."""
        # Arrange
        data = {
            "good": {"help": "valid", "args": ""},
            "bad": {"args": "only"},
            "also_bad": "just a string",
        }
        # Act
        commands = {}
        for name, entry in data.items():
            if isinstance(entry, dict) and "help" in entry:
                commands[name] = TargetCommand(
                    name=name, help=entry["help"], args=entry.get("args", "")
                )
        # Assert
        assert len(commands) == 1  # only valid entry kept
        assert "good" in commands  # valid entry present
        assert "bad" not in commands  # missing help skipped
        assert "also_bad" not in commands  # non-dict skipped

    def test_json_with_preamble(self) -> None:
        """JSON extraction should work even with preamble text."""
        # Arrange
        raw = 'Some preamble text\r\n{"AT": {"help": "test", "args": ""}}\r\n'
        start = raw.find("{")
        # Act
        data = json.loads(raw[start:])
        # Assert
        assert "AT" in data  # found JSON despite preamble


# -- /help.target subcommand --------------------------------------------------


class TestHelpTarget:
    """Tests for the /help.target subcommand."""

    def test_no_target_commands(self, engine) -> None:
        """Shows message when no commands imported."""
        # Arrange
        eng, output = engine
        # Act
        result = eng.dispatch("help.target")
        # Assert
        messages = [t for t, _ in output]
        assert any("No target" in m for m in messages)  # says no commands

    def test_lists_imported_commands(self, engine) -> None:
        """Lists target commands after import."""
        # Arrange
        eng, output = engine
        eng.set_target_commands({
            "AT": TargetCommand(name="AT", help="Connection test"),
            "AT+INFO": TargetCommand(name="AT+INFO", help="Device info"),
        })
        # Act
        output.clear()
        result = eng.dispatch("help.target")
        # Assert
        messages = " ".join(t for t, _ in output)
        assert "AT" in messages  # AT listed
        assert "AT+INFO" in messages  # AT+INFO listed
        assert "Target Device" in messages  # section header shown

    def test_shows_args(self, engine) -> None:
        """Target commands with args display them."""
        # Arrange
        eng, output = engine
        eng.set_target_commands({
            "AT+LED": TargetCommand(name="AT+LED", help="Control LED", args="<on|off>"),
        })
        # Act
        output.clear()
        result = eng.dispatch("help.target")
        # Assert
        messages = " ".join(t for t, _ in output)
        assert "on|off" in messages  # args shown

    def test_reports_count(self, engine) -> None:
        """Reports total count of target commands."""
        # Arrange
        eng, output = engine
        eng.set_target_commands({
            "AT": TargetCommand(name="AT", help="test"),
            "AT+INFO": TargetCommand(name="AT+INFO", help="test"),
            "AT+TEMP": TargetCommand(name="AT+TEMP", help="test"),
        })
        # Act
        output.clear()
        result = eng.dispatch("help.target")
        # Assert
        messages = " ".join(t for t, _ in output)
        assert "3 device commands" in messages  # count reported


# -- Config key ---------------------------------------------------------------


class TestTargetHelpCmdConfig:
    def test_config_has_key(self, engine) -> None:
        # Arrange
        eng, _ = engine
        # Assert
        expected = "AT+HELP.JSON"
        actual = eng.cfg.get("device_json_cmd", "")
        assert actual == expected  # config key present

    def test_demo_cfg_has_key(self) -> None:
        """The demo.cfg should have device_json_cmd set."""
        # Arrange
        from pathlib import Path
        demo_cfg = (
            Path(__file__).parent.parent
            / "src" / "termapy" / "builtins" / "demo" / "demo.cfg"
        )
        cfg = json.loads(demo_cfg.read_text())
        # Assert
        assert cfg["device_json_cmd"] == "AT+HELP.JSON"  # demo configured

    def test_default_cfg_has_key(self) -> None:
        """DEFAULT_CFG should include device_json_cmd."""
        # Arrange
        from termapy.defaults import DEFAULT_CFG
        # Assert
        assert "device_json_cmd" in DEFAULT_CFG  # key in defaults
        assert DEFAULT_CFG["device_json_cmd"] == ""  # default is empty


# -- Custom prefix ------------------------------------------------------------


class TestCustomPrefix:
    """Verify commands track the configured cmd_prefix."""

    def test_cmd_helper_uses_default_prefix(self, tmp_path) -> None:
        # Arrange
        cfg = {"cmd_prefix": "/"}
        config_path = tmp_path / "t" / "t.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None)
        # Act / Assert
        assert eng.cmd("import") == "/import"  # default prefix
        assert eng.cmd("help") == "/help"  # default prefix

    def test_cmd_helper_uses_custom_prefix(self, tmp_path) -> None:
        # Arrange
        cfg = {"cmd_prefix": "!"}
        config_path = tmp_path / "t" / "t.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None, prefix="!")
        # Act / Assert
        assert eng.cmd("import") == "!import"  # custom prefix
        assert eng.cmd("help") == "!help"  # custom prefix

    def test_dispatch_with_custom_prefix(self, tmp_path) -> None:
        """Commands dispatched via custom prefix should work."""
        # Arrange
        cfg = {"cmd_prefix": "!"}
        config_path = tmp_path / "t" / "t.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        output = []
        eng = ReplEngine(cfg, str(config_path),
                         lambda t, c=None: output.append((t, c)), prefix="!")
        # Act
        result = eng.dispatch("ver")
        # Assert
        assert result.success  # dispatch works with custom prefix
