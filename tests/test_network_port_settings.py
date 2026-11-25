"""A network port refuses what it cannot carry: socket:// the port settings and lines, any network port an inline delay.

pyserial's ``socket://`` gives only bytes (baud, frame, flow control, DTR,
RTS and break go nowhere; CTS, DSR and CD read 1), so those commands refuse
there instead of reporting success.  ``rfc2217://`` carries them, and a
device port or ``loop://`` is unaffected.  Neither network kind keeps an
inline ``~Nms`` delay, so ``/proto.send`` refuses one there.
"""

from __future__ import annotations

import copy
import json
import socket
import threading
import types
from collections.abc import Callable, Iterator

import pytest

from termapy.cli import CLITerminal
from termapy.config import hardware_signals
from termapy.defaults import DEFAULT_CFG
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.port_server import Bridge, ServeEntry
from termapy.repl import SETTING_NO_VALUE

pytestmark = [pytest.mark.sw_loopback, pytest.mark.rule("socket-port-settings-refused")]

LOCAL = "127.0.0.1"

# Every /port command that sets or reads what a socket:// port does not carry.
REFUSED_ON_SOCKET = [
    "/port.baud_rate 9600",
    "/port.byte_size 8",
    "/port.parity N",
    "/port.stop_bits 1",
    "/port.flow_control none",
    "/port.mode 9600 8N1",
    "/port.dtr on",
    "/port.rts on",
    "/port.cts",
    "/port.dsr",
    "/port.ri",
    "/port.cd",
    "/port.break",
]


class _TcpSink:
    """A local TCP server a ``socket://`` port connects to: it takes one client and discards what it sends."""

    def __init__(self) -> None:
        self._server = socket.create_server((LOCAL, 0))
        self.port = self._server.getsockname()[1]
        self._client: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """Accept one client and read until it goes."""
        try:
            self._client, _ = self._server.accept()
            while self._client.recv(4096):
                pass
        except OSError:
            return

    def close(self) -> None:
        """Close the client and the listener, and wait for the thread."""
        for sock in (self._client, self._server):
            if sock is not None:
                sock.close()
        self._thread.join(5)


@pytest.fixture
def rig(tmp_path) -> Iterator[Callable[..., CLITerminal]]:
    """A ``CLITerminal`` on the port named, its config at ``tmp_path/rig/rig.cfg``; connected unless asked not to."""
    terminals: list[CLITerminal] = []

    def make(port: str, *, connect: bool = True) -> CLITerminal:
        cfg = copy.deepcopy(DEFAULT_CFG)
        cfg["serial"]["port"] = port
        config_path = tmp_path / "rig" / "rig.cfg"
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_text(json.dumps(cfg))
        terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
        terminal.repl.global_root = tmp_path
        terminals.append(terminal)
        if connect:
            assert terminal._connect() is True, f"{port} opens"
        return terminal

    yield make
    for terminal in terminals:
        terminal._disconnect()


@pytest.fixture
def sink() -> Iterator[_TcpSink]:
    """A local TCP server for a ``socket://`` port."""
    server = _TcpSink()
    yield server
    server.close()


@pytest.fixture
def served_loop() -> Iterator[str]:
    """``loop://`` served over RFC 2217 on 127.0.0.1: the URL a client opens."""
    bridge = Bridge.open([ServeEntry("loop://", 0)], LOCAL, report=lambda _line: None)
    bridge.start()
    yield bridge.servers[0].url
    bridge.stop()


@pytest.mark.parametrize("line", REFUSED_ON_SOCKET)
def test_a_socket_port_refuses_its_settings_and_lines(rig, sink, line):
    """Each setting and line command fails with the socket:// reason instead of reporting success."""
    # Arrange
    terminal = rig(f"socket://{LOCAL}:{sink.port}")

    # Act
    actual = terminal.repl.dispatch_full(line)

    # Assert
    assert (actual.success, actual.error) == (False, STATE_MESSAGES["port_settings"]), (
        "refused with the reason, not a success that changed nothing"
    )


def test_with_no_port_open_a_setting_still_answers_its_value(rig):
    """No port open: a setting's bare query answers the config's value, and a line answers Not connected."""
    # Arrange
    terminal = rig("loop://", connect=False)
    expected = terminal.cfg["serial"]["baud_rate"]

    # Act
    actual_setting = terminal.repl.dispatch_full("/port.baud_rate")
    actual_line = terminal.repl.dispatch_full("/port.dtr on")

    # Assert
    assert (actual_setting.success, actual_setting.value) == (True, expected), (
        "the gate lets the query through while no port is open"
    )
    assert actual_line.error == STATE_MESSAGES["serial_connected"], (
        "the gate says Not connected. first"
    )


def test_a_device_port_keeps_its_settings_and_lines(rig):
    """On loop:// the same commands run as before."""
    # Arrange
    terminal = rig("loop://")

    # Act
    actual = [
        terminal.repl.dispatch_full(line) for line in ("/port.baud_rate 9600", "/port.dtr on")
    ]

    # Assert
    assert [result.success for result in actual] == [True, True], [
        result.error for result in actual
    ]


def test_rfc2217_carries_the_settings_and_lines(rig, served_loop):
    """An rfc2217:// port takes a baud rate and a line change: only socket:// refuses them."""
    # Arrange
    terminal = rig(served_loop)

    # Act
    actual = [
        terminal.repl.dispatch_full(line) for line in ("/port.baud_rate 9600", "/port.dtr off")
    ]

    # Assert
    assert [result.success for result in actual] == [True, True], [
        result.error for result in actual
    ]


def test_a_network_port_refuses_an_inline_delay_but_sends_without_one(rig, served_loop):
    """Over rfc2217:// /proto.send with a ~Nms delay refuses; the same bytes without it come back from loop://."""
    # Arrange
    terminal = rig(served_loop)

    # Act
    actual_delayed = terminal.repl.dispatch_full("/proto.send 01 ~10ms 02")
    actual_plain = terminal.repl.dispatch_full("/proto.send 01 02")

    # Assert
    assert actual_delayed.error == STATE_MESSAGES["local_timing"], (
        "a timing the network cannot keep"
    )
    assert (actual_plain.success, actual_plain.value) == (True, "0102"), "loop:// echoes the bytes"


def test_a_setting_button_reads_a_dash_on_a_socket_port(rig, sink, capsys):
    """A Baud button's read is refused quietly: the label shows - and nothing is printed."""
    # Arrange
    terminal = rig(f"socket://{LOCAL}:{sink.port}")
    capsys.readouterr()

    # Act
    actual = terminal.repl.setting_text("port.baud_rate cycle")

    # Assert
    assert actual == SETTING_NO_VALUE, "the label reads -, as with the port closed"
    assert capsys.readouterr().out == "", "no refusal line for a button's read"


def test_the_connect_banner_shows_no_socket_lines():
    """A socket:// port's fixed modem levels are left out of the banner; a device's are shown."""
    # Arrange
    levels = {"dtr": True, "rts": True, "cts": True, "dsr": True, "ri": False, "cd": True}
    socket_port = types.SimpleNamespace(port="socket://host:7000", **levels)
    device_port = types.SimpleNamespace(port="COM3", **levels)

    # Act
    actual_socket = hardware_signals(socket_port)
    actual_device = hardware_signals(device_port)

    # Assert
    assert actual_socket == "", "fixed readings would only mislead"
    assert actual_device == "DTR=1 RTS=1 CTS=1 DSR=1 RI=0 CD=1", "a device's levels as before"
