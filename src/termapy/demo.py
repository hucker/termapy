"""Simulated serial device for demo mode.

Provides ``FakeSerial``, a duck-typed replacement for ``serial.Serial``
that responds to ASCII text commands and binary Modbus RTU frames.
No Textual or pyserial dependencies — safe to import anywhere.
"""

from __future__ import annotations

from collections.abc import Callable
import random
import struct
import threading
import time


class FakeSerial:
    """Simulated serial port that responds to AT commands and Modbus RTU.

    Duck-types ``serial.Serial`` so the app can use it transparently
    when port is set to ``"DEMO"``.

    Args:
        baudrate: Baud rate (cosmetic, does not affect timing).
        port: Port name reported by the instance.
    """

    def __init__(self, baudrate: int = 115200, port: str = "DEMO") -> None:
        self._port: str = port
        self._baudrate: int = baudrate
        self._is_open: bool = True
        self._dtr: bool = True
        self._rts: bool = True

        self._timeout: float | None = None

        self._lock = threading.Lock()
        self._input_buf = bytearray()
        self._output_buf = bytearray()

        # Device state
        self._led_state: bool = False
        self._start_time: float = time.time()
        self._connect_count: int = 1
        self._device_name: str = "Bassomatic v77"
        self._device_baud: int = 115200

        # Modbus holding registers (addr 0-99)
        self._registers: dict[int, int] = {}

    # -- serial.Serial properties ------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether the port is open."""
        return self._is_open

    @property
    def port(self) -> str:
        """Port name."""
        return self._port

    @port.setter
    def port(self, value: str) -> None:
        self._port = value

    @property
    def baudrate(self) -> int:
        """Baud rate."""
        return self._baudrate

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        self._baudrate = value

    @property
    def dtr(self) -> bool:
        """Data Terminal Ready."""
        return self._dtr

    @dtr.setter
    def dtr(self, value: bool) -> None:
        self._dtr = value

    @property
    def rts(self) -> bool:
        """Request To Send."""
        return self._rts

    @rts.setter
    def rts(self, value: bool) -> None:
        self._rts = value

    @property
    def timeout(self) -> float | None:
        """Read timeout in seconds (mirrors ``serial.Serial.timeout``)."""
        return self._timeout

    @timeout.setter
    def timeout(self, value: float | None) -> None:
        self._timeout = value

    @property
    def in_waiting(self) -> int:
        """Number of bytes available to read."""
        with self._lock:
            return len(self._output_buf)

    # -- serial.Serial methods ---------------------------------------------

    def write(self, data: bytes) -> int:
        """Write data to the simulated device.

        Accumulates bytes and processes when a line ending is detected
        (ASCII) or a complete Modbus frame arrives (binary).

        Args:
            data: Bytes to write.

        Returns:
            Number of bytes written.
        """
        with self._lock:
            self._input_buf.extend(data)
            self._process_input()
        return len(data)

    def read(self, size: int = 1) -> bytes:
        """Read up to *size* bytes from the output buffer.

        Blocks up to ``self.timeout`` seconds waiting for data, matching
        the behaviour of ``serial.Serial.read()``.

        Args:
            size: Maximum number of bytes to read.

        Returns:
            Available bytes (may be fewer than *size*).
        """
        deadline = time.time() + (self._timeout or 0)
        while True:
            with self._lock:
                if self._output_buf:
                    chunk = bytes(self._output_buf[:size])
                    del self._output_buf[:size]
                    return chunk
            if time.time() >= deadline:
                return b""
            time.sleep(0.001)

    def close(self) -> None:
        """Close the simulated port."""
        self._is_open = False

    def send_break(self, duration: float = 0.25) -> None:
        """Send break — no-op for simulated device."""

    # -- Internal processing ------------------------------------------------

    def _process_input(self) -> None:
        """Dispatch accumulated input as ASCII or Modbus.

        Binary detection first: if the first byte is non-printable and we
        have a complete frame (4+ bytes), treat as Modbus RTU. Otherwise
        look for a line ending to dispatch as ASCII text.
        """
        buf = self._input_buf

        # Binary Modbus: first byte outside printable ASCII range
        if len(buf) >= 4 and not (0x20 <= buf[0] < 0x7F):
            frame = bytes(buf)
            buf.clear()
            response = self._handle_modbus(frame)
            self._enqueue(response)
            return

        # ASCII: look for line ending
        for eol in (b"\r\n", b"\r", b"\n"):
            idx = buf.find(eol)
            if idx >= 0:
                line = bytes(buf[:idx])
                del buf[: idx + len(eol)]
                cmd = line.decode("ascii", errors="replace").strip()
                response = self._handle_ascii(cmd)
                self._enqueue(response)
                return

    def _enqueue(self, response: bytes) -> None:
        """Add response to output buffer (available immediately).

        Args:
            response: Bytes to enqueue.
        """
        if response:
            self._output_buf.extend(response)

    # -- ASCII command handler ----------------------------------------------

    def _handle_ascii(self, cmd: str) -> bytes:
        """Process an ASCII text command and return the response.

        Args:
            cmd: The command string (stripped, no line ending).

        Returns:
            Response bytes with ``\\r\\n`` line endings.
        """
        if not cmd:
            return b"\r\n"

        upper = cmd.upper()

        if upper == "AT":
            return b"OK\r\n"

        if upper == "AT+PROD-ID":
            return b"+PROD-ID:BASSOMATIC-77\r\n"

        if upper == "AT+INFO":
            uptime = self._uptime_str()
            free_mem = random.randint(28000, 32000)
            return (
                f"Bassomatic v77 v1.0\r\n"
                f"Uptime: {uptime}\r\n"
                f"Free memory: {free_mem} bytes\r\n"
            ).encode()

        if upper == "AT+TEMP":
            temp = round(random.uniform(22.0, 25.0), 1)
            return f"+TEMP: {temp}C\r\n".encode()

        if upper.startswith("AT+LED"):
            parts = cmd.split()
            if len(parts) >= 2:
                arg = parts[1].lower()
                if arg == "on":
                    self._led_state = True
                    return b"OK\r\n"
                elif arg == "off":
                    self._led_state = False
                    return b"OK\r\n"
            return b"ERROR: Usage: AT+LED on|off\r\n"

        if upper.startswith("AT+NAME"):
            return self._handle_set_query(
                cmd, upper, "AT+NAME", self._device_name,
                self._set_name,
            )

        if upper.startswith("AT+BAUD"):
            return self._handle_set_query(
                cmd, upper, "AT+BAUD", str(self._device_baud),
                self._set_baud,
            )

        if upper == "AT+STATUS":
            led = "ON" if self._led_state else "OFF"
            uptime = self._uptime_str()
            return (
                f"LED: {led}\r\n"
                f"Uptime: {uptime}\r\n"
                f"Connections: {self._connect_count}\r\n"
            ).encode()

        if upper == "AT+RESET":
            self._led_state = False
            self._start_time = time.time()
            self._connect_count += 1
            self._enqueue(b"")
            return (
                b"Resetting...\r\n"
                b"[Boot] Bassomatic v77 v1.0\r\n"
                b"[Boot] Hardware OK\r\n"
                b"[Boot] Ready\r\n"
            )

        if upper == "HELP":
            return (
                b"Available commands:\r\n"
                b"  AT          - Connection test\r\n"
                b"  AT+PROD-ID  - Product identifier\r\n"
                b"  AT+INFO     - Device information\r\n"
                b"  AT+TEMP     - Read temperature\r\n"
                b"  AT+LED on|off - Control LED\r\n"
                b"  AT+NAME?    - Query device name\r\n"
                b"  AT+NAME=val - Set device name\r\n"
                b"  AT+BAUD?    - Query baud rate\r\n"
                b"  AT+BAUD=val - Set baud rate\r\n"
                b"  AT+STATUS   - Device status\r\n"
                b"  AT+RESET    - Reset device\r\n"
                b"  mem <addr> [len] - Memory dump\r\n"
                b"  help        - This help\r\n"
            )

        if upper.startswith("MEM"):
            return self._handle_mem(cmd)

        return f"ERROR: Unknown command '{cmd}'\r\n".encode()

    def _handle_mem(self, cmd: str) -> bytes:
        """Generate a deterministic hex dump for ``mem <addr> [len]``.

        Args:
            cmd: The full mem command string.

        Returns:
            Formatted hex dump bytes.
        """
        parts = cmd.split()
        try:
            addr = int(parts[1], 0) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return b"ERROR: Usage: mem <addr> [len]\r\n"

        try:
            length = int(parts[2], 0) if len(parts) > 2 else 64
        except ValueError:
            length = 64

        length = max(1, min(length, 256))
        lines: list[str] = []
        for offset in range(0, length, 16):
            row_addr = addr + offset
            row_bytes = []
            for i in range(min(16, length - offset)):
                # Deterministic: hash of address
                val = ((row_addr + i) * 2654435761) & 0xFF
                row_bytes.append(val)
            hex_part = " ".join(f"{b:02X}" for b in row_bytes)
            ascii_part = "".join(
                chr(b) if 0x20 <= b < 0x7F else "." for b in row_bytes
            )
            lines.append(f"  {row_addr:08X}: {hex_part:<48s} {ascii_part}")
        return ("\r\n".join(lines) + "\r\n").encode()

    def _uptime_str(self) -> str:
        """Return formatted uptime string."""
        elapsed = int(time.time() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins}m {secs}s"

    # -- Set/query helpers --------------------------------------------------

    def _handle_set_query(
        self,
        cmd: str,
        upper: str,
        prefix: str,
        current: str,
        setter: Callable[[str], str | None],
    ) -> bytes:
        """Handle AT+KEY=value (set) and AT+KEY? (query) patterns.

        Args:
            cmd: Original command string (preserves case for values).
            upper: Uppercased command for matching.
            prefix: The AT+KEY prefix (e.g. ``"AT+NAME"``).
            current: Current value as a string for query responses.
            setter: Callable to set the new value; returns error string
                    or None on success.

        Returns:
            Response bytes with ``\\r\\n`` line endings.
        """
        rest = cmd[len(prefix):]
        if rest == "?":
            return f"+{prefix[3:]}:{current}\r\n".encode()
        if rest.startswith("="):
            value = rest[1:]
            err = setter(value)
            if err:
                return f"ERROR: {err}\r\n".encode()
            return b"OK\r\n"
        return f"ERROR: Usage: {prefix}? or {prefix}=<value>\r\n".encode()

    def _set_name(self, value: str) -> str | None:
        """Set device name.

        Args:
            value: New name string.

        Returns:
            Error message, or None on success.
        """
        value = value.strip()
        if not value:
            return "Name cannot be empty"
        if len(value) > 32:
            return "Name too long (max 32 chars)"
        self._device_name = value
        return None

    def _set_baud(self, value: str) -> str | None:
        """Set device baud rate.

        Args:
            value: Baud rate as a string.

        Returns:
            Error message, or None on success.
        """
        valid = {9600, 19200, 38400, 57600, 115200}
        try:
            baud = int(value)
        except ValueError:
            return f"Invalid baud rate: {value}"
        if baud not in valid:
            return f"Unsupported baud rate. Valid: {', '.join(str(b) for b in sorted(valid))}"
        self._device_baud = baud
        return None

    # -- Modbus RTU handler -------------------------------------------------

    def _handle_modbus(self, frame: bytes) -> bytes:
        """Process a Modbus RTU frame and return the response.

        Args:
            frame: Raw Modbus RTU frame including CRC.

        Returns:
            Modbus response frame with CRC.
        """
        if len(frame) < 4:
            return b""

        # Verify CRC
        payload = frame[:-2]
        received_crc = struct.unpack("<H", frame[-2:])[0]
        if _modbus_crc(payload) != received_crc:
            # Return exception: illegal data value (code 0x03)
            return _modbus_exception(frame[0], frame[1], 0x03)

        slave_id = frame[0]
        func_code = frame[1]

        if func_code == 0x03:
            # Read holding registers
            return self._modbus_read_registers(slave_id, frame)
        elif func_code == 0x06:
            # Write single register — echo back
            return _modbus_add_crc(frame[:-2])
        else:
            # Unsupported function — exception code 0x01
            return _modbus_exception(slave_id, func_code, 0x01)

    def _modbus_read_registers(self, slave_id: int, frame: bytes) -> bytes:
        """Handle Modbus function 0x03 — read holding registers.

        Args:
            slave_id: Modbus slave address.
            frame: Full request frame.

        Returns:
            Response frame with register values and CRC.
        """
        if len(frame) < 6:
            return _modbus_exception(slave_id, 0x03, 0x03)

        start_reg = struct.unpack(">H", frame[2:4])[0]
        num_regs = struct.unpack(">H", frame[4:6])[0]
        num_regs = min(num_regs, 125)  # Modbus limit

        byte_count = num_regs * 2
        resp = bytearray([slave_id, 0x03, byte_count])
        for i in range(num_regs):
            reg_addr = start_reg + i
            # Return stored value or deterministic default
            val = self._registers.get(reg_addr, (reg_addr * 13 + 7) & 0xFFFF)
            resp.extend(struct.pack(">H", val))

        return _modbus_add_crc(bytes(resp))


# -- Module-level Modbus helpers -------------------------------------------


def _modbus_crc(data: bytes) -> int:
    """Compute Modbus CRC16 (polynomial 0xA001).

    Args:
        data: Payload bytes (excluding CRC).

    Returns:
        16-bit CRC value.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _modbus_add_crc(data: bytes) -> bytes:
    """Append Modbus CRC16 to a frame.

    Args:
        data: Frame bytes without CRC.

    Returns:
        Frame bytes with CRC appended (little-endian).
    """
    crc = _modbus_crc(data)
    return data + struct.pack("<H", crc)


def _modbus_exception(slave_id: int, func_code: int, exception_code: int) -> bytes:
    """Build a Modbus exception response.

    Args:
        slave_id: Slave address.
        func_code: Original function code.
        exception_code: Exception code (1=illegal function, 3=illegal data).

    Returns:
        Exception response frame with CRC.
    """
    resp = bytes([slave_id, func_code | 0x80, exception_code])
    return _modbus_add_crc(resp)
