"""Dev-board starter projects: the ``/board`` table, finding a board, its config, its firmware.

``termapy.boards.BOARDS`` is one row per board with a folder of package
data beside it.  These tests pin that every row's files exist and agree
with each other (the device code's commands are the profile's), that a
board is found by its VID:PID on the serial-port list and, failing that,
in its boot loader on the USB tree, that its config is a fresh
``new_config`` connecting by the board's serial number, and that the
commands and ``--board`` reach all of it.  Port lists and USB trees are
injected (``source=`` / ``usb_source=``), so no test needs the board.
"""

from __future__ import annotations

import ast
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
)
from termapy.config import ConfigExistsError
from termapy.defaults import DEFAULT_CFG, default_cfg
from termapy.folders import PROFILE_SUFFIX
from termapy.plugins import CapabilitySet
from termapy.port_control import ChipFacts
from termapy.profile import load_profile, validate_profile
from termapy.repl import ReplEngine
from termapy.usb_tree import UsbRecord

PICO = find_board("pico")
BOARDS_DIR = Path(__file__).parent.parent / "src" / "termapy" / "boards"

# A Pico running the starter, as the port list reports it.
PICO_PORT = ChipFacts(device="COM5", serial="E66548545710822C", vid_pid="2E8A:0005")
# An FTDI adapter, a port that is not a Pico.
FTDI_PORT = ChipFacts(device="COM7", serial="BG03U7VTA", vid_pid="0403:6001")


def ports(*facts: ChipFacts):
    """A port source listing exactly ``facts``."""
    return lambda: list(facts)


def usb(*vid_pids: str):
    """A USB source with one device per VID:PID, no serial ports."""
    return lambda: [
        UsbRecord(path=f"1-{index}", vid_pid=vid_pid) for index, vid_pid in enumerate(vid_pids)
    ]


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
        """The profile's commands are exactly the device code's COMMANDS table: the two cannot drift."""
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
            profile = set(load_profile(BOARDS_DIR / board.name / "profile.json")["commands"])
            assert profile == firmware, f"{board.name}: profile and firmware disagree"

    def test_find_board_rejects_an_unknown_name(self):
        """An unknown board is a lookup failure that lists the boards."""
        with pytest.raises(ValueError, match=r"Unknown board: nosuch \(use pico\)"):
            find_board("nosuch")


class TestAttached:
    def test_a_running_board_is_found_by_vid_pid(self):
        """The Pico's port is picked out of the list by VID:PID, with its serial number."""
        found = attached(PICO, source=ports(FTDI_PORT, PICO_PORT), usb_source=usb())
        assert found.running == (("COM5", "E66548545710822C"),)
        assert found.booting is False, "a running board is not looked for in the boot loader"

    def test_a_board_in_its_boot_loader_is_seen_on_the_usb_tree(self):
        """No running board, but its boot loader on the USB tree: attached, not flashed."""
        found = attached(PICO, source=ports(FTDI_PORT), usb_source=usb("2E8A:0003"))
        assert found.running == ()
        assert found.booting is True

    def test_nothing_attached(self):
        """Neither on the port list nor on the USB tree: not attached."""
        found = attached(PICO, source=ports(FTDI_PORT), usb_source=usb("0403:6001"))
        assert (found.running, found.booting) == ((), False)


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
        with pytest.raises(LookupError, match="boot loader.*Flash MicroPython"):
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
        assert result.error == "Unknown board: nosuch (use pico)"

    def test_board_new_without_a_board_attached_fails(self, env: Env, tmp_path):
        """/board.new with no Pico attached fails, naming the board, and creates no config."""
        result = env.engine.dispatch("board.new pico")
        assert not result.success
        assert "Raspberry Pi Pico" in result.error
        assert not (tmp_path / "termapy_cfg" / "pico").exists()

    def test_board_firmware_copies_the_device_code(self, env: Env, tmp_path):
        """/board.firmware writes the device code into the folder and names the flashing step."""
        folder = tmp_path / "fw"

        result = env.engine.dispatch(f"board.firmware pico folder={folder}")

        assert result.success, result.error
        assert (folder / "main.py").read_bytes() == firmware_paths(PICO)[0].read_bytes()
        assert any("Flash MicroPython" in line for line in env.output), "the next step is named"


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
        assert "Unknown board: nosuch (use pico)" in result.stderr

    def test_board_with_a_config_is_a_usage_error(self, tmp_path):
        """--board names its own config, so a second one on the line exits 2."""
        result = self._run(tmp_path, "other.cfg", "--board", "pico")
        assert result.returncode == 2
        assert "--board cannot be combined" in result.stderr
