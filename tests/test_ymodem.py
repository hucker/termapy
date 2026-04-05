"""Tests for YMODEM file transfer — FakeSerial responder and ymodem library integration."""

from __future__ import annotations

import time

import pytest

from termapy.demo import FakeSerial, _xmodem_crc16


# -- Helpers ----------------------------------------------------------------

def _send_cmd(dev: FakeSerial, cmd: str) -> str:
    """Send an ASCII command and return the text response."""
    dev.write(cmd.encode() + b"\r")
    time.sleep(0.01)
    return dev.read(4096).decode(errors="replace")


def _enter_ymodem_mode(dev: FakeSerial, mode: str) -> None:
    """Send AT+YMODEM command and consume the OK text response.

    Leaves only YMODEM protocol bytes in the output buffer.

    Args:
        dev: FakeSerial device.
        mode: "RECV" or "SEND".
    """
    dev.write(f"AT+YMODEM={mode}\r".encode())
    time.sleep(0.01)
    resp = dev.read(4096)
    ok_text = b"OK\r\n"
    if resp.startswith(ok_text) and len(resp) > len(ok_text):
        dev._output_buf.extend(resp[len(ok_text):])


SOH = 0x01
STX = 0x02
EOT = 0x04
ACK = 0x06
NAK = 0x15
CRC_START = ord("C")
CAN = 0x18


def _build_block(block_num: int, data: bytes, use_stx: bool = False) -> bytes:
    """Build a YMODEM block with CRC-16.

    Args:
        block_num: Block sequence number (0-255).
        data: Payload data (128 or 1024 bytes).
        use_stx: Use STX (1024-byte) header instead of SOH.

    Returns:
        Complete block with header and CRC trailer.
    """
    header_byte = STX if use_stx else SOH
    blk = block_num & 0xFF
    crc = _xmodem_crc16(data)
    return bytes([header_byte, blk, 0xFF - blk]) + data + bytes([crc >> 8, crc & 0xFF])


def _build_header_block(filename: str, filesize: int) -> bytes:
    """Build a YMODEM block 0 (header with filename and size)."""
    payload = filename.encode("ascii") + b"\x00" + str(filesize).encode("ascii") + b" 0\x00"
    payload = payload.ljust(128, b"\x00")
    return _build_block(0, payload)


def _build_empty_header() -> bytes:
    """Build an empty YMODEM block 0 (batch end)."""
    return _build_block(0, b"\x00" * 128)


@pytest.fixture
def dev() -> FakeSerial:
    """Create a FakeSerial instance for testing."""
    fs = FakeSerial(baudrate=9600)
    fs.timeout = 1.0
    return fs


# -- FakeSerial YMODEM recv tests ------------------------------------------


class TestFakeSerialYmodemRecv:
    """Test FakeSerial in YMODEM receive mode (device receives from host)."""

    def test_recv_mode_starts_with_c(self, dev: FakeSerial) -> None:
        """AT+YMODEM=RECV returns OK then 'C' to request CRC mode."""
        # Act
        dev.write(b"AT+YMODEM=RECV\r")
        time.sleep(0.01)
        response = dev.read(4096)

        # Assert
        assert b"OK\r\n" in response  # command accepted
        assert response[-1] == CRC_START  # ends with 'C'

    def test_recv_header_and_data(self, dev: FakeSerial) -> None:
        """Device receives header block, then data blocks, then EOT."""
        # Arrange
        _enter_ymodem_mode(dev, "RECV")
        # Consume the initial 'C' that requests CRC mode
        initial = dev.read(4096)
        assert initial == bytes([CRC_START])  # initial C consumed

        test_data = bytes(range(100))

        # Send header block 0 (filename + size)
        header = _build_header_block("test.bin", len(test_data))
        dev.write(header)
        time.sleep(0.01)
        resp = dev.read(4096)
        # Should get ACK + 'C' (ack header, request data)
        assert resp == bytes([ACK, CRC_START])  # ACK header, then C for data

        # Send data block 1 (SOH, 128 bytes, padded)
        padded = test_data + b"\x1a" * (128 - len(test_data))
        data_block = _build_block(1, padded)
        dev.write(data_block)
        time.sleep(0.01)
        resp = dev.read(4096)
        assert resp == bytes([ACK])  # data block ACKed

        # Send first EOT
        dev.write(bytes([EOT]))
        time.sleep(0.01)
        resp = dev.read(4096)
        assert resp == bytes([NAK])  # first EOT gets NAK

        # Send second EOT
        dev.write(bytes([EOT]))
        time.sleep(0.01)
        resp = dev.read(4096)
        assert resp == bytes([ACK, CRC_START])  # ACK + C for next file

        # Send empty header (batch end)
        dev.write(_build_empty_header())
        time.sleep(0.01)
        resp = dev.read(4096)
        assert resp == bytes([ACK])  # batch end acknowledged

        # Verify received data in VFS
        assert "test.bin" in dev.vfs  # file stored in VFS
        assert dev.vfs["test.bin"] == test_data  # data matches (trimmed to size)
        assert dev.ymodem_received_name == "test.bin"  # filename parsed
        assert dev.ymodem_received_size == len(test_data)  # size parsed

    def test_recv_bad_crc_naks(self, dev: FakeSerial) -> None:
        """Device NAKs a block with invalid CRC."""
        # Arrange
        _enter_ymodem_mode(dev, "RECV")
        dev.read(4096)  # consume initial 'C'

        # Build header with bad CRC
        payload = b"test.bin\x00200\x00".ljust(128, b"\x00")
        bad_block = bytes([SOH, 0x00, 0xFF]) + payload + bytes([0xFF, 0xFF])

        # Act
        dev.write(bad_block)
        time.sleep(0.01)
        resp = dev.read(4096)

        # Assert
        assert resp == bytes([NAK])  # bad CRC rejected

    def test_recv_invalid_command(self, dev: FakeSerial) -> None:
        """AT+YMODEM without = returns error."""
        actual = _send_cmd(dev, "AT+YMODEM")
        assert "ERROR" in actual  # bad usage


# -- FakeSerial YMODEM send tests ------------------------------------------


class TestFakeSerialYmodemSend:
    """Test FakeSerial in YMODEM send mode (device sends to host)."""

    def test_send_mode_waits_for_c(self, dev: FakeSerial) -> None:
        """AT+YMODEM=SEND returns OK and waits."""
        # Act
        dev.write(b"AT+YMODEM=SEND firmware_v1.bin\r")
        time.sleep(0.01)
        response = dev.read(4096)

        # Assert
        assert response == b"OK\r\n"  # no data yet

    def test_send_c_triggers_header(self, dev: FakeSerial) -> None:
        """Sending 'C' triggers header block 0 with filename and size."""
        # Arrange
        _enter_ymodem_mode(dev, "SEND firmware_v1.bin")

        # Act
        dev.write(b"C")
        time.sleep(0.01)
        block = dev.read(4096)

        # Assert
        assert block[0] == SOH  # SOH for 128-byte block 0
        assert block[1] == 0x00  # block number 0
        assert block[2] == 0xFF  # complement
        data = block[3:131]
        # Parse filename
        null_idx = data.index(0x00)
        filename = data[:null_idx].decode("ascii")
        assert filename == "firmware_v1.bin"  # canned filename

    def test_send_full_transfer(self, dev: FakeSerial) -> None:
        """Complete YMODEM transfer of canned 2048-byte payload."""
        # Arrange
        _enter_ymodem_mode(dev, "SEND firmware_v1.bin")

        # Send 'C' to get header block 0
        dev.write(b"C")
        time.sleep(0.01)
        header_block = dev.read(4096)
        assert header_block[0] == SOH  # header is SOH
        data = header_block[3:131]
        null_idx = data.index(0x00)
        filename = data[:null_idx].decode("ascii")
        assert filename == "firmware_v1.bin"

        # ACK header
        dev.write(bytes([ACK]))
        time.sleep(0.01)
        # Should get nothing — waiting for 'C' to start data
        resp = dev.read(4096)
        assert resp == b""  # waiting for C

        # Send 'C' to start data transfer
        dev.write(b"C")
        time.sleep(0.01)
        block1 = dev.read(4096)
        assert block1[0] == STX  # data blocks use STX (1024 bytes)
        assert block1[1] == 0x01  # block sequence 1 (after header block 0)
        data1 = block1[3:1027]
        crc = _xmodem_crc16(data1)
        actual_crc = (block1[1027] << 8) | block1[1028]
        assert actual_crc == crc  # CRC valid

        received_data = bytearray(data1)

        # ACK block 1, get block 2
        dev.write(bytes([ACK]))
        time.sleep(0.01)
        block2 = dev.read(4096)
        assert block2[0] == STX  # STX
        assert block2[1] == 0x02  # block sequence 2
        data2 = block2[3:1027]
        received_data.extend(data2)

        # ACK block 2, expect EOT
        dev.write(bytes([ACK]))
        time.sleep(0.01)
        eot1 = dev.read(4096)
        assert eot1 == bytes([EOT])  # first EOT

        # NAK the first EOT
        dev.write(bytes([NAK]))
        time.sleep(0.01)
        eot2 = dev.read(4096)
        assert eot2 == bytes([EOT])  # second EOT

        # ACK the second EOT
        dev.write(bytes([ACK]))
        time.sleep(0.01)

        # Send 'C' for batch end
        dev.write(b"C")
        time.sleep(0.01)
        batch_end = dev.read(4096)
        assert batch_end[0] == SOH  # empty header block
        batch_data = batch_end[3:131]
        assert batch_data[0] == 0x00  # empty filename = batch end

        # ACK batch end
        dev.write(bytes([ACK]))
        time.sleep(0.01)

        # Verify data — canned payload is bytes(i & 0xFF for i in range(2048))
        expected = bytes(i & 0xFF for i in range(2048))
        assert bytes(received_data) == expected  # full payload matches


# -- Integration: ymodem library + FakeSerial ------------------------------


class TestYmodemLibraryIntegration:
    """Test the ymodem PyPI library against the FakeSerial responder."""

    @staticmethod
    def _make_read_write(dev: FakeSerial):
        """Create read/write callables for ModemSocket."""
        def read(size: int, timeout: float | None = None) -> bytes:
            old_timeout = dev.timeout
            dev.timeout = timeout if timeout is not None else 1.0
            data = dev.read(size)
            dev.timeout = old_timeout
            return data

        def write(data: bytes | bytearray, timeout: float | None = None) -> int:
            return dev.write(bytes(data))

        return read, write

    def test_library_send_to_device(self, dev: FakeSerial, tmp_path) -> None:
        """ymodem library sends a file to FakeSerial in recv mode."""
        from ymodem.Socket import ModemSocket
        from ymodem.Protocol import ProtocolType

        # Arrange
        test_data = b"YMODEM test payload! " * 50  # 1050 bytes
        src_file = tmp_path / "send_test.bin"
        src_file.write_bytes(test_data)

        _enter_ymodem_mode(dev, "RECV")

        read, write = self._make_read_write(dev)
        modem = ModemSocket(read, write, protocol_type=ProtocolType.YMODEM)

        # Act
        ok = modem.send([str(src_file)])

        # Assert
        assert ok is True  # transfer succeeded
        assert "send_test.bin" in dev.vfs  # stored in VFS
        assert dev.vfs["send_test.bin"] == test_data  # data matches (trimmed)
        assert dev.ymodem_received_name == "send_test.bin"  # filename sent

    def test_library_recv_from_device(self, dev: FakeSerial, tmp_path) -> None:
        """ymodem library receives a file from FakeSerial in send mode."""
        from ymodem.Socket import ModemSocket
        from ymodem.Protocol import ProtocolType

        # Arrange
        _enter_ymodem_mode(dev, "SEND firmware_v1.bin")

        read, write = self._make_read_write(dev)
        modem = ModemSocket(read, write, protocol_type=ProtocolType.YMODEM)

        # Act
        ok = modem.recv(str(tmp_path))

        # Assert
        assert ok is True  # transfer succeeded
        received_file = tmp_path / "firmware_v1.bin"
        assert received_file.exists()  # file created with correct name
        content = received_file.read_bytes()
        expected = bytes(i & 0xFF for i in range(2048))
        assert content == expected  # payload matches
