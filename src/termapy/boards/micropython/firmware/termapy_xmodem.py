"""XMODEM file transfer for the MicroPython monitor: receive from or send to the host.

The monitor (termapy_json.py) is line-based, but a transfer is raw bytes.  A
builtin handler runs synchronously inside the monitor's dispatch, so it can
take over the raw serial link for the length of one transfer and return a
status line when done -- the line contract is untouched.  This module is that
responder: checksum-mode 128-byte blocks (CRC mode too, detected from the
block size), interoperating with termapy's host XMODEM, which sends C then
falls back to NAK on receive, and waits for the device's opening byte on send.

The received file is held in a module buffer so a later ``xmodem send`` streams
it back, which is what a round-trip test checks.  A transfer BLOCKS the
monitor's asyncio task for its duration; the Pico starter runs no other task,
so that is fine.  Every read is bounded by a timeout, and a stalled transfer
sends CAN and returns to line mode, so a wedged host cannot hang the board.

Device side only -- the host side is termapy's /xfer.xmodem.send / .recv.
"""

import sys
import time

try:
    import select
except ImportError:  # pragma: no cover - the board always has it
    select = None

try:
    import micropython
except ImportError:  # pragma: no cover - only off the board
    micropython = None

SOH = 0x01
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18
CRC = ord("C")
PAD = 0x1A

BLOCK = 128
READ_TIMEOUT_MS = 2000
START_RETRIES = 10

# The last received file, so "xmodem send" can stream it back.
_received = bytearray()


def _crc16(data):
    """CRC-16/XMODEM (poly 0x1021, init 0) of ``data``, bit by bit (no table)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class RawLink:
    """The serial link as raw bytes: read with a timeout, write at once.

    Built by the monitor from sys.stdin / sys.stdout so a transfer reads and
    writes the same link the monitor does, but as bytes, not decoded text.
    """

    def __init__(self):
        self._rx = sys.stdin.buffer
        self._tx = sys.stdout.buffer
        self._poller = None
        if select is not None:
            self._poller = select.poll()
            self._poller.register(self._rx, select.POLLIN)

    def read(self, count, timeout_ms=READ_TIMEOUT_MS):
        """Up to ``count`` bytes within ``timeout_ms``; fewer (or empty) on timeout."""
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        out = bytearray()
        while len(out) < count:
            remaining = time.ticks_diff(deadline, time.ticks_ms())
            if remaining <= 0:
                break
            if self._poller is not None and not self._poller.poll(remaining):
                break
            chunk = self._rx.read(count - len(out))
            if chunk:
                out.extend(chunk)
        return bytes(out)

    def write(self, data):
        """Send ``data`` on the link."""
        self._tx.write(data)


def _raw_mode(on):
    """Stop (on) or restore (off) the REPL catching Ctrl-C, so raw block bytes pass through.

    A data byte 0x03 in an XMODEM block would otherwise raise KeyboardInterrupt
    and drop the board to the REPL mid-transfer.
    """
    if micropython is not None:
        micropython.kbd_intr(-1 if on else 0x03)


def receive(link):
    """Receive one file from the host into the module buffer; return the byte count.

    Sends NAK to start (checksum mode, the host's fallback), accepts checksum
    or CRC blocks by their size, ACKs a good block and NAKs a bad one, and ends
    on EOT.  On a stall it sends CAN and returns what it has.
    """
    _raw_mode(True)
    try:
        return _receive(link)
    finally:
        _raw_mode(False)


def _receive(link):
    """The receive loop, inside raw mode."""
    global _received
    data = bytearray()
    expected = 1
    link.write(bytes([NAK]))
    while True:
        head = link.read(1)
        if not head:
            link.write(bytes([CAN, CAN]))
            break
        first = head[0]
        if first == EOT:
            link.write(bytes([ACK]))
            break
        if first == CAN:
            break
        if first != SOH:
            continue
        # SOH: blk, ~blk, 128 data, then 1 (checksum) or 2 (CRC) check bytes.
        meta = link.read(2)
        body = link.read(BLOCK)
        if len(meta) < 2 or len(body) < BLOCK:
            link.write(bytes([NAK]))
            continue
        # One trailing byte is a checksum; a second, if it arrives, is CRC.
        check = link.read(1, 200)
        crc_mode = False
        extra = link.read(1, 50)
        if extra:
            check = check + extra
            crc_mode = True
        blk, inv = meta[0], meta[1]
        if (blk + inv) & 0xFF != 0xFF:
            link.write(bytes([NAK]))
            continue
        if crc_mode:
            got = (check[0] << 8) | check[1]
            ok = got == _crc16(body)
        else:
            ok = check[0] == (sum(body) & 0xFF)
        if not ok:
            link.write(bytes([NAK]))
            continue
        if blk == expected:
            data.extend(body)
            expected = (expected + 1) & 0xFF
        link.write(bytes([ACK]))
    _received = bytearray(data)
    return len(data)


def send(link, data):
    """Send ``data`` to the host; return the byte count, or -1 if the host never started.

    Waits for the host's opening byte (NAK = checksum, C = CRC), sends 128-byte
    blocks padded with 0x1A, retransmits on NAK, and ends with EOT.
    """
    _raw_mode(True)
    try:
        return _send(link, data)
    finally:
        _raw_mode(False)


def _send(link, data):
    """The send loop, inside raw mode."""
    crc_mode = False
    for _ in range(START_RETRIES):
        start = link.read(1, 1000)
        if start and start[0] in (NAK, CRC):
            crc_mode = start[0] == CRC
            break
    else:
        link.write(bytes([CAN, CAN]))
        return -1
    total = len(data)
    block = 1
    offset = 0
    while offset < total:
        chunk = data[offset : offset + BLOCK]
        if len(chunk) < BLOCK:
            chunk = chunk + bytes([PAD]) * (BLOCK - len(chunk))
        frame = bytes([SOH, block & 0xFF, (0xFF - block) & 0xFF]) + chunk
        if crc_mode:
            crc = _crc16(chunk)
            frame += bytes([crc >> 8, crc & 0xFF])
        else:
            frame += bytes([sum(chunk) & 0xFF])
        link.write(frame)
        reply = link.read(1, READ_TIMEOUT_MS)
        if not reply:
            link.write(bytes([CAN, CAN]))
            return -1
        if reply[0] == ACK:
            offset += BLOCK
            block = (block + 1) & 0xFF
        elif reply[0] == CAN:
            return -1
        # NAK or anything else: retransmit the same block.
    link.write(bytes([EOT]))
    link.read(1, 1000)
    return total


def buffered():
    """The number of bytes the last receive stored (for a bare state read)."""
    return len(_received)


def to_send():
    """The bytes a send streams back: the last received file."""
    return bytes(_received)
