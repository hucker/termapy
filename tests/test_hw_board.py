"""Real transfers and commands against an attached MicroPython board (registry ``hw-board-never-on-github``).

The X/Y/Z transfer tests elsewhere run against FakeSerial, a ``socket://``
rig, or lrzsz pipes -- none against real hardware.  These drive termapy's host
XMODEM and the board's own commands over a real USB-serial link to a
MicroPython board running the shipped firmware, and check a byte-identical
round trip plus the board's id, led, temp and MEM.INFO.  They prove the code
against an independent implementation on a real device, which FakeSerial (no OS
driver, no USB-CDC) structurally cannot.

Each test is parametrized over the MicroPython boards termapy ships: it finds
the board by the VID:PID in its ``BOARDS`` row among the enumerated ports,
reads its serial from the port list (the serial IS the board's identity), and
confirms the firmware is running by the ``id`` command (whose reply is the
board's ``machine.unique_id`` -- a prefix of the USB serial on the ESP32-S3, the
whole of it on the Pico).  A board not attached skips; one with stale firmware
(no ``xmodem`` command) skips telling the operator to copy it.  Gated: needs
``--run-hw-board``, never runs on GitHub, holds each port on one xdist worker.
"""

from __future__ import annotations

import io
import json
import time
from typing import TYPE_CHECKING, NoReturn

import pytest

from termapy.boards import BOARDS
from termapy.port_control import _gather_all_chip_facts
from termapy.vendor.xmodem import XMODEM

if TYPE_CHECKING:
    import serial

    from termapy.boards import Board

# One process may hold a given port (see test_hw_loopback.py for why).
pytestmark = [
    pytest.mark.slow,
    pytest.mark.hw_board,
    pytest.mark.xdist_group("hw_board"),
]

BAUD = 115200
BOOT_SETTLE_S = 1.5

# The MicroPython boards whose firmware carries the xmodem responder.
_MICROPYTHON_BOARDS = [board for board in BOARDS if board.runtime.name == "MicroPython"]


def _rig_unusable(config: pytest.Config, reason: str) -> NoReturn:
    """Skip, or fail when the run demanded a board with ``--require-hw-board``."""
    if config.getoption("--require-hw-board"):
        pytest.fail(
            f"{reason} -- --require-hw-board demands the attached board",
            pytrace=False,
        )
    pytest.skip(reason)


def _attached(board: Board):
    """The ``(device, serial)`` of ``board`` among the enumerated ports, or None.

    Found by the VID:PID in the board's ``usb`` tuple, exactly as the board
    system resolves a running board; the serial is read from the port list.
    """
    for facts in _gather_all_chip_facts(fast=True):
        if facts.vid_pid in board.usb:
            return facts.device, facts.serial
    return None


def _ask(port: serial.Serial, line: str, wait: float = 0.3) -> str:
    """Send a monitor command line and return its text reply."""
    port.write(line.encode() + b"\r")
    time.sleep(wait)
    return port.read(port.in_waiting or 1).decode(errors="replace").strip()


@pytest.fixture(params=_MICROPYTHON_BOARDS, ids=[board.name for board in _MICROPYTHON_BOARDS])
def board_port(request, pytestconfig: pytest.Config):
    """An open port to the parametrized board, confirmed to run the xmodem firmware.

    Identity is the board's serial (its ``id``), not an echo; a board answers
    the protocol, it does not echo.  The ``id`` reply is the board's
    ``unique_id`` -- the whole USB serial (Pico) or a prefix of it (ESP32-S3) --
    so it must be a prefix of the enumerated serial, not just equal to it.
    """
    import serial

    import termapy  # noqa: F401 -- registers the vendored pyserial

    board = request.param
    found = _attached(board)
    if found is None:
        _rig_unusable(pytestconfig, f"no {board.name} board attached (VID:PID {board.usb})")
    device, port_serial = found

    try:
        port = serial.Serial(device, BAUD, timeout=1.0)
    except (OSError, serial.SerialException) as e:
        _rig_unusable(pytestconfig, f"{device} ({board.name}) would not open: {e}")

    try:
        time.sleep(BOOT_SETTLE_S)
        port.reset_input_buffer()
        board_id = _ask(port, "id")
        if board_id.startswith("{"):
            # The firmware answers in its JSON framing when that is on (or the
            # default): the id is the reply's result.
            board_id = str(json.loads(board_id).get("result", ""))
        if not (port_serial and board_id and port_serial.startswith(board_id)):
            _rig_unusable(
                pytestconfig,
                f"{device} ({board.name}) answered 'id' with {board_id!r}, not a prefix of the "
                f"port serial {port_serial!r} -- not the termapy firmware",
            )
        help_line = _ask(port, "help xmodem")
        if "xmodem" not in help_line.lower() or "unknown" in help_line.lower():
            _rig_unusable(
                pytestconfig,
                f"the {board.name} has no 'xmodem' command -- copy the current firmware first: "
                "uvx mpremote connect <port> cp termapy_json.py termapy_xmodem.py : + reset",
            )
        yield board, port, board_id
    finally:
        port.close()


def _getc(port: serial.Serial):
    def getc(size: int, timeout: float = 1):
        port.timeout = timeout
        data = port.read(size)
        return data or None

    return getc


def _putc(port: serial.Serial):
    def putc(data: bytes, timeout: float = 1):
        return port.write(data)

    return putc


@pytest.mark.rule("hw-board-never-on-github")
def test_a_file_round_trips_through_the_board(board_port) -> None:
    """A file pushed to the board via XMODEM and pulled back is byte-identical.

    Push: the board receives (sends NAK to start, the host sends 128-byte
    blocks).  Pull: the board sends its buffer back.  The payload crosses a
    block boundary and does not end in 0x1A, so the host's padding strip
    leaves exactly the original.
    """
    # Arrange -- a payload that is not a block multiple and does not end in pad
    _board, port, _id = board_port
    payload = bytes(range(256)) * 3 + b"tail-not-pad\x00"
    getc, putc = _getc(port), _putc(port)

    # Act -- push: board receives
    port.reset_input_buffer()
    port.write(b"xmodem recv\r")
    time.sleep(0.2)
    pushed = XMODEM(getc, putc).send(io.BytesIO(payload), retry=16, timeout=3)
    time.sleep(0.4)
    port.read(port.in_waiting or 1)  # drain the handler's status line

    # Act -- pull: board sends its buffer back
    port.write(b"xmodem send\r")
    time.sleep(0.2)
    stream = io.BytesIO()
    XMODEM(getc, putc).recv(stream, retry=16, timeout=3)
    received = stream.getvalue().rstrip(b"\x1a")

    # Assert
    assert pushed is True, "the push to the board succeeded"
    assert received == payload, "the file survived the round trip to the board and back"


# -- The board's own commands, in JSON framing -------------------------------


def _json_ask(port: serial.Serial, line: str, wait: float = 0.3) -> str:
    """Send a command in JSON framing and return its one reply line (the first line)."""
    port.reset_input_buffer()
    port.write(line.encode() + b"\r")
    time.sleep(wait)
    reply = port.read(port.in_waiting or 1).decode(errors="replace")
    return reply.splitlines()[0] if reply.strip() else ""


@pytest.fixture
def board_json(board_port):
    """The parametrized board in JSON framing for a test, switched back afterwards."""
    _board, port, board_id = board_port
    _json_ask(port, "json on")
    yield port, board_id
    _json_ask(port, "json off")


class TestBoardCommands:
    """A board's own commands answer correctly over the real link, in JSON framing.

    These exercise the board's JSON-RPC reply path on real hardware: id is the
    board's unique id, led echoes the state it set, temp reads a plausible room
    value, and MEM.INFO reports the memory facts the host MEM spec needs.
    """

    def test_id_is_the_board_unique_id(self, board_json) -> None:
        """id answers with the board's unique id, a prefix of its USB serial."""
        port, board_id = board_json
        reply = json.loads(_json_ask(port, "id"))
        assert reply["result"] == board_id, "id is the board's unique id"

    def test_led_echoes_the_state_it_set(self, board_json) -> None:
        """led on then led off each answer with the state now in force."""
        port, _id = board_json
        on = json.loads(_json_ask(port, "led on"))
        off = json.loads(_json_ask(port, "led off"))
        assert (on["result"], off["result"]) == ("on", "off"), "led answers its new state"

    def test_temp_is_a_plausible_reading(self, board_json) -> None:
        """temp answers with a number in a sane range for a board at room temperature.

        The on-chip sensor reads a few degrees above ambient; 0 to 60 C is wide
        enough for a cold room or a warm enclosure without passing a broken
        reading (a disconnected sensor reads far outside it).
        """
        port, _id = board_json
        reply = json.loads(_json_ask(port, "temp"))
        celsius = reply["result"]
        assert isinstance(celsius, (int, float)), f"temp is a number, got {celsius!r}"
        assert 0.0 <= celsius <= 60.0, f"temp {celsius} C is outside a plausible room range"

    def test_mem_info_reports_the_memory_facts(self, board_json) -> None:
        """MEM.INFO answers with the board's memory shape the host MEM spec reads.

        MEM.INFO is a native command: its reply is a JSON object line followed
        by OK, not a JSON-RPC envelope, so the object is parsed directly.
        """
        port, _id = board_json
        facts = json.loads(_json_ask(port, "MEM.INFO"))
        assert facts["address_bits"] == 32, "a 32-bit address space"
        assert facts["endian"] == "le", "little-endian"
        assert facts["max_block"] >= 1, "a positive maximum block size"
        assert isinstance(facts["modify"], bool), "modify is a flag"


@pytest.fixture
def board_machine(board_json, pytestconfig: pytest.Config):
    """The board in JSON framing, confirmed to have ``machine`` on the link."""
    port, _id = board_json
    if "error" in json.loads(_json_ask(port, "help machine")):
        _rig_unusable(
            pytestconfig,
            "the board has no 'machine' object -- copy the current firmware first: "
            "uvx mpremote connect <port> cp main.py termapy_json.py : + reset",
        )
    return port


@pytest.mark.rule("board-object-by-name")
class TestBoardMachine:
    """MicroPython's real machine module answers by name over the real link.

    The unit tests run on CPython against a stand-in; these are the same
    lines against MicroPython's own ``dir``, ``getattr``, ``callable`` and
    native types, which a host cannot stand in for.  Nothing here drives a pin.
    """

    def test_machine_lists_the_module_names(self, board_machine) -> None:
        """machine alone answers the module's public names, sorted, Pin and freq among them."""
        names = json.loads(_json_ask(board_machine, "machine"))["result"]
        assert {"Pin", "ADC", "freq", "unique_id"} <= set(names), "what every port has"
        assert names == sorted(names), "sorted on the board, whose dir() keeps no order"
        assert not [name for name in names if name.startswith("_")], "nothing private"

    def test_a_class_lists_its_names(self, board_machine) -> None:
        """machine.Pin answers the class's names: its constants and its methods."""
        names = json.loads(_json_ask(board_machine, "machine.Pin"))["result"]
        assert {"IN", "OUT", "value"} <= set(names), "the pin modes and the level method"

    def test_a_function_answers_a_number_twice(self, board_machine) -> None:
        """machine.freq calls it and answers the CPU clock as a JSON number, the same on a second ask."""
        actual = [json.loads(_json_ask(board_machine, "machine.freq"))["result"] for _ in "12"]
        assert isinstance(actual[0], int) and actual[0] >= 1_000_000, f"a clock in Hz: {actual}"
        assert actual[0] == actual[1], "each line calls afresh"

    def test_a_constant_and_a_method_through_its_class(self, board_machine) -> None:
        """machine.Pin.OUT is read as a number, and machine.Pin.value 0 is Pin(0).value(), a level."""
        mode = json.loads(_json_ask(board_machine, "machine.Pin.OUT"))["result"]
        assert isinstance(mode, int), "a native class's constant is a value, not a call"
        level = json.loads(_json_ask(board_machine, "machine.Pin.value 0"))["result"]
        assert level in (0, 1), "the first value made the Pin; reading it sets no mode"

    def test_bytes_answer_as_the_id_command_does(self, board_machine) -> None:
        """machine.unique_id is bytes, answered as the uppercase hex the id command prints."""
        by_name = json.loads(_json_ask(board_machine, "machine.unique_id"))["result"]
        by_command = json.loads(_json_ask(board_machine, "id"))["result"]
        assert by_name == by_command, "one hex spelling for the same bytes"

    def test_a_missing_name_and_python_syntax_are_refused_and_the_board_recovers(
        self, board_machine
    ) -> None:
        """A name machine lacks is -32601, a call written as Python is too, and the next line answers."""
        for line in ("machine.nosuch", "machine.freq()"):
            error = json.loads(_json_ask(board_machine, line))["error"]
            assert error["code"] == -32601, line
            assert error["message"] == "unknown name: " + line, "nothing was evaluated"
        assert "result" in json.loads(_json_ask(board_machine, "machine.freq")), "still answering"
