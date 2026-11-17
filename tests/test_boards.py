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
import asyncio
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
from mem_wire_cases import WIRE_ERRORS, masked, row

from termapy.boards import (
    ARDUINO,
    BOARDS,
    MICROPYTHON,
    AlreadyConfigured,
    Board,
    attached,
    board_config,
    configs_for_serial,
    copy_firmware,
    create_board_config,
    find_board,
    firmware_paths,
    install_command,
    profile_source,
    stale_files,
)
from termapy.config import ConfigExistsError
from termapy.defaults import DEFAULT_CFG, default_cfg
from termapy.firmware import find_target, source_path
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


PICO_ARDUINO = find_board("pico-arduino")
ESP32S3_ARDUINO = find_board("esp32s3-arduino")
# The same Pico running the Arduino starter: the core gives USB the same serial number.
PICO_ARDUINO_PORT = ChipFacts(device="COM5", serial="E66548545710822C", vid_pid="2E8A:000A")

_MICROPYTHON_BOARDS = [board for board in BOARDS if board.runtime is MICROPYTHON]
_ARDUINO_BOARDS = [board for board in BOARDS if board.runtime is ARDUINO]


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
        "machine": "machine",
        "machine.freq": "machine.",
        "machine.Pin.init 25 machine.Pin.OUT value=1": "machine.",
        "xmodem": "xmodem",
        "xmodem recv": "xmodem",
        "xmodem send": "xmodem",
        "MEM.R 0x20000000 16": "MEM.R",
        "MEM.W 0x20000000 DEADBEEF": "MEM.W",
        "MEM.M 0x20000000 FFFFFF00 00000001": "MEM.M",
        "MEM.INFO": "MEM.INFO",
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
        "machine": "machine",
        "machine.Pin": "machine.",
        "machine.ADC.read_u16 1": "machine.",
        "xmodem": "xmodem",
        "xmodem recv": "xmodem",
        "xmodem send": "xmodem",
        "MEM.R 0x1000 16": "MEM.R",
        "MEM.R 1000 64": "MEM.R",
        "MEM.W 0x1000 DEADBEEF": "MEM.W",
        "MEM.W 0x1000 de ad": "MEM.W",
        "MEM.M 0x1000 FFFFFF00 00000001": "MEM.M",
        "MEM.M 0x1001 F0 0F": "MEM.M",
        "MEM.INFO": "MEM.INFO",
    },
    "pico-arduino": {
        "help": "help",
        "help led": "help",
        "json": "json",
        "json on": "json",
        "led": "led",
        "led off": "led",
        "led 1": "led",
        "temp": "temp",
        "id": "id",
        "MEM.R 0x20000000 16": "MEM.R",
        "MEM.R 0 64": "MEM.R",
        "MEM.W 0x20000000 DEADBEEF": "MEM.W",
        "MEM.M 0x20000000 FFFFFF00 00000001": "MEM.M",
        "MEM.INFO": "MEM.INFO",
    },
    "esp32s3-arduino": {
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
        "MEM.R 0x3FC88000 16": "MEM.R",
        "MEM.W 0x3FC88000 de ad": "MEM.W",
        "MEM.M 0x3FC88000 FFFFFF00 00000001": "MEM.M",
        "MEM.INFO": "MEM.INFO",
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

    @pytest.mark.rule("board-profile-complete")
    def test_profile_describes_the_firmware_commands(self):
        """The profile names every command the board answers: its table's, the monitor's, and each object's two forms."""
        monitor_commands = set(BOARD_JSON.Monitor({}, print).builtins)
        mem_commands = set(BOARD_MEM.Memory(None, None).commands())
        for board in BOARDS:
            main = firmware_paths(board)[0]
            if board.runtime is ARDUINO:
                firmware = _sketch_command_names(main)
                assert firmware, f"{board.name}: no commands found in {main.name}"
                expected = firmware | _c_monitor_commands()
            else:
                firmware = _firmware_command_names(main)
                assert firmware, f"{board.name}: no commands found in {main.name}"
                expected = firmware | monitor_commands
                for name in _firmware_object_names(main):
                    # An object is two entries: the name alone, and the name with an expression.
                    expected |= {name, name + "."}
                if "termapy_mem.py" in board.runtime.files:
                    expected |= mem_commands
            actual = set(load_profile(BOARDS_DIR / board.name / "profile.json")["commands"])
            assert actual == expected, f"{board.name}: profile and firmware disagree"

    @pytest.mark.rule("board-profile-complete")
    def test_profile_governs_every_form(self):
        """Every form a board takes reaches its profile entry, and the entry's types take it."""
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
        """Every MicroPython board gets the runtime's one copy of each shared file; no board folder holds its own."""
        shared = {
            path
            for board in BOARDS
            for path in firmware_paths(board)
            if path.name in board.runtime.files
        }
        assert len(shared) == len({path.name for path in shared}), (
            f"one copy of each shared file, not one per board: {shared}"
        )
        for board in BOARDS:
            for name in board.runtime.files:
                copy = BOARDS_DIR / board.name / "firmware" / name
                assert not copy.exists(), f"{board.name} carries its own {name}"

    def test_find_board_rejects_an_unknown_name(self):
        """An unknown board is a lookup failure that lists the boards."""
        with pytest.raises(
            ValueError,
            match=r"Unknown board: nosuch \(use esp32s3, esp32s3-arduino, pico, pico-arduino\)",
        ):
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


class TestArduino:
    """The Arduino starters: the same boards in C, the shared code the /firmware kit's own."""

    def test_each_runtime_sees_the_pico_running_the_other(self, tmp_path):
        """A Pico running Arduino is pico-arduino's board, and to the MicroPython row it is attached without MicroPython."""
        # Act
        arduino = attached(PICO_ARDUINO, source=ports(FTDI_PORT, PICO_ARDUINO_PORT))
        micropython = attached(PICO, source=ports(FTDI_PORT), usb_source=usb("2E8A:000A"))
        reverse = attached(PICO_ARDUINO, source=ports(PICO_PORT), usb_source=usb("2E8A:0005"))

        # Assert
        assert arduino.running == (("COM5", "E66548545710822C"),), "found by 2E8A:000A"
        assert micropython.without_runtime is True, "flash MicroPython, says the pico row"
        assert reverse.without_runtime is True, "and the other way round"

    def test_new_config_holds_a_folder_arduino_builds(self, tmp_path):
        """/board.new lays out firmware/ as a sketch: firmware.ino and the kit's C beside it, byte for byte."""
        # Act
        created = create_board_config(PICO_ARDUINO, tmp_path, source=ports(PICO_ARDUINO_PORT))

        # Assert
        folder = created.path.parent / "firmware"
        actual = [path.name for path in created.firmware]
        assert actual[0] == "firmware.ino", "Arduino builds the folder its .ino is named for"
        assert set(actual[1:]) == {
            f"termapy_{module}.{ext}" for module in ("monitor", "json", "mem") for ext in "ch"
        }
        for name in actual[1:]:
            module = name.removeprefix("termapy_").split(".")[0]
            shipped = source_path(find_target(module), name)
            assert (folder / name).read_bytes() == shipped.read_bytes(), f"{name}: the kit's own"
        data = json.loads(created.path.read_text(encoding="utf-8"))
        assert data["serial"]["port"] == "E66548545710822C", "by serial number"
        assert data["title"] == "Raspberry Pi Pico (Arduino)"

    def test_a_sketch_is_copied_under_its_folder_s_name(self, tmp_path):
        """Arduino builds a folder whose .ino is named for it: fw/ gets fw.ino, the C keeps its names."""
        written = copy_firmware(PICO_ARDUINO, tmp_path / "fw")

        actual = [path.name for path in written]

        assert actual[0] == "fw.ino", "the sketch takes the folder's name"
        assert written[0].read_bytes() == firmware_paths(PICO_ARDUINO)[0].read_bytes()
        assert "termapy_monitor.c" in actual, "every other file keeps its own"

    def test_the_install_line_builds_the_folder(self, tmp_path):
        """The Arduino rows' line names the firmware folder, quoted when it holds a space."""
        folder = tmp_path / "my board" / "firmware"
        files = [folder / path.name for path in firmware_paths(PICO_ARDUINO)]

        actual = install_command(PICO_ARDUINO, files, "COM5")

        assert actual == (
            f'arduino-cli compile --upload --fqbn rp2040:rp2040:rpipico --port COM5 "{folder}"'
        )

    @pytest.mark.slow
    @pytest.mark.xdist_group("arduino")
    @pytest.mark.parametrize("board", _ARDUINO_BOARDS, ids=lambda board: board.name)
    def test_the_sketch_builds_with_arduino_cli(self, board, tmp_path):
        """Each Arduino starter's folder compiles with its core, warnings on and none printed."""
        cli = shutil.which("arduino-cli")
        if cli is None:
            pytest.skip("arduino-cli is not on PATH")
        fqbn = re.search(r"--fqbn (\S+)", board.install)
        assert fqbn is not None, f"{board.name}: the install line names the board"
        core = ":".join(fqbn.group(1).split(":")[:2])
        installed = subprocess.run(
            [cli, "core", "list"], capture_output=True, text=True, check=False, timeout=60
        )
        if core not in installed.stdout:
            pytest.skip(f"the {core} core is not installed")
        # A folder not named firmware, so the build proves the sketch is named for its folder.
        folder = tmp_path / "bench_sketch"
        copy_firmware(board, folder)

        # Act
        result = subprocess.run(
            [cli, "compile", "--fqbn", fqbn.group(1), "--warnings", "all", str(folder)],
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )

        # Assert
        assert result.returncode == 0, result.stdout + result.stderr
        # Only ours: the core's own sources are the core's business.
        warnings = [
            line
            for line in (result.stdout + result.stderr).splitlines()
            if "warning:" in line and ("termapy_" in line or "firmware.ino" in line)
        ]
        assert warnings == [], "the shipped C and the sketch build clean"


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
        """The config's port is MicroPython's serial number, and its folder holds every device file."""
        # Act
        created = create_board_config(ESP32S3, tmp_path, source=ports(CH340K_PORT, ESP32S3_PORT))

        # Assert
        data = json.loads(created.path.read_text(encoding="utf-8"))
        assert data["serial"]["port"] == "58E6C5716A840000", "by serial number, not COM9"
        assert data["title"] == "ESP32-S3 DevKitC-1"
        actual = [path.name for path in created.firmware]
        assert actual == [
            "main.py",
            "termapy_json.py",
            "termapy_mem.py",
            "termapy_xmodem.py",
        ], "the MEM and XMODEM modules too"


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
        assert data["profile_path"] == f"pico{PROFILE_SUFFIX}", "it loads at every connect"
        assert data["protocol"] == "ndjson", "JSON mode sends the board ids"

    def test_a_named_config_gets_its_own_profile_name(self, tmp_path):
        """With a name, the config and its profile both carry it, so the profile loads with it."""
        created = create_board_config(PICO, tmp_path, "bench", source=ports(PICO_PORT))
        assert created.path == tmp_path / "bench" / "bench.cfg"
        assert (tmp_path / "bench" / f"bench{PROFILE_SUFFIX}").is_file()
        data = json.loads(created.path.read_text(encoding="utf-8"))
        assert data["profile_path"] == f"bench{PROFILE_SUFFIX}", "the profile_path follows the name"

    @pytest.mark.rule("board-update")
    def test_a_new_config_is_up_to_date(self, tmp_path):
        """What /board.new writes is what /board.update would: a fresh config has nothing stale."""
        created = create_board_config(PICO, tmp_path, source=ports(PICO_PORT))
        assert stale_files(PICO, created.path) == []

    @pytest.mark.rule("board-update")
    def test_line_endings_alone_are_not_a_change(self, tmp_path):
        """A copy that differs only in CRLF against LF, as two git checkouts on Windows write it, is up to date."""
        # Arrange -- every copy rewritten with the other line ending
        created = create_board_config(PICO, tmp_path, source=ports(PICO_PORT))
        copies = [created.path.parent / f"pico{PROFILE_SUFFIX}", *created.firmware]
        for path in copies:
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

        # Act
        actual = stale_files(PICO, created.path)

        # Assert
        assert actual == [], "line endings are not a change"

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
        assert (
            result.error
            == "Unknown board: nosuch (use esp32s3, esp32s3-arduino, pico, pico-arduino)"
        )

    def test_board_new_without_a_board_attached_fails(self, env: Env, tmp_path):
        """/board.new with no Pico attached fails, naming the board, and creates no config."""
        result = env.engine.dispatch("board.new pico")
        assert not result.success
        assert "Raspberry Pi Pico" in result.error
        assert not (tmp_path / "termapy_cfg" / "pico").exists()

    @pytest.mark.rule("writes-in-cfg-folder")
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
            PICO, [folder / path.name for path in firmware_paths(PICO)], "PORT"
        )
        assert result.data["install"] == expected, "no Pico attached, so the port is a placeholder"
        assert (
            f"Put them on the board: {expected}  (replace PORT with the board's port: /port.list)"
            in env.output
        ), "a word every shell takes as it stands, and what to put there"
        assert not Path("main.py").exists(), "the working directory is never written"

    def test_board_firmware_relative_folder_is_under_the_config(self, env: Env, tmp_path):
        """A relative folder= lands under the config's folder."""
        result = env.engine.dispatch("board.firmware pico folder=fw")
        assert result.success, result.error
        assert (tmp_path / "cfg" / "fw" / "main.py").is_file()


def _interactive(env: Env, *, confirm=None) -> None:
    """Give the engine a host's wiring and a person at the keyboard who answers each question with ``confirm``.

    A nested command is dispatched as every host wires it
    (``TerminalHost._build_internal_handle``); a bare engine leaves it a no-op.
    """
    engine = env.engine
    env.engine.ctx.internal.dispatch = lambda line: engine.dispatch(line, report=False)
    env.engine.ctx.internal.apply_cfg = engine._apply_cfg
    env.engine.ctx.capabilities = CapabilitySet(
        filesystem_unconfined=True, interactive=True, confirm_dialog=confirm is not None
    )
    env.engine.ctx.sync_capabilities()
    if confirm is not None:
        env.engine.ctx.ui._confirm_impl = confirm


def _old_board_config(tmp_path) -> Path:
    """The env's config as an older termapy left it: an old profile, one old file, one current, two missing."""
    folder = (tmp_path / "cfg").resolve()
    (folder / f"test{PROFILE_SUFFIX}").write_text(
        json.dumps({"profile_version": 2, "profile_revision": "1.1.0", "commands": {}}),
        encoding="utf-8",
    )
    (folder / "firmware").mkdir()
    (folder / "firmware" / "main.py").write_text("print('old')\n", encoding="utf-8")
    shared = firmware_paths(PICO)[1]
    (folder / "firmware" / shared.name).write_bytes(shared.read_bytes())
    return folder


@pytest.mark.rule("board-update")
class TestBoardUpdate:
    """/board.update brings an existing board config's copies up to what termapy ships."""

    def test_it_replaces_what_differs_names_the_profile_and_loads_it(self, env: Env, tmp_path):
        """The old and missing copies are written, the current one is left, and the profile loads."""
        # Arrange
        folder = _old_board_config(tmp_path)
        _interactive(env)
        current = folder / "firmware" / firmware_paths(PICO)[1].name
        before = current.stat().st_mtime_ns

        # Act
        result = env.engine.dispatch("board.update pico")

        # Assert
        assert result.success, result.error
        expected = [folder / f"test{PROFILE_SUFFIX}"] + [
            folder / "firmware" / path.name
            for path in firmware_paths(PICO)
            if path.name != current.name
        ]
        assert result.data["replaced"] == [str(path) for path in expected], "profile first"
        assert result.value == len(expected), "the value is the count"
        assert current.stat().st_mtime_ns == before, "an identical copy is not rewritten"
        assert stale_files(PICO, folder / "test.cfg") == [], "everything now matches"
        saved = json.loads((folder / "test.cfg").read_text(encoding="utf-8"))
        assert saved["profile_path"] == f"test{PROFILE_SUFFIX}", "it loads at the next connect"
        assert saved["protocol"] == "ndjson", "the board's setting its code depends on"
        assert env.engine.cfg["protocol"] == "ndjson", "the session follows the file"
        assert "led" in env.engine.ctx.ns("active_profile")["commands"], "and is loaded now"
        assert f"  test{PROFILE_SUFFIX}  1.1.0 -> {_shipped_revision(PICO)}" in env.output
        assert "  firmware/termapy_mem.py  (new)" in env.output, "a missing copy is named new"
        assert result.data["install"] == install_command(
            PICO, [folder / "firmware" / path.name for path in firmware_paths(PICO)], "PORT"
        ), "the whole set, for the board; no Pico is attached"

    @pytest.mark.rule("writes-in-cfg-folder")
    def test_a_relative_config_path_gets_full_paths(self, env: Env, tmp_path, monkeypatch):
        """With the config path relative, as a TUI started in the project folder has it, the reply names full paths."""
        # Arrange -- the working folder is the project, the config path relative to it
        folder = _old_board_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        env.engine.ctx.config_path = str(Path("cfg") / "test.cfg")
        _interactive(env)

        # Act
        result = env.engine.dispatch("board.update pico")

        # Assert
        assert result.success, result.error
        assert all(Path(path).is_absolute() for path in result.data["replaced"]), "full paths"
        expected = install_command(
            PICO, [folder / "firmware" / path.name for path in firmware_paths(PICO)], "PORT"
        )
        assert result.data["install"] == expected, "pastes into a shell in any folder"

    def test_the_same_revision_with_other_contents_says_so(self, env: Env, tmp_path):
        """A profile at the shipped revision whose contents differ is named as that, not 2.2.0 -> 2.2.0."""
        # Arrange -- the shipped profile, edited by hand
        folder = _old_board_config(tmp_path)
        shipped = json.loads(profile_source(PICO).read_text(encoding="utf-8"))
        shipped["commands"].pop("temp")
        (folder / f"test{PROFILE_SUFFIX}").write_text(json.dumps(shipped), encoding="utf-8")
        _interactive(env)

        # Act
        env.engine.dispatch("board.update pico")

        # Assert
        expected = f"  test{PROFILE_SUFFIX}  {_shipped_revision(PICO)}, contents differ"
        assert expected in env.output

    def test_a_second_update_changes_nothing(self, env: Env, tmp_path):
        """Run twice, the second finds the config up to date, writes nothing and still loads the profile."""
        # Arrange
        _old_board_config(tmp_path)
        _interactive(env)
        env.engine.dispatch("board.update pico")
        env.engine.dispatch("profile.unload")
        env.output.clear()

        # Act
        result = env.engine.dispatch("board.update pico")

        # Assert
        assert result.success, result.error
        assert (result.value, result.data["replaced"]) == (0, [])
        assert "test is up to date." in env.output
        assert result.data["install"] == "", (
            "no device code changed, so nothing to put on the board"
        )
        assert env.engine.ctx.ns("active_profile"), "the profile is loaded again"

    def test_a_no_changes_nothing(self, env: Env, tmp_path):
        """Answered no, it fails and every file is as it was; the question lists each file."""
        # Arrange
        folder = _old_board_config(tmp_path)
        questions: list[str] = []
        _interactive(env, confirm=lambda message: questions.append(message) or False)
        before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}

        # Act
        result = env.engine.dispatch("board.update pico")

        # Assert
        assert result.error == "Config not updated: test"
        after = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
        assert after == before, "nothing written"
        assert questions[0].startswith("Update test from termapy's Raspberry Pi Pico starter?")
        assert "  firmware/main.py" in questions[0].splitlines(), "each file it would replace"
        assert f"  test.cfg  profile_path = test{PROFILE_SUFFIX}" in questions[0].splitlines()
        assert "  test.cfg  protocol = ndjson" in questions[0].splitlines()

    def test_the_board_defaults_to_the_config_name(self, env: Env):
        """Bare, the board is the one the config is named for; a config named otherwise is refused."""
        _interactive(env)
        result = env.engine.dispatch("board.update")
        assert (
            result.error == "Unknown board: test (use esp32s3, esp32s3-arduino, pico, pico-arduino)"
        )

    def test_without_a_person_it_never_runs(self, env: Env, tmp_path):
        """Without an interactive session (MCP), the gate refuses and nothing is written."""
        folder = _old_board_config(tmp_path)
        result = env.engine.dispatch("board.update pico")
        assert not result.success, "MANAGE_FILES: an agent never replaces a profile"
        assert (folder / "firmware" / "main.py").read_text(encoding="utf-8") == "print('old')\n"


def _shipped_revision(board: Board) -> str:
    """The profile_revision of the board's shipped profile."""
    return json.loads(profile_source(board).read_text(encoding="utf-8"))["profile_revision"]


def _firmware_command_names(main_path) -> set[str]:
    """The command names a board's main.py registers, from either form it may use.

    A starter registers commands as ``@commands.command("name", ...)`` decorators
    or as a ``COMMANDS = {"name": ...}`` dict literal; this reads the names from
    whichever is present, so the profile-completeness guard follows the firmware
    without being welded to one form.
    """
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        # @<anything>.command("name", ...) -- the decorator form.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "command"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
        # COMMANDS = {"name": ...} -- the dict form.
        elif (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "COMMANDS" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            names.update(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    return names


def _firmware_object_names(main_path) -> set[str]:
    """The names a board's main.py puts on the link as objects: the keys of ``objects={...}``."""
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    return {
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword) and node.arg == "objects"
        if isinstance(node.value, ast.Dict)
        for key in node.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _sketch_command_names(sketch_path) -> set[str]:
    """The command names an Arduino starter's sketch registers: the first string of each ``kCommands`` row."""
    text = sketch_path.read_text(encoding="utf-8")
    table = re.search(r"kCommands\[\]\s*=\s*\{(.*?)\n\};", text, flags=re.DOTALL)
    assert table is not None, f"{sketch_path.name}: no kCommands table"
    return set(re.findall(r'^\s*\{\s*"([^"]+)",', table.group(1), flags=re.MULTILINE))


def _c_monitor_commands() -> set[str]:
    """The commands the C monitor answers itself, read from its ``sBuiltinNames``."""
    source = next(path for path in firmware_paths(PICO_ARDUINO) if path.name == "termapy_monitor.c")
    names = re.search(r"sBuiltinNames\[[^\]]*\]\s*=\s*\{([^}]*)\}", source.read_text("utf-8"))
    assert names is not None, "termapy_monitor.c names its own commands in sBuiltinNames"
    return set(re.findall(r'"([^"]+)"', names.group(1)))


def _load_board_module(board: Board, name: str):
    """One of MicroPython's shared files, imported from the package as a board imports it."""
    path = next(path for path in firmware_paths(board) if path.name == name)
    spec = importlib.util.spec_from_file_location("board_" + path.stem, path)
    assert spec is not None and spec.loader is not None, "the board's module loads"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BOARD_JSON = _load_board_module(PICO, "termapy_json.py")
BOARD_MEM = _load_board_module(ESP32S3, "termapy_mem.py")


class Waiting:
    """The characters that have arrived on a link, read one at a time; ``""`` once none is left."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def __call__(self) -> str:
        char, self.text = self.text[:1], self.text[1:]
        return char


class Link:
    """A Monitor over a fake command table and any native commands, with the reply bytes it wrote."""

    def __init__(self, native=None, read: Waiting | None = None, objects=None):
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
        self.monitor = BOARD_JSON.Monitor(self.commands, self.out.append, native, read, objects)

    def ask(self, line: str) -> list[str]:
        """Send one line; return the reply lines it produced."""
        start = len(self.out)
        self.monitor.handle(line)
        return self.out[start:]

    def poll(self) -> list[str]:
        """Run one poll(); return the reply lines it produced."""
        start = len(self.out)
        self.monitor.poll()
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
        expected["xmodem"] = link.monitor.builtins["xmodem"][0]

        # Act
        reply = link.ask('{"jsonrpc":"2.0","method":"help","id":3}')

        # Assert
        assert len(reply) == 1, "one envelope"
        envelope = json.loads(reply[0])
        assert envelope["result"] == expected, "the help table is the result itself, no data member"
        assert set(envelope) == {"jsonrpc", "result", "id"}, "a plain JSON-RPC response"

    def test_help_in_json_is_in_name_order(self):
        """JSON help lists the commands sorted by name, the order the text lines have, whatever order the table was written in."""
        # Arrange -- a table written out of order
        link = Link()
        link.ask("json on")

        # Act
        envelope = link.ask("help")[0]
        text_names = [line.split()[0] for line in Link().ask("help")]

        # Assert -- the key order on the wire, not the parsed dict's
        names = list(json.loads(envelope, object_pairs_hook=dict)["result"])
        assert names == sorted(names), "built once in name order, as MicroPython keeps none"
        assert names == text_names, "both framings read the one table"

    def test_help_in_text_is_sorted_and_aligned(self):
        """Text help is one line per command, sorted by name, every help line starting in one column."""
        # Act
        lines = Link().ask("help")

        # Assert
        names = [line.split()[0] for line in lines]
        assert names == sorted(names), "the table is built in name order"
        assert "json" in names and "help" in names, "the monitor's own commands are listed"
        # help, json and xmodem are the real builtins; their help does not follow
        # the synthetic "the <name> command" shape this checks alignment with.
        builtins = ("help", "json", "xmodem")
        columns = {line.index("the ") for line in lines if line.split()[0] not in builtins}
        assert len(columns) == 1, "the help lines are aligned"
        # The column is two past the longest name; xmodem (6) is now the longest,
        # so arr (3) is padded to 6 - 3 + 2 = 5 spaces.
        assert lines[0] == "arr" + " " * 5 + "the arr command\r\n", (
            "two spaces past xmodem, the longest"
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

    def test_a_command_set_builds_the_same_table_as_a_dict(self):
        """The @command decorator fills the same {name: (help, handler)} a dict literal does.

        A monitor cannot tell which form built its commands: the decorator is
        sugar for a project that prefers it, the explicit dict stays the starter
        form (and is what the AST profile guard reads).
        """
        # Arrange -- the same two commands, once by decorator, once by dict
        commands = BOARD_JSON.CommandSet()

        @commands.command("led", "the LED")
        def _led(args):
            return "on"

        @commands.command("ping", "replies pong")
        def _ping(args):
            return "pong"

        as_dict = {"led": ("the LED", _led), "ping": ("replies pong", _ping)}

        # Assert -- the decorator's table is the dict, and a monitor dispatches it
        assert commands.table == as_dict, "the decorator builds the dict-literal shape"
        out = []
        monitor = BOARD_JSON.Monitor(commands.table, write=out.append, read=lambda: "")
        monitor.handle("led")
        monitor.handle("ping")
        assert [line.strip() for line in out] == ["on", "pong"], "the monitor dispatches them"


class FakePin:
    """A pin for a monitor's object tests: its level is kept per pin number, as a real pin's is."""

    OUT = 1
    levels: dict[int, int] = {}

    def __init__(self, number: int, mode: int = 0) -> None:
        self.number = number

    def value(self, level: int | None = None) -> int | None:
        if level is None:
            return FakePin.levels.get(self.number, 0)
        FakePin.levels[self.number] = level
        return None

    def __repr__(self) -> str:
        return f"Pin({self.number})"


def fake_machine() -> types.SimpleNamespace:
    """What stands in for MicroPython's machine: a class, four functions and a private name."""
    FakePin.levels = {}
    return types.SimpleNamespace(
        Pin=FakePin,
        freq=lambda: 125_000_000,
        unique_id=lambda: b"\xe6\x61\x0a",
        same=lambda value: value,
        pair=lambda first, second=0: (first, second),
        _hidden=1,
    )


def object_link() -> Link:
    """A monitor with the fake machine on the link as ``machine``, in JSON framing."""
    link = Link(objects={"machine": fake_machine()})
    link.ask("json on")
    return link


def object_error(link: Link, line: str) -> tuple[int, str]:
    """The code and message of the failure ``line`` gets in JSON framing."""
    error = json.loads(link.ask(line)[0])["error"]
    return error["code"], error["message"]


@pytest.mark.rule("board-object-by-name")
class TestMonitorObjects:
    """An object on the link is found by name: a dotted path and values, never Python."""

    def test_a_module_lists_its_public_names(self):
        """A module answers its public names, sorted: how a caller finds what is there."""
        link = Link(objects={"library": json})
        names = link.ask("library")
        assert "dumps\r\n" in names and "loads\r\n" in names, "the module's functions"
        assert names == sorted(names), "sorted"
        assert not [name for name in names if name.startswith("_")], "nothing private"

    def test_any_object_lists_its_names_by_its_bare_name(self):
        """An object that is neither a module nor a class still lists its names when asked by name."""
        link = object_link()
        assert link.ask("machine") == ['{"result":["Pin","freq","pair","same","unique_id"]}\n'], (
            "sorted, and _hidden is not listed"
        )

    def test_a_class_lists_its_names(self):
        """A class answers its public names as a module does, so a caller walks down by name."""
        link = object_link()
        assert link.ask("machine.Pin") == ['{"result":["OUT","levels","value"]}\n']

    def test_a_function_is_called_and_answers_its_value_typed(self):
        """A path that ends at a function calls it: a number, bytes as uppercase hex, a tuple as an array."""
        link = object_link()
        assert link.ask("machine.freq") == ['{"result":125000000}\n']
        assert link.ask("machine.unique_id") == ['{"result":"E6610A"}\n'], "uppercase hex"
        assert link.ask("machine.pair 1") == ['{"result":[1,0]}\n']

    def test_a_path_that_ends_at_a_value_answers_it(self):
        """A constant is read by its path, with nothing to call."""
        link = object_link()
        assert link.ask("machine.Pin.OUT") == ['{"result":1}\n']

    def test_each_kind_of_value_word(self):
        """A value is decimal, 0x hex, negative, a float, a quoted string or a path under the object."""
        link = object_link()
        for word, expected in (
            ("16", "16"),
            ("0x10", "16"),
            ("-3", "-3"),
            ("2.5", "2.5"),
            ('"LED"', '"LED"'),
            ("'LED'", '"LED"'),
            ("machine.Pin.OUT", "1"),
        ):
            assert link.ask("machine.same " + word) == ['{"result":' + expected + "}\n"], word

    def test_a_named_value(self):
        """name=value reaches the function as a keyword, read like any other value."""
        link = object_link()
        assert link.ask("machine.pair 1 second=0x02") == ['{"result":[1,2]}\n']

    def test_a_method_through_its_class_takes_the_id_first(self):
        """machine.Pin.value 2 1 is Pin(2).value(1): the first value makes the object."""
        link = object_link()
        assert link.ask("machine.Pin.value 2 1") == ['{"result":null}\n']
        assert link.ask("machine.Pin.value 2") == ['{"result":1}\n']
        actual = object_error(link, "machine.Pin.value")
        expected = (BOARD_JSON.BAD_ARGS, "machine.Pin.value: the first value is the Pin's id")
        assert actual == expected, "no id, no object"

    def test_a_class_given_values_makes_the_object(self):
        """A class with values is constructed, and the instance answers as Python prints it."""
        link = object_link()
        assert link.ask("machine.Pin 7") == ['{"result":"Pin(7)"}\n']

    def test_a_write_then_two_reads(self):
        """A level set by one line is read by the next, twice in a row, and another pin is untouched."""
        # Arrange
        link = object_link()

        # Act
        link.ask("machine.Pin.value 2 1")
        actual = [link.ask("machine.Pin.value 2") for _ in range(2)]

        # Assert
        assert actual == [['{"result":1}\n']] * 2, "each line makes its object afresh"
        assert link.ask("machine.Pin.value 3") == ['{"result":0}\n']

    def test_nothing_is_evaluated(self):
        """Python syntax is only an unknown name or an invalid value, and a private name is not there."""
        link = object_link()
        unknown = BOARD_JSON.UNKNOWN_COMMAND
        assert object_error(link, "machine.freq()") == (unknown, "unknown name: machine.freq()")
        assert object_error(link, "machine._hidden") == (unknown, "unknown name: machine._hidden")
        assert object_error(link, "machine.Pin.__init__")[0] == unknown, "no dunder walk"
        assert object_error(link, "machine.same machine._hidden")[0] == unknown, "nor as a value"
        actual = object_error(link, "machine.same __import__('os')")
        assert actual == (BOARD_JSON.BAD_ARGS, "invalid value: __import__('os')")

    def test_a_value_that_is_not_one_is_refused(self):
        """A bare word is never a string, and a listing or a constant takes no values."""
        link = object_link()
        bad = BOARD_JSON.BAD_ARGS
        assert object_error(link, "machine.same on") == (bad, "invalid value: on")
        assert object_error(link, "machine freq") == (bad, "invalid value: freq")
        assert object_error(link, "machine 1") == (bad, "machine takes no values")
        assert object_error(link, "machine.Pin.OUT 1") == (bad, "machine.Pin.OUT takes no values")

    def test_a_failure_names_its_cause_and_the_link_recovers(self):
        """A missing name is -32601, a raising call -32603 naming its type, and the next line works."""
        link = object_link()
        actual = object_error(link, "machine.nosuch")
        assert actual == (BOARD_JSON.UNKNOWN_COMMAND, "unknown name: machine.nosuch")
        code, message = object_error(link, "machine.freq 1")
        assert code == BOARD_JSON.INTERNAL_ERROR, "the call raised"
        assert message.startswith("TypeError: "), message
        assert link.ask("machine.freq") == ['{"result":125000000}\n'], "the link answers after"
        link.ask("json off")
        assert link.ask("machine.nosuch") == ["ERR unknown name: machine.nosuch\r\n"]

    def test_a_json_request_carries_the_values_as_params(self):
        """A request's method is the path and its params the values; its id is echoed."""
        link = Link(objects={"machine": fake_machine()})
        assert link.ask('{"method":"machine.Pin.value","params":[2,1],"id":5}') == [
            '{"result":null,"id":5}\n'
        ]
        assert link.ask('{"cmd":"machine.Pin.value","args":"2","id":6}') == [
            '{"result":1,"id":6}\n'
        ]

    def test_only_the_name_or_the_name_and_a_dot_is_an_object_line(self):
        """A word that merely starts with the name is an unknown command, and a command's words are its own."""
        link = object_link()
        assert link.ask("machinery") == [
            '{"error":{"code":-32601,"message":"unknown command: machinery"}}\n'
        ]
        assert link.ask("echo machine.freq") == ['{"result":"machine.freq"}\n'], (
            "a command's arguments are not looked up"
        )

    def test_help_lists_an_object(self):
        """help names each object with how to use it, beside the commands."""
        link = Link(objects={"machine": fake_machine()})
        expected = (
            "a Python object: machine lists its names, machine.<name> {values} reads or calls one"
        )
        assert link.ask("help machine") == [expected + "\r\n"]
        assert "machine" in {line.split()[0] for line in link.ask("help")}

    def test_an_object_cannot_take_a_command_name(self):
        """An object named like a command, the table's or the monitor's own, is refused at construction."""
        for name in ("help", "echo"):
            with pytest.raises(ValueError, match="object name taken by a command: " + name):
                Link(objects={name: fake_machine()})

    def test_without_objects_a_path_is_an_unknown_command(self):
        """A monitor given no objects looks nothing up."""
        assert Link().ask("machine.freq") == ["ERR unknown command: machine.freq\r\n"]

    def test_the_board_code_evaluates_nothing(self):
        """No shared board file calls eval or exec: an object line is names and values."""
        for path in sorted((BOARDS_DIR / "micropython" / "firmware").glob("*.py")):
            calls = {
                node.func.id
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert not calls & {"eval", "exec", "compile", "__import__"}, path.name

    def test_each_board_marks_a_call_destructive(self):
        """Every MicroPython board profile's path entry is destructive, so an MCP agent runs it only after a yes; the listing is readonly."""
        for board in _MICROPYTHON_BOARDS:
            commands = load_profile(BOARDS_DIR / board.name / "profile.json")["commands"]
            names = _firmware_object_names(firmware_paths(board)[0])
            assert names, f"{board.name}: main.py puts an object on the link"
            for name in names:
                assert commands[name + "."]["safety"] == "destructive", f"{board.name}: {name}."
                assert commands[name]["safety"] == "readonly", f"{board.name}: {name}"


class TestMonitorLines:
    """The monitor gathers characters into lines and never waits for one (super-loop poll)."""

    def test_a_line_is_answered_when_its_ending_arrives(self):
        """Characters before the line ending only gather; the ending answers the line."""
        # Arrange
        link = Link()

        # Act
        gathered = [link.monitor.feed(char) for char in "num"]

        # Assert
        assert (gathered, link.out) == ([False, False, False], []), "no reply mid-line"
        assert link.monitor.feed("\r") is True
        assert link.out == ["3.14\r\n"]

    def test_each_line_ending_ends_a_line_once(self):
        """CR, LF and CRLF each end one line; CRLF's second character is not a second command."""
        link = Link(read=Waiting("num\rnum\nnum\r\n\r\n"))
        replies = [link.poll() for _ in range(4)]
        assert replies == [["3.14\r\n"]] * 3 + [[]], "three replies, and the blank line gets none"

    def test_poll_returns_at_once_without_a_whole_line(self):
        """With no line ending waiting, poll() returns with no reply and keeps what it read."""
        # Arrange
        waiting = Waiting("nu")
        link = Link(read=waiting)

        # Act / Assert
        assert link.poll() == [], "half a command answers nothing"
        assert link.poll() == [], "and so does an empty link"
        waiting.text = "m\r"
        assert link.poll() == ["3.14\r\n"], "the rest of the line completes it"

    def test_poll_answers_one_line_per_call(self):
        """Two commands waiting at once are answered on two polls, in order, and the third finds nothing."""
        link = Link(read=Waiting("num\rneg\r"))
        actual = [link.poll(), link.poll(), link.poll()]
        assert actual == [["3.14\r\n"], ["-42\r\n"], []]

    def test_poll_says_whether_it_answered(self):
        """poll() is True after answering a line and False when no whole line was waiting."""
        link = Link(read=Waiting("num\rnu"))
        assert [link.monitor.poll(), link.monitor.poll()] == [True, False]

    def test_run_answers_as_a_task_beside_another(self):
        """run() answers a burst of commands as an asyncio task, and another task gets its turns."""
        # Arrange
        link = Link(read=Waiting("num\rneg\recho hi\r"))
        turns: list[int] = []

        async def project() -> None:
            for turn in range(5):
                turns.append(turn)
                await asyncio.sleep(0)

        async def scenario() -> None:
            monitor = asyncio.create_task(link.monitor.run(interval=0))
            await project()
            for _ in range(10):
                await asyncio.sleep(0)
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor

        # Act
        asyncio.run(scenario())

        # Assert
        assert link.out == ["3.14\r\n", "-42\r\n", "hi\r\n"], "every command, in order"
        assert turns == [0, 1, 2, 3, 4], "the monitor never kept the loop to itself"

    def test_the_same_line_twice_answers_twice(self):
        """A command sent twice is answered twice: the buffer empties at each line ending."""
        link = Link(read=Waiting("echo hi\r\necho hi\r\n"))
        actual = [link.poll(), link.poll()]
        assert actual == [["hi\r\n"], ["hi\r\n"]]

    def test_a_line_of_line_max_is_taken(self):
        """A line of exactly LINE_MAX characters is answered whole."""
        words = "x" * (BOARD_JSON.LINE_MAX - len("echo "))
        link = Link(read=Waiting("echo " + words + "\r"))
        assert link.poll() == [words + "\r\n"]

    def test_a_line_that_never_ends_stays_bounded(self):
        """With no line ending the held line never passes LINE_MAX; the ending refuses it, and the next line works."""
        # Arrange -- a sustained run, four limits' worth, with no consumer of a line
        link = Link()
        for char in "y" * (4 * BOARD_JSON.LINE_MAX):
            link.monitor.feed(char)

        # Act
        held = len(link.monitor._chars)
        link.monitor.feed("\n")
        refused = list(link.out)

        # Assert
        assert held <= BOARD_JSON.LINE_MAX, "memory stays bounded however long the line runs"
        assert refused == ["ERR line too long\r\n"], "the line is refused once, at its ending"
        assert link.ask("num") == ["3.14\r\n"], "the monitor takes the next line"

    def test_a_long_line_in_json_framing_is_a_json_error(self):
        """In JSON framing the refusal is an envelope, -32700, with no id from an earlier request."""
        link = Link()
        link.ask('{"cmd":"num","id":5}')
        for char in "{" + "z" * BOARD_JSON.LINE_MAX + "\r":
            link.monitor.feed(char)
        assert link.out[-1] == '{"error":{"code":-32700,"message":"line too long"}}\n'


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


class FakeBus:
    """A board's bus for termapy_mem.Memory: 256 bytes of RAM at address 0, as mem_host.c's harness has.

    The word at FAULT_ADDR stands for one the platform cannot read, so a
    refusal inside MEM.M's lock can be told apart from the guard's before
    it.  Every access and every lock is recorded.
    """

    FAULT_ADDR = 0x40

    def __init__(self):
        self.ram = bytearray(256)
        self.accesses: list[tuple[str, int, int]] = []
        self.locks = 0
        self.depth = 0

    def read(self, addr: int, count: int) -> bytes | None:
        self.accesses.append(("read", addr, count))
        faulted = addr < self.FAULT_ADDR + 4 and self.FAULT_ADDR < addr + count
        if addr + count > len(self.ram) or faulted:
            return None
        return bytes(self.ram[addr : addr + count])

    def write(self, addr: int, data: bytes) -> bool:
        self.accesses.append(("write", addr, len(data)))
        if addr + len(data) > len(self.ram):
            return False
        self.ram[addr : addr + len(data)] = data
        return True

    def lock(self) -> str:
        self.locks += 1
        self.depth += 1
        return "irq state"

    def unlock(self, state: str) -> None:
        assert state == "irq state", "unlock gets back what lock returned"
        self.depth -= 1


def mem_link(guard=None, lock=True) -> tuple[Link, FakeBus]:
    """A monitor serving termapy_mem's commands over a fresh FakeBus, and the bus."""
    bus = FakeBus()
    locks = (bus.lock, bus.unlock) if lock else (None, None)
    memory = BOARD_MEM.Memory(bus.read, bus.write, *locks, guard=guard)
    return Link(native=memory.commands()), bus


def wire(link: Link, line: str) -> str:
    """One line's whole reply as it reached the wire."""
    return "".join(link.ask(line))


class TestMicroPythonMem:
    """MicroPython's termapy_mem.py answers the MEM wire spec as termapy_mem.c does, with no map of its own."""

    @pytest.mark.rule("mem-wire-cases-shared")
    @pytest.mark.parametrize(("line", "reason"), WIRE_ERRORS)
    def test_the_wire_refusals(self, line, reason):
        """Each refusal in the shared table is the one ERR line the C answers."""
        link, _bus = mem_link()
        assert wire(link, "MEM." + line) == f"ERR {reason}\r\n"

    def test_read_rows_of_sixteen(self):
        """A read is rows of sixteen bytes; the remainder is a shorter row."""
        link, _bus = mem_link()
        assert wire(link, "MEM.R 0 16") == row(0, bytes(16)) + "OK\r\n"
        assert wire(link, "MEM.R 0 20") == row(0, bytes(16)) + row(0x10, bytes(4)) + "OK\r\n", (
            "the remainder is a second row"
        )

    def test_write_then_read_back(self):
        """Written bytes read back; 0x, lower-case and spaced hex pairs are accepted."""
        # Arrange
        link, _bus = mem_link()

        # Act
        written = [wire(link, "MEM.W 0x10 DEADBEEF"), wire(link, "MEM.W 20 de ad")]

        # Assert
        assert written == ["OK\r\n", "OK\r\n"]
        assert wire(link, "MEM.R 10 4") == row(0x10, b"\xde\xad\xbe\xef") + "OK\r\n"
        assert wire(link, "MEM.R 0x20 2") == row(0x20, b"\xde\xad") + "OK\r\n"

    @pytest.mark.parametrize(
        ("addr", "old", "and_mask", "or_mask"),
        [
            (0x10, b"\xde\xad\xbe\xef", 0xFFFFFF00, 0x00000001),
            (0x20, b"\x34\x12", 0xFF00, 0x0001),
            (0x30, b"\x12", 0xF0, 0x0F),
        ],
        ids=["u32", "u16", "u8"],
    )
    def test_modify_masks_the_word_the_core_sees(self, addr, old, and_mask, or_mask):
        """MEM.M masks the word as the core loads it, replies the old word, and locks once."""
        # Arrange: the masks' digit count picks the width, as on the wire.
        link, bus = mem_link()
        digits = len(old) * 2
        wire(link, f"MEM.W {addr:X} {old.hex().upper()}")

        # Act
        pre = wire(link, f"MEM.M {addr:X} {and_mask:0{digits}X} {or_mask:0{digits}X}")

        # Assert
        assert pre == row(addr, old) + "OK\r\n", "MEM.M replies the PRE-modify word"
        expected = row(addr, masked(old, and_mask, or_mask)) + "OK\r\n"
        assert wire(link, f"MEM.R {addr:X} {len(old)}") == expected
        assert (bus.locks, bus.depth) == (1, 0), "locked once around the read-modify-write"

    def test_a_second_modify_starts_from_the_first(self):
        """Two MEM.M in a row: the second's before-word is what the first left."""
        # Arrange
        link, bus = mem_link()

        # Act
        first = wire(link, "MEM.M 10 FF 01")
        second = wire(link, "MEM.M 10 FF 02")

        # Assert
        assert first == row(0x10, b"\x00") + "OK\r\n"
        assert second == row(0x10, b"\x01") + "OK\r\n", "the first modify's result is the old byte"
        assert wire(link, "MEM.R 10 1") == row(0x10, b"\x03") + "OK\r\n"
        assert (bus.locks, bus.depth) == (2, 0), "each modify locks once and unlocks"

    def test_a_refusal_inside_the_lock_still_unlocks(self):
        """The platform refuses the word under the lock: ERR range, and the lock is released."""
        link, bus = mem_link()
        assert wire(link, "MEM.M 40 FFFFFFFF 00000000") == "ERR range\r\n"
        assert (bus.locks, bus.depth) == (1, 0), "a refused read must not leave IRQs masked"

    def test_info_reports_the_interface_and_no_map(self):
        """MEM.INFO is one JSON line in the C's order, with no regions: the board has no map."""
        # Arrange
        endian = "le" if sys.byteorder == "little" else "be"
        with_lock, _bus = mem_link()
        without_lock, _bus = mem_link(lock=False)

        # Act / Assert
        expected = f'{{"max_block": 64, "address_bits": 32, "endian": "{endian}", "modify": true}}'
        assert wire(with_lock, "MEM.INFO") == expected + "\r\nOK\r\n"
        assert '"modify"' not in wire(without_lock, "MEM.INFO"), "no lock, no MEM.M to offer"
        assert wire(without_lock, "MEM.M 0 FF 00") == "ERR usage\r\n", "as the C answers it"

    @pytest.mark.rule("board-mem-pure")
    def test_no_guard_reads_any_address(self):
        """With no guard, the address goes to the bus as given, with nothing in front of it."""
        link, bus = mem_link()
        assert wire(link, "MEM.R F0 16") == row(0xF0, bytes(16)) + "OK\r\n"
        assert wire(link, "MEM.R 100 1") == "ERR range\r\n", "only the platform itself refused"
        assert bus.accesses == [("read", 0xF0, 16), ("read", 0x100, 1)], "nothing but the asks"

    @pytest.mark.rule("board-mem-pure")
    def test_the_guard_sees_every_access_before_it_runs(self):
        """The guard is asked first, and a refusal is ERR range with the bus untouched."""
        # Arrange: a board rule that the top half is read-only.
        asked: list[tuple[int, int, bool]] = []

        def guard(addr: int, count: int, write: bool) -> bool:
            asked.append((addr, count, write))
            return not (write and addr + count > 0x80)

        link, bus = mem_link(guard=guard)

        # Act
        replies = [wire(link, "MEM.R 80 4"), wire(link, "MEM.W 7F 0102"), wire(link, "MEM.W 10 AB")]

        # Assert
        assert replies == [row(0x80, bytes(4)) + "OK\r\n", "ERR range\r\n", "OK\r\n"]
        assert asked == [(0x80, 4, False), (0x7F, 2, True), (0x10, 1, True)]
        assert bus.accesses == [("read", 0x80, 4), ("write", 0x10, 1)], (
            "the refused write never ran"
        )

    @pytest.mark.rule("board-mem-pure")
    def test_a_modify_the_guard_refuses_never_takes_the_lock(self):
        """MEM.M asks the guard to read and to write before masking IRQs."""
        # Arrange
        asked: list[tuple[int, int, bool]] = []

        def guard(addr: int, count: int, write: bool) -> bool:
            asked.append((addr, count, write))
            return not write

        link, bus = mem_link(guard=guard)

        # Act
        reply = wire(link, "MEM.M 10 FF 01")

        # Assert
        assert reply == "ERR range\r\n"
        assert asked == [(0x10, 1, False), (0x10, 1, True)], "asked as a read, then as a write"
        assert (bus.locks, bus.accesses) == (0, []), "nothing locked, nothing touched"

    @pytest.mark.rule("board-mem-native-text")
    def test_mem_answers_in_text_whatever_the_framing(self):
        """In JSON framing a MEM line still gets the spec's text, and the framing stays."""
        # Arrange
        link, _bus = mem_link()
        link.ask("json on")

        # Act
        replies = [wire(link, "MEM.R 0 1"), wire(link, "MEM.R 0 0"), wire(link, "echo hi")]

        # Assert
        assert replies[0] == row(0, b"\x00") + "OK\r\n", "rows and OK, CRLF, no envelope"
        assert replies[1] == "ERR length\r\n", "a refusal is the spec's ERR line"
        assert replies[2] == '{"result":"hi"}\n', "the next command is still answered in JSON"

    @pytest.mark.rule("board-mem-native-text")
    def test_a_json_request_gets_an_envelope(self):
        """A JSON request for a MEM verb gets the lines as its result, a refusal as its error."""
        link, _bus = mem_link()
        assert link.ask('{"method":"MEM.R","params":["0","1"],"id":7}') == [
            '{"result":["00000000: 00","OK"],"id":7}\n'
        ]
        assert link.ask('{"method":"MEM.R","params":"0 0","id":8}') == [
            '{"error":{"code":-32602,"message":"length"},"id":8}\n'
        ], "ERR length is the error envelope, a bad argument"

    def test_a_native_fault_is_err_text_or_internal_error(self):
        """A native handler that raises: ERR <message> to a plain line, -32603 to a JSON request."""

        # Arrange
        def broken(args):
            raise RuntimeError("bus fault")

        link = Link(native={"MEM.X": ("broken", broken)})

        # Act / Assert
        assert wire(link, "MEM.X") == "ERR bus fault\r\n"
        assert link.ask('{"method":"MEM.X","id":1}') == [
            '{"error":{"code":-32603,"message":"bus fault"},"id":1}\n'
        ]

    def test_help_lists_the_mem_commands(self):
        """help includes the native commands beside the table's and the monitor's own."""
        link, _bus = mem_link()
        names = {line.split()[0] for line in link.ask("help")}
        assert {"MEM.R", "MEM.W", "MEM.M", "MEM.INFO", "echo", "help"} <= names

    def test_a_native_name_cannot_take_another_table_s(self):
        """A native command named like a table's or the monitor's own is refused at construction."""
        entry = ("mine", lambda args: [])
        with pytest.raises(ValueError, match="command name in both tables: MEM.R"):
            BOARD_JSON.Monitor({"MEM.R": entry}, print, {"MEM.R": entry})
        with pytest.raises(ValueError, match="command name taken by the monitor: help"):
            BOARD_JSON.Monitor({}, print, {"help": entry})

    def test_widths_follow_the_direct_helpers(self):
        """Aligned words, then halves, then bytes: termapy_mem.c's DirectRead/Write access pattern."""
        assert list(BOARD_MEM.widths(0, 7)) == [(0, 4), (4, 2), (6, 1)]
        assert list(BOARD_MEM.widths(1, 8)) == [(0, 1), (1, 2), (3, 4), (7, 1)]
        assert list(BOARD_MEM.widths(2, 2)) == [(0, 2)], "an aligned half is one access"


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
        assert (
            "Unknown board: nosuch (use esp32s3, esp32s3-arduino, pico, pico-arduino)"
            in result.stderr
        )

    def test_board_with_a_config_is_a_usage_error(self, tmp_path):
        """--board names its own config, so a second one on the line exits 2."""
        result = self._run(tmp_path, "other.cfg", "--board", "pico")
        assert result.returncode == 2
        assert "--board cannot be combined" in result.stderr
