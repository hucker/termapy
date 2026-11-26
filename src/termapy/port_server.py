"""Share serial ports on the network over RFC 2217: the core of ``termapy --serve``.

A ``Bridge`` holds the ports it serves, each a ``PortServer`` with its own TCP
port, so a client anywhere names one port with one URL
(``rfc2217://host:2217``).  The protocol is pyserial's own
``serial.rfc2217.PortManager``: it answers the client's Telnet and RFC 2217
negotiation and applies its baud rate, frame, DTR, RTS and break to the real
port, so a termapy at the far end changes the settings as it would on a local
port.  What this module adds is the loop around it and termapy's rules:

- A port opens through the one opener, ``config.open_serial`` (registry
  ``serve-opens-like-a-session``), so ``TERMAPY_ALLOWED_PORTS``, the exclusive
  lock and serial-number names hold as in any session.
- One client per port at a time; another is refused at once, with a line
  saying who holds the port.
- A port that goes away (unplugged, a board that re-enumerates) is reopened
  about once a second while its TCP port keeps listening, so the URL never
  changes; a serial-number name finds the board at its new COM number.
- Each served port has an accept thread and each client two pumps, network to
  port and port to network; ``Bridge.stop`` joins every one of them (registry
  ``serve-pumps-joined``).  The bridge is not a frontend, so its readers are
  its own, not ``SerialEngine``'s.

Nothing here imports the command system, Textual or Rich: a server reaches
"listening" with the opener, pyserial and the socket (registry
``flag-paths-light``).  ``cli_flags.run_serve`` is the flag around it, and
``start_detached`` the one launch of a server in the background
(``--detach``, registry ``serve-detaches``).
"""

from __future__ import annotations

import copy
import errno
import select
import socket
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from serial import SerialException, SerialTimeoutException
from serial.rfc2217 import PortManager

from termapy.breadcrumbs import listen_url
from termapy.port_allow import PortNotAllowedError, url_scheme

if TYPE_CHECKING:
    import subprocess

# Where a server listens unless --listen names another address: this machine only.

# The first TCP port an unnumbered entry takes, then the next free one:
# 2217 is RFC 2217's customary port and pyserial's server default.
FIRST_TCP_PORT = 2217

# How often each loop looks up from a blocking call, so a stop is seen soon.
_ACCEPT_POLL_S = 0.2
_RECV_POLL_S = 0.2
# How often the modem lines (CTS, DSR, RI, CD) are sent to the client, and a
# lost port is tried again.
_MODEM_POLL_S = 1.0
_REOPEN_S = 1.0
_RECV_BYTES = 4096
# A client that vanishes without closing (a laptop asleep, Wi-Fi gone) sends
# nothing, so only TCP keepalive notices: a probe after this much silence,
# another every interval, and the connection given up after this many go
# unanswered.  A dead peer frees its port in about 50 s (registry
# serve-pumps-joined).
KEEPALIVE_IDLE_S = 30
KEEPALIVE_INTERVAL_S = 5
KEEPALIVE_PROBES = 4
# How long a stop waits for each thread: every loop wakes within a poll.
_JOIN_S = 2.0
# How long a write to the port may take before the device is taken to be
# refusing data (flow control held, CTS never asserted).  Each write is a
# second's worth of bytes at the port's rate (``_write_all``), so a slow
# baud rate never trips it.  Without it one blocked write held the client
# slot for the server's life (review 2026-11-26, N1).
WRITE_TIMEOUT_S = 5.0
# The smallest slice ``_write_all`` cuts, so a very low rate still moves.
_MIN_WRITE_SLICE = 64

Report = Callable[[str], None]
Opener = Callable[..., Any]


class ServeError(Exception):
    """A port that would not open or a TCP port that would not listen: nothing is served."""


@dataclass(frozen=True)
class ServeEntry:
    """One port to serve and the TCP port it is served on.

    Attributes:
        port: The serial port as typed: a name, a USB serial number, a ``|``
            fallback chain, or a simulator (``DEMO``).
        tcp_port: The TCP port a client connects to; 0 lets the system pick
            one (tests), read back from ``PortServer.tcp_port``.
    """

    port: str
    tcp_port: int


def parse_entries(texts: list[str], first: int = FIRST_TCP_PORT) -> list[ServeEntry]:
    """``--serve``'s words as entries: ``PORT`` or ``PORT=TCPPORT`` each.

    An entry is split at its last ``=`` only when digits follow, so a name
    that holds an ``=`` stays whole.  An unnumbered entry takes the next TCP
    port from ``first`` that no entry names.

    Args:
        texts: The words after ``--serve``.
        first: The TCP port the first unnumbered entry takes.

    Raises:
        ValueError: An empty entry, a TCP port outside 1-65535 or named
            twice, a serial port named twice, or a ``spy://`` port, whose log
            needs a config's ``cap/`` folder.
    """
    named: list[tuple[str, int | None]] = []
    for text in texts:
        port, sep, number = text.rpartition("=")
        if sep and port and number.isdigit():
            if not 0 < int(number) < 65536:
                raise ValueError(f"Invalid TCP port: {number}")
            named.append((port, int(number)))
        elif text:
            named.append((text, None))
        else:
            raise ValueError("Invalid port: an empty entry")
    for port, _ in named:
        if url_scheme(port) == "spy":
            raise ValueError(f"Invalid port: {port} (spy:// logs to a config's cap/ folder)")
    ports = [port.casefold() for port, _ in named]
    twice = sorted({port for port in ports if ports.count(port) > 1})
    if twice:
        raise ValueError(f"Port named twice: {', '.join(twice)}")
    numbers = [number for _, number in named if number is not None]
    taken = {number for number in numbers if numbers.count(number) > 1}
    if taken:
        raise ValueError(f"TCP port named twice: {', '.join(map(str, sorted(taken)))}")
    used = set(numbers)
    entries: list[ServeEntry] = []
    next_free = first
    for port, number in named:
        if number is None:
            while next_free in used:
                next_free += 1
            number = next_free
            used.add(number)
        entries.append(ServeEntry(port, number))
    return entries


def _detach_flags() -> int:
    """Windows' process-creation flags for a server started in the background; 0 elsewhere.

    ``CREATE_NO_WINDOW``, not ``DETACHED_PROCESS``: a venv's ``python.exe`` is a
    launcher that starts the real interpreter, and a detached launcher leaves
    that interpreter no console, so Windows opens it a console window of its
    own, which flashed on screen.  With no window the launcher gets a console
    that is never shown, and the interpreter shares it.  That console is not
    the one ``--detach`` was typed in, so closing that window does not reach
    the server, and a group of its own keeps a Ctrl+C there from reaching it.
    Windows ignores ``CREATE_NO_WINDOW`` beside ``DETACHED_PROCESS``, so the two
    never go together.
    """
    import subprocess
    import sys

    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def start_detached(argv: list[str], output: Path) -> subprocess.Popen[bytes]:
    """Start ``termapy <argv>`` as a process of its own, its output appended to ``output``.

    No window and no controlling terminal: on Windows a console never shown,
    in a group of its own (``_detach_flags``), so closing the window it was
    started from or a Ctrl+C there never reaches it; elsewhere a session of
    its own.  Its
    standard input is closed, and what it prints, its banner, every event
    and any failure, goes to ``output``.  The caller learns that it listens
    from its breadcrumb, as every other termapy does (registry
    ``serve-detaches``).

    Args:
        argv: The arguments after ``termapy``, without ``--detach``.
        output: The file its output goes to, created if missing.

    Returns:
        The started process; on Windows a venv's ``python.exe`` is a
        launcher, so its PID may not be the server's.
    """
    import subprocess
    import sys

    windows = sys.platform == "win32"
    with output.open("ab") as sink:
        return subprocess.Popen(
            [sys.executable, "-m", "termapy", *argv],
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=_detach_flags(),
            start_new_session=not windows,
        )


def port_cfg(port: str) -> dict[str, Any]:
    """The config ``config.open_serial`` reads to open ``port``: the defaults with that port.

    A client sets the baud rate and frame over RFC 2217 as it connects, so
    the defaults only last until then.
    """
    from termapy.defaults import DEFAULT_CFG

    cfg = copy.deepcopy(DEFAULT_CFG)
    cfg["serial"]["port"] = port
    return cfg


def _device_of(port: Any, typed: str) -> str:
    """The device a port object opened (``COM7`` for a serial number typed), else what was typed."""
    return str(getattr(port, "port", "") or typed)


# The read timeout the port-to-network pump relies on to look up: the
# opener's own for a real port; the simulators open with none and would spin.
_READ_TIMEOUT_S = 0.05


def _ready(port: Any) -> Any:
    """``port`` with a short read timeout, so its pump wakes to see a stop, and a write timeout; returned for chaining."""
    if not getattr(port, "timeout", None):
        port.timeout = _READ_TIMEOUT_S
    if getattr(port, "write_timeout", None) is None:
        port.write_timeout = WRITE_TIMEOUT_S
    return port


def _write_all(port: Any, payload: bytes) -> None:
    """Write ``payload`` to ``port`` in slices of about a second each at its baud rate.

    Each slice finishes well inside ``WRITE_TIMEOUT_S`` at any rate the
    device accepts, so a timeout means the device took nothing, not that the
    rate was slow.

    Raises:
        SerialTimeoutException: A slice did not go out in ``WRITE_TIMEOUT_S``.
        SerialException: The port failed.
    """
    rate = int(getattr(port, "baudrate", 0) or 0)
    step = max(_MIN_WRITE_SLICE, rate // 10)
    for start in range(0, len(payload), step):
        port.write(payload[start : start + step])


def _keep_alive(conn: socket.socket) -> None:
    """Turn on TCP keepalive for ``conn`` at the timings above, as far as this OS lets them be set.

    macOS names the idle time ``TCP_KEEPALIVE`` where Linux and Windows say
    ``TCP_KEEPIDLE``.  An OS that refuses one timing keeps its own default
    for it; keepalive itself is still on.
    """
    idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    timings = (
        (idle, KEEPALIVE_IDLE_S),
        (getattr(socket, "TCP_KEEPINTVL", None), KEEPALIVE_INTERVAL_S),
        (getattr(socket, "TCP_KEEPCNT", None), KEEPALIVE_PROBES),
    )
    settings = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    settings += [(socket.IPPROTO_TCP, option, value) for option, value in timings if option]
    for level, option, value in settings:
        try:
            conn.setsockopt(level, option, value)
        except OSError:
            continue


def _close_quietly(port: Any) -> None:
    """Close a serial port that may already be gone; nothing else can be done about a failure."""
    try:
        port.close()
    except (SerialException, OSError):
        pass


# A port that will not open: what the opener raises, every one a message for a person.
_OPEN_ERRORS = (SerialException, OSError, ValueError, PortNotAllowedError)


def _open(opener: Opener, port: str, policy: Any) -> Any:
    """Open ``port`` through ``opener``; an ambiguous serial number is an ``OSError`` like any refusal."""
    from termapy.port_control import AmbiguousSerialNumberError

    try:
        return _ready(opener(port_cfg(port), policy=policy))
    except AmbiguousSerialNumberError as e:
        raise OSError(str(e)) from e


class _Client:
    """One connected client: the socket, pyserial's protocol state and the two pumps."""

    def __init__(
        self,
        server: PortServer,
        port: Any,
        conn: socket.socket,
        who: str,
    ) -> None:
        """Bridge ``conn`` to ``port``; ``start`` runs the pumps.

        ``PortManager`` sends its first negotiation from its constructor
        through ``write``, so the socket is set up before it is made.
        """
        self.server = server
        self.port = port
        self.conn = conn
        self.who = who
        self._send_lock = threading.Lock()
        self._done = threading.Event()
        conn.settimeout(_RECV_POLL_S)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        _keep_alive(conn)
        name = server.entry.port
        self._threads = (
            threading.Thread(
                target=self._network_to_port, name=f"termapy-serve-in-{name}", daemon=True
            ),
            threading.Thread(
                target=self._port_to_network, name=f"termapy-serve-out-{name}", daemon=True
            ),
        )
        self.manager = PortManager(port, self)

    @property
    def alive(self) -> bool:
        """Whether the client is still connected."""
        return not self._done.is_set()

    def write(self, data: bytes) -> None:
        """Send to the client: the connection ``PortManager`` writes its replies to, from either pump."""
        with self._send_lock:
            self.conn.sendall(data)

    def start(self) -> None:
        """Run both pumps."""
        for thread in self._threads:
            thread.start()

    def end(self) -> None:
        """Stop pumping and close the socket; safe from either pump, and twice."""
        if self._done.is_set():
            return
        self._done.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()
        self.server.client_ended(self)

    def join(self) -> None:
        """End the client and wait for both pumps; never from a pump itself."""
        self.end()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(_JOIN_S)

    def _network_to_port(self) -> None:
        """What the client sends goes to the port, its RFC 2217 requests applied on the way."""
        try:
            self._pump_network_to_port()
        finally:
            # However the pump leaves, even by a bug, the client is closed, so
            # it never holds the port against every later client.
            self.end()

    def _pump_network_to_port(self) -> None:
        """The loop of ``_network_to_port``: until the client closes or the port fails."""
        while not self._done.is_set():
            try:
                data = self.conn.recv(_RECV_BYTES)
            except TimeoutError as e:
                # The poll's own timeout has no errno: nothing came, look again.
                # One with an errno is TCP keepalive giving up on a peer that
                # vanished, which must end the client, not spin here.
                if e.errno is None:
                    continue
                break
            except OSError:
                break
            if not data:
                break
            try:
                payload = b"".join(self.manager.filter(data))
            except (ValueError, SerialException) as e:
                # A setting the port refuses: pyserial restores a refused value
                # it caught itself, and a driver's refusal (SetCommState) is a
                # SerialException.  The client hears no change and the session
                # goes on; a port that just went is the out-pump's next read to find.
                self.server.report(
                    f"{self.server.name}: {self.who} asked for a setting the port refused: {e}"
                )
                continue
            except (struct.error, KeyError, IndexError, TypeError) as e:
                # A malformed RFC 2217 option (a value too short, an unknown
                # code): a client that speaks it wrongly is ended in one line,
                # not a thread traceback (review 2026-11-26, N2).
                self.server.report(
                    f"{self.server.name}: {self.who} sent a malformed option and was dropped: "
                    f"{type(e).__name__}: {e}"
                )
                break
            except OSError:
                break  # a reply to the client could not be sent: it is gone
            try:
                if payload:
                    _write_all(self.port, payload)
            except SerialTimeoutException:
                # The device took nothing (flow control held): this client ends
                # and frees the slot; the port stays served for the next one.
                self.server.report(
                    f"{self.server.name}: {self.who} dropped: the port took nothing for "
                    f"{WRITE_TIMEOUT_S:g} s (flow control held?)"
                )
                break
            except (SerialException, OSError) as e:
                self.server.port_lost(self.port, e)
                break

    def _port_to_network(self) -> None:
        """What the port receives goes to the client; the modem lines about once a second."""
        try:
            self._pump_port_to_network()
        finally:
            self.end()  # as _network_to_port: never a client left holding the port

    def _pump_port_to_network(self) -> None:
        """The loop of ``_port_to_network``: until the client closes or the port fails."""
        modem_at = 0.0
        while not self._done.is_set():
            try:
                data = self.port.read(self.port.in_waiting or 1)
            except (SerialException, OSError) as e:
                self.server.port_lost(self.port, e)
                break
            try:
                if data:
                    self.write(b"".join(self.manager.escape(data)))
                now = time.monotonic()
                if now - modem_at >= _MODEM_POLL_S:
                    modem_at = now
                    self.manager.check_modem_lines()
            except SerialException as e:
                self.server.port_lost(self.port, e)
                break
            except OSError:
                break


# A documentation address (TEST-NET-1, RFC 5737): a UDP socket connected to it
# names the local address the default route would use, and sends nothing.
_ROUTE_PROBE = ("192.0.2.1", 9)


def _default_route_address() -> str | None:
    """The IPv4 address this machine reaches its default route from, or None with no route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(_ROUTE_PROBE)
        address = str(probe.getsockname()[0])
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith(("0.", "127.")) else address


def listen_choices() -> list[tuple[str, str]]:
    """The addresses ``--listen`` can name on this machine, each with what it means.

    ``localhost`` first (this machine only), then the address of the default
    route (the LAN, most likely), then this machine's other IPv4 addresses.
    termapy never picks one: a machine has several (Wi-Fi, Ethernet, a VPN, a
    WSL or Hyper-V adapter), and serving on the wrong one is a port open to the
    wrong network.  Loopback and link-local (``169.254.*``) addresses are left
    out: nothing else can reach them.  Nothing is sent to find them.
    """
    route = _default_route_address()
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    addresses = {str(info[4][0]) for info in infos}
    others = sorted(
        address
        for address in addresses
        if address != route and not address.startswith(("127.", "169.254."))
    )
    choices = [("localhost", "this machine only")]
    if route is not None:
        choices.append((route, "the default route's: your LAN, most likely"))
    choices += [(address, "") for address in others]
    return choices


def _listen(host: str, tcp_port: int) -> list[socket.socket]:
    """A listening socket on every address ``host`` names, all on one TCP port.

    ``localhost`` names ``::1`` and ``127.0.0.1``, and a client tries them in
    that order; Windows takes two seconds to refuse an address nothing
    listens on, so a server on ``127.0.0.1`` alone made every
    ``rfc2217://localhost`` open wait.  An address of a family this machine
    does not have is skipped; a TCP port in use on any address is the error
    it is.  Each socket has the accept poll's timeout, so an accepted
    connection is a blocking socket, as the pumps expect.

    Raises:
        OSError: No address would listen, or the port is in use.
    """
    addresses: list[tuple[int, str]] = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, tcp_port, type=socket.SOCK_STREAM):
        if (family, sockaddr[0]) not in addresses:
            addresses.append((family, str(sockaddr[0])))
    listeners: list[socket.socket] = []
    try:
        for family, address in addresses:
            port = listeners[0].getsockname()[1] if listeners else tcp_port
            try:
                listener = socket.create_server((address, port), family=family)
            except OSError as e:
                if e.errno in (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT):
                    continue
                raise
            listener.settimeout(_ACCEPT_POLL_S)
            listeners.append(listener)
    except OSError:
        for listener in listeners:
            listener.close()
        raise
    if not listeners:
        raise OSError(errno.EADDRNOTAVAIL, f"no address of {host} will listen")
    return listeners


class PortServer:
    """One served port: its serial port, its listening socket and the client on it."""

    def __init__(
        self,
        entry: ServeEntry,
        host: str,
        port: Any,
        *,
        report: Report,
        reopen: Callable[[], Any],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Listen for ``entry`` on ``host``; ``start`` accepts.

        Args:
            entry: The port and its TCP port.
            host: The address to listen on.
            port: The serial port, open.
            report: Where event lines go (connected, refused, lost, ...).
            reopen: Opens the port again after it was lost; raises as the
                opener does.
            on_change: Called after the open device or the client changed
                (lost, reopened, connected, left), so the caller can rewrite
                its breadcrumb.

        Raises:
            ServeError: The TCP port will not listen (in use, or ``host`` is
                not this machine's).
        """
        self.entry = entry
        self.host = host
        self.report = report
        self._reopen = reopen
        self._on_change = on_change
        self._lock = threading.Lock()
        self._port: Any | None = port
        self.device = _device_of(port, entry.port)
        self._client: _Client | None = None
        self._stop = threading.Event()
        try:
            self._listeners = _listen(host, entry.tcp_port)
        except OSError as e:
            raise ServeError(
                f"Cannot listen on {host}:{entry.tcp_port} for {entry.port}: {e}"
            ) from e
        self.tcp_port: int = self._listeners[0].getsockname()[1]
        self._thread = threading.Thread(
            target=self._accept_loop, name=f"termapy-serve-{entry.port}", daemon=True
        )

    @property
    def name(self) -> str:
        """The port as typed, for event lines."""
        return self.entry.port

    @property
    def connected(self) -> bool:
        """Whether the serial port is open now (False while lost)."""
        return self._port is not None

    @property
    def url(self) -> str:
        """What a client names to reach this port: ``rfc2217://host:2217``."""
        return listen_url("rfc2217", self.host, self.tcp_port)

    @property
    def client_address(self) -> str:
        """The address of the client on this port now, ``""`` when none is connected."""
        with self._lock:
            client = self._client
        return client.who if client is not None and client.alive else ""

    def start(self) -> None:
        """Start accepting clients."""
        self._thread.start()

    def stop(self) -> None:
        """Close the client, the listener and the port; wait for every thread.  Safe twice.

        The accept thread is joined first, so a client it was admitting as
        the stop came is the one joined after it, never one left running.
        """
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(_JOIN_S)
        with self._lock:
            client = self._client
        if client is not None:
            client.join()
        for listener in self._listeners:
            listener.close()
        with self._lock:
            port, self._port = self._port, None
        if port is not None:
            _close_quietly(port)

    def client_ended(self, client: _Client) -> None:
        """A client left (it closed, or the port went): say so once, and let the breadcrumb drop it."""
        with self._lock:
            if self._client is not client:
                return
        self.report(f"{self.name}: {client.who} disconnected")
        self._changed()

    def _changed(self) -> None:
        """Tell the caller the device or the client changed (``on_change``)."""
        if self._on_change is not None:
            self._on_change()

    def port_lost(self, port: Any, error: Exception) -> None:
        """The serial port failed under a client: close it and let the accept loop reopen it."""
        with self._lock:
            if self._port is not port:
                return
            self._port = None
            client = self._client
        _close_quietly(port)
        self.report(f"{self.name}: port lost: {error}")
        if client is not None:
            client.end()
        self._changed()

    def _accept_loop(self) -> None:
        """Accept clients until stopped, checking an idle port and retrying a lost one about once a second."""
        retry_at = 0.0
        while not self._stop.is_set():
            if time.monotonic() >= retry_at:
                retry_at = time.monotonic() + _REOPEN_S
                if self._port is None:
                    self._try_reopen()
                else:
                    self._check_idle_port()
            try:
                ready, _, _ = select.select(self._listeners, [], [], _ACCEPT_POLL_S)
            except (OSError, ValueError):
                break  # a listener closed under it: the server is stopping
            for listener in ready:
                try:
                    conn, address = listener.accept()
                except TimeoutError:
                    continue  # the client gave up before it was accepted
                except OSError:
                    return
                self._admit(conn, str(address[0]))

    def _check_idle_port(self) -> None:
        """Notice a port that vanished while no client used it, as a client's failed read would.

        A client's pumps find a lost port when a read or write fails; with no
        client nothing touched the port, so a device unplugged or re-enumerated
        (a hub reset) left the server on a dead handle, still claiming the port
        in its breadcrumb while another program opened the device that came
        back (found 2026-11-25).  ``in_waiting`` asks the port's status and
        reads no data: it fails on a device that is gone, on Windows and Linux.
        """
        with self._lock:
            port = self._port
            client = self._client
        if port is None or (client is not None and client.alive):
            return
        try:
            _ = port.in_waiting
        except (SerialException, OSError) as e:
            self.port_lost(port, e)

    def _try_reopen(self) -> None:
        """Open the lost port again; quiet while it is still gone."""
        try:
            port = self._reopen()
        except _OPEN_ERRORS:
            return
        with self._lock:
            self._port = port
        self.device = _device_of(port, self.entry.port)
        self.report(f"{self.name}: reopened ({self.device})")
        self._changed()

    def _admit(self, conn: socket.socket, who: str) -> None:
        """Bridge a new client to the port, or refuse it at once when the port is busy or lost."""
        if self._stop.is_set():
            conn.close()  # it came as the server stopped
            return
        with self._lock:
            previous = self._client
            busy = previous is not None and previous.alive
            port = self._port
        if port is None or (busy and previous is not None):
            reason = (
                "the port is not connected"
                if port is None
                else f"in use by {previous.who if previous else 'another client'}"
            )
            self.report(f"{self.name}: refused {who}: {reason}")
            conn.close()
            return
        if previous is not None:
            previous.join()
        # A new session starts clean: what the device said while no one was
        # connected is not this client's.
        try:
            port.reset_input_buffer()
        except (SerialException, OSError) as e:
            self.port_lost(port, e)
            conn.close()
            return
        try:
            client = _Client(self, port, conn, who)
        except OSError:
            conn.close()
            return
        with self._lock:
            self._client = client
        self.report(f"{self.name}: {who} connected")
        client.start()
        self._changed()


class Bridge:
    """The ports ``termapy --serve`` shares: opened, listening, then serving until stopped."""

    def __init__(self, servers: list[PortServer]) -> None:
        """Hold ``servers``; ``Bridge.open`` builds one."""
        self.servers = servers

    @classmethod
    def open(
        cls,
        entries: list[ServeEntry],
        host: str,
        *,
        report: Report,
        on_change: Callable[[], None] | None = None,
        opener: Opener | None = None,
        policy: Any = None,
    ) -> Bridge:
        """Open every port and listen on every TCP port, or open nothing.

        Every port opens first, through ``opener`` (``config.open_serial``),
        so a mistyped name or a refused port ends the run before anything
        listens; then every TCP port listens.  On any failure what was opened
        is closed again.

        Args:
            entries: The ports and their TCP ports (``parse_entries``).
            host: The address to listen on.
            report: Where event lines go.
            on_change: Called when a port is lost or reopened.
            opener: Opens a port from a config, ``config.open_serial`` by
                default; a test hands in its own.
            policy: The port policy; None is this process's.

        Raises:
            ServeError: A port would not open or a TCP port would not listen.
        """
        if opener is None:
            from termapy.config import open_serial

            opener = open_serial
        chosen = opener
        opened: list[tuple[ServeEntry, Any]] = []
        servers: list[PortServer] = []
        try:
            for entry in entries:
                try:
                    opened.append((entry, _open(chosen, entry.port, policy)))
                except _OPEN_ERRORS as e:
                    raise ServeError(f"Cannot open {entry.port}: {e}") from e
            for entry, port in opened:
                servers.append(
                    PortServer(
                        entry,
                        host,
                        port,
                        report=report,
                        reopen=lambda typed=entry.port: _open(chosen, typed, policy),
                        on_change=on_change,
                    )
                )
        except ServeError:
            for server in servers:
                server.stop()
            for _, port in opened[len(servers) :]:
                _close_quietly(port)
            raise
        return cls(servers)

    def start(self) -> None:
        """Start accepting on every port."""
        for server in self.servers:
            server.start()

    def stop(self) -> None:
        """Stop every server and wait for its threads.  Safe twice."""
        for server in self.servers:
            server.stop()

    @property
    def devices(self) -> list[str]:
        """The devices open now, in entry order; a lost port is left out."""
        return [server.device for server in self.servers if server.connected]
