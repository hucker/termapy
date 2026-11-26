"""termapy --serve's core: its entries, a bridge over RFC 2217, one client at a time, a lost port reopened, every thread joined.

The bridge is driven through pyserial's own ``rfc2217://`` client, the one
termapy opens a network port with, against ports on 127.0.0.1 the system
picks (TCP port 0).  The served ports are ``loop://`` and the DEMO
simulator, so nothing here needs hardware; ``tests/test_hw_loopback.py``
serves the jumpered adapter.
"""

from __future__ import annotations

import errno
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator

import pytest
import serial
from serial import SerialException

from termapy import port_server
from termapy.config import open_serial
from termapy.demo import FakeSerial
from termapy.port_allow import PortAllowlist, PortPolicy
from termapy.port_server import (
    KEEPALIVE_IDLE_S,
    KEEPALIVE_INTERVAL_S,
    KEEPALIVE_PROBES,
    Bridge,
    PortServer,
    ServeEntry,
    ServeError,
    _detach_flags,
    listen_choices,
    parse_entries,
    port_cfg,
)

pytestmark = pytest.mark.sw_loopback

LOCAL = "127.0.0.1"


def _until(condition: Callable[[], bool], seconds: float = 5.0) -> bool:
    """Wait for ``condition`` rather than for a guessed time; whether it came true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _client(server: PortServer, **settings) -> serial.SerialBase:
    """termapy's network client on ``server``: pyserial's ``rfc2217://``, as a config's port opens it."""
    return serial.serial_for_url(server.url, timeout=1, **settings)


def _read(port: serial.SerialBase, count: int, seconds: float = 3.0) -> bytes:
    """Read until ``count`` bytes have come or ``seconds`` are up."""
    received = b""
    deadline = time.monotonic() + seconds
    while len(received) < count and time.monotonic() < deadline:
        received += port.read(count - len(received))
    return received


def _refused(server: PortServer) -> bool:
    """Whether a client that tries ``server`` is turned away: its connection fails, or what it sends never comes back.

    A refused client is accepted and closed at once, which pyserial reports
    as a failed open or a failed write, depending on when it notices.
    """
    try:
        port = _client(server)
    except OSError:
        return True
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            port.write(b"x")
            if port.read(1) == b"x":
                return False
        return True
    except OSError:
        return True
    finally:
        try:
            port.close()
        except OSError:
            pass


def _serve_threads() -> list[str]:
    """The names of the bridge threads alive in this process."""
    return [
        thread.name for thread in threading.enumerate() if thread.name.startswith("termapy-serve")
    ]


@pytest.fixture
def served() -> Iterator[Callable[..., tuple[Bridge, list[str]]]]:
    """Open and start a bridge, on 127.0.0.1 unless ``host`` says; every one made is stopped after the test."""
    bridges: list[Bridge] = []

    def serve(*ports: str, opener=None, policy=None, host=LOCAL) -> tuple[Bridge, list[str]]:
        events: list[str] = []
        bridge = Bridge.open(
            [ServeEntry(port, 0) for port in ports],
            host,
            report=events.append,
            opener=opener,
            policy=policy,
        )
        bridges.append(bridge)
        bridge.start()
        return bridge, events

    yield serve
    for bridge in bridges:
        bridge.stop()


class _Flaky(FakeSerial):
    """The DEMO simulator as a port that can be unplugged: its reads and status fail while ``gone`` is set."""

    gone = False
    # How often the port's status was asked: an idle server's check counts here.
    checks = 0

    def read(self, size: int = 1) -> bytes:
        """Fail as a vanished USB device does, else read the simulator."""
        if self.gone:
            raise SerialException("device reports readiness to read but returned no data")
        return super().read(size)

    @property
    def in_waiting(self) -> int:
        """Fail as a vanished device's status call does (Windows' ClearCommError), else the simulator's."""
        self.checks += 1
        if self.gone:
            raise SerialException(
                "ClearCommError failed (PermissionError(13, 'Access is denied.'))"
            )
        return super().in_waiting


class _VanishedPeer:
    """A client connection whose far end went away without closing: keepalive gave up, so every receive fails with ETIMEDOUT."""

    def __init__(self) -> None:
        self.closed = False

    def settimeout(self, seconds: float) -> None:
        """Accepted, as a real socket does."""

    def setsockopt(self, level: int, option: int, value: int) -> None:
        """Accepted, as a real socket does."""

    def sendall(self, data: bytes) -> None:
        """The negotiation goes out before anything is known to be wrong."""

    def recv(self, size: int) -> bytes:
        """What the OS reports once its keepalive probes go unanswered."""
        raise TimeoutError(errno.ETIMEDOUT, "the peer did not answer keepalive probes")

    def shutdown(self, how: int) -> None:
        """Accepted, as a real socket does."""

    def close(self) -> None:
        """Record that the server let the connection go."""
        self.closed = True


class _Opener:
    """A stand-in for ``config.open_serial``: hands out ``_Flaky`` ports, and refuses while ``refusing``."""

    def __init__(self) -> None:
        self.opened: list[_Flaky] = []
        self.refusing = False

    def __call__(self, cfg: dict, *, policy=None) -> _Flaky:
        """Open the next port, or fail as an absent device does."""
        if self.refusing:
            raise SerialException(f"could not open port {cfg['serial']['port']}")
        port = _Flaky(port="COM9")
        self.opened.append(port)
        return port


@pytest.mark.rule("serve-detaches")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-creation flags")
def test_a_detached_server_starts_with_no_window():
    """A server put in the background opens no window: no console shown, a group of its own, never DETACHED_PROCESS.

    Windows ignores CREATE_NO_WINDOW beside DETACHED_PROCESS, and a detached venv
    launcher left its interpreter to open a console window of its own.
    """
    # Arrange -- the skipif keeps this to Windows; the assert says so to the type checker,
    # which reads no marker and knows no Windows-only subprocess flags elsewhere
    assert sys.platform == "win32", "Windows only"

    # Act
    actual = _detach_flags()

    # Assert
    assert actual & subprocess.CREATE_NO_WINDOW, "a console never shown"
    assert actual & subprocess.CREATE_NEW_PROCESS_GROUP, (
        "a Ctrl+C where it was typed never reaches it"
    )
    assert not actual & subprocess.DETACHED_PROCESS, "which would cancel the no-window flag"


class TestEntries:
    def test_unnumbered_ports_take_2217_and_up(self):
        """Each unnumbered entry takes the next TCP port from 2217, in the order given."""
        actual = parse_entries(["COM3", "COM4"])
        assert actual == [ServeEntry("COM3", 2217), ServeEntry("COM4", 2218)]

    def test_a_numbered_port_keeps_its_number_and_the_rest_skip_it(self):
        """=TCPPORT names the number; the unnumbered entries take the free ones around it."""
        actual = parse_entries(["COM3=2218", "COM4", "COM5"])
        expected = [ServeEntry("COM3", 2218), ServeEntry("COM4", 2217), ServeEntry("COM5", 2219)]
        assert actual == expected

    def test_only_digits_after_the_last_equals_are_a_number(self):
        """A port name that holds = stays whole unless digits follow the last one."""
        actual = parse_entries(["rfc2217://bench:2217?logging=debug", "loop://?x=1=2300"])
        expected = [
            ServeEntry("rfc2217://bench:2217?logging=debug", 2217),
            ServeEntry("loop://?x=1", 2300),
        ]
        assert actual == expected

    @pytest.mark.parametrize(
        ("words", "message"),
        [
            (["COM3=2300", "COM4=2300"], "TCP port named twice: 2300"),
            (["COM3", "com3"], "Port named twice: com3"),
            (["COM3=70000"], "Invalid TCP port: 70000"),
            (["COM3=0"], "Invalid TCP port: 0"),
            (["spy://COM3?file=a.txt"], "Invalid port: spy://COM3?file=a.txt"),
            ([""], "Invalid port: an empty entry"),
        ],
    )
    def test_a_bad_entry_is_refused(self, words, message):
        """A doubled TCP port or serial port, a number outside 1-65535, spy:// or nothing is refused before anything opens."""
        with pytest.raises(ValueError) as refused:
            parse_entries(words)
        assert str(refused.value).startswith(message), "the message names what was wrong"


@pytest.mark.rule("serve-listen-named")
def test_the_listen_choices_start_with_localhost_and_leave_out_the_unreachable():
    """localhost first, then the default route's address when there is one; never loopback or link-local."""
    # Act
    actual = listen_choices()

    # Assert
    assert actual[0] == ("localhost", "this machine only"), "this machine only, first"
    rest = [address for address, _ in actual[1:]]
    assert not [a for a in rest if a.startswith(("127.", "169.254."))], (
        "nothing else can reach those"
    )
    marked = [address for address, note in actual if note.startswith("the default route")]
    assert marked == rest[:1] or not marked, "the default route's address, if any, comes next"


class TestBridge:
    @pytest.mark.rule("serve-listen-named")
    def test_localhost_listens_on_every_address_it_names(self, served):
        """Each address localhost names takes a connection, so a client never waits out one nothing listens on."""
        # Arrange -- on Windows a refused ::1 cost every rfc2217://localhost open two seconds
        bridge, _ = served("loop://", host="localhost")
        port = bridge.servers[0].tcp_port
        addresses = sorted(
            {
                str(info[4][0])
                for info in socket.getaddrinfo("localhost", port, type=socket.SOCK_STREAM)
            }
        )

        # Act
        actual = {}
        for address in addresses:
            try:
                socket.create_connection((address, port), timeout=1).close()
                actual[address] = True
            except OSError:
                actual[address] = False

        # Assert
        assert actual == dict.fromkeys(addresses, True), "every address of localhost listens"

    def test_bytes_and_settings_cross_the_network(self, served):
        """What a client writes reaches the port and comes back, and a baud change it makes reaches the served port."""
        # Arrange
        bridge, _ = served("loop://")
        server = bridge.servers[0]
        client = _client(server, baudrate=9600)

        # Act
        client.write(b"over rfc2217")
        echoed = _read(client, 12)
        client.baudrate = 57600

        # Assert
        assert echoed == b"over rfc2217", "loop:// echoes, through both pumps"
        assert _until(lambda: server._port.baudrate == 57600), "the client's setting is the port's"
        client.close()

    @pytest.mark.rule("serve-opens-like-a-session")
    def test_termapys_own_opener_reaches_a_served_simulator(self, served):
        """A config whose port is the server's URL opens through config.open_serial, and the DEMO simulator answers AT."""
        # Arrange
        bridge, _ = served("DEMO")

        # Act
        port = open_serial(port_cfg(bridge.servers[0].url))
        try:
            port.write(b"AT\r")
            reply = _read(port, 4)
        finally:
            port.close()

        # Assert
        assert reply == b"OK\r\n", "the simulator's answer, carried back over the network"

    def test_two_ports_are_served_apart(self, served):
        """Each served port has its own TCP port, and what goes to one never reaches the other."""
        # Arrange
        bridge, _ = served("loop://", "DEMO")
        loop, demo = (_client(server) for server in bridge.servers)

        # Act
        loop.write(b"one")
        demo.write(b"AT\r")
        actual = (_read(loop, 3), _read(demo, 4), loop.read(1))

        # Assert
        assert actual == (b"one", b"OK\r\n", b""), "each port answers its own client alone"
        assert bridge.servers[0].tcp_port != bridge.servers[1].tcp_port, "a TCP port each"
        loop.close()
        demo.close()

    def test_a_second_client_is_refused_and_the_first_works_on(self, served):
        """Twice: while one client holds the port another is refused at once; after it leaves, a new one works."""
        # Arrange
        bridge, events = served("loop://")
        server = bridge.servers[0]

        for _ in range(2):
            holder = _client(server)

            # Act
            refused = _refused(server)
            holder.write(b"still mine")
            actual = _read(holder, 10)
            holder.close()

            # Assert
            assert refused, "a second client is turned away"
            assert actual == b"still mine", "the refusal left the holder's session alone"
            assert _until(lambda: server._client is not None and not server._client.alive)
        refusals = [event for event in events if "refused" in event]
        assert len(refusals) == 2, "one refusal per round, each said"
        assert "in use by 127.0.0.1" in refusals[0], "the refusal says who holds the port"

    @pytest.mark.rule("serve-opens-like-a-session")
    def test_the_client_is_named_while_connected_and_each_change_is_told(self):
        """client_address is the client's address while it is connected and empty after; on_change hears both."""
        # Arrange
        changes: list[str] = []
        holder: list[Bridge] = []
        bridge = Bridge.open(
            [ServeEntry("loop://", 0)],
            LOCAL,
            report=lambda line: None,
            on_change=lambda: changes.append(holder[0].servers[0].client_address),
        )
        holder.append(bridge)
        bridge.start()
        server = bridge.servers[0]

        try:
            for _ in range(2):
                # Act
                client = _client(server)
                connected = _until(lambda: server.client_address == LOCAL)
                client.close()
                left = _until(lambda: server.client_address == "")

                # Assert
                assert connected, "the client's address while it is connected"
                assert left, "and none once it has left"
        finally:
            bridge.stop()
        assert changes[:4] == [LOCAL, "", LOCAL, ""], (
            "the breadcrumb is told at each connect and leave"
        )

    @pytest.mark.rule("serve-pumps-joined")
    def test_a_client_connection_keeps_alive_at_the_set_timings(self, served):
        """Each accepted connection has TCP keepalive on, probing after KEEPALIVE_IDLE_S of silence, as far as the OS reports it."""
        # Arrange
        bridge, _ = served("loop://")
        server = bridge.servers[0]
        client = _client(server)
        assert _until(lambda: server.client_address == LOCAL), "connected"
        assert server._client is not None
        conn = server._client.conn

        # Act
        actual = {"keepalive": bool(conn.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))}
        for name, option in (
            (
                "idle",
                getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None),
            ),
            ("interval", getattr(socket, "TCP_KEEPINTVL", None)),
            ("probes", getattr(socket, "TCP_KEEPCNT", None)),
        ):
            if option:
                actual[name] = conn.getsockopt(socket.IPPROTO_TCP, option)
        client.close()

        # Assert
        expected = {
            "keepalive": True,
            "idle": KEEPALIVE_IDLE_S,
            "interval": KEEPALIVE_INTERVAL_S,
            "probes": KEEPALIVE_PROBES,
        }
        assert actual == {key: expected[key] for key in actual}, "the timings this OS reports"

    @pytest.mark.rule("serve-pumps-joined")
    def test_a_vanished_client_is_dropped_and_frees_the_port(self, served):
        """Twice: when keepalive gives up on a peer, the client ends at once, not spinning, and the next client gets the port."""
        # Arrange
        bridge, events = served("loop://")
        server = bridge.servers[0]

        for _ in range(2):
            vanished = _VanishedPeer()

            # Act
            server._admit(vanished, "192.168.1.20")
            dropped = _until(lambda: vanished.closed)
            after = _client(server)
            after.write(b"mine now")
            reply = _read(after, 8)
            after.close()

            # Assert
            assert dropped, "the dead connection is closed"
            assert reply == b"mine now", "the port is free for the next client"
        assert events.count("loop://: 192.168.1.20 disconnected") == 2, "each drop is said"

    @pytest.mark.rule("serve-pumps-joined")
    def test_stop_twice_leaves_no_thread_and_closes_the_port(self, served):
        """With a client connected, stop joins the accept thread and both pumps, closes the port and the TCP port, and a second stop is harmless."""
        # Arrange
        bridge, _ = served("loop://")
        server = bridge.servers[0]
        client = _client(server)
        port = server._port
        assert _until(lambda: len(_serve_threads()) == 3), "an accept thread and two pumps"

        # Act
        bridge.stop()
        bridge.stop()

        # Assert
        assert _serve_threads() == [], "every thread joined"
        assert port.is_open is False, "the served port is closed"
        with pytest.raises(OSError):
            socket.create_connection((LOCAL, server.tcp_port), timeout=0.5)
        client.close()

    @pytest.mark.rule("serve-pumps-joined")
    def test_a_port_lost_with_no_client_is_noticed_and_reopened(self, served):
        """A port that vanishes while no client is connected is dropped from what the server holds, then reopened."""
        # Arrange
        opener = _Opener()
        bridge, events = served("BG03U7VTA", opener=opener)
        server = bridge.servers[0]
        opener.refusing = True

        # Act: unplug with nobody connected
        opener.opened[0].gone = True
        lost = _until(lambda: not server.connected)
        held_while_gone = bridge.devices
        # Act: plug back in
        opener.refusing = False
        reopened = _until(lambda: server.connected)

        # Assert
        assert lost, "the idle check notices the port is gone"
        assert held_while_gone == [], "the breadcrumb stops claiming the port"
        assert opener.opened[0].is_open is False, "the dead handle is closed"
        assert reopened, "the port comes back through the same opener"
        said = [event.split(": ", 1)[1].split(":")[0] for event in events]
        assert "port lost" in said and "reopened (COM9)" in said, events

    @pytest.mark.rule("serve-pumps-joined")
    def test_a_healthy_idle_port_is_checked_and_kept(self, served):
        """An idle port that answers its status is kept across checks: nothing is reported, nothing reopened."""
        # Arrange
        opener = _Opener()
        bridge, events = served("BG03U7VTA", opener=opener)
        port = opener.opened[0]

        # Act -- three checks, a second apart
        checked = _until(lambda: port.checks >= 3)

        # Assert
        assert checked, "an idle server asks its port's status about once a second"
        assert (bridge.servers[0].connected, events) == (True, []), "a healthy port stays put"
        assert len(opener.opened) == 1, "and is never reopened"

    @pytest.mark.rule("serve-pumps-joined")
    def test_a_lost_port_drops_its_client_refuses_while_gone_and_is_reopened(self, served):
        """An unplugged port drops its client and keeps its URL; a client is refused while it is gone, and works once it is reopened."""
        # Arrange
        opener = _Opener()
        bridge, events = served("BG03U7VTA", opener=opener)
        server = bridge.servers[0]
        client = _client(server)
        opener.refusing = True

        # Act: unplug
        opener.opened[0].gone = True
        lost = _until(lambda: not server.connected)
        refused_while_gone = _refused(server)
        # Act: plug back in
        opener.refusing = False
        reopened = _until(lambda: server.connected)
        after = _client(server)
        after.write(b"AT\r")
        reply = _read(after, 4)

        # Assert
        assert lost, "the read failure is noticed and the port closed"
        assert refused_while_gone, "a client is turned away while the port is gone"
        assert opener.opened[0].is_open is False, "the lost port is closed"
        assert reopened, "the port comes back through the same opener"
        assert reply == b"OK\r\n", "a new client works on the reopened port, at the same URL"
        said = [event.split(": ", 1)[1].split(":")[0] for event in events]
        assert "port lost" in said and "reopened (COM9)" in said, events
        assert any("the port is not connected" in event for event in events), "the refusal says why"
        client.close()
        after.close()


@pytest.mark.rule("serve-opens-like-a-session")
class TestOpening:
    def test_a_refused_port_serves_nothing(self):
        """A port TERMAPY_ALLOWED_PORTS refuses stops the bridge before any TCP port listens."""
        # Arrange
        policy = PortPolicy(allowlist=PortAllowlist.parse("COM7"))

        # Act
        with pytest.raises(ServeError) as refused:
            Bridge.open([ServeEntry("COM99", 0)], LOCAL, report=lambda line: None, policy=policy)

        # Assert
        assert str(refused.value).startswith("Cannot open COM99: "), "the port is named"
        assert "TERMAPY_ALLOWED_PORTS" in str(refused.value), "and the variable that refused it"
        assert _serve_threads() == [], "nothing started"

    def test_a_port_that_will_not_open_closes_the_ones_before(self):
        """Every port opens before anything listens; when a later one fails, the earlier ones are closed again."""
        # Arrange
        opened: list[FakeSerial] = []

        def opener(cfg: dict, *, policy=None) -> FakeSerial:
            if cfg["serial"]["port"] == "COM8":
                raise SerialException("could not open port COM8")
            port = FakeSerial()
            opened.append(port)
            return port

        # Act
        with pytest.raises(ServeError, match="Cannot open COM8"):
            Bridge.open(
                [ServeEntry("COM7", 0), ServeEntry("COM8", 0)],
                LOCAL,
                report=lambda line: None,
                opener=opener,
            )

        # Assert
        assert [port.is_open for port in opened] == [False], "COM7 opened, then closed again"

    def test_a_tcp_port_in_use_serves_nothing(self):
        """A TCP port something else listens on is a ServeError naming it, and the opened port is closed."""
        # Arrange
        holder = socket.create_server((LOCAL, 0))
        taken = holder.getsockname()[1]
        opened: list[FakeSerial] = []

        def opener(cfg: dict, *, policy=None) -> FakeSerial:
            opened.append(FakeSerial())
            return opened[-1]

        # Act
        try:
            with pytest.raises(ServeError, match=f"Cannot listen on {LOCAL}:{taken} for COM7"):
                Bridge.open(
                    [ServeEntry("COM7", taken)], LOCAL, report=lambda line: None, opener=opener
                )
        finally:
            holder.close()

        # Assert
        assert opened[0].is_open is False, "the port opened for it is closed again"


class _StalledPort:
    """A served port whose device takes nothing: every write waits out its write timeout, as one under held flow control does."""

    def __init__(self) -> None:
        self.inner = serial.serial_for_url("loop://", timeout=0.05)
        self.write_timeout: float | None = None
        self.baudrate = 115200

    def write(self, data: bytes) -> int:
        """Wait out the timeout, then raise as pyserial does when nothing went out."""
        time.sleep(self.write_timeout or 0)
        raise serial.SerialTimeoutException("Write timeout")

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


class TestPumpFailures:
    """What the network pump does when the device or the client misbehaves: one line said, the port kept."""

    @pytest.mark.rule("serve-pumps-joined")
    def test_a_port_that_takes_nothing_drops_the_client_and_keeps_serving(
        self, served, monkeypatch
    ):
        """A write the device never takes ends that client within the write timeout, and the next client is admitted."""
        # Arrange -- a short timeout, so the test waits a fraction of a second
        monkeypatch.setattr(port_server, "WRITE_TIMEOUT_S", 0.3)
        stalled = _StalledPort()
        bridge, events = served("stalled", opener=lambda cfg, policy=None: stalled)
        server = bridge.servers[0]

        # Act
        first = _client(server)
        first.write(b"AT\r")
        dropped = _until(lambda: any("took nothing" in event for event in events))
        first.close()
        second_admitted = _until(lambda: not server.client_address)

        # Assert
        assert stalled.write_timeout == 0.3, "the served port opened with a write timeout"
        assert dropped, f"the blocked client was dropped and said so: {events}"
        assert second_admitted and server.connected, "the slot is free and the port still served"

    def test_a_malformed_option_drops_the_client_in_one_line(self, served):
        """A short SET_BAUDRATE from a client ends that client with one report line; the port serves the next."""
        # Arrange
        bridge, events = served("loop://")
        server = bridge.servers[0]
        host, port = server.url.removeprefix("rfc2217://").rsplit(":", 1)
        malformed = b"\xff\xfa\x2c\x01\x00\x01\xff\xf0"  # SET_BAUDRATE with two of its four bytes

        # Act
        raw = socket.create_connection((host.strip("[]"), int(port)), timeout=2)
        raw.sendall(malformed)
        said = _until(lambda: any("malformed option" in event for event in events))
        raw.close()
        after = _client(server)
        after.write(b"still")
        reply = _read(after, 5)
        after.close()

        # Assert
        assert said, f"the drop is one report line: {events}"
        assert reply == b"still", "the next client is served"
