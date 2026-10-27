"""Dev-board starter projects: the ``/board`` table, finding a board, its config, its firmware.

``termapy.boards.BOARDS`` is one row per board with a folder of package
data beside it.  These tests pin that every row's files exist and agree
with each other (the device code's commands are the profile's), that a
board is found by its VID:PID on the serial-port list and, failing that,
attached without its runtime on the USB tree, that its config is a fresh
``new_config`` connecting by the board's serial number, and that the
commands and ``--board`` reach all of it.  Port lists and USB trees are
injected (``source=`` / ``usb_source=``), so no test needs the board.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from termapy.boards import (
    BOARDS,
    AlreadyConfigured,
    attached,
    board_config,
    configs_for_serial,
    copy_firmware,
    create_board_config,
    find_board,
    firmware_paths,
    install_command,
)
from termapy.config import ConfigExistsError
from termapy.defaults import DEFAULT_CFG, default_cfg
from termapy.folders import PROFILE_SUFFIX
from termapy.plugins import CapabilitySet
from termapy.port_control import ChipFacts
from termapy.profile import TypeRegistry, load_profile, match_profile_command, validate_profile
from termapy.repl import ReplEngine
from termapy.usb_tree import UsbRecord

PICO = find_board("pico")
BOARDS_DIR = Path(__file__).parent.parent / "src" / "termapy" / "boards"

# A Pico running the starter, as the port list reports it.
PICO_PORT = ChipFacts(device="COM5", serial="E66548545710822C", vid_pid="2E8A:0005")
# An FTDI adapter, a port that is not a Pico.
FTDI_PORT = ChipFacts(device="COM7", serial="BG03U7VTA", vid_pid="0403:6001")

ESP32S3 = find_board("esp32s3")
# The bench ESP32-S3 running MicroPython, on the chip's own USB: the serial
# number is its factory MAC in hex and MicroPython's two unfilled bytes.
ESP32S3_PORT = ChipFacts(device="COM9", serial="58E6C5716A840000", vid_pid="303A:4001")
# Its USB-to-UART bridge, a CH340K, which has no serial number.
CH340K_PORT = ChipFacts(device="COM6", vid_pid="1A86:7522")


def ports(*facts: ChipFacts):
    """A port source listing exactly ``facts``."""
    return lambda: list(facts)


def usb(*vid_pids: str):
    """A USB source with one device per VID:PID, no serial ports."""
    return lambda: [
        UsbRecord(path=f"1-{index}", vid_pid=vid_pid) for index, vid_pid in enumerate(vid_pids)
    ]


# Every form each board's device code takes, and the profile entry that governs it.
BOARD_FORMS = {
    "pico": {
        "help": "help",
        "help led": "help",
        "json": "json",
        "json on": "json",
        "led": "led",
        "led off": "led",
        "led 1": "led",
        "temp": "temp",
        "id": "id",
    },
    "esp32s3": {
        "help": "help",
        "help led_rgb": "help",
        "json": "json",
        "json off": "json",
        "led": "led",
        "led on": "led",
        "led 0": "led",
        "led_rgb": "led_rgb",
        "led_rgb 255 0 0x20": "led_rgb",
        "temp": "temp",
        "id": "id",
    },
}


class TestTable:
    def test_every_board_ships_its_files(self):
        """Each row has its config, profile and firmware in the package: a row is never a promise."""
        for board in BOARDS:
            folder = BOARDS_DIR / board.name
            assert (folder / "config.json").is_file(), f"{board.name}: config.json"
            assert (folder / "profile.json").is_file(), f"{board.name}: profile.json"
            for path in firmware_paths(board):
                assert path.is_file(), f"{board.name}: firmware {path.name}"

    def test_config_overrides_are_known_keys(self):
        """A board's config.json only sets keys a config has, so a misspelled key fails here."""
        for board in BOARDS:
            overrides = json.loads((BOARDS_DIR / board.name / "config.json").read_text("utf-8"))
            unknown = sorted(set(overrides) - set(DEFAULT_CFG))
            assert unknown == [], f"{board.name}: not config keys: {unknown}"

    def test_profile_validates(self):
        """Each board's profile passes the profile schema, as /profile.validate checks it."""
        for board in BOARDS:
            profile = load_profile(BOARDS_DIR / board.name / "profile.json")
            result = validate_profile(profile)
            assert result.ok, f"{board.name}: {result.errors}"
            assert result.warnings == [], f"{board.name}: {result.warnings}"

    def test_profile_describes_the_firmware_commands(self):
        """board-profile-complete: the profile names every command the board answers, its table's and the monitor's."""
        monitor_commands = set(BOARD_JSON.Monitor({}, print).builtins)
        for board in BOARDS:
            main = firmware_paths(board)[0]
            tree = ast.parse(main.read_text(encoding="utf-8"))
            table = next(
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "COMMANDS" for t in node.targets)
            )
            assert isinstance(table, ast.Dict), "COMMANDS is a dict literal"
            firmware = {key.value for key in table.keys if isinstance(key, ast.Constant)}
            expected = firmware | monitor_commands
            actual = set(load_profile(BOARDS_DIR / board.name / "profile.json")["commands"])
            assert actual == expected, f"{board.name}: profile and firmware disagree"

    def test_profile_governs_every_form(self):
        """board-profile-complete: every form a board takes reaches its profile entry, and the entry's types take it."""
        for board in BOARDS:
            profile = load_profile(BOARDS_DIR / board.name / "profile.json")
            registry = TypeRegistry.from_profile(profile)
            forms = BOARD_FORMS[board.name]
            assert set(forms.values()) == set(profile["commands"]), (
                f"{board.name}: every profile command has a form here"
            )
            for line, expected in forms.items():
                match = match_profile_command(line, profile["commands"])
                assert match is not None, f"{board.name}: no profile entry takes {line!r}"
                name, spec, bound = match
                assert name == expected, f"{board.name}: {line!r} reached the wrong entry"
                for typed_arg in spec.get("typed_args", []):
                    if typed_arg["name"] in bound:
                        outcome = registry.validate(typed_arg["type"], bound[typed_arg["name"]])
                        assert outcome.ok, f"{board.name}: {line!r} refused: {outcome.error}"

    def test_runtime_code_ships_once(self):
        """Every MicroPython board gets the runtime's one termapy_json.py; no board folder holds a copy."""
        shared = {
            path
            for board in BOARDS
            for path in firmware_paths(board)
            if path.name in board.runtime.files
        }
        assert len(shared) == 1, f"one shared file, not one per board: {shared}"
        for board in BOARDS:
            for name in board.runtime.files:
                copy = BOARDS_DIR / board.name / "firmware" / name
                assert not copy.exists(), f"{board.name} carries its own {name}"

    def test_find_board_rejects_an_unknown_name(self):
        """An unknown board is a lookup failure that lists the boards."""
        with pytest.raises(ValueError, match=r"Unknown board: nosuch \(use esp32s3, pico\)"):
            find_board("nosuch")


class TestAttached:
    def test_a_running_board_is_found_by_vid_pid(self):
        """The Pico's port is picked out of the list by VID:PID, with its serial number."""
        found = attached(PICO, source=ports(FTDI_PORT, PICO_PORT), usb_source=usb())
        assert found.running == (("COM5", "E66548545710822C"),)
        assert found.without_runtime is False, "a running board is not looked for without it"

    def test_a_board_in_its_boot_loader_is_seen_on_the_usb_tree(self):
        """No running board, but its boot loader on the USB tree: attached, without its runtime."""
        found = attached(PICO, source=ports(FTDI_PORT), usb_source=usb("2E8A:0003"))
        assert found.running == ()
        assert found.without_runtime is True

    def test_nothing_attached(self):
        """Neither on the port list nor on the USB tree: not attached."""
        found = attached(PICO, source=ports(FTDI_PORT), usb_source=usb("0403:6001"))
        assert (found.running, found.without_runtime) == ((), False)


class TestEsp32s3:
    """The second board: found on the chip's own USB, never through its USB-to-UART bridge."""

    def test_found_on_its_own_usb_by_serial_number(self):
        """MicroPython's port is picked out by 303A:4001 with its serial number; the CH340K is not claimed."""
        found = attached(
            ESP32S3, source=ports(CH340K_PORT, FTDI_PORT, ESP32S3_PORT), usb_source=usb()
        )
        assert found.running == (("COM9", "58E6C5716A840000"),)

    def test_without_micropython_it_is_told_to_flash(self, tmp_path):
        """The chip's USB Serial/JTAG (303A:1001) means attached without MicroPython: flash it, make nothing."""
        with pytest.raises(LookupError) as caught:
            create_board_config(
                ESP32S3, tmp_path, source=ports(CH340K_PORT), usb_source=usb("303A:1001")
            )
        message = str(caught.value)
        assert message.startswith(
            "ESP32-S3 DevKitC-1 is attached but not running MicroPython. Flash MicroPython"
        ), message
        assert "SPIRAM_OCT" in message, "the octal-PSRAM build is named"
        assert not (tmp_path / "esp32s3").exists(), "no half-made config"

    def test_config_connects_by_serial_number(self, tmp_path):
        """The config's port is MicroPython's serial number, and its folder holds both device files."""
        # Act
        created = create_board_config(ESP32S3, tmp_path, source=ports(CH340K_PORT, ESP32S3_PORT))

        # Assert
        data = json.loads(created.path.read_text(encoding="utf-8"))
        assert data["serial"]["port"] == "58E6C5716A840000", "by serial number, not COM9"
        assert data["title"] == "ESP32-S3 DevKitC-1"
        assert [path.name for path in created.firmware] == ["main.py", "termapy_json.py"]


class TestCreateConfig:
    def test_config_connects_by_the_board_serial_number(self, tmp_path):
        """The config is a fresh new_config with the board's settings, its port the board's serial number."""
        # Act
        created = create_board_config(PICO, tmp_path, source=ports(PICO_PORT))

        # Assert
        data = json.loads(created.path.read_text(encoding="utf-8"))
        assert created.path == tmp_path / "pico" / "pico.cfg"
        assert (created.port, created.device, created.others) == ("E66548545710822C", "COM5", 0)
        assert data["serial"]["port"] == "E66548545710822C", "found by serial number, not COM5"
        assert data["auto_connect"] is True, "the config connects when it loads"
        assert data["title"] == "Raspberry Pi Pico", "the board's settings are over the defaults"
        assert data["config_version"] == DEFAULT_CFG["config_version"], "a current config"
        profile = created.path.parent / f"pico{PROFILE_SUFFIX}"
        assert profile.read_bytes() == (BOARDS_DIR / "pico" / "profile.json").read_bytes()

    def test_a_named_config_gets_its_own_profile_name(self, tmp_path):
        """With a name, the config and its profile both carry it, so the profile loads with it."""
        created = create_board_config(PICO, tmp_path, "bench", source=ports(PICO_PORT))
        assert created.path == tmp_path / "bench" / "bench.cfg"
        assert (tmp_path / "bench" / f"bench{PROFILE_SUFFIX}").is_file()

    def test_the_config_folder_holds_the_board_code(self, tmp_path):
        """Everything for the board lives in its config's folder: the device code lands in firmware/."""
        created = create_board_config(PICO, tmp_path, source=ports(PICO_PORT))
        folder = tmp_path / "pico" / "firmware"
        assert [path.name for path in created.firmware] == [
            path.name for path in firmware_paths(PICO)
        ], "the board's own code and its runtime's"
        for path in firmware_paths(PICO):
            assert (folder / path.name).read_bytes() == path.read_bytes(), path.name

    def test_install_command_names_the_port_and_full_paths(self, tmp_path):
        """The install line has the port and every file in full, quoted when a path holds a space."""
        files = [tmp_path / "main.py", tmp_path / "with space" / "termapy_json.py"]
        actual = install_command(PICO, files, "COM5")
        assert actual == (f'uvx mpremote connect COM5 cp {files[0]} "{files[1]}" : + reset'), (
            "usable from any folder"
        )

    def test_two_boards_take_the_first_and_count_the_rest(self, tmp_path):
        """With two Picos attached the first in port order is chosen, and the other is reported."""
        second = ChipFacts(device="COM9", serial="0123456789ABCDEF", vid_pid="2E8A:0005")
        created = create_board_config(PICO, tmp_path, source=ports(PICO_PORT, second))
        assert (created.device, created.others) == ("COM5", 1)

    def test_no_board_fails_and_creates_nothing(self, tmp_path):
        """With no running board there is nothing to connect to: it fails, saying what to do."""
        with pytest.raises(LookupError, match="No Raspberry Pi Pico running MicroPython"):
            create_board_config(PICO, tmp_path, source=ports(FTDI_PORT), usb_source=usb())
        assert not (tmp_path / "pico").exists(), "no half-made config"

    def test_a_board_in_its_boot_loader_is_told_to_flash(self, tmp_path):
        """A board waiting in its boot loader gets the flashing step, not 'plug it in'."""
        with pytest.raises(
            LookupError, match="attached but not running MicroPython. Flash MicroPython"
        ):
            create_board_config(PICO, tmp_path, source=ports(), usb_source=usb("2E8A:0003"))

    def test_an_existing_config_is_not_replaced(self, tmp_path):
        """A config by that name for another board is refused: a board config is never overwritten."""
        other = ChipFacts(device="COM9", serial="0123456789ABCDEF", vid_pid="2E8A:0005")
        create_board_config(PICO, tmp_path, source=ports(other))
        before = (tmp_path / "pico" / "pico.cfg").read_text(encoding="utf-8")
        with pytest.raises(ConfigExistsError):
            create_board_config(PICO, tmp_path, source=ports(PICO_PORT))
        assert (tmp_path / "pico" / "pico.cfg").read_text(encoding="utf-8") == before

    def test_a_board_with_a_config_gets_no_second_one(self, tmp_path):
        """A config under any name that connects to this board stops a second one, twice in a row."""
        # Arrange -- the board already has a config, named bench
        create_board_config(PICO, tmp_path, "bench", source=ports(PICO_PORT))

        # Act / Assert -- asking again, by the default name or another, points at bench
        for name in ("", "spare"):
            with pytest.raises(AlreadyConfigured) as caught:
                create_board_config(PICO, tmp_path, name, source=ports(PICO_PORT))
            assert caught.value.path == tmp_path / "bench" / "bench.cfg"
            assert caught.value.serial == "E66548545710822C"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["bench"], "nothing new made"

    def test_configs_for_serial_reads_port_candidates(self, tmp_path):
        """A port of several candidates names the board when any one is its serial, in any case."""
        cfg_path = tmp_path / "rig" / "rig.cfg"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            json.dumps({"serial": {"port": "COM3|e66548545710822c"}}), encoding="utf-8"
        )
        before = cfg_path.read_text(encoding="utf-8")
        assert configs_for_serial(tmp_path, "E66548545710822C") == [cfg_path]
        assert configs_for_serial(tmp_path, "0123456789ABCDEF") == []
        assert cfg_path.read_text(encoding="utf-8") == before, "a read never writes"

    def test_board_config_keeps_the_defaults_it_does_not_set(self):
        """Keys the board does not set keep their defaults: the board adds, it does not replace."""
        cfg = board_config(PICO, "pico", "SN1")
        defaults = default_cfg()
        assert cfg["eol"] == defaults["eol"]
        assert cfg["serial"]["baud_rate"] == defaults["serial"]["baud_rate"]


class TestFirmware:
    def test_copy_is_byte_for_byte_and_refuses_twice(self, tmp_path):
        """The board's device code is copied exactly, and a second copy stops before writing."""
        written = copy_firmware(PICO, tmp_path / "fw")
        for path, source in zip(written, firmware_paths(PICO), strict=True):
            assert path.read_bytes() == source.read_bytes(), path.name
        with pytest.raises(FileExistsError):
            copy_firmware(PICO, tmp_path / "fw")


@dataclass
class Env:
    """A bare engine and the lines it printed."""

    engine: ReplEngine
    output: list[str]


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    """An engine whose config directory is ``tmp_path`` and whose port list is the fake fleet.

    ``TERMAPY_DEMO_FLEET`` stands in for the machine's ports (it has no
    Pico), the lever handler tests use because a handler takes no
    ``source=``.
    """
    monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
    (tmp_path / "termapy_cfg").mkdir()
    monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path / "termapy_cfg"))
    cfg = {"port": "COM4", "baud_rate": 115200}
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    output: list[str] = []
    engine = ReplEngine(cfg, str(config_path), lambda text, color=None: output.append(text))
    engine.ctx.ns("flags")["output_level"] = "verbose"
    engine.ctx.capabilities = CapabilitySet(filesystem_unconfined=True)
    engine.ctx.sync_capabilities()
    engine.ctx.config_path = str(config_path)  # a loaded config, as a frontend sets it
    return Env(engine, output)


class TestCommands:
    def test_board_list_is_the_table(self, env: Env):
        """/board.list answers one record per board, with where it is attached as data."""
        result = env.engine.dispatch("board.list")
        assert result.success, result.error
        assert [record["name"] for record in result.data] == [board.name for board in BOARDS]
        assert result.data[0]["ports"] == [], "the fake fleet has no Pico"

    def test_bare_board_lists(self, env: Env):
        """Bare /board runs its declared bare_sub, the listing."""
        bare = env.engine.dispatch("board")
        listed = env.engine.dispatch("board.list")
        assert [record["name"] for record in bare.data] == [
            record["name"] for record in listed.data
        ]

    def test_board_new_unknown_board_fails(self, env: Env):
        """/board.new with an unknown board lists the boards."""
        result = env.engine.dispatch("board.new nosuch")
        assert result.error == "Unknown board: nosuch (use esp32s3, pico)"

    def test_board_new_without_a_board_attached_fails(self, env: Env, tmp_path):
        """/board.new with no Pico attached fails, naming the board, and creates no config."""
        result = env.engine.dispatch("board.new pico")
        assert not result.success
        assert "Raspberry Pi Pico" in result.error
        assert not (tmp_path / "termapy_cfg" / "pico").exists()

    def test_board_firmware_copies_into_the_config_folder(self, env: Env, tmp_path):
        """/board.firmware writes into the config's firmware/ and prints the install command in full."""
        # Act
        result = env.engine.dispatch("board.firmware pico")

        # Assert
        folder = (tmp_path / "cfg" / "firmware").resolve()
        assert result.success, result.error
        for path in firmware_paths(PICO):
            assert (folder / path.name).read_bytes() == path.read_bytes(), path.name
        expected = install_command(
            PICO, [folder / path.name for path in firmware_paths(PICO)], "<port>"
        )
        assert result.data["install"] == expected, "no Pico attached, so the port is a placeholder"
        assert f"Put them on the running board: {expected}" in env.output
        assert not Path("main.py").exists(), "the working directory is never written"

    def test_board_firmware_relative_folder_is_under_the_config(self, env: Env, tmp_path):
        """A relative folder= lands under the config's folder."""
        result = env.engine.dispatch("board.firmware pico folder=fw")
        assert result.success, result.error
        assert (tmp_path / "cfg" / "fw" / "main.py").is_file()


def _load_board_json():
    """MicroPython's shared termapy_json.py, imported from the package as a board imports it."""
    path = next(path for path in firmware_paths(PICO) if path.name == "termapy_json.py")
    spec = importlib.util.spec_from_file_location("board_termapy_json", path)
    assert spec is not None and spec.loader is not None, "the board's module loads"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BOARD_JSON = _load_board_json()


class Link:
    """A Monitor over a fake command table, with the reply bytes it wrote."""

    def __init__(self):
        def fail(args):
            raise BOARD_JSON.CommandError("usage: fail")

        def boom(args):
            raise RuntimeError("sensor fault")

        handlers = {
            "echo": lambda args: " ".join(args),
            "words": lambda args: args,
            "num": lambda args: 3.14,
            "neg": lambda args: -42,
            "flag": lambda args: True,
            "str": lambda args: 'hello "quoted" \\ tab\tend',
            "obj": lambda args: {"name": "stack", "used": 812},
            "arr": lambda args: [1, "two", 3.5, True],
            "fail": fail,
            "boom": boom,
        }
        self.commands = {
            name: ("the " + name + " command", handler) for name, handler in handlers.items()
        }
        self.out: list[str] = []
        self.monitor = BOARD_JSON.Monitor(self.commands, self.out.append)

    def ask(self, line: str) -> list[str]:
        """Send one line; return the reply lines it produced."""
        start = len(self.out)
        self.monitor.handle(line)
        return self.out[start:]


class TestMicroPythonJson:
    """MicroPython's termapy_json.py speaks the termapy_json.c contract (TESTER.md sections 2 to 5)."""

    def test_text_framing_is_the_value_and_err(self):
        """Text framing: the value as lines ending CRLF, a failure as ERR <message>."""
        link = Link()
        assert link.ask("num") == ["3.14\r\n"]
        assert link.ask("arr") == ["1\r\n", "two\r\n", "3.5\r\n", "true\r\n"]
        assert link.ask("obj") == ["name stack\r\n", "used 812\r\n"]
        assert link.ask("fail") == ["ERR usage: fail\r\n"]
        assert link.ask("nosuch") == ["ERR unknown command: nosuch\r\n"]
        assert link.ask("   ") == [], "a blank line is ignored"

    def test_framing_round_trip(self):
        """json on answers in JSON, json off in text, anything else is off; bare json reports."""
        link = Link()
        assert link.ask("json") == ["off\r\n"]
        assert link.ask("json on") == ['{"result":"on"}\n'], "the flip happens before the reply"
        assert link.ask("json") == ['{"result":"on"}\n']
        assert link.ask("json off") == ["off\r\n"]
        link.ask("json on")
        assert link.ask("json maybe") == ["off\r\n"], "an unknown word fails closed to off"

    def test_envelope_is_compact_and_typed(self):
        """JSON results keep their type and the envelope is compact, as the C module's fixtures pin."""
        link = Link()
        link.ask("json on")
        assert link.ask("num") == ['{"result":3.14}\n']
        assert link.ask("neg") == ['{"result":-42}\n']
        assert link.ask("flag") == ['{"result":true}\n']
        assert link.ask("str") == ['{"result":"hello \\"quoted\\" \\\\ tab\\tend"}\n']
        assert link.ask("arr") == ['{"result":[1,"two",3.5,true]}\n']
        assert link.ask("obj") == ['{"result":{"name":"stack","used":812}}\n']

    def test_error_codes(self):
        """Unknown command -32601, a handler's refusal -32602, any other failure -32603."""
        link = Link()
        link.ask("json on")
        assert link.ask("nosuch") == [
            '{"error":{"code":-32601,"message":"unknown command: nosuch"}}\n'
        ]
        assert link.ask("fail") == ['{"error":{"code":-32602,"message":"usage: fail"}}\n']
        assert link.ask("boom") == ['{"error":{"code":-32603,"message":"sensor fault"}}\n']

    def test_a_request_line_switches_to_json_and_echoes_its_id(self):
        """A {..} line flips framing by itself; its id is echoed last, and a plain line after has none."""
        link = Link()
        assert link.ask('{"cmd":"echo","args":"hi there","id":7}') == [
            '{"result":"hi there","id":7}\n'
        ]
        assert link.ask("echo again") == ['{"result":"again"}\n'], "no id without a request"
        assert link.ask('{ "cmd" : "neg" , "id" : 11 }') == ['{"result":-42,"id":11}\n']
        assert link.ask('{"cmd":"neg","id":-3}') == ['{"result":-42,"id":-3}\n']

    def test_request_forms(self):
        """cmd/args and method/params, string or flat array; cmd wins over method, args over params."""
        link = Link()
        assert link.ask('{"cmd":"echo"}') == ['{"result":""}\n']
        assert link.ask('{"method":"echo","params":"5ms 2s"}') == ['{"result":"5ms 2s"}\n']
        assert link.ask('{"method":"words","params":["5ms","2s"]}') == ['{"result":["5ms","2s"]}\n']
        assert link.ask('{"method":"words","params":[1,true,"x"]}') == [
            '{"result":["1","true","x"]}\n'
        ], "bare scalars are joined verbatim"
        assert link.ask('{"method":"words","params":[]}') == ['{"result":[]}\n']
        assert link.ask('{"cmd":"neg","method":"num"}') == ['{"result":-42}\n'], "cmd wins"
        assert link.ask('{"cmd":"echo","args":"a","params":"b"}') == ['{"result":"a"}\n']

    def test_jsonrpc_tag_is_mirrored(self):
        """A request carrying jsonrpc gets it back first, and always an id, null when it had none."""
        link = Link()
        assert link.ask('{"jsonrpc":"2.0","method":"neg","id":1}') == [
            '{"jsonrpc":"2.0","result":-42,"id":1}\n'
        ]
        assert link.ask('{"jsonrpc":"2.0","method":"neg"}') == [
            '{"jsonrpc":"2.0","result":-42,"id":null}\n'
        ]
        assert link.ask('{"jsonrpc":"2.0","method":"nope"}') == [
            '{"jsonrpc":"2.0","error":{"code":-32601,"message":"unknown command: nope"},"id":null}\n'
        ]
        assert link.ask("neg") == ['{"result":-42}\n'], "the tag does not outlive its request"

    def test_malformed_requests_answer_in_json_and_the_link_recovers(self):
        """Each malformed request is a -32700 envelope, id'd when the id was readable, and the next line works."""
        link = Link()
        malformed = '{"error":{"code":-32700,"message":"malformed json"}}\n'
        for line in (
            '{"nope":1}',
            '{"cmd":"neg',
            '{"cmd","neg"}',
            '{"cmd":"echo","args":5}',
            '{"cmd":"echo","args":["x"]}',
            '{"method":"echo","params":{"a":1}}',
            '{"method":"echo","params":[["x"]]}',
            '{"method":"echo","params":5}',
            '{"method":"echo","params":null}',
            '{"cmd":"   "}',
        ):
            assert link.ask(line) == [malformed], line
        assert link.ask('{"cmd":5,"id":3}') == [
            '{"error":{"code":-32700,"message":"malformed json"},"id":3}\n'
        ]
        assert link.ask("neg") == ['{"result":-42}\n'], "the link answers normally afterwards"

    def test_id_rules(self):
        """Only an integer id of at most ten digits is echoed; a bool or a longer one is dropped."""
        link = Link()
        assert link.ask('{"cmd":"neg","id":true}') == ['{"result":-42}\n']
        assert link.ask('{"cmd":"neg","id":"7"}') == ['{"result":-42}\n']
        assert link.ask('{"cmd":"neg","id":12345678901}') == ['{"result":-42}\n']
        assert link.ask('{"cmd":"neg","id":1234567890}') == ['{"result":-42,"id":1234567890}\n']

    def test_help_in_json_maps_every_command_to_its_help(self):
        """JSON help is one object, name to help line: the table's commands and the monitor's own."""
        # Arrange
        link = Link()
        link.ask("json on")
        expected = {name: line for name, (line, _) in link.commands.items()}
        expected["help"] = link.monitor.builtins["help"][0]
        expected["json"] = link.monitor.builtins["json"][0]

        # Act
        reply = link.ask('{"jsonrpc":"2.0","method":"help","id":3}')

        # Assert
        assert len(reply) == 1, "one envelope"
        envelope = json.loads(reply[0])
        assert envelope["result"] == expected, "the help table is the result itself, no data member"
        assert set(envelope) == {"jsonrpc", "result", "id"}, "a plain JSON-RPC response"

    def test_help_in_text_is_sorted_and_aligned(self):
        """Text help is one line per command, sorted by name, every help line starting in one column."""
        # Act
        lines = Link().ask("help")

        # Assert
        names = [line.split()[0] for line in lines]
        assert names == sorted(names), "sorted by hand: MicroPython's dicts keep no order"
        assert "json" in names and "help" in names, "the monitor's own commands are listed"
        columns = {line.index("the ") for line in lines if line.split()[0] not in ("help", "json")}
        assert len(columns) == 1, "the help lines are aligned"
        assert lines[0] == "arr" + " " * 4 + "the arr command\r\n", (
            "two spaces past words, the longest"
        )

    def test_help_for_one_command(self):
        """help <command> answers its line; an unknown name or a second word is a -32602 refusal."""
        link = Link()
        assert link.ask("help num") == ["the num command\r\n"]
        assert link.ask("help json") == [link.monitor.builtins["json"][0] + "\r\n"]
        assert link.ask("help nosuch") == ["ERR unknown command: nosuch\r\n"]
        assert link.ask("help num echo") == ["ERR usage: help {command}\r\n"]
        link.ask("json on")
        assert link.ask("help nosuch") == [
            '{"error":{"code":-32602,"message":"unknown command: nosuch"}}\n'
        ], "help itself exists, so its bad argument is -32602, not -32601"

    def test_a_table_cannot_take_a_monitor_command_name(self):
        """A table row named help or json is refused at construction instead of silently shadowed."""
        with pytest.raises(ValueError, match="command name taken by the monitor: help"):
            BOARD_JSON.Monitor({"help": ("mine", lambda args: "")}, print)


class TestIntArg:
    """int_arg reads a board command's number: decimal or 0x hex, refused out of range."""

    def test_decimal_and_hex(self):
        """Decimal and 0x hex in either case read as the number; nothing else is hex."""
        actual = [
            BOARD_JSON.int_arg(word, "level", 0, 255) for word in ("0", "255", "0xFF", "0X1a")
        ]
        assert actual == [0, 255, 255, 26]

    def test_out_of_range_is_refused_not_clipped(self):
        """A bad or out-of-range word is a -32602 refusal that names it and the range."""
        for word in ("256", "-1", "0x100", "abc", "ff", "0x", ""):
            with pytest.raises(BOARD_JSON.CommandError) as caught:
                BOARD_JSON.int_arg(word, "level", 0, 255)
            assert caught.value.message == f"invalid level: {word} (use 0 to 255)", word
            assert caught.value.code == BOARD_JSON.BAD_ARGS, "a bad argument, not a fault"

    def test_board_and_profile_agree_on_a_level(self):
        """The ESP32-S3 profile's level type, checked by termapy, takes exactly what the board takes."""
        # Arrange
        profile = load_profile(BOARDS_DIR / "esp32s3" / "profile.json")
        registry = TypeRegistry.from_profile(profile)

        def board_takes(word: str) -> bool:
            try:
                BOARD_JSON.int_arg(word, "level", 0, 255)
            except BOARD_JSON.CommandError:
                return False
            return True

        # Act / Assert
        for word in ("0", "32", "255", "0xFF", "0x0", "256", "-1", "0x100", "abc"):
            actual = registry.validate("level", word).ok
            assert actual == board_takes(word), f"termapy and the board disagree on {word!r}"


class TestOnOffArg:
    """on_off_arg reads a board switch: on or 1, off or 0, anything else refused."""

    def test_the_four_words(self):
        """on and 1 are True, off and 0 are False."""
        actual = [BOARD_JSON.on_off_arg(word, "state") for word in ("on", "1", "off", "0")]
        assert actual == [True, True, False, False]

    def test_any_other_word_is_refused(self):
        """A word outside the four is a -32602 refusal naming it, never a silent off."""
        for word in ("ON", "true", "yes", "2", "onn", ""):
            with pytest.raises(BOARD_JSON.CommandError) as caught:
                BOARD_JSON.on_off_arg(word, "state")
            assert caught.value.message == f"invalid state: {word} (use on, off, 1 or 0)", word
            assert caught.value.code == BOARD_JSON.BAD_ARGS, "a bad argument, not a fault"

    def test_board_and_profile_agree_on_a_switch(self):
        """Each board profile's on_off type, checked by termapy, takes exactly what the board takes."""

        def board_takes(word: str) -> bool:
            try:
                BOARD_JSON.on_off_arg(word, "state")
            except BOARD_JSON.CommandError:
                return False
            return True

        for board in BOARDS:
            registry = TypeRegistry.from_profile(
                load_profile(BOARDS_DIR / board.name / "profile.json")
            )
            for word in ("on", "off", "1", "0", "ON", "true", "2", ""):
                actual = registry.validate("on_off", word).ok
                assert actual == board_takes(word), (
                    f"{board.name}: termapy and the board disagree on {word!r}"
                )


@pytest.mark.slow
class TestBoardFlag:
    """``termapy --board BOARD`` through main() in a subprocess, ``--check`` standing in for a frontend."""

    @staticmethod
    def _run(cwd: Path, *argv: str):
        """Run ``termapy <argv>`` in ``cwd`` against the fake port fleet, which has no Pico."""
        env = {key: value for key, value in os.environ.items() if key != "TERMAPY_CFG_DIR"}
        env["TERMAPY_DEMO_FLEET"] = "1"
        return subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.argv = ['termapy', *{list(argv)!r}]; "
                "from termapy.entry import main; main()",
            ],
            cwd=cwd,
            env=env,
            input="",
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_an_existing_board_config_is_reused(self, tmp_path):
        """With the board's config already there, --board starts it untouched."""
        # Arrange
        created = create_board_config(PICO, tmp_path / "termapy_cfg", source=ports(PICO_PORT))
        before = created.path.read_text(encoding="utf-8")

        # Act
        result = self._run(tmp_path, "--board", "pico", "--check")

        # Assert
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"status": "ok"}
        assert "Using" in result.stderr
        assert created.path.read_text(encoding="utf-8") == before, "the config is untouched"

    def test_no_board_attached_exits_1_and_creates_nothing(self, tmp_path):
        """With no Pico attached and no config yet, --board says why and creates nothing."""
        result = self._run(tmp_path, "--board", "pico", "--check")
        assert result.returncode == 1
        assert "Raspberry Pi Pico" in result.stderr
        assert not (tmp_path / "termapy_cfg" / "pico").exists()

    def test_unknown_board_is_a_usage_error(self, tmp_path):
        """An unknown board exits 2 and lists the boards."""
        result = self._run(tmp_path, "--board", "nosuch")
        assert result.returncode == 2
        assert "Unknown board: nosuch (use esp32s3, pico)" in result.stderr

    def test_board_with_a_config_is_a_usage_error(self, tmp_path):
        """--board names its own config, so a second one on the line exits 2."""
        result = self._run(tmp_path, "other.cfg", "--board", "pico")
        assert result.returncode == 2
        assert "--board cannot be combined" in result.stderr
