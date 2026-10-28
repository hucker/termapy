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
  ``<name>.profile.json`` so it loads with the config.

Device code every board on a runtime shares belongs to the runtime, a row
of its own (``MICROPYTHON``) with a folder of the same shape
(``micropython/firmware/``): one copy, never one per board.

A config connects to the board by its USB serial number, found when
``/board.new`` creates the config: the row's ``usb`` VID:PIDs pick out
the board running its runtime among the attached ports, and
``without_runtime_usb`` recognizes one attached but not running it (the
Pico's boot loader, an ESP32-S3 without MicroPython).  A new board is a
row and a folder, never a branch in a handler.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import config_summary, create_config, new_config
from termapy.firmware import copy_files
from termapy.folders import FIRMWARE, PROFILE_SUFFIX, ensure_folder
from termapy.port_control import PortSource, list_port_records

if TYPE_CHECKING:
    from termapy.usb_tree import UsbSource


@dataclass(frozen=True)
class Runtime:
    """What a starter's device code runs on, and the device code every board on it shares.

    Attributes:
        name: Its name for people (``MicroPython``).
        folder: Its package folder beside the boards' (``micropython``);
            the shared files are in its ``firmware/``.
        files: The shared device code, one copy for every board on it.
    """

    name: str
    folder: str
    files: tuple[str, ...] = ()


MICROPYTHON = Runtime("MicroPython", "micropython", ("termapy_json.py",))


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
        runtime_extras: Shared files of the runtime that only some boards
            use, after its own (``termapy_mem.py``, the MEM commands); a
            file every board on the runtime uses belongs in ``Runtime.files``.
        flash: One line on getting the firmware onto the board.
        install: The shell command that puts copied firmware on a running
            board, with ``{port}`` and ``{files}`` to fill in.
    """

    name: str
    title: str
    runtime: Runtime
    usb: tuple[str, ...]
    without_runtime_usb: tuple[str, ...] = ()
    firmware: tuple[str, ...] = ()
    runtime_extras: tuple[str, ...] = ()
    flash: str = ""
    install: str = ""


BOARDS: tuple[Board, ...] = (
    # VID:PIDs from Raspberry Pi's own list, github.com/raspberrypi/usb-pid:
    # 0005 is the MicroPython firmware, 0003 the RP2040 boot loader.
    Board(
        "pico",
        "Raspberry Pi Pico",
        MICROPYTHON,
        usb=("2E8A:0005",),
        without_runtime_usb=("2E8A:0003",),
        firmware=("main.py",),
        flash="Flash MicroPython (RPI_PICO), then copy main.py and termapy_json.py to the board.",
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
        runtime_extras=("termapy_mem.py",),
        flash=(
            "Flash MicroPython (ESP32_GENERIC_S3, the SPIRAM_OCT build on an octal-PSRAM "
            "module such as N8R8 or N16R8) with esptool, then copy main.py, "
            "termapy_json.py and termapy_mem.py to the board."
        ),
        install="uvx mpremote connect {port} cp {files} : + reset",
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


def _package(board: Board):
    """The board's folder of package data."""
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
        for path in sorted(cfg_root.glob("*/*.cfg"))
        if needle in (part.strip().lower() for part in config_summary(path)["port"].split("|"))
    ]


def board_config(board: Board, name: str, port: str) -> dict:
    """The board's config: a fresh ``new_config`` with the board's settings over it."""
    cfg = new_config(name, port=port, auto_connect=True)
    cfg.update(json.loads((_package(board) / "config.json").read_text(encoding="utf-8")))
    return cfg


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
    profile = (_package(board) / "profile.json").read_bytes()
    (path.parent / f"{name}{PROFILE_SUFFIX}").write_bytes(profile)
    # Everything for the board lives in its config's folder, its code too.
    firmware = copy_firmware(board, ensure_folder(path.parent / FIRMWARE))
    return Created(path, serial, device, len(found.running) - 1, tuple(firmware))


def firmware_paths(board: Board) -> list[Path]:
    """Where the board's device code sits in the installed package: its own, then its runtime's."""
    own = _package(board) / "firmware"
    shared = files(__name__) / board.runtime.folder / "firmware"
    return [Path(str(own / name)) for name in board.firmware] + [
        Path(str(shared / name)) for name in board.runtime.files + board.runtime_extras
    ]


def copy_firmware(board: Board, folder: Path) -> list[Path]:
    """Write the board's device code into ``folder``; never over an existing file.

    Raises:
        FileExistsError: Its message is the path that already exists.
        OSError: The folder could not be created or a file not written.
    """
    return copy_files([(path, path.name) for path in firmware_paths(board)], folder)


def install_command(board: Board, files: Sequence[Path], port: str) -> str:
    """The shell command that puts ``files`` on the board at ``port``, or ``""`` when it has none.

    Paths are written in full, quoted when they hold a space, so the line
    can be pasted into any shell from any folder.
    """
    if not board.install:
        return ""
    quoted = " ".join(f'"{path}"' if " " in str(path) else str(path) for path in files)
    return board.install.format(port=port, files=quoted)
