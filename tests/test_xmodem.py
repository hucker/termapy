"""Tests for XMODEM file transfer — QueueByteReader, FakeSerial responder, and protocol integration."""

from __future__ import annotations

import queue
import time

import pytest
from xmodem import XMODEM

from termapy.builtins.plugins.xmodem_xfer import QueueByteReader
from termapy.demo import FakeSerial, _xmodem_crc16


# -- QueueByteReader tests -------------------------------------------------


class TestQueueByteReader:
    def test_getc_single_byte(self) -> None:
        """getc(1) returns exactly 1 byte from a queued chunk."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        reader = QueueByteReader(q)
        q.put(b"\x41\x42\x43")

        # Act
        actual = reader.getc(1, timeout=1)

        # Assert
        assert actual == b"\x41"  # first byte of chunk

    def test_getc_exact_chunk(self) -> None:
        """getc(N) returns exactly N bytes when chunk matches."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        reader = QueueByteReader(q)
        q.put(b"\x01\x02\x03\x04")

        # Act
        actual = reader.getc(4, timeout=1)

        # Assert
        assert actual == b"\x01\x02\x03\x04"  # full chunk

    def test_getc_across_chunks(self) -> None:
        """getc assembles bytes from multiple queued chunks."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        reader = QueueByteReader(q)
        q.put(b"\x01\x02")
        q.put(b"\x03\x04")

        # Act
        actual = reader.getc(4, timeout=1)

        # Assert
        assert actual == b"\x01\x02\x03\x04"  # assembled from 2 chunks

    def test_getc_preserves_remainder(self) -> None:
        """Leftover bytes from one getc are available to the next."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        reader = QueueByteReader(q)
        q.put(b"\x01\x02\x03\x04\x05")

        # Act
        first = reader.getc(2, timeout=1)
        second = reader.getc(3, timeout=1)

        # Assert
        assert first == b"\x01\x02"  # first 2 bytes
        assert second == b"\x03\x04\x05"  # remaining 3 bytes

    def test_getc_128_bytes(self) -> None:
        """getc(128) returns a full XMODEM data block."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        reader = QueueByteReader(q)
        expected = bytes(range(128))
        q.put(expected)

        # Act
        actual = reader.getc(128, timeout=1)

        # Assert
        assert actual == expected  # full 128-byte block

    def test_getc_timeout_returns_none(self) -> None:
        """getc returns None when queue is empty and timeout expires."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        reader = QueueByteReader(q)

        # Act
        t0 = time.monotonic()
        actual = reader.getc(1, timeout=0.1)
        elapsed = time.monotonic() - t0

        # Assert
        assert actual is None  # timed out
        assert elapsed < 0.5  # didn't hang

    def test_getc_partial_then_timeout(self) -> None:
        """getc returns None if not enough bytes arrive before timeout."""
        # Arrange
        q: queue.Queue[bytes] = queue.Queue()
        reader = QueueByteReader(q)
        q.put(b"\x01")  # only 1 byte, but asking for 4

        # Act
        actual = reader.getc(4, timeout=0.1)

        # Assert
        assert actual is None  # not enough bytes


# -- XMODEM CRC-16 tests --------------------------------------------------


class TestXmodemCrc16:
    def test_empty_data(self) -> None:
        actual = _xmodem_crc16(b"")
        assert actual == 0x0000  # CRC of empty data

    def test_known_value(self) -> None:
        """Verify against known XMODEM CRC-16 test vector."""
        # "123456789" has XMODEM CRC-16 = 0x31C3
        actual = _xmodem_crc16(b"123456789")
        assert actual == 0x31C3  # standard test vector

    def test_single_byte(self) -> None:
        actual = _xmodem_crc16(b"\x00")
        assert isinstance(actual, int)  # returns int
        assert 0 <= actual <= 0xFFFF  # 16-bit range


# -- FakeSerial XMODEM responder tests -------------------------------------


def _send_cmd(dev: FakeSerial, cmd: str) -> str:
    """Send an ASCII command and return the text response."""
    dev.write(cmd.encode() + b"\r")
    time.sleep(0.01)
    return dev.read(4096).decode(errors="replace")


@pytest.fixture
def dev() -> FakeSerial:
    """Create a FakeSerial instance for testing."""
    fs = FakeSerial(baudrate=9600)
    fs.timeout = 0.5
    return fs


class TestFakeSerialXmodemRecv:
    """Test FakeSerial in XMODEM receive mode (device receives from host)."""

    def test_recv_mode_starts_with_nak(self, dev: FakeSerial) -> None:
        """AT+XMODEM=RECV returns OK then NAK to initiate transfer."""
        # Act
        dev.write(b"AT+XMODEM=RECV\r")
        time.sleep(0.01)
        response = dev.read(4096)

        # Assert
        assert b"OK\r\n" in response  # command accepted
        assert response[-1] == 0x15  # ends with NAK

    def test_recv_single_block_checksum(self, dev: FakeSerial) -> None:
        """Device ACKs a valid checksum-mode block and stores to VFS."""
        # Arrange
        dev.write(b"AT+XMODEM=RECV upload.bin\r")
        time.sleep(0.01)
        dev.read(4096)  # consume OK + NAK

        data = bytes(range(128))
        cksum = sum(data) & 0xFF
        block = bytes([0x01, 0x01, 0xFE]) + data + bytes([cksum])

        # Act
        dev.write(block)
        time.sleep(0.01)
        response = dev.read(4096)

        # Assert
        assert response == bytes([0x06])  # ACK

        # Send EOT
        dev.write(bytes([0x04]))
        time.sleep(0.01)
        eot_response = dev.read(4096)
        assert eot_response == bytes([0x06])  # ACK for EOT

        assert dev.xmodem_received_data == data  # data stored correctly
        assert dev.vfs["upload.bin"] == data  # stored in VFS

    def test_recv_bad_checksum_naks(self, dev: FakeSerial) -> None:
        """Device NAKs a block with invalid checksum."""
        # Arrange
        dev.write(b"AT+XMODEM=RECV\r")
        time.sleep(0.01)
        dev.read(4096)  # consume OK + NAK

        data = bytes(128)
        bad_cksum = 0xFF  # wrong checksum
        block = bytes([0x01, 0x01, 0xFE]) + data + bytes([bad_cksum])

        # Act
        dev.write(block)
        time.sleep(0.01)
        response = dev.read(4096)

        # Assert
        assert response == bytes([0x15])  # NAK

    def test_recv_bad_block_complement_naks(self, dev: FakeSerial) -> None:
        """Device NAKs a block where blk + ~blk != 0xFF."""
        # Arrange
        dev.write(b"AT+XMODEM=RECV\r")
        time.sleep(0.01)
        dev.read(4096)

        data = bytes(128)
        cksum = sum(data) & 0xFF
        # blk=1, ~blk should be 0xFE, but send 0x00
        block = bytes([0x01, 0x01, 0x00]) + data + bytes([cksum])

        # Act
        dev.write(block)
        time.sleep(0.01)
        response = dev.read(4096)

        # Assert
        assert response == bytes([0x15])  # NAK

    def test_recv_invalid_command(self, dev: FakeSerial) -> None:
        """AT+XMODEM without = returns error."""
        actual = _send_cmd(dev, "AT+XMODEM")
        assert "ERROR" in actual  # bad usage


class TestFakeSerialXmodemSend:
    """Test FakeSerial in XMODEM send mode (device sends to host)."""

    def test_send_mode_waits_for_nak(self, dev: FakeSerial) -> None:
        """AT+XMODEM=SEND returns OK and waits for NAK before sending."""
        # Act
        dev.write(b"AT+XMODEM=SEND config.dat\r")
        time.sleep(0.01)
        response = dev.read(4096)

        # Assert
        assert response == b"OK\r\n"  # no data block yet

    def test_send_nak_triggers_first_block(self, dev: FakeSerial) -> None:
        """Sending NAK starts transmission of first block."""
        # Arrange
        dev.write(b"AT+XMODEM=SEND config.dat\r")
        time.sleep(0.01)
        dev.read(4096)  # consume OK

        # Act — send NAK to start
        dev.write(bytes([0x15]))
        time.sleep(0.01)
        block = dev.read(4096)

        # Assert
        assert block[0] == 0x01  # SOH
        assert block[1] == 0x01  # block number 1
        assert block[2] == 0xFE  # complement
        assert len(block) == 132  # SOH + blk + ~blk + 128 data + checksum

    def test_send_crc_mode_with_c(self, dev: FakeSerial) -> None:
        """Sending 'C' starts CRC mode transmission."""
        # Arrange
        dev.write(b"AT+XMODEM=SEND config.dat\r")
        time.sleep(0.01)
        dev.read(4096)

        # Act — send 'C' for CRC mode
        dev.write(b"C")
        time.sleep(0.01)
        block = dev.read(4096)

        # Assert
        assert block[0] == 0x01  # SOH
        assert len(block) == 133  # SOH + blk + ~blk + 128 data + 2 CRC bytes

    def test_send_full_transfer_checksum(self, dev: FakeSerial) -> None:
        """Complete checksum-mode transfer of config.dat (1 block, padded)."""
        # Arrange — config.dat is 64 bytes = 1 block padded to 128
        dev.write(b"AT+XMODEM=SEND config.dat\r")
        time.sleep(0.01)
        dev.read(4096)

        # Block 1
        dev.write(bytes([0x15]))  # NAK to start
        time.sleep(0.01)
        block1 = dev.read(4096)
        assert block1[0] == 0x01  # SOH
        data1 = block1[3:131]
        expected_cksum = sum(data1) & 0xFF
        assert block1[131] == expected_cksum  # checksum valid

        # ACK block 1, expect EOT
        dev.write(bytes([0x06]))
        time.sleep(0.01)
        eot = dev.read(4096)
        assert eot == bytes([0x04])  # EOT

        # Assert — first 64 bytes match config.dat, rest is 0x1A padding
        assert data1[:64] == bytes(range(64))  # config.dat content
        assert all(b == 0x1A for b in data1[64:])  # XMODEM padding

    def test_send_full_transfer_crc(self, dev: FakeSerial) -> None:
        """Complete CRC-mode transfer of config.dat (1 block, padded)."""
        # Arrange
        dev.write(b"AT+XMODEM=SEND config.dat\r")
        time.sleep(0.01)
        dev.read(4096)

        # Block 1 — CRC mode
        dev.write(b"C")
        time.sleep(0.01)
        block1 = dev.read(4096)
        assert block1[0] == 0x01  # SOH
        data1 = block1[3:131]
        expected_crc = _xmodem_crc16(data1)
        actual_crc = (block1[131] << 8) | block1[132]
        assert actual_crc == expected_crc  # CRC valid

        # ACK block 1, expect EOT
        dev.write(bytes([0x06]))
        time.sleep(0.01)
        eot = dev.read(4096)
        assert eot == bytes([0x04])  # EOT

        # Assert — first 64 bytes match config.dat
        assert data1[:64] == bytes(range(64))  # config.dat content


# -- Integration: xmodem library + FakeSerial ------------------------------


class TestXmodemLibraryIntegration:
    """Test the xmodem PyPI library against the FakeSerial responder."""

    def _make_modem(self, dev: FakeSerial) -> XMODEM:
        """Create an XMODEM instance wired to a FakeSerial device."""
        def getc(size: int, timeout: int = 1) -> bytes | None:
            data = dev.read(size)
            return data if data else None

        def putc(data: bytes, timeout: int = 1) -> int:
            return dev.write(data)

        return XMODEM(getc, putc)

    @staticmethod
    def _enter_xmodem_mode(dev: FakeSerial, mode: str) -> None:
        """Send AT+XMODEM command and consume the OK text response.

        Leaves only the XMODEM protocol bytes in the output buffer
        so the xmodem library sees clean protocol data.

        Args:
            dev: FakeSerial device.
            mode: "RECV" or "SEND".
        """
        dev.write(f"AT+XMODEM={mode}\r".encode())
        time.sleep(0.01)
        # Read everything — OK text + possible protocol byte
        resp = dev.read(4096)
        # Strip the OK text, put back any trailing protocol bytes
        ok_text = b"OK\r\n"
        if resp.startswith(ok_text) and len(resp) > len(ok_text):
            dev._output_buf.extend(resp[len(ok_text):])

    def test_library_send_to_device(self, dev: FakeSerial, tmp_path) -> None:
        """xmodem library sends a file to FakeSerial VFS."""
        # Arrange
        test_data = b"Hello XMODEM! " * 20  # 280 bytes = 3 blocks
        src_file = tmp_path / "send_test.bin"
        src_file.write_bytes(test_data)

        self._enter_xmodem_mode(dev, "RECV upload.bin")

        modem = self._make_modem(dev)

        # Act
        with open(src_file, "rb") as f:
            ok = modem.send(f)

        # Assert
        assert ok is True  # transfer succeeded
        received = dev.vfs["upload.bin"]
        assert received[:len(test_data)] == test_data  # data matches
        # Padding is 0x1A (SUB) — XMODEM has no size metadata
        padding = received[len(test_data):]
        assert all(b == 0x1A for b in padding)  # correct padding

    def test_library_recv_from_device(self, dev: FakeSerial, tmp_path) -> None:
        """xmodem library receives config.dat from FakeSerial VFS."""
        # Arrange
        dst_file = tmp_path / "recv_test.bin"

        self._enter_xmodem_mode(dev, "SEND config.dat")

        modem = self._make_modem(dev)

        # Act
        with open(dst_file, "wb") as f:
            ok = modem.recv(f)

        # Assert
        assert ok is not None  # transfer succeeded
        received = dst_file.read_bytes()
        assert received[:64] == bytes(range(64))  # config.dat content

    def test_roundtrip(self, dev: FakeSerial, tmp_path) -> None:
        """Send a file to device VFS, then verify the VFS has it."""
        # Arrange
        test_data = bytes(range(200))
        src_file = tmp_path / "roundtrip.bin"
        src_file.write_bytes(test_data)

        self._enter_xmodem_mode(dev, "RECV roundtrip.bin")

        modem = self._make_modem(dev)

        # Act
        with open(src_file, "rb") as f:
            ok = modem.send(f)

        # Assert
        assert ok is True  # transfer succeeded
        received = dev.vfs["roundtrip.bin"]
        assert received[:len(test_data)] == test_data  # roundtrip data matches
