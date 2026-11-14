"""Tests for ZMODEM file transfer - the queue transport bridge, both ends of a transfer over a socket port, and lrzsz interop.

The DEMO device speaks only XMODEM and YMODEM, so the far end here is the
protocol engine itself behind pyserial's ``socket://`` port (a TCP server on
127.0.0.1 the CLI rig connects to) and, where lrzsz is installed, the
reference ``sz`` / ``rz`` over pipes.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import IO

import pytest
from yesterwind_xyzmodem import ZModem
from yesterwind_xyzmodem.exceptions import XYZModemError
from yesterwind_xyzmodem.transport import StreamTransport
from yesterwind_xyzmodem.zmodem import ZABORT, ZRINIT, _build_hex_header

from termapy.builtins.commands._xmodem_handlers import QueueByteReader
from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.demo import FakeSerial
from termapy.folder_ops import staged_receive
from termapy.folders import ensure_folder
from termapy.zmodem_transport import (
    ZMODEM_CANCEL,
    QueueTransport,
    TransferAborted,
    receive_files,
    send_files,
)

PAYLOAD = bytes(range(256)) * 8

Role = Callable[[StreamTransport], Awaitable[object]]


def _no_progress(progress: object) -> None:
    """A progress callback that keeps nothing."""


def _wait_for(condition: Callable[[], bool], what: str, timeout_s: float = 10.0) -> None:
    """Poll ``condition`` until it holds; fail naming ``what`` after ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


# -- The bridge ---------------------------------------------------------------


def _transport(
    q: queue.Queue[bytes], written: list[bytes] | None = None, cancel: threading.Event | None = None
) -> QueueTransport:
    """A transport over ``q`` whose writes land in ``written``."""
    sink = written if written is not None else []
    return QueueTransport(QueueByteReader(q, cancel=cancel), sink.append, cancel)


@pytest.mark.rule("xfer-no-port-contention")
class TestQueueTransport:
    """The bridge reads termapy's rx_queue for the engine, byte by byte, and never the port."""

    def test_a_byte_comes_from_a_queued_chunk(self) -> None:
        """read_byte_with_timeout answers the first byte of a chunk as an int."""
        q: queue.Queue[bytes] = queue.Queue()
        q.put(b"AB")
        actual = asyncio.run(_transport(q).read_byte_with_timeout(1))
        assert actual == 0x41, "the first byte, as the engine expects"

    def test_two_reads_in_a_row_keep_the_remainder(self) -> None:
        """The second read continues where the first stopped."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        q.put(b"ABC")
        transport = _transport(q)

        async def two() -> tuple[int, bytes]:
            first = await transport.read_byte_with_timeout(1)
            rest = await transport.read_with_timeout(2, 1)
            return first, rest

        # Act
        actual = asyncio.run(two())

        # Assert
        assert actual == (0x41, b"BC"), "nothing lost between reads"

    def test_a_read_spans_two_chunks(self) -> None:
        """A read larger than one chunk assembles the next chunk in."""
        q: queue.Queue[bytes] = queue.Queue()
        q.put(b"\x01\x02")
        q.put(b"\x03\x04")
        actual = asyncio.run(_transport(q).read_with_timeout(4, 1))
        assert actual == b"\x01\x02\x03\x04", "two items in one read"

    def test_an_empty_queue_times_out(self) -> None:
        """With nothing arriving the read raises the TimeoutError the engine catches."""
        q: queue.Queue[bytes] = queue.Queue()
        with pytest.raises(TimeoutError):
            asyncio.run(_transport(q).read_byte_with_timeout(0.2))

    def test_a_set_cancel_ends_a_read(self) -> None:
        """Esc ends a waiting read with the private cancel, long before the timeout."""
        q: queue.Queue[bytes] = queue.Queue()
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(TransferAborted):
            asyncio.run(_transport(q, cancel=cancel).read_byte_with_timeout(30))

    def test_a_set_cancel_refuses_a_write(self) -> None:
        """After Esc nothing more goes to the device through the transport."""
        written: list[bytes] = []
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(TransferAborted):
            asyncio.run(_transport(queue.Queue(), written, cancel).write(b"x"))
        assert written == [], "the write was refused"

    def test_a_write_reaches_the_port(self) -> None:
        """A write the engine makes reaches the port's write as bytes."""
        written: list[bytes] = []
        asyncio.run(_transport(queue.Queue(), written).write(b"hi"))
        assert written == [b"hi"], "the bytes reached the port"

    def test_a_long_stream_read_byte_by_byte_holds_one_chunk(self) -> None:
        """Reading a thousand chunks one byte at a time keeps the reader's buffer at one chunk."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        chunk = bytes(range(64))
        for _ in range(1000):
            q.put(chunk)
        reader = QueueByteReader(q)
        transport = QueueTransport(reader, lambda data: None, None)

        async def read_all() -> bytes:
            out = bytearray()
            for _ in range(64_000):
                out.append(await transport.read_byte_with_timeout(1))
            return bytes(out)

        # Act
        actual = asyncio.run(read_all())

        # Assert
        assert actual == chunk * 1000, "every byte, in order"
        assert len(reader._buf) <= len(chunk), "the buffer never grows past one chunk"


# -- The far end: the engine behind a socket:// port ---------------------------


class FarEnd:
    """The other end of the serial link: a TCP server the CLI rig's ``socket://`` port connects to.

    ``role`` runs once ``start`` is set, over the library's own
    ``StreamTransport``, so the handler's opening ``drain()`` cannot eat the
    first frame; afterwards, and with no role, the session records every byte
    the rig sends in ``seen`` until the rig disconnects.
    """

    def __init__(self) -> None:
        self.port = 0
        self.start = threading.Event()
        self.seen = bytearray()
        self.role: Role | None = None
        self.result: object = None
        self.error: BaseException | None = None
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve()), daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "the far end listens"

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        server = await asyncio.start_server(self._session, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        async with server:
            await self._stop.wait()

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.to_thread(self.start.wait)
        try:
            if self.role is not None:
                self.result = await self.role(StreamTransport(reader, writer))
            while data := await reader.read(4096):
                self.seen.extend(data)
        except (XYZModemError, OSError, EOFError) as e:
            self.error = e
        finally:
            writer.close()

    def close(self) -> None:
        """Stop listening and end the serving thread."""
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(5)


def _sender(paths: list[Path]) -> Role:
    """A role that sends ``paths`` as one ZMODEM batch, the library at the far end."""

    async def role(transport: StreamTransport) -> int:
        with contextlib.ExitStack() as stack:
            files = [
                (str(path), stack.enter_context(path.open("rb")), path.stat().st_size)
                for path in paths
            ]
            return await ZModem(transport).send(files)

    return role


def _receiver(folder: Path) -> Role:
    """A role that receives one ZMODEM batch into ``folder``."""

    async def role(transport: StreamTransport) -> list[str]:
        return await ZModem(transport).receive(str(folder))

    return role


def _header_only(frame_type: int) -> Role:
    """A role that sends one hex header and then only listens."""

    async def role(transport: StreamTransport) -> None:
        await transport.write(_build_hex_header(frame_type, 0, 0, 0, 0))

    return role


@pytest.fixture
def far_end() -> Iterator[FarEnd]:
    end = FarEnd()
    yield end
    end.close()


@pytest.fixture
def far_cli(tmp_path, far_end: FarEnd) -> Iterator[CLITerminal]:
    """The CLI rig connected to the far end over pyserial's ``socket://`` port, its config at ``tmp_path/rig/rig.cfg``."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **copy.deepcopy(DEFAULT_CFG),
        "serial": {
            **default_serial,
            "port": f"socket://127.0.0.1:{far_end.port}",
            "baud_rate": 115200,
        },
    }
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path
    assert terminal._connect() is True, "the socket port opens"
    yield terminal
    terminal._disconnect()


def _start_on_worker(cli: CLITerminal, far_end: FarEnd, line: str) -> tuple[threading.Thread, list]:
    """Dispatch ``line`` on a worker thread and release the far end once the port is claimed."""
    results: list = []
    worker = threading.Thread(target=lambda: results.append(cli.repl.dispatch_full(line)))
    worker.start()
    _wait_for(lambda: cli.engine.serial_claimed, "the transfer to claim the port")
    far_end.start.set()
    return worker, results


def _dispatch_against(cli: CLITerminal, far_end: FarEnd, line: str):
    """Run ``line`` against the far end to completion and return its result."""
    worker, results = _start_on_worker(cli, far_end, line)
    worker.join(60)
    assert not worker.is_alive(), f"{line} ended"
    return results[0]


def _write_files(folder: Path, **contents: bytes) -> list[Path]:
    """Create ``folder/<name>`` for each keyword; the paths in keyword order."""
    ensure_folder(folder)
    paths = []
    for name, data in contents.items():
        path = folder / name
        path.write_bytes(data)
        paths.append(path)
    return paths


class TestArguments:
    """The argument errors of /xfer.zmodem.send and /xfer.zmodem.recv, before any byte moves."""

    def test_send_needs_a_file(self, cli: CLITerminal) -> None:
        """A bare send is a usage error."""
        result = cli.repl.dispatch_full("/xfer.zmodem.send")
        assert not result.success and "Usage:" in result.error, result.error

    def test_a_missing_file_names_the_file_meant(self, cli: CLITerminal) -> None:
        """A misspelled file is 'File not found' with the near name beside it."""
        _write_files(cli.ctx.fs.cap_dir, fw_v2=b"x")
        result = cli.repl.dispatch_full("/xfer.zmodem.send fw_v3")
        assert result.error.startswith("File not found:"), result.error
        assert "fw_v2" in result.error, "the near name is offered"

    def test_a_missing_directory_is_reported(self, cli: CLITerminal) -> None:
        """A receive into a folder that does not exist fails before it waits."""
        result = cli.repl.dispatch_full("/xfer.zmodem.recv nowhere")
        assert result.error.startswith("Directory not found:"), result.error


@pytest.mark.slow
class TestSendCommand:
    """/xfer.zmodem.send streams files to a ZMODEM receiver and reports what it sent."""

    def test_a_file_lands_at_the_far_end(
        self, far_cli: CLITerminal, far_end: FarEnd, tmp_path
    ) -> None:
        """One file arrives byte-identical under its name, and the reply names it."""
        # Arrange
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        far_end.role = _receiver(inbox)
        _write_files(far_cli.ctx.fs.cap_dir, **{"fw.bin": PAYLOAD})

        # Act
        result = _dispatch_against(far_cli, far_end, "/xfer.zmodem.send fw.bin")

        # Assert
        assert result.success, result.error
        assert (inbox / "fw.bin").read_bytes() == PAYLOAD, "the far end has the file"
        assert result.value == "fw.bin", "the reply names what was sent"

    def test_two_files_go_in_one_batch(
        self, far_cli: CLITerminal, far_end: FarEnd, tmp_path
    ) -> None:
        """Two names on the line are one session with two files."""
        # Arrange
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        far_end.role = _receiver(inbox)
        _write_files(far_cli.ctx.fs.cap_dir, **{"a.bin": PAYLOAD, "b.txt": b"second\n"})

        # Act
        result = _dispatch_against(far_cli, far_end, "/xfer.zmodem.send a.bin b.txt")

        # Assert
        assert result.success, result.error
        actual = sorted(path.name for path in inbox.iterdir())
        assert actual == ["a.bin", "b.txt"], "both files arrived"
        assert (inbox / "b.txt").read_bytes() == b"second\n", "the second is intact"

    def test_twice_in_a_row_on_one_connection(
        self, far_cli: CLITerminal, far_end: FarEnd, tmp_path
    ) -> None:
        """A second send after a first works on the same link: no state leaks between transfers."""
        # Arrange -- the far end receives two sessions in turn
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()

        async def role(transport: StreamTransport) -> list[str]:
            engine = ZModem(transport)
            return await engine.receive(str(first)) + await engine.receive(str(second))

        far_end.role = role
        _write_files(far_cli.ctx.fs.cap_dir, **{"one.bin": PAYLOAD, "two.bin": PAYLOAD[::-1]})

        # Act
        result_one = _dispatch_against(far_cli, far_end, "/xfer.zmodem.send one.bin")
        result_two = _dispatch_against(far_cli, far_end, "/xfer.zmodem.send two.bin")

        # Assert
        assert (result_one.success, result_two.success) == (True, True), (
            result_one.error,
            result_two.error,
        )
        assert (first / "one.bin").read_bytes() == PAYLOAD, "the first session's file"
        assert (second / "two.bin").read_bytes() == PAYLOAD[::-1], "the second session's file"

    def test_a_cancel_mid_send_tells_the_device(
        self, far_cli: CLITerminal, far_end: FarEnd
    ) -> None:
        """Esc while the far end stalls fails the send and puts the cancel sequence on the wire."""
        # Arrange -- the far end answers ZRINIT and then never sends ZRPOS
        far_end.role = _header_only(ZRINIT)
        _write_files(far_cli.ctx.fs.cap_dir, **{"fw.bin": PAYLOAD})
        worker, results = _start_on_worker(far_cli, far_end, "/xfer.zmodem.send fw.bin")
        _wait_for(lambda: len(far_end.seen) > 0, "the ZFILE frame to reach the far end")

        # Act
        far_cli._xfer_cancel.set()
        worker.join(30)

        # Assert
        assert not worker.is_alive(), "the cancel ended the send"
        assert (results[0].success, results[0].error) == (False, "ZMODEM send canceled.")
        _wait_for(lambda: far_end.seen.endswith(ZMODEM_CANCEL), "the cancel sequence")


@pytest.mark.slow
class TestRecvCommand:
    """/xfer.zmodem.recv lands what the sender names, into the folder, only when the transfer completes."""

    @pytest.mark.rule("xfer-recv-staged")
    def test_a_completed_receive_replaces_the_file_at_the_name(
        self, far_cli: CLITerminal, far_end: FarEnd, tmp_path
    ) -> None:
        """A finished transfer replaces an older file at the name and leaves no staging folder."""
        # Arrange
        (src,) = _write_files(tmp_path / "out", **{"data.bin": PAYLOAD})
        far_end.role = _sender([src])
        cap = far_cli.ctx.fs.cap_dir
        (target,) = _write_files(cap, **{"data.bin": b"old contents"})

        # Act
        result = _dispatch_against(far_cli, far_end, "/xfer.zmodem.recv")

        # Assert
        assert result.success, result.error
        assert target.read_bytes() == PAYLOAD, "the new content replaced the old"
        assert list(cap.glob(".part-*")) == [], "the staging folder is gone"
        assert (result.value, result.data) == (str(cap), [str(target)]), "the reply names the file"

    def test_a_receive_into_a_bare_config_creates_cap(
        self, far_cli: CLITerminal, far_end: FarEnd, tmp_path
    ) -> None:
        """With no cap/ yet, the receive creates it for the incoming file."""
        # Arrange
        (src,) = _write_files(tmp_path / "out", **{"fresh.bin": PAYLOAD})
        far_end.role = _sender([src])
        cap = far_cli.ctx.fs.cap_dir
        assert not cap.exists(), "a bare config has no cap/"

        # Act
        result = _dispatch_against(far_cli, far_end, "/xfer.zmodem.recv")

        # Assert
        assert result.success, result.error
        assert (cap / "fresh.bin").read_bytes() == PAYLOAD, "cap/ appeared with the file in it"

    def test_two_files_in_one_session(
        self, far_cli: CLITerminal, far_end: FarEnd, tmp_path
    ) -> None:
        """A two-file batch lands both, in the order sent."""
        # Arrange
        sources = _write_files(tmp_path / "out", **{"a.bin": PAYLOAD, "b.txt": b"second\n"})
        far_end.role = _sender(sources)
        cap = far_cli.ctx.fs.cap_dir

        # Act
        result = _dispatch_against(far_cli, far_end, "/xfer.zmodem.recv")

        # Assert
        assert result.success, result.error
        assert result.data == [str(cap / "a.bin"), str(cap / "b.txt")], "both landed, named"
        assert (cap / "b.txt").read_bytes() == b"second\n", "the second is intact"

    def test_a_second_file_is_fully_present(
        self, far_cli: CLITerminal, far_end: FarEnd, tmp_path
    ) -> None:
        """The second file of a batch is whole, not just its first block: the receive loop does not stop early."""
        # Arrange -- two files, the second larger than one subpacket so a
        # one-block-only bug would truncate it
        big = PAYLOAD * 3
        sources = _write_files(tmp_path / "out", **{"a.bin": b"first\n", "b.bin": big})
        far_end.role = _sender(sources)
        cap = far_cli.ctx.fs.cap_dir

        # Act
        result = _dispatch_against(far_cli, far_end, "/xfer.zmodem.recv")

        # Assert
        assert result.success, result.error
        assert (cap / "b.bin").read_bytes() == big, "every block of the second file landed"

    @pytest.mark.rule("xfer-recv-staged")
    def test_a_canceled_receive_leaves_the_old_file(
        self, far_cli: CLITerminal, far_end: FarEnd
    ) -> None:
        """Esc while nothing arrives fails the receive, keeps the file at the name and tells the device."""
        # Arrange -- a silent far end; the receive waits for a header
        far_end.start.set()
        cap = far_cli.ctx.fs.cap_dir
        (target,) = _write_files(cap, **{"keep.bin": b"precious"})
        worker, results = _start_on_worker(far_cli, far_end, "/xfer.zmodem.recv")
        _wait_for(lambda: bool(list(cap.glob(".part-*"))), "the receive to open its staging folder")

        # Act
        far_cli._xfer_cancel.set()
        worker.join(30)

        # Assert
        assert not worker.is_alive(), "the cancel ended the receive"
        assert (results[0].success, results[0].error) == (False, "ZMODEM recv canceled.")
        assert target.read_bytes() == b"precious", "the file at the name survived"
        assert list(cap.glob(".part-*")) == [], "the staging folder is gone"
        _wait_for(lambda: far_end.seen.endswith(ZMODEM_CANCEL), "the cancel sequence")

    def test_a_remote_cancel_is_a_failure(self, far_cli: CLITerminal, far_end: FarEnd) -> None:
        """A ZABORT from the device ends the receive as a failure that says who canceled."""
        # Arrange
        far_end.role = _header_only(ZABORT)
        cap = far_cli.ctx.fs.cap_dir
        (target,) = _write_files(cap, **{"keep.bin": b"precious"})

        # Act
        result = _dispatch_against(far_cli, far_end, "/xfer.zmodem.recv")

        # Assert
        assert (result.success, result.error) == (False, "ZMODEM recv canceled by the device.")
        assert target.read_bytes() == b"precious", "nothing at the name changed"


# -- The DEMO device's own ZMODEM peer ----------------------------------------


@pytest.mark.slow
class TestDemoDevice:
    """termapy --demo speaks ZMODEM: /xfer.zmodem round-trips against the device's own engine."""

    def test_a_pull_lands_the_device_file(self, cli: CLITerminal) -> None:
        """AT+ZMODEM=SEND then /xfer.zmodem.recv pulls the device's file into cap/."""
        # Arrange -- the device sends its canned firmware over ZMODEM
        cli.repl.dispatch_full("AT+ZMODEM=SEND firmware_v1.bin")

        # Act
        result = cli.repl.dispatch_full("/xfer.zmodem.recv")

        # Assert
        assert result.success, result.error
        landed = cli.ctx.fs.cap_dir / "firmware_v1.bin"
        assert landed.read_bytes() == bytes(i & 0xFF for i in range(2048)), "the canned payload"

    def test_a_push_reaches_the_device_vfs(self, cli: CLITerminal) -> None:
        """AT+ZMODEM=RECV then /xfer.zmodem.send puts the host's file on the device."""
        # Arrange
        cap = cli.ctx.fs.cap_dir
        (src,) = _write_files(cap, **{"upload.bin": PAYLOAD})
        device = cli.engine.port_obj
        assert isinstance(device, FakeSerial), "the DEMO engine opens a FakeSerial"
        cli.repl.dispatch_full("AT+ZMODEM=RECV")

        # Act
        result = cli.repl.dispatch_full("/xfer.zmodem.send upload.bin")

        # Assert
        assert result.success, result.error
        _wait_for(lambda: "upload.bin" in device.vfs, "the device to store the file")
        assert device.vfs["upload.bin"] == src.read_bytes(), "the pushed bytes are on the device"

    def test_the_device_leaks_no_bytes_before_the_transfer_claims(self, cli: CLITerminal) -> None:
        """AT+ZMODEM=SEND must not emit a protocol byte until the host starts: the device peer waits.

        Found in manual use: the device's opening ZRINIT / ZRQINIT reached the
        terminal in the gap before /xfer.zmodem.recv claimed the port, printing
        as noise and parsing its last line as a stray command.
        """
        # Arrange
        device = cli.engine.port_obj
        assert isinstance(device, FakeSerial), "the DEMO engine opens a FakeSerial"

        # Act -- arm the device sender, then look at what it has queued
        cli.repl.dispatch_full("AT+ZMODEM=SEND firmware_v1.bin")
        _settle = 0.2
        time.sleep(_settle)
        leaked = device.in_waiting

        # Assert -- nothing is waiting: the peer holds its header until the host speaks
        assert leaked == 0, f"the device queued {leaked} bytes before the host started the transfer"
        # And the transfer still completes once the host does start.
        result = cli.repl.dispatch_full("/xfer.zmodem.recv")
        assert result.success, result.error

    def test_no_protocol_tail_survives_the_transfer(self, cli: CLITerminal) -> None:
        """After a pull completes, the device's output buffer is empty: a trailing ZFIN was drained."""
        # Arrange / Act
        cli.repl.dispatch_full("AT+ZMODEM=SEND firmware_v1.bin")
        result = cli.repl.dispatch_full("/xfer.zmodem.recv")
        device = cli.engine.port_obj
        assert isinstance(device, FakeSerial), "the DEMO engine opens a FakeSerial"

        # Assert
        assert result.success, result.error
        peer = device._zmodem_peer
        assert peer is not None, "AT+ZMODEM=SEND created the peer"
        _wait_for(lambda: not peer.active, "the device peer to finish")
        assert device.in_waiting == 0, "no protocol tail was left for the next prompt to parse"


# -- lrzsz: the reference implementation over pipes ---------------------------

_SZ = shutil.which("sz")
_RZ = shutil.which("rz")


def _pump(stream: IO[bytes], rx: queue.Queue[bytes]) -> None:
    """Move a process's output into the rx queue, chunk by chunk, until it closes."""
    fd = stream.fileno()
    while data := os.read(fd, 4096):
        rx.put(data)


def _piped(argv: list[str], cwd: Path) -> tuple[subprocess.Popen[bytes], QueueTransport]:
    """Start ``argv`` with its stdin and stdout bridged to a transport, as a serial link would be."""
    proc = subprocess.Popen(
        argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    assert proc.stdin is not None and proc.stdout is not None, "pipes requested"
    rx: queue.Queue[bytes] = queue.Queue()
    threading.Thread(target=_pump, args=(proc.stdout, rx), daemon=True).start()
    stdin = proc.stdin

    def write(data: bytes) -> None:
        stdin.write(data)
        stdin.flush()

    return proc, QueueTransport(QueueByteReader(rx), write, None)


@pytest.mark.slow
@pytest.mark.skipif(_SZ is None or _RZ is None, reason="lrzsz (sz and rz) is not installed")
class TestLrzszInterop:
    """termapy's ZMODEM against the reference implementation, lrzsz's sz and rz, over pipes."""

    def test_receive_from_sz(self, tmp_path) -> None:
        """What sz sends lands byte-identical under its name, and sz exits clean."""
        # Arrange
        (src,) = _write_files(tmp_path / "out", **{"from_sz.bin": PAYLOAD})
        inbox = tmp_path / "inbox"
        assert _SZ is not None
        proc, transport = _piped([_SZ, "-b", "-q", str(src)], tmp_path)

        # Act
        with staged_receive(inbox) as staging:
            receive_files(transport, staging.folder, _no_progress)
            landed = staging.commit()

        # Assert
        assert proc.wait(10) == 0, "sz saw a complete session"
        assert landed == [inbox / "from_sz.bin"], "the file is in place"
        assert (inbox / "from_sz.bin").read_bytes() == PAYLOAD, "byte-identical"

    def test_send_two_files_to_rz(self, tmp_path) -> None:
        """A two-file batch reaches rz intact, and rz exits clean."""
        # Arrange
        sources = _write_files(tmp_path / "out", **{"a.bin": PAYLOAD, "b.txt": b"second\n"})
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        assert _RZ is not None
        proc, transport = _piped([_RZ, "-b", "-q"], inbox)

        # Act
        sent = send_files(transport, sources, _no_progress)

        # Assert
        assert proc.wait(10) == 0, "rz saw a complete session"
        assert sent == len(PAYLOAD) + len(b"second\n"), "every data byte was counted"
        assert (inbox / "a.bin").read_bytes() == PAYLOAD, "the first file"
        assert (inbox / "b.txt").read_bytes() == b"second\n", "the second file"
