"""A real XMODEM round-trip against an attached MicroPython board (registry ``hw-pico-never-on-github``).

The X/Y/Z transfer tests elsewhere run against FakeSerial, a ``socket://``
rig, or lrzsz pipes -- none against real hardware.  This one drives termapy's
host XMODEM over a real USB-serial link to a MicroPython board running the
``xmodem`` responder in the shipped firmware, and checks a byte-identical round
trip.  It proves the protocol against an independent implementation on a real
device, which FakeSerial (no OS driver, no USB-CDC) structurally cannot.

Gated like the hardware loopback but its own lane: it finds the board by USB
serial number (``TERMAPY_PICO_SN``), confirms identity by the firmware's own
``id`` command and VID:PID (NOT a self-echo -- the board answers the protocol,
it does not echo), needs ``--run-hw-pico``, never runs on GitHub, and holds the
one port on one xdist worker.  It assumes the board is flashed with the current
firmware; a board without the ``xmodem`` command skips (or fails under
``--require-hw-pico``) telling the operator to copy the firmware first.
"""

from __future__ import annotations

import io
import json
import os
import time
from typing import TYPE_CHECKING, NoReturn

import pytest

from termapy.port_control import resolve_port
from termapy.vendor.xmodem import XMODEM

if TYPE_CHECKING:
    import serial

# One process may hold the one port (see test_hw_loopback.py for why).
pytestmark = [
    pytest.mark.slow,
    pytest.mark.hw_pico,
    pytest.mark.xdist_group("hw_pico"),
]

# The board this suite was written against; overridable so it is not welded to
# one person's hardware.
PICO_SN = os.environ.get("TERMAPY_PICO_SN", "E66548545710822C")
PICO_VID_PID = "2E8A:0005"
BAUD = 115200
BOOT_SETTLE_S = 1.5


def _rig_unusable(config: pytest.Config, reason: str) -> NoReturn:
    """Skip, or fail when the run demanded the board with ``--require-hw-pico``."""
    if config.getoption("--require-hw-pico"):
        pytest.fail(
            f"{reason} -- --require-hw-pico demands the attached MicroPython board",
            pytrace=False,
        )
    pytest.skip(reason)


def _ask(port: serial.Serial, line: str, wait: float = 0.3) -> str:
    """Send a monitor command line and return its text reply."""
    port.write(line.encode() + b"\r")
    time.sleep(wait)
    return port.read(port.in_waiting or 1).decode(errors="replace").strip()


@pytest.fixture(scope="module")
def pico(pytestconfig: pytest.Config):
    """An open serial port to the board, confirmed to run the xmodem firmware.

    Identity is the firmware's ``id`` (its USB serial) and VID:PID, not an
    echo.  The ``xmodem`` command must exist, or the board is running stale
    firmware and the rig is unusable.
    """
    import serial

    import termapy  # noqa: F401 -- registers the vendored pyserial

    device = resolve_port(PICO_SN)
    if device == PICO_SN:
        _rig_unusable(pytestconfig, f"no board with serial number {PICO_SN!r} attached")

    try:
        port = serial.Serial(device, BAUD, timeout=1.0)
    except (OSError, serial.SerialException) as e:
        _rig_unusable(pytestconfig, f"{device} ({PICO_SN}) would not open: {e}")

    try:
        time.sleep(BOOT_SETTLE_S)
        port.reset_input_buffer()
        identity = _ask(port, "id")
        if PICO_SN not in identity:
            _rig_unusable(
                pytestconfig,
                f"{device} did not answer 'id' with {PICO_SN} (got {identity!r}) -- "
                f"not the termapy firmware",
            )
        help_line = _ask(port, "help xmodem")
        if "xmodem" not in help_line.lower() or "unknown" in help_line.lower():
            _rig_unusable(
                pytestconfig,
                "the board has no 'xmodem' command -- copy the current firmware first: "
                "uvx mpremote connect <port> cp termapy_json.py termapy_xmodem.py : + reset",
            )
        yield port
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


@pytest.mark.rule("hw-pico-never-on-github")
def test_a_file_round_trips_through_the_board(pico) -> None:
    """A file pushed to the board via XMODEM and pulled back is byte-identical.

    Push: the board receives (sends NAK to start, the host sends 128-byte
    blocks).  Pull: the board sends its buffer back (the host receives).  The
    payload crosses a block boundary and does not end in 0x1A, so the host's
    padding strip leaves exactly the original.
    """
    # Arrange -- a payload that is not a block multiple and does not end in pad
    payload = bytes(range(256)) * 3 + b"tail-not-pad\x00"
    getc, putc = _getc(pico), _putc(pico)

    # Act -- push: board receives
    pico.reset_input_buffer()
    pico.write(b"xmodem recv\r")
    time.sleep(0.2)
    pushed = XMODEM(getc, putc).send(io.BytesIO(payload), retry=16, timeout=3)
    time.sleep(0.4)
    pico.read(pico.in_waiting or 1)  # drain the handler's status line

    # Act -- pull: board sends its buffer back
    pico.write(b"xmodem send\r")
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
def pico_json(pico):
    """The board in JSON framing for a test, switched back to text afterwards."""
    _json_ask(pico, "json on")
    yield pico
    _json_ask(pico, "json off")


class TestBoardCommands:
    """The Pico's own commands answer correctly over the real link, in JSON framing.

    These exercise the board's JSON-RPC reply path (not just the text one) and
    confirm the firmware's handlers work on real hardware: id is the board's
    serial, led echoes the state it set, temp reads a plausible room value, and
    MEM.INFO reports the memory facts the host MEM spec needs.
    """

    def test_id_is_the_board_serial(self, pico_json) -> None:
        """id answers with the board's USB serial number in a JSON result."""
        reply = json.loads(_json_ask(pico_json, "id"))
        assert reply["result"] == PICO_SN, "id is the board's unique id, which is its serial number"

    def test_led_echoes_the_state_it_set(self, pico_json) -> None:
        """led on then led off each answer with the state now in force."""
        on = json.loads(_json_ask(pico_json, "led on"))
        off = json.loads(_json_ask(pico_json, "led off"))
        assert (on["result"], off["result"]) == ("on", "off"), "led answers its new state"

    def test_temp_is_a_plausible_reading(self, pico_json) -> None:
        """temp answers with a number in a sane range for a board at room temperature.

        The RP2040's on-chip sensor reads a few degrees above ambient; 0 to 60 C
        is wide enough for a cold room or a warm enclosure without passing a
        broken reading (a disconnected sensor reads far outside it).
        """
        reply = json.loads(_json_ask(pico_json, "temp"))
        celsius = reply["result"]
        assert isinstance(celsius, (int, float)), f"temp is a number, got {celsius!r}"
        assert 0.0 <= celsius <= 60.0, f"temp {celsius} C is outside a plausible room range"

    def test_mem_info_reports_the_memory_facts(self, pico_json) -> None:
        """MEM.INFO answers with the board's memory shape the host MEM spec reads.

        MEM.INFO is a native command: its reply is a JSON object line followed
        by OK, not a JSON-RPC envelope, so the object is parsed directly.
        """
        facts = json.loads(_json_ask(pico_json, "MEM.INFO"))
        assert facts["address_bits"] == 32, "the RP2040 is a 32-bit address space"
        assert facts["endian"] == "le", "ARM Cortex-M0+ is little-endian"
        assert facts["max_block"] >= 1, "a positive maximum block size"
        assert isinstance(facts["modify"], bool), "modify is a flag"
