"""Tests for chunked writes: with tx_chunking on, SerialPort.write hands a long write to the driver in 192-byte pieces.

Measured 2026-11-10 on an FT232R: 4 KB writes lost lines at 750000 baud and
up, 192-byte writes lost none (help/ports.md, "Large sends go out in
pieces").  These pin the contract over a recording port; the hardware
loopback's ``TestChunkedWrites`` pins the claim on the adapter itself.
"""

from __future__ import annotations

import queue

import pytest

from termapy.serial_port import TX_CHUNK_BYTES, SerialPort

pytestmark = pytest.mark.rule("tx-chunked-writes")


class _RecordingPort:
    """A port that keeps every write it is given, and fails the Nth one when asked."""

    def __init__(self, fail_at: int = 0) -> None:
        self.writes: list[bytes] = []
        self._fail_at = fail_at

    def write(self, data: bytes) -> int:
        if self._fail_at and len(self.writes) + 1 == self._fail_at:
            raise OSError("write failed")
        self.writes.append(data)
        return len(data)


def _port(
    chunking: bool = True, fail_at: int = 0
) -> tuple[SerialPort, _RecordingPort, list[tuple[str, str]]]:
    """A SerialPort over a recording port, logging into a list; ``chunking`` is what the key answers."""
    fake = _RecordingPort(fail_at)
    logged: list[tuple[str, str]] = []
    sp = SerialPort(
        port=fake,
        rx_queue=queue.Queue(),
        log=lambda direction, text: logged.append((direction, text)),
        tx_chunking=lambda: chunking,
    )
    return sp, fake, logged


def test_the_chunk_is_half_an_ft232r_fifo():
    """The piece size the docs quote is 192 bytes, half the FT232R's 384-byte FIFO."""
    assert TX_CHUNK_BYTES == 192


class TestChunkedWrite:
    def test_a_long_write_goes_out_in_pieces(self):
        """1000 bytes reach the driver as five 192-byte writes and a 40-byte remainder, in order."""
        # Arrange
        sp, fake, _ = _port()
        data = bytes(range(256)) * 3 + bytes(range(232))

        # Act
        sp.write(data)

        # Assert
        actual = [len(piece) for piece in fake.writes]
        expected = [192, 192, 192, 192, 192, 40]
        assert actual == expected, "pieces of TX_CHUNK_BYTES, the last the remainder"
        assert b"".join(fake.writes) == data, "the pieces are the write, in order"

    def test_the_log_line_is_the_whole_write(self):
        """One ``>`` line for the write, after its pieces went out, never one per piece."""
        # Arrange
        sp, _, logged = _port()

        # Act
        sp.write(b"x" * 1000)

        # Assert
        assert logged == [(">", "x" * 1000)]

    def test_off_sends_the_write_whole(self):
        """With tx_chunking off a long write is one write, as before the key existed."""
        # Arrange
        sp, fake, _ = _port(chunking=False)

        # Act
        sp.write(b"x" * 1000)

        # Assert
        assert fake.writes == [b"x" * 1000]

    def test_a_short_write_is_one_write(self):
        """A write no longer than the chunk goes whole: a text line or a keystroke is untouched."""
        # Arrange
        sp, fake, _ = _port()

        # Act
        sp.write(b"AT+VER\r")
        sp.write(b"y" * TX_CHUNK_BYTES)

        # Assert
        assert fake.writes == [b"AT+VER\r", b"y" * TX_CHUNK_BYTES]

    def test_two_writes_in_a_row_stay_separate(self):
        """The pieces of one write never carry into the next: 200 then 100 bytes is 192, 8, 100."""
        # Arrange
        sp, fake, _ = _port()

        # Act
        sp.write(b"a" * 200)
        sp.write(b"b" * 100)

        # Assert
        actual = [len(piece) for piece in fake.writes]
        assert actual == [192, 8, 100]

    def test_the_key_is_read_at_each_write(self):
        """Flipping tx_chunking between two writes changes the second, with no reconnect."""
        # Arrange
        state = {"on": True}
        fake = _RecordingPort()
        sp = SerialPort(port=fake, rx_queue=queue.Queue(), tx_chunking=lambda: state["on"])

        # Act
        sp.write(b"a" * 300)
        state["on"] = False
        sp.write(b"b" * 300)

        # Assert
        actual = [len(piece) for piece in fake.writes]
        assert actual == [192, 108, 300], "the first write chunked, the second whole"

    def test_a_failed_piece_logs_what_went_out_and_raises(self):
        """A piece the port refuses raises to the caller, and the ``>`` line holds only the pieces before it."""
        # Arrange
        sp, fake, logged = _port(fail_at=3)

        # Act / Assert
        with pytest.raises(OSError, match="write failed"):
            sp.write(b"x" * 1000)
        assert [len(piece) for piece in fake.writes] == [192, 192], "two pieces went out"
        assert logged == [(">", "x" * 384)], "the log claims only what went out"
