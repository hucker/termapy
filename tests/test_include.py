"""Tests for /include command -- target device command help from JSON."""

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
    )
    from termapy.plugins import IOHandle
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        engine=engine_api,
        io=IOHandle(
            write=lambda t, c=None: output.append((t, c)),
            write_markup=lambda t: output.append((t, None)),
        ),
    )
    eng.set_context(ctx)
    # Seed the `flags` namespace (would be done by app.py._build_context).
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex_mode"] = False
    return eng, output


# -- Demo device AT+HELP.JSON ------------------------------------------------


class TestDemoHelpJson:
    """Tests for the AT+HELP.JSON command on the demo device."""

    def test_returns_valid_json(self, dev: FakeSerial) -> None:
        # Act
        actual = _send_cmd(dev, "AT+HELP.JSON")
        # Assert
        data = json.loads(actual)
        assert isinstance(data, dict), "response is a JSON object"
        assert "commands" in data, "has commands wrapper"

    def test_contains_at_commands(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert "AT" in cmds, "contains AT command"
        assert "AT+INFO" in cmds, "contains AT+INFO"
        assert "AT+TEMP" in cmds, "contains AT+TEMP"
        assert "AT+STATUS" in cmds, "contains AT+STATUS"

    def test_contains_non_at_commands(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert "mem" in cmds, "contains mem command"

    def test_contains_gps_commands(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert "$GPGGA" in cmds, "NMEA position fix"
        assert "$GPRMC" in cmds, "NMEA nav data"
        assert "$GPGSA" in cmds, "NMEA DOP"
        assert "$GPGSV" in cmds, "NMEA satellites in view"

    def test_entries_have_help_field(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        for name, entry in cmds.items():
            assert "help" in entry, f"'{name}' missing help field"
            assert isinstance(entry["help"], str), f"'{name}' help is not a string"

    def test_entries_have_args_field(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        for name, entry in cmds.items():
            assert "args" in entry, f"'{name}' missing args field"
            assert isinstance(entry["args"], str), f"'{name}' args is not a string"

    def test_led_has_args(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert cmds["AT+LED"]["args"] != "", "LED has required arg"

    def test_at_has_empty_args(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert cmds["AT"]["args"] == "", "AT takes no args"

    def test_command_count(self, dev: FakeSerial) -> None:
        # Act
        cmds = json.loads(_send_cmd(dev, "AT+HELP.JSON"))["commands"]
        # Assert
        assert len(cmds) >= 10, "at least 10 commands exposed"


# -- TargetCommand dataclass --------------------------------------------------


class TestTargetCommand:
    def test_create_with_args(self) -> None:
        # Act
        tc = TargetCommand(name="AT+LED", help="Control LED", args="<on|off>")
        # Assert
        assert tc.name == "AT+LED", "name preserved"
        assert tc.help == "Control LED", "help preserved"
        assert tc.args == "<on|off>", "args preserved"

    def test_create_without_args(self) -> None:
        # Act
        tc = TargetCommand(name="AT", help="Connection test")
        # Assert
        assert tc.args == "", "args defaults to empty"

    def test_defaults_long_help_and_flags(self) -> None:
        """New optional fields default to empty so old-shape callers work."""
        # Act
        tc = TargetCommand(name="X", help="x")
        # Assert
        assert tc.long_help == "", "long_help defaults empty"
        assert tc.flags == {}, "flags default to empty dict"

    def test_stores_long_help_and_flags(self) -> None:
        """Caller-supplied long_help and flags survive construction."""
        # Arrange / Act
        tc = TargetCommand(
            name="AT+LED",
            help="Control LED",
            args="<on|off>",
            long_help="multi-line\nprose here",
            flags={"--blink": "blink mode", "-b": "--blink"},
        )
        # Assert
        assert tc.long_help == "multi-line\nprose here", "long_help preserved"
        assert tc.flags["--blink"] == "blink mode", "canonical flag preserved"
        assert tc.flags["-b"] == "--blink", "alias preserved"


# -- ReplEngine target command storage ----------------------------------------


class TestTargetCommandStorage:
    def test_initially_empty(self, engine) -> None:
        # Arrange
        eng, _ = engine
        # Assert
        assert eng.ctx.ns("target_commands") == {}, "starts empty"

    def test_set_target_commands(self, engine) -> None:
        # Arrange
        eng, _ = engine
        commands = {
            "AT": TargetCommand(name="AT", help="Connection test"),
            "AT+INFO": TargetCommand(name="AT+INFO", help="Device info"),
        }
        # Act
        target = eng.ctx.ns("target_commands")
        target.clear()
        target.update(commands)
        # Assert
        assert len(target) == 2, "both stored"
        assert "AT" in target, "AT present"
        assert "AT+INFO" in target, "AT+INFO present"

    def test_set_replaces_previous(self, engine) -> None:
        # Arrange
        eng, _ = engine
        target = eng.ctx.ns("target_commands")
        target.clear()
        target.update({
            "OLD": TargetCommand(name="OLD", help="old cmd"),
        })
        # Act
        target.clear()
        target.update({
            "NEW": TargetCommand(name="NEW", help="new cmd"),
        })
        # Assert
        assert "OLD" not in target, "old entry removed"
        assert "NEW" in target, "new entry present"

    def test_clear_target_commands(self, engine) -> None:
        # Arrange
        eng, _ = engine
        target = eng.ctx.ns("target_commands")
        target.update({
            "AT": TargetCommand(name="AT", help="Connection test"),
        })
        # Act
        target.clear()
        # Assert
        assert target == {}, "cleared"


# -- JSON parsing (include helpers) -------------------------------------------


class TestReadJsonParsing:
    """Test the JSON parsing logic from include._read_json indirectly."""

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
        assert len(commands) >= 10, "built from all entries"
        assert commands["AT+LED"].args.startswith("<on|off>"), "args preserved"

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
        assert len(commands) == 1, "only valid entry kept"
        assert "good" in commands, "valid entry present"
        assert "bad" not in commands, "missing help skipped"
        assert "also_bad" not in commands, "non-dict skipped"

    def test_json_with_preamble(self) -> None:
        """JSON extraction should work even with preamble text."""
        # Arrange
        raw = 'Some preamble text\r\n{"AT": {"help": "test", "args": ""}}\r\n'
        start = raw.find("{")
        # Act
        data = json.loads(raw[start:])
        # Assert
        assert "AT" in data, "found JSON despite preamble"


# -- _build_commands / _to_json_dict round-trip ------------------------------


class TestBuildCommandsExtraFields:
    """Coverage for the new optional long_help + flags keys in JSON."""

    def test_build_reads_long_help_and_flags(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _build_commands
        cmd_dict = {
            "AT+LED": {
                "help": "LED",
                "args": "<on|off>",
                "long_help": "Drive the LED line.",
                "flags": {"--blink": "blink mode", "-b": "--blink"},
            }
        }

        # Act
        actual = _build_commands(cmd_dict)

        # Assert
        tc = actual["AT+LED"]
        assert tc.long_help == "Drive the LED line.", "long_help read"
        assert tc.flags == {"--blink": "blink mode", "-b": "--blink"}, \
            "flags dict read verbatim"

    def test_build_ignores_non_string_long_help(self) -> None:
        """A device emitting a non-string long_help shouldn't break include."""
        # Arrange
        from termapy.builtins.plugins.include import _build_commands
        cmd_dict = {"X": {"help": "h", "long_help": {"oops": "object"}}}

        # Act
        actual = _build_commands(cmd_dict)

        # Assert
        assert actual["X"].long_help == "", "non-string long_help dropped"

    def test_build_ignores_malformed_flags(self) -> None:
        """Non-dict or non-string values in flags are dropped, not fatal."""
        # Arrange
        from termapy.builtins.plugins.include import _build_commands
        cmd_dict = {
            "A": {"help": "h", "flags": ["not", "a", "dict"]},
            "B": {"help": "h", "flags": {"--ok": "good", "--bad": 123}},
        }

        # Act
        actual = _build_commands(cmd_dict)

        # Assert
        assert actual["A"].flags == {}, "non-dict flags dropped"
        assert actual["B"].flags == {"--ok": "good"}, \
            "only string-valued entries kept"

    def test_roundtrip_preserves_full_entry(self) -> None:
        """JSON -> TargetCommand -> JSON preserves every populated field."""
        # Arrange
        from termapy.builtins.plugins.include import _build_commands, _to_json_dict
        original = {
            "X": {
                "help": "h",
                "args": "<a>",
                "long_help": "body",
                "flags": {"--verbose": "talk more"},
            }
        }

        # Act
        commands = _build_commands(original)
        actual = _to_json_dict(commands)

        # Assert
        assert actual == {"commands": original}, "round-trip is byte-identical"

    def test_roundtrip_old_shape_adds_no_fields(self) -> None:
        """An entry with only help + args round-trips without spurious keys."""
        # Arrange
        from termapy.builtins.plugins.include import _build_commands, _to_json_dict
        original = {"AT": {"help": "Connection test", "args": ""}}

        # Act
        commands = _build_commands(original)
        actual = _to_json_dict(commands)

        # Assert -- no "long_help": "" or "flags": {} sneaking in.
        assert actual == {"commands": original}, \
            "old-shape JSON survives round-trip unchanged"


# -- version comparator (_is_newer) ------------------------------------------


class TestIsNewer:
    """``_is_newer`` tells the fetch path whether to overwrite the cache."""

    def test_new_none_is_never_newer(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _is_newer
        # Act / Assert
        assert _is_newer(None, "1.0.0") is False, \
            "missing new version never wins"
        assert _is_newer(None, None) is False, \
            "missing on both sides is not newer"

    def test_cached_none_makes_new_win(self) -> None:
        """When the cache has no recorded version, any new version is newer."""
        # Arrange
        from termapy.builtins.plugins.include import _is_newer
        # Act / Assert -- first time a device starts publishing version
        assert _is_newer("1.0.0", None) is True, \
            "new wins when cache has no version"

    def test_semver_ordering(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _is_newer
        # Act / Assert
        assert _is_newer("1.4.0", "1.3.9") is True, \
            "patch bump is newer"
        assert _is_newer("2.0.0", "1.99.99") is True, \
            "major bump is newer"
        assert _is_newer("1.10.0", "1.9.0") is True, \
            "ten > nine numerically (not lexically)"

    def test_equal_is_not_newer(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _is_newer
        # Act / Assert
        assert _is_newer("1.4.0", "1.4.0") is False, \
            "equal versions do not overwrite"

    def test_older_is_not_newer(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _is_newer
        # Act / Assert
        assert _is_newer("1.0.0", "1.4.0") is False, \
            "downgrades do not overwrite"

    def test_unparseable_falls_back_to_inequality(self) -> None:
        """Non-PEP-440 strings (e.g. git hashes) compare by equality."""
        # Arrange
        from termapy.builtins.plugins.include import _is_newer
        # Act / Assert
        assert _is_newer("a3f2c91", "b9d40aa") is True, \
            "different hash strings treated as newer"
        assert _is_newer("a3f2c91", "a3f2c91") is False, \
            "identical hash strings are not newer"


# -- version round-trip through JSON -----------------------------------------


class TestVersionRoundTrip:
    """The optional top-level ``version`` field survives save+load."""

    def test_to_json_dict_omits_version_when_absent(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _build_commands, _to_json_dict
        original = {"AT": {"help": "h", "args": ""}}

        # Act
        actual = _to_json_dict(_build_commands(original))

        # Assert
        assert "version" not in actual, \
            "no version field without explicit opt-in"

    def test_to_json_dict_emits_version_when_given(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _build_commands, _to_json_dict
        commands = _build_commands({"AT": {"help": "h"}})

        # Act
        actual = _to_json_dict(commands, version="2.1.0")

        # Assert
        assert actual["version"] == "2.1.0", "version written at top level"
        assert "commands" in actual, "commands still present"

    def test_extract_version_handles_missing_or_malformed(self) -> None:
        # Arrange
        from termapy.builtins.plugins.include import _extract_version
        # Act / Assert
        assert _extract_version({}) is None, \
            "absent version returns None"
        assert _extract_version({"version": ""}) is None, \
            "empty string version returns None"
        assert _extract_version({"version": 42}) is None, \
            "non-string version returns None"
        assert _extract_version({"version": "1.0.0"}) == "1.0.0", \
            "string version returns verbatim"


# -- auto-include version gate -----------------------------------------------


class TestIncludeVersionGate:
    """End-to-end: the version field actually controls overwrites."""

    def _write_cache(self, tmp_path, payload: dict) -> None:
        """Seed .target_menu.json with the given JSON payload."""
        cache = tmp_path / "test" / ".target_menu.json"
        cache.write_text(json.dumps(payload), encoding="utf-8")

    def _run_fetch(self, engine, device_json: dict, *, force: bool = False):
        """Mock _read_json and call _fetch_and_include, return (result, output)."""
        from termapy.builtins.plugins import include
        eng, output = engine
        original = include._read_json
        include._read_json = lambda ctx, tms: device_json  # ty: ignore[invalid-assignment]
        try:
            # Pretend we're connected so the gate evaluates.
            eng.ctx.serial.is_connected = lambda: True
            eng.ctx.serial.io = lambda: _NullContext()
            eng.ctx.serial.drain = lambda: 0
            eng.ctx.serial.send = lambda text: None
            output.clear()
            result = include._fetch_and_include(
                eng.ctx, "AT+HELP.JSON", 100, force=force,
            )
        finally:
            include._read_json = original
        return result, output

    def test_newer_fetch_overwrites(self, engine, tmp_path) -> None:
        # Arrange -- cache at 1.0.0 with one entry
        eng, _ = engine
        cfg_dir = tmp_path / "test"
        self._write_cache(tmp_path, {
            "version": "1.0.0",
            "commands": {"OLD": {"help": "old"}},
        })
        device_json = {
            "version": "1.1.0",
            "commands": {"NEW": {"help": "new"}},
        }

        # Act
        result, output = self._run_fetch(engine, device_json)

        # Assert -- NEW replaces OLD, cache on disk carries the new version
        assert result.success, "fetch succeeded"
        target = eng.ctx.ns("target_commands")
        assert "NEW" in target and "OLD" not in target, \
            "newer version wins"
        cached = json.loads(
            (cfg_dir / ".target_menu.json").read_text(encoding="utf-8")
        )
        assert cached["version"] == "1.1.0", "cache updated to new version"

    def test_older_fetch_keeps_cache(self, engine, tmp_path) -> None:
        # Arrange -- cache at 1.1.0, device downgrades to 1.0.0
        eng, _ = engine
        cfg_dir = tmp_path / "test"
        self._write_cache(tmp_path, {
            "version": "1.1.0",
            "commands": {"KEEPME": {"help": "stay"}},
        })
        device_json = {
            "version": "1.0.0",
            "commands": {"OVERWRITE": {"help": "would-be"}},
        }

        # Act
        result, output = self._run_fetch(engine, device_json)

        # Assert -- cached entry survives, disk not rewritten
        assert result.success, "fetch still reports success"
        target = eng.ctx.ns("target_commands")
        assert "KEEPME" in target, "cached entry loaded"
        assert "OVERWRITE" not in target, "older fetch did not overwrite"
        cached = json.loads(
            (cfg_dir / ".target_menu.json").read_text(encoding="utf-8")
        )
        assert cached["version"] == "1.1.0", "cache version unchanged"

    def test_equal_fetch_keeps_cache(self, engine, tmp_path) -> None:
        # Arrange -- both sides at 1.0.0
        eng, _ = engine
        self._write_cache(tmp_path, {
            "version": "1.0.0",
            "commands": {"CACHED": {"help": "c"}},
        })
        device_json = {
            "version": "1.0.0",
            "commands": {"REFRESHED": {"help": "r"}},
        }

        # Act
        result, output = self._run_fetch(engine, device_json)

        # Assert
        assert result.success, "ok"
        target = eng.ctx.ns("target_commands")
        assert "CACHED" in target and "REFRESHED" not in target, \
            "equal version keeps cache"

    def test_missing_new_version_keeps_cache(self, engine, tmp_path) -> None:
        """A device that stopped publishing a version can't overwrite a versioned cache."""
        # Arrange
        eng, _ = engine
        self._write_cache(tmp_path, {
            "version": "1.0.0",
            "commands": {"CACHED": {"help": "c"}},
        })
        device_json = {
            "commands": {"NEW": {"help": "n"}},
        }

        # Act
        result, output = self._run_fetch(engine, device_json)

        # Assert
        assert result.success, "ok"
        target = eng.ctx.ns("target_commands")
        assert "CACHED" in target, "unversioned fetch can't override versioned cache"
        assert "NEW" not in target, "unversioned fetch ignored"

    def test_no_cache_first_fetch_wins(self, engine) -> None:
        """With no cache on disk, a fresh fetch always loads regardless of version."""
        # Arrange -- no cache written
        eng, _ = engine
        device_json = {"commands": {"FIRST": {"help": "f"}}}

        # Act
        result, output = self._run_fetch(engine, device_json)

        # Assert
        assert result.success, "ok"
        assert "FIRST" in eng.ctx.ns("target_commands"), \
            "first-time fetch always applies"

    def test_reload_force_ignores_gate(self, engine, tmp_path) -> None:
        """/include.reload passes force=True and overwrites even an older fetch."""
        # Arrange -- cache at 1.1.0, device at 1.0.0
        eng, _ = engine
        self._write_cache(tmp_path, {
            "version": "1.1.0",
            "commands": {"CACHED": {"help": "c"}},
        })
        device_json = {
            "version": "1.0.0",
            "commands": {"FORCED": {"help": "f"}},
        }

        # Act -- force=True simulates /include.reload
        result, output = self._run_fetch(engine, device_json, force=True)

        # Assert
        assert result.success, "ok"
        target = eng.ctx.ns("target_commands")
        assert "FORCED" in target, \
            "force overrides the version gate"
        assert "CACHED" not in target, \
            "forced fetch replaces cache content"


class _NullContext:
    """Tiny stand-in for ctx.serial.io()'s context manager in tests."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


# -- /help.target subcommand --------------------------------------------------


class TestHelpTarget:
    """Tests for the /help.target subcommand."""

    def test_no_target_commands(self, engine) -> None:
        """Shows message when no commands included."""
        # Arrange
        eng, output = engine
        # Act
        eng.dispatch("help.target")
        # Assert
        messages = [t for t, _ in output]
        assert any("No target" in m for m in messages), "says no commands"

    def test_lists_included_commands(self, engine) -> None:
        """Lists target commands after include."""
        # Arrange
        eng, output = engine
        eng.ctx.ns("target_commands").update({
            "AT": TargetCommand(name="AT", help="Connection test"),
            "AT+INFO": TargetCommand(name="AT+INFO", help="Device info"),
        })
        # Act
        output.clear()
        eng.dispatch("help.target")
        # Assert
        messages = " ".join(t for t, _ in output)
        assert "AT" in messages, "AT listed"
        assert "AT+INFO" in messages, "AT+INFO listed"
        assert "Target Device" in messages, "section header shown"

    def test_shows_args(self, engine) -> None:
        """Target commands with args display them."""
        # Arrange
        eng, output = engine
        eng.ctx.ns("target_commands").update({
            "AT+LED": TargetCommand(name="AT+LED", help="Control LED", args="<on|off>"),
        })
        # Act
        output.clear()
        eng.dispatch("help.target")
        # Assert
        messages = " ".join(t for t, _ in output)
        assert "on|off" in messages, "args shown"

    def test_reports_count(self, engine) -> None:
        """Reports total count of target commands."""
        # Arrange
        eng, output = engine
        eng.ctx.ns("target_commands").update({
            "AT": TargetCommand(name="AT", help="test"),
            "AT+INFO": TargetCommand(name="AT+INFO", help="test"),
            "AT+TEMP": TargetCommand(name="AT+TEMP", help="test"),
        })
        # Act
        output.clear()
        eng.dispatch("help.target")
        # Assert
        messages = " ".join(t for t, _ in output)
        assert "3 device commands" in messages, "count reported"


# -- Config key ---------------------------------------------------------------


class TestTargetHelpCmdConfig:
    def test_config_has_key(self, engine) -> None:
        # Arrange
        eng, _ = engine
        # Assert
        expected = "AT+HELP.JSON"
        actual = eng.cfg.get("device_json_cmd", "")
        assert actual == expected, "config key present"

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
        assert cfg["device_json_cmd"] == "AT+HELP.JSON", "demo configured"

    def test_default_cfg_has_key(self) -> None:
        """DEFAULT_CFG should include device_json_cmd."""
        # Arrange
        from termapy.defaults import DEFAULT_CFG
        # Assert
        assert "device_json_cmd" in DEFAULT_CFG, "key in defaults"
        assert DEFAULT_CFG["device_json_cmd"] == "", "default is empty"


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
        assert eng.cmd("include") == "/include", "default prefix"
        assert eng.cmd("help") == "/help", "default prefix"

    def test_cmd_helper_uses_custom_prefix(self, tmp_path) -> None:
        # Arrange
        cfg = {"cmd_prefix": "!"}
        config_path = tmp_path / "t" / "t.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None, prefix="!")
        # Act / Assert
        assert eng.cmd("include") == "!include", "custom prefix"
        assert eng.cmd("help") == "!help", "custom prefix"

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
        assert result.success, "dispatch works with custom prefix"


# -- /help <target> man-page parity ------------------------------------------


class TestHelpTargetManPage:
    """/help <target> renders DESCRIPTION + FLAGS when provided."""

    def _seed(self, eng, tc: TargetCommand) -> None:
        target = eng.ctx.ns("target_commands")
        target.clear()
        target[tc.name] = tc

    def test_renders_description_from_long_help(self, engine) -> None:
        # Arrange
        eng, output = engine
        self._seed(eng, TargetCommand(
            name="AT+INFO",
            help="Device information",
            long_help="Multi-line\nprose body.",
        ))

        # Act
        output.clear()
        eng.dispatch("help AT+INFO")

        # Assert
        texts = [t for t, _ in output]
        assert any("DESCRIPTION" in t for t in texts), \
            "DESCRIPTION section header appears"
        assert any("Multi-line" in t for t in texts), \
            "long_help body rendered"

    def test_renders_flags_section(self, engine) -> None:
        # Arrange
        eng, output = engine
        self._seed(eng, TargetCommand(
            name="AT+LED",
            help="Control LED",
            args="<on|off>",
            flags={"--blink": "blink at 2 Hz", "-b": "--blink"},
        ))

        # Act
        output.clear()
        eng.dispatch("help AT+LED")

        # Assert
        texts = [t for t, _ in output]
        assert any("FLAGS" in t for t in texts), \
            "FLAGS section header appears"
        assert any("--blink" in t and "blink at 2 Hz" in t for t in texts), \
            "canonical flag + description rendered"
        assert any("-b" in t for t in texts), \
            "alias collapses onto canonical line"

    def test_omits_description_when_no_long_help(self, engine) -> None:
        """An old-shape target command doesn't emit an empty DESCRIPTION section."""
        # Arrange
        eng, output = engine
        self._seed(eng, TargetCommand(name="AT", help="Connection test"))

        # Act
        output.clear()
        eng.dispatch("help AT")

        # Assert
        texts = [t for t, _ in output]
        assert not any("DESCRIPTION" in t for t in texts), \
            "no DESCRIPTION when long_help empty"
        assert not any("FLAGS" in t for t in texts), \
            "no FLAGS when flags empty"

    def test_source_marker_present(self, engine) -> None:
        """Every target-command man page carries 'source: target device'."""
        # Arrange
        eng, output = engine
        self._seed(eng, TargetCommand(name="AT+X", help="x"))

        # Act
        output.clear()
        eng.dispatch("help AT+X")

        # Assert
        texts = [t for t, _ in output]
        assert any("source: target device" in t for t in texts), \
            "source annotation rendered"

    def test_case_sensitive_lookup_preserves_upper(self, engine) -> None:
        """/help preserves argument case so AT+ commands match exactly."""
        # Arrange
        eng, output = engine
        self._seed(eng, TargetCommand(
            name="AT+INFO",
            help="Device information",
            long_help="DIST-INFO-MARKER",
        ))

        # Act -- typed exactly as the device emits it
        output.clear()
        eng.dispatch("help AT+INFO")

        # Assert -- DESCRIPTION landed, no "No command matches" error
        texts = [t for t, _ in output]
        assert any("DIST-INFO-MARKER" in t for t in texts), \
            "exact-case device name is found (not lowercased)"
        assert not any("No command matches" in t for t in texts), \
            "case-preserved input is not forwarded to candidate fallback"


# -- /search indexes target_commands -----------------------------------------


class TestSearchIndexesTargets:
    """Target commands appear in /search results alongside REPL plugins."""

    def test_match_in_target_long_help_found(self, engine) -> None:
        """A probe token that only lives in a target's long_help surfaces via /search."""
        # Arrange
        eng, output = engine
        eng.ctx.ns("target_commands").update({
            "AT+WIDGET": TargetCommand(
                name="AT+WIDGET",
                help="widget ops",
                long_help="Handles the __search_probe__ calibration sequence.",
            ),
        })

        # Act
        result = eng.dispatch("search __search_probe__")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "AT+WIDGET" in names, \
            "target command found via long_help text"

    def test_match_in_target_flag_description_found(self, engine) -> None:
        """A term living only in a target's flag description is still findable."""
        # Arrange
        eng, output = engine
        eng.ctx.ns("target_commands").update({
            "AT+SAMPLE": TargetCommand(
                name="AT+SAMPLE",
                help="sample sensor",
                flags={"--rapid": "__search_probe__ fast polling mode"},
            ),
        })

        # Act
        result = eng.dispatch("search __search_probe__")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "AT+SAMPLE" in names, \
            "flag descriptions indexed for target commands"

    def test_target_results_tagged(self, engine) -> None:
        """Rendered target hits carry the (target) marker."""
        # Arrange
        eng, output = engine
        eng.ctx.ns("target_commands").update({
            "AT+MARKER": TargetCommand(
                name="AT+MARKER",
                help="__search_probe__ only in target",
            ),
        })

        # Act
        output.clear()
        eng.dispatch("search __search_probe__")

        # Assert
        texts = [t for t, _ in output]
        assert any("(target)" in t for t in texts), \
            "search output marks target-device hits"

    def test_plugin_wins_on_name_collision(self, engine) -> None:
        """When a plugin and target share a name, the plugin view is used."""
        # Arrange
        eng, output = engine
        # 'help' is a real built-in plugin; try to shadow it.
        eng.ctx.ns("target_commands").update({
            "help": TargetCommand(name="help", help="device help (shadow)"),
        })

        # Act
        output.clear()
        eng.dispatch("search help")

        # Assert -- rendered header should not carry (target) for 'help'
        texts = [t for t, _ in output]
        assert not any(
            "help" in t and "(target)" in t for t in texts
        ), "plugin takes precedence over target on collision"


# -- /include -> active_profile mirroring (v2 unification) -------------------


class TestIncludeAndProfileSeparation:
    """``/include`` and ``active_profile`` are independent namespaces.

    Old behavior (removed): ``/include`` mirrored v2 manifests into
    ``active_profile`` so the MCP executor saw device-published
    response schemas without an explicit ``/profile.load``.  That
    convenience conflated display data (target_commands) with
    execution data (active_profile) and surfaced as a real bug when
    a port switch left stale target_commands visible alongside a
    freshly-loaded profile.

    New contract: ``/include`` populates ``target_commands`` only.
    ``active_profile`` is owned exclusively by ``/profile.load`` and
    by MCP's auto-load-on-connect.  These tests pin that boundary so
    a future "convenience" PR can't quietly re-introduce the leak.
    """

    def _run_fetch(self, engine, device_json: dict, *, force: bool = False):
        """Mock _read_json and call _fetch_and_include, return result."""
        from termapy.builtins.plugins import include
        eng, output = engine
        original = include._read_json
        include._read_json = lambda ctx, tms: device_json  # ty: ignore[invalid-assignment]
        try:
            eng.ctx.serial.is_connected = lambda: True
            eng.ctx.serial.io = lambda: _NullContext()
            eng.ctx.serial.drain = lambda: 0
            eng.ctx.serial.send = lambda text: None
            output.clear()
            result = include._fetch_and_include(
                eng.ctx, "AT+HELP.JSON", 100, force=force,
            )
        finally:
            include._read_json = original
        return result

    def test_v2_manifest_does_not_touch_active_profile(self, engine) -> None:
        # Arrange — even a fully-fledged v2 manifest with response
        # schemas, transport, and error_detection must NOT leak into
        # active_profile.  Display data and execution data stay apart.
        eng, _ = engine
        device_json = {
            "version": "2.0.0",
            "transport": {"line_ending_send": "\r\n"},
            "error_detection": {"pattern": "^ERROR.*$"},
            "commands": {
                "AT+TEMP": {
                    "help": "Read temperature",
                    "response": {
                        "format": "regex",
                        "pattern": r"(?P<celsius>-?\d+\.\d+)C",
                        "types": {"celsius": "float"},
                    },
                },
            },
        }
        # Act
        result = self._run_fetch(engine, device_json)
        # Assert
        assert result.success, "include succeeded"
        actual_target = eng.ctx.ns("target_commands")
        assert "AT+TEMP" in actual_target, (
            "target_commands populated for /help display"
        )
        actual_profile = eng.ctx.ns("active_profile")
        expected_profile: dict = {}
        assert actual_profile == expected_profile, (
            "active_profile untouched -- /include never writes it"
        )

    def test_v1_manifest_does_not_touch_active_profile(self, engine) -> None:
        # Arrange — pure v1 (help/args only).  Same contract: no leak.
        eng, _ = engine
        device_json = {
            "version": "1.0.0",
            "commands": {
                "FOO": {"help": "old-style command", "args": "<x>"},
            },
        }
        # Act
        result = self._run_fetch(engine, device_json)
        # Assert
        assert result.success, "v1 include still succeeds"
        actual_target = eng.ctx.ns("target_commands")
        assert "FOO" in actual_target, "target_commands populated as before"
        actual_profile = eng.ctx.ns("active_profile")
        expected_profile: dict = {}
        assert actual_profile == expected_profile, (
            "v1 manifest does NOT touch active_profile"
        )

    def test_clear_does_not_touch_active_profile(self, engine) -> None:
        # Arrange — independently set both namespaces, simulating a
        # session where the user ran /include and /profile.load.
        eng, _ = engine
        eng.ctx.ns("target_commands").update({
            "AT": TargetCommand(name="AT", help="from include"),
        })
        profile_ns = eng.ctx.ns("active_profile")
        profile_ns.update({
            "commands": {"AT": {"help": "from profile"}},
            "__source_path": "/some/file.profile.json",
        })
        # Act
        eng.dispatch("include.clear")
        # Assert -- /include.clear scopes itself to /include's data
        actual_target = eng.ctx.ns("target_commands")
        expected_target: dict = {}
        assert actual_target == expected_target, "target_commands cleared"
        actual_profile = eng.ctx.ns("active_profile")
        assert actual_profile.get("__source_path") == "/some/file.profile.json", (
            "active_profile survived /include.clear (separate namespace)"
        )
        assert "AT" in actual_profile.get("commands", {}), (
            "active_profile commands survived /include.clear"
        )
