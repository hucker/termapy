"""Starter projects for real dev boards, one command away: the ``/board`` registry.

A starter turns a new dev board into a device termapy drives on day one,
interactively and over MCP: the code that runs on the board, and the host
config and device profile that find it and describe its commands.  It is
not the simulator: ``--demo`` fakes a port, a board starter talks to a real
one.

Each board is a row of ``BOARDS`` and a folder of package data beside this
file, ``<name>/``:

- ``firmware/`` -- the board's own device code, ASCII only (registry
  ``firmware-ascii-only``); ``/board.firmware`` copies it out for flashing.
- ``config.json`` -- the settings the board's config adds to a fresh
  ``config.new_config``, so a starter config is made the one way every
  config is (registry ``config-created-once``).
- ``profile.json`` -- the device profile, installed as the config's
  ``<name>.profile.json``, which the config's ``profile_path`` names so it
  loads at every connect.

A config made from a starter keeps copies of the profile and the device
code; ``/board.update`` replaces the copies that differ from what the
installed termapy ships (``stale_files``, ``replace_stale``).

Device code every board on a runtime shares belongs to the runtime, a row
of its own with one folder of it: ``MICROPYTHON``'s is
``micropython/firmware/`` here, and ``ARDUINO``'s is the ``/firmware`` kit's
C (``termapy/firmware/c/``), the monitor and the modules under it.  One copy,
never one per board.

A config connects to the board by its USB serial number, found when
``/board.new`` creates the config: the row's ``usb`` VID:PIDs pick out
the board running its runtime among the attached ports, and
``without_runtime_usb`` recognizes one attached but not running it (the
Pico's boot loader, an ESP32-S3 without MicroPython).  A new board is a
row and a folder, never a branch in a handler.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy.config import (
    config_files,
    config_summary,
    create_config,
    new_config,
    write_config_file,
)
from termapy.defaults import cfg_value
from termapy.firmware import copy_files
from termapy.folders import FIRMWARE, PROFILE_SUFFIX, ensure_folder

# The port list and the package's files are read where a command asks for
# them: ``/board`` loads at every start, and a one-shot that never lists
# ports should not pay for ``port_control`` (registry ``flag-paths-light``).
if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

    from termapy.port_control import PortSource
    from termapy.usb_tree import UsbSource


@dataclass(frozen=True)
class Runtime:
    """What a starter's device code runs on, and the device code every board on it shares.

    Attributes:
        name: Its name for people (``MicroPython``).
        folder: Where the shared files are, as folder names under the
            ``termapy`` package (``("boards", "micropython", "firmware")``).
        files: The shared device code, one copy for every board on it.
    """

    name: str
    folder: tuple[str, ...]
    files: tuple[str, ...] = ()


# The command monitor, the MEM commands and the XMODEM responder, on every
# MicroPython board.
MICROPYTHON = Runtime(
    "MicroPython",
    ("boards", "micropython", "firmware"),
    ("termapy_json.py", "termapy_mem.py", "termapy_xmodem.py"),
)

# The C command monitor and the modules under it, on every Arduino board: the
# /firmware kit's own files, so a fix there reaches the boards.  Arduino
# compiles every .c in the sketch's folder, beside the board's firmware.ino.
ARDUINO = Runtime(
    "Arduino",
    ("firmware", "c"),
    (
        "termapy_monitor.c",
        "termapy_monitor.h",
        "termapy_json.c",
        "termapy_json.h",
        "termapy_mem.c",
        "termapy_mem.h",
    ),
)


@dataclass(frozen=True)
class Board:
    """One board termapy ships a starter for.

    Attributes:
        name: What the user asks for (``pico``); also its package folder.
        title: The board's name for people (``Raspberry Pi Pico``).
        runtime: What the starter's device code runs on.
        usb: The ``VID:PID`` values the board enumerates as while it runs
            its runtime, uppercase hex; how ``/board.new`` finds it.
        without_runtime_usb: The ``VID:PID`` values it shows attached but
            not running its runtime, to say it needs flashing: a boot
            loader, or a USB port the chip has whatever it runs.
        firmware: The board's own device code in ``<name>/firmware/``, the
            one to start from first; the runtime's shared files follow it.
        flash: One line on getting the firmware onto the board.
        install: The shell command that puts copied firmware on a running
            board, with ``{port}``, and ``{files}`` or ``{folder}`` (the
            folder they were copied to), to fill in.
    """

    name: str
    title: str
    runtime: Runtime
    usb: tuple[str, ...]
    without_runtime_usb: tuple[str, ...] = ()
    firmware: tuple[str, ...] = ()
    flash: str = ""
    install: str = ""


BOARDS: tuple[Board, ...] = (
    # VID:PIDs from Raspberry Pi's own list, github.com/raspberrypi/usb-pid:
    # 0005 is the MicroPython firmware, 0003 the RP2040 boot loader; 000A is
    # the board running Arduino, the pico-arduino row's.
    Board(
        "pico",
        "Raspberry Pi Pico",
        MICROPYTHON,
        usb=("2E8A:0005",),
        without_runtime_usb=("2E8A:0003", "2E8A:000A"),
        firmware=("main.py",),
        flash=(
            "Flash MicroPython (RPI_PICO), then copy main.py, termapy_json.py, "
            "termapy_mem.py and termapy_xmodem.py to the board."
        ),
        install="uvx mpremote connect {port} cp {files} : + reset",
    ),
    # On the chip's own USB port.  4001 is MicroPython's TinyUSB serial port,
    # 0x4000 plus the CDC bit (MicroPython ports/esp32/mpconfigport.h); 1001 is
    # the chip's built-in USB Serial/JTAG, there whatever it runs and shared by
    # ten Espressif chips (Espressif's esp-pylib constants.py, esptool's
    # targets).  The USB-to-UART port's bridge has no serial number to find.
    Board(
        "esp32s3",
        "ESP32-S3 DevKitC-1",
        MICROPYTHON,
        usb=("303A:4001",),
        without_runtime_usb=("303A:1001",),
        firmware=("main.py",),
        flash=(
            "Flash MicroPython (ESP32_GENERIC_S3, the SPIRAM_OCT build on an octal-PSRAM "
            "module such as N8R8 or N16R8) with esptool, then copy main.py, "
            "termapy_json.py, termapy_mem.py and termapy_xmodem.py to the board."
        ),
        install="uvx mpremote connect {port} cp {files} : + reset",
    ),
    # The arduino-pico core (github.com/earlephilhower/arduino-pico) gives the
    # Pico 2E8A:000A, its boards.txt's rpipico.pid.0; 0005 is the board
    # running MicroPython instead, 0003 its boot loader.
    Board(
        "pico-arduino",
        "Raspberry Pi Pico",
        ARDUINO,
        usb=("2E8A:000A",),
        without_runtime_usb=("2E8A:0003", "2E8A:0005"),
        firmware=("firmware.ino",),
        flash=(
            "Install arduino-cli and the arduino-pico core (rp2040:rp2040), then build and "
            "upload the firmware folder; the first time, hold BOOTSEL while plugging the "
            "board in."
        ),
        install="arduino-cli compile --upload --fqbn rp2040:rp2040:rpipico --port {port} {folder}",
    ),
    # Arduino-ESP32 (github.com/espressif/arduino-esp32) in its default USB
    # mode, Hardware CDC and JTAG (boards.txt: esp32s3.build.usb_mode=1), with
    # USB CDC On Boot answers on the chip's built-in USB Serial/JTAG, 1001: the
    # port the chip has whatever it runs, so a bare S3 is found by it too.
    # 4001 is the board running MicroPython instead.
    Board(
        "esp32s3-arduino",
        "ESP32-S3 DevKitC-1",
        ARDUINO,
        usb=("303A:1001",),
        without_runtime_usb=("303A:4001",),
        firmware=("firmware.ino",),
        flash=(
            "Install arduino-cli and the Arduino-ESP32 core (esp32:esp32), then build and "
            "upload the firmware folder to the chip's own USB port; if the upload cannot "
            "connect, hold BOOT and press RESET first."
        ),
        install=(
            "arduino-cli compile --upload --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc "
            "--port {port} {folder}"
        ),
    ),
)


@dataclass(frozen=True)
class Attached:
    """What of a board is plugged in right now.

    Attributes:
        running: ``(device, serial number)`` of each port running the runtime.
        without_runtime: Whether a board is attached but not running it.
            The Pico's boot loader is a USB drive, not a serial port, so
            this comes from the USB tree, and is False where termapy cannot
            read one (macOS).
    """

    running: tuple[tuple[str, str], ...]
    without_runtime: bool


class AlreadyConfigured(Exception):
    """A config already connects to the board, so no second one is made.

    Attributes:
        path: The existing ``.cfg``.
        serial: The board's serial number it names.
    """

    def __init__(self, path: Path, serial: str) -> None:
        self.path = path
        self.serial = serial
        super().__init__(f"{path.stem} already connects to this board (serial number {serial})")


@dataclass(frozen=True)
class Created:
    """What ``create_board_config`` made.

    Attributes:
        path: The new ``.cfg``.
        port: The port value written into it (the serial number).
        device: The device it resolves to now (``COM5``).
        others: How many more running boards were attached and not chosen.
        firmware: The board's device code, copied into the config's
            ``firmware/`` folder.
    """

    path: Path
    port: str
    device: str
    others: int
    firmware: tuple[Path, ...] = ()


def find_board(name: str, boards: Sequence[Board] = BOARDS) -> Board:
    """The row ``name`` names.

    Raises:
        ValueError: ``Unknown board: X (use pico)``.
    """
    for board in boards:
        if board.name == name:
            return board
    known = ", ".join(sorted(board.name for board in boards))
    raise ValueError(f"Unknown board: {name} (use {known})")


def attached(
    board: Board,
    *,
    source: PortSource | None = None,
    usb_source: UsbSource | None = None,
) -> Attached:
    """Where the board shows up now: on a serial port running its runtime, or attached without it.

    Reads the port list the way ``/port.list`` does, opening nothing.  Only
    when no running board is found does it walk the USB tree for one
    without its runtime, so the common case never pays for the walk.

    Args:
        board: The row to look for.
        source: A substitute port list, as ``port_control`` takes it.
        usb_source: Substitute USB records, as ``usb_tree`` takes them.
    """
    from termapy.port_control import list_port_records

    running = tuple(
        (record["device"], record["serial_number"] or record["device"])
        for record in list_port_records(source=source)
        if record["vid_pid"] in board.usb
    )
    if running or not board.without_runtime_usb:
        return Attached(running, False)
    return Attached(running, _without_runtime(board, usb_source))


def _without_runtime(board: Board, usb_source: UsbSource | None) -> bool:
    """Whether any USB device is the board without its runtime; False where the tree is unreadable."""
    from termapy.usb_tree import UnsupportedPlatform, gather_usb_tree

    try:
        pending = list(gather_usb_tree(usb_source, facts_list=[]))
    except (UnsupportedPlatform, OSError):
        return False
    while pending:
        node = pending.pop()
        if (node.vid_pid or "").upper() in board.without_runtime_usb:
            return True
        pending.extend(node.children)
    return False


def not_attached_message(board: Board, found: Attached) -> str:
    """Why there is no running board to make a config for, and what to do."""
    if found.without_runtime:
        return f"{board.title} is attached but not running {board.runtime.name}. {board.flash}"
    usb = ", ".join(board.usb)
    return (
        f"No {board.title} running {board.runtime.name} found (USB {usb}). Plug it in; if it "
        f"is new, {board.flash[0].lower()}{board.flash[1:]}"
    )


def _package(board: Board) -> Traversable:
    """The board's folder of package data."""
    from importlib.resources import files

    return files(__name__) / board.name


def configs_for_serial(cfg_root: Path, serial: str) -> list[Path]:
    """The configs under ``cfg_root`` whose port names ``serial``, read without loading them.

    A port value is ``|``-separated candidates (``SN|COM5``); serial numbers
    compare case-insensitively, as ``port_control`` resolves them.
    ``config_summary`` parses each file in memory and writes nothing
    (registry ``reads-never-write``).
    """
    needle = serial.lower()
    return [
        path
        for path in config_files(cfg_root)
        if needle in (part.strip().lower() for part in config_summary(path)["port"].split("|"))
    ]


def board_config(board: Board, name: str, port: str) -> dict:
    """The board's config: a fresh ``new_config`` with the board's settings over it.

    Its ``profile_path`` names the profile beside it, so every frontend
    loads the board's commands at connect.
    """
    cfg = new_config(name, port=port)
    cfg.update(json.loads((_package(board) / "config.json").read_text(encoding="utf-8")))
    cfg["profile_path"] = profile_name(name)
    return cfg


def profile_name(config_name: str) -> str:
    """The file name of a config's profile, beside it: ``pico.profile.json``."""
    return f"{config_name}{PROFILE_SUFFIX}"


def profile_source(board: Board) -> Path:
    """Where the board's profile sits in the installed package."""
    return Path(str(_package(board) / "profile.json"))


def create_board_config(
    board: Board,
    cfg_root: Path,
    name: str = "",
    *,
    source: PortSource | None = None,
    usb_source: UsbSource | None = None,
) -> Created:
    """Create the board's config and profile, connecting by the attached board's serial number.

    Never replaces anything: a config by that name, or any config that
    already connects to this board, stops it before a file is written
    (decision 2026-10-25: board setup changes nothing that is already
    there).  The first running board in port order is chosen when several
    are attached; ``Created.others`` says how many were not.

    Args:
        board: The row to set up.
        cfg_root: The config directory (``termapy_cfg/``).
        name: The config's name; the board's name when empty.
        source: A substitute port list, as ``port_control`` takes it.
        usb_source: Substitute USB records, as ``usb_tree`` takes them.

    Returns:
        What was made.

    Raises:
        LookupError: No running board is attached; the message says why
            and what to do.
        AlreadyConfigured: A config already connects to this board.
        ConfigExistsError: A config by that name is already there.
        ValueError: ``name`` is not a valid config name.
        OSError: The config could not be written.
    """
    found = attached(board, source=source, usb_source=usb_source)
    if not found.running:
        raise LookupError(not_attached_message(board, found))
    device, serial = found.running[0]
    existing = configs_for_serial(cfg_root, serial)
    if existing:
        raise AlreadyConfigured(existing[0], serial)
    name = name or board.name
    path = create_config(cfg_root / name / f"{name}.cfg", board_config(board, name, serial))
    (path.parent / profile_name(name)).write_bytes(profile_source(board).read_bytes())
    # Everything for the board lives in its config's folder, its code too.
    firmware = copy_firmware(board, ensure_folder(path.parent / FIRMWARE))
    return Created(path, serial, device, len(found.running) - 1, tuple(firmware))


def firmware_paths(board: Board) -> list[Path]:
    """Where the board's device code sits in the installed package: its own, then its runtime's."""
    from importlib.resources import files

    own = _package(board) / "firmware"
    shared = files("termapy").joinpath(*board.runtime.folder)
    return [Path(str(own / name)) for name in board.firmware] + [
        Path(str(shared / name)) for name in board.runtime.files
    ]


# Arduino builds a folder whose main file is named for it, so a sketch is
# copied under the folder's name: firmware.ino in a config's firmware/.
SKETCH_SUFFIX = ".ino"


def copy_name(path: Path, folder: Path) -> str:
    """The name a shipped device file gets in ``folder``: its own, or the folder's for a sketch."""
    return f"{folder.name}{SKETCH_SUFFIX}" if path.suffix == SKETCH_SUFFIX else path.name


def copy_firmware(board: Board, folder: Path) -> list[Path]:
    """Write the board's device code into ``folder``; never over an existing file.

    Raises:
        FileExistsError: Its message is the path that already exists.
        OSError: The folder could not be created or a file not written.
    """
    return copy_files([(path, copy_name(path, folder)) for path in firmware_paths(board)], folder)


@dataclass(frozen=True)
class Stale:
    """A config's copy of a file the board's starter ships, which differs from the shipped one.

    Attributes:
        source: The file the installed termapy ships.
        destination: The config's copy, which ``replace_stale`` writes
            over; it may be missing.
    """

    source: Path
    destination: Path


def stale_files(board: Board, config_path: Path) -> list[Stale]:
    """The config's copies of the board's profile and device code that differ from what termapy ships.

    The profile is the config's ``<name>.profile.json``, the device code the
    copies in its ``firmware/``.  A missing copy counts; one identical byte
    for byte does not, so a config that is up to date has none.  It reads
    and writes nothing else (registry ``reads-never-write``).

    Args:
        board: The board the config is for.
        config_path: The config's ``.cfg``.

    Returns:
        The profile first if it differs, then the device code in
        ``firmware_paths`` order.
    """
    folder = config_path.parent
    pairs = [(profile_source(board), folder / profile_name(config_path.stem))]
    code = folder / FIRMWARE
    pairs += [(path, code / copy_name(path, code)) for path in firmware_paths(board)]
    return [
        Stale(source, destination)
        for source, destination in pairs
        if not destination.is_file() or _text(destination) != _text(source)
    ]


def _text(path: Path) -> bytes:
    """A shipped text file's bytes with CRLF read as LF.

    A git checkout on Windows can write the shipped files with CRLF, so a
    copy made from another checkout differs in line endings alone; that is
    not a change.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def replace_stale(stale: Sequence[Stale]) -> None:
    """Write each shipped file over the config's copy, byte for byte.

    Raises:
        OSError: A file could not be written.
    """
    for item in stale:
        if item.destination.parent.name == FIRMWARE:
            ensure_folder(item.destination.parent)
        item.destination.write_bytes(item.source.read_bytes())


def profile_revision(path: Path) -> str:
    """A profile file's ``profile_revision``, or ``""`` when it has none or cannot be read."""
    try:
        revision = json.loads(path.read_text(encoding="utf-8")).get("profile_revision", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return revision if isinstance(revision, str) else ""


def _config_as_written(config_path: Path) -> dict:
    """The config file's JSON as it is on disk, with no defaults or migration laid over it.

    Raises:
        OSError: The config could not be read.
        ValueError: It is not a JSON object.
    """
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError(f"not a JSON object: {config_path}")
    return cfg


# The settings in a board's config.json that its device code depends on, so
# /board.update brings them to a config made before they were added; the rest
# (title, buttons, on-connect commands) are the user's to change.
BOARD_KEYS = ("protocol",)


def missing_settings(board: Board, config_path: Path) -> dict[str, Any]:
    """The settings the config file needs from its starter: what ``apply_settings`` would write.

    An empty ``profile_path`` gets the profile beside it, and each of
    ``BOARD_KEYS`` that differs from the board's ``config.json`` gets the
    board's value.  Read from the file as written, which is what the next
    load reads.

    Raises:
        OSError: The config could not be read.
        ValueError: It is not a JSON object.
    """
    cfg = _config_as_written(config_path)
    settings: dict[str, Any] = {}
    if not cfg_value(cfg, "profile_path"):
        settings["profile_path"] = profile_name(config_path.stem)
    starter = json.loads((_package(board) / "config.json").read_text(encoding="utf-8"))
    for key in BOARD_KEYS:
        if cfg_value(cfg, key) != starter[key]:
            settings[key] = starter[key]
    return settings


def apply_settings(config_path: Path, settings: Mapping[str, Any]) -> None:
    """Write ``settings`` into the config file, read as written and written back by ``write_config_file``.

    Nothing the session changed reaches the file.

    Raises:
        OSError: The config could not be read or written.
        ValueError: It is not a JSON object.
    """
    cfg = _config_as_written(config_path)
    cfg.update(settings)
    write_config_file(config_path, cfg)


def install_command(board: Board, files: Sequence[Path], port: str) -> str:
    """The shell command that puts ``files`` on the board at ``port``, or ``""`` when it has none.

    Paths are written in full, quoted when they hold a space, so the line
    can be pasted into any shell from any folder.  ``{folder}`` is the folder
    the first file is in, for a toolchain that builds a folder (arduino-cli).
    """
    if not board.install:
        return ""

    def quoted(path: Path) -> str:
        return f'"{path}"' if " " in str(path) else str(path)

    folder = quoted(files[0].parent) if files else ""
    return board.install.format(
        port=port, files=" ".join(quoted(path) for path in files), folder=folder
    )
