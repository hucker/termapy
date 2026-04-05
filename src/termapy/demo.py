"""Simulated serial device for demo mode.

Provides ``FakeSerial``, a duck-typed replacement for ``serial.Serial``
that responds to ASCII text commands and binary Modbus RTU frames.
No Textual or pyserial dependencies - safe to import anywhere.

Note: The DEMO port simulates most serial port properties but is not a
perfect substitute.  Use real hardware for testing real projects.
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
    when port is set to ``"DEMO"``.  Serial properties (baud rate, parity,
    etc.) are accepted but cosmetic - they do not affect timing or framing.
    Use real hardware for testing real projects.

    Args:
        baudrate: Baud rate (cosmetic, does not affect timing).
        port: Port name reported by the instance.
    """

    def __init__(self, baudrate: int = 115200, port: str = "DEMO") -> None:
        self._port: str = port
        self._baudrate: int = baudrate
        self._is_open: bool = True
        self._bytesize: int = 8
        self._parity: str = "N"
        self._stopbits: float = 1
        self._dtr: bool = True
        self._rts: bool = True
        self._rtscts: bool = False
        self._xonxoff: bool = False

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

        # GPS state
        self._gps_fix: bool = True
        self._gps_sats: int = 9

        # XMODEM state
        self._xmodem_state: str | None = None  # None, "recv", "send"
        self._xmodem_recv_buf: bytearray = bytearray()
        self._xmodem_send_data: bytes = b""
        self._xmodem_block_num: int = 0
        self._xmodem_crc_mode: bool = False

        # YMODEM state
        self._ymodem_state: str | None = None  # None, "recv", "send"
        self._ymodem_phase: str = ""  # "header", "data", "eot", "batch_end"
        self._ymodem_recv_buf: bytearray = bytearray()
        self._ymodem_recv_name: str = ""
        self._ymodem_recv_size: int = 0
        self._ymodem_send_data: bytes = b""
        self._ymodem_send_name: str = ""
        self._ymodem_block_num: int = 0
        self._ymodem_eot_count: int = 0

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
    def bytesize(self) -> int:
        """Data bits."""
        return self._bytesize

    @bytesize.setter
    def bytesize(self, value: int) -> None:
        self._bytesize = value

    @property
    def parity(self) -> str:
        """Parity."""
        return self._parity

    @parity.setter
    def parity(self, value: str) -> None:
        self._parity = value

    @property
    def stopbits(self) -> float:
        """Stop bits."""
        return self._stopbits

    @stopbits.setter
    def stopbits(self, value: float) -> None:
        self._stopbits = value

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
    def rtscts(self) -> bool:
        """RTS/CTS hardware flow control."""
        return self._rtscts

    @rtscts.setter
    def rtscts(self, value: bool) -> None:
        self._rtscts = value

    @property
    def xonxoff(self) -> bool:
        """XON/XOFF software flow control."""
        return self._xonxoff

    @xonxoff.setter
    def xonxoff(self, value: bool) -> None:
        self._xonxoff = value

    @property
    def timeout(self) -> float | None:
        """Read timeout in seconds (mirrors ``serial.Serial.timeout``)."""
        return self._timeout

    @timeout.setter
    def timeout(self, value: float | None) -> None:
        self._timeout = value

    @property
    def cts(self) -> bool:
        """Clear To Send (simulated, always True)."""
        return True

    @property
    def dsr(self) -> bool:
        """Data Set Ready (simulated, always True)."""
        return True

    @property
    def ri(self) -> bool:
        """Ring Indicator (simulated, always False)."""
        return False

    @property
    def cd(self) -> bool:
        """Carrier Detect (simulated, always True)."""
        return True

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
        """Send break - no-op for simulated device."""

    # -- XMODEM protocol constants --------------------------------------------

    _SOH = 0x01  # Start of 128-byte block
    _EOT = 0x04  # End of transmission
    _ACK = 0x06
    _NAK = 0x15
    _CRC_START = ord("C")  # CRC mode initiation

    # Canned payload for AT+XMODEM=SEND (deterministic, 256 bytes = 2 blocks)
    _XMODEM_SEND_PAYLOAD = bytes(range(256))

    _STX = 0x02  # Start of 1024-byte block (YMODEM)
    _CAN = 0x18  # Cancel

    # Canned payload for AT+YMODEM=SEND (deterministic, 2048 bytes = 2 x 1K blocks)
    _YMODEM_SEND_PAYLOAD = bytes(i & 0xFF for i in range(2048))
    _YMODEM_SEND_FILENAME = "demo_data.bin"

    # -- Internal processing ------------------------------------------------

    def _process_input(self) -> None:
        """Dispatch accumulated input as ASCII or Modbus.

        When in XMODEM mode, handle protocol bytes directly before
        falling through to normal ASCII/Modbus dispatch.

        Binary detection first: if the first byte is non-printable and we
        have a complete frame (4+ bytes), treat as Modbus RTU. Otherwise
        look for a line ending to dispatch as ASCII text.
        """
        buf = self._input_buf

        # XMODEM/YMODEM mode: handle protocol bytes before normal dispatch
        if self._xmodem_state is not None:
            self._process_xmodem()
            return
        if self._ymodem_state is not None:
            self._process_ymodem()
            return

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

        # -- NMEA / GPS commands -----------------------------------------------

        if cmd.startswith("$GP"):
            return self._handle_nmea_query(cmd)

        if cmd.startswith("$PMTK"):
            return self._handle_pmtk(cmd)

        if upper == "AT+HELP.JSON":
            return self._help_json()

        if upper.startswith("MEM"):
            return self._handle_mem(cmd)

        if upper.startswith("AT+TEXTDUMP"):
            return self._handle_textdump(cmd)

        if upper.startswith("AT+BINDUMP"):
            return self._handle_bindump(cmd)

        if upper.startswith("AT+XMODEM"):
            return self._handle_xmodem_cmd(cmd, upper)

        if upper.startswith("AT+YMODEM"):
            return self._handle_ymodem_cmd(cmd, upper)

        return f"ERROR: Unknown command '{cmd}'\r\n".encode()

    def _help_json(self) -> bytes:
        """Return device descriptor as a JSON object."""
        import json
        descriptor = {
            "commands": {
                "AT": {"help": "Connection test", "args": ""},
                "AT+PROD-ID": {"help": "Product identifier", "args": ""},
                "AT+INFO": {"help": "Device information", "args": ""},
                "AT+TEMP": {"help": "Read temperature", "args": ""},
                "AT+LED": {"help": "Control LED", "args": "<on|off>"},
                "AT+NAME?": {"help": "Query device name", "args": ""},
                "AT+NAME=": {"help": "Set device name", "args": "<val>"},
                "AT+BAUD?": {"help": "Query baud rate", "args": ""},
                "AT+BAUD=": {"help": "Set baud rate", "args": "<val>"},
                "AT+STATUS": {"help": "Device status", "args": ""},
                "AT+RESET": {"help": "Reset device", "args": ""},
                "AT+TEXTDUMP": {"help": "Emit text readings", "args": "<n>"},
                "AT+BINDUMP": {"help": "Emit binary records", "args": "{type} <n>"},
                "$GPGGA": {"help": "NMEA position fix", "args": ""},
                "$GPRMC": {"help": "NMEA recommended nav data", "args": ""},
                "$GPGSA": {"help": "NMEA DOP and active satellites", "args": ""},
                "$GPGSV": {"help": "NMEA satellites in view", "args": ""},
                "mem": {"help": "Memory dump", "args": "<addr> {len}"},
                "AT+XMODEM=RECV": {"help": "Receive file via XMODEM", "args": ""},
                "AT+XMODEM=SEND": {"help": "Send canned file via XMODEM", "args": ""},
                "AT+YMODEM=RECV": {"help": "Receive file via YMODEM", "args": ""},
                "AT+YMODEM=SEND": {"help": "Send canned file via YMODEM", "args": ""},
                "AT+HELP.JSON": {"help": "Device command help (JSON)", "args": ""},
            },
        }
        return (json.dumps(descriptor) + "\r\n").encode()

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

    def _handle_textdump(self, cmd: str) -> bytes:
        """Emit *count* lines of timestamped sensor readings.

        Args:
            cmd: The full command string, e.g. ``"AT+TEXTDUMP 50"``.

        Returns:
            Multi-line text response with simulated readings.
        """
        parts = cmd.split()
        try:
            count = int(parts[1]) if len(parts) > 1 else 10
        except ValueError:
            return b"ERROR: Usage: AT+TEXTDUMP <count>\r\n"
        count = max(1, min(count, 1000))
        lines: list[str] = []
        for i in range(count):
            temp = round(22.0 + (i * 7 % 30) / 10.0, 1)
            voltage = round(3.3 + (i * 13 % 20) / 100.0, 2)
            ts = f"{i * 50:06d}ms"
            lines.append(f"[{ts}] temp={temp}C voltage={voltage}V sample={i}")
        return ("\r\n".join(lines) + "\r\n").encode()

    _BINDUMP_TYPES: dict[str, tuple[str, int]] = {
        "i8": ("<b", 1), "u8": ("<B", 1),
        "i16": ("<h", 2), "u16": ("<H", 2),
        "i32": ("<i", 4), "u32": ("<I", 4),
        "i64": ("<q", 8), "u64": ("<Q", 8),
        "f4": ("<f", 4), "f8": ("<d", 8),
    }

    # Mixed record: 10s label, u8, u16, u32, float32 (little-endian, 21 bytes)
    # Format spec: fmt=Label:S1-10 Counter:U11 Val16:U13-12 Val32:U17-14 Temp:F21-18
    _MIXED_RECORD_SIZE = 21
    _MIXED_LABELS = [
        "Sensor-A  ", "Sensor-B  ", "Motor-1   ", "Motor-2   ",
        "Pump-Main ", "Valve-In  ", "Valve-Out ", "Thermo-1  ",
        "Thermo-2  ", "Pressure  ",
    ]

    def _handle_bindump(self, cmd: str) -> bytes:
        """Emit binary records as raw bytes.

        With a type argument, emits single-type values.
        Without a type (just a count), emits mixed 21-byte records
        containing a string label, u8, u16, u32, and float32.

        Args:
            cmd: ``"AT+BINDUMP <count>"`` or ``"AT+BINDUMP <type> <count>"``.

        Returns:
            Empty (data streams in background), or error message.
        """
        parts = cmd.split()
        if len(parts) < 2:
            return (
                b"Usage: AT+BINDUMP <count>         (mixed 21-byte records)\r\n"
                b"       AT+BINDUMP <type> <count>  (single-type values)\r\n"
            )

        # Detect: AT+BINDUMP <count> (mixed) vs AT+BINDUMP <type> <count>
        if len(parts) == 2 or parts[1].isdigit():
            return self._bindump_mixed(parts)
        return self._bindump_typed(parts)

    def _bindump_mixed(self, parts: list[str]) -> bytes:
        """Stream mixed 21-byte records (label + u8 + u16 + u32 + f32)."""
        try:
            count = int(parts[1] if len(parts) == 2 else parts[2])
        except ValueError:
            return b"ERROR: count must be an integer\r\n"
        count = max(1, min(count, 1000))
        labels = self._MIXED_LABELS

        data = bytearray()
        for i in range(count):
            label = labels[i % len(labels)].encode("ascii")[:10]
            label = label.ljust(10)  # pad to 10 bytes
            counter = i & 0xFF
            val16 = (i * 137) & 0xFFFF
            val32 = (i * 2654435761) & 0xFFFFFFFF
            temp = 20.0 + (i % 50) * 0.3
            record = (
                label
                + struct.pack("<B", counter)
                + struct.pack("<H", val16)
                + struct.pack("<I", val32)
                + struct.pack("<f", temp)
            )
            data.extend(record)
        self._enqueue(bytes(data))
        return b""

    def _bindump_typed(self, parts: list[str]) -> bytes:
        """Stream single-type binary values."""
        type_str = parts[1].lower()
        info = self._BINDUMP_TYPES.get(type_str)
        if not info:
            valid = ", ".join(sorted(self._BINDUMP_TYPES))
            return f"ERROR: Unknown type '{parts[1]}'. Valid: {valid}\r\n".encode()

        try:
            count = int(parts[2])
        except ValueError:
            return b"ERROR: count must be an integer\r\n"
        count = max(1, min(count, 10000))

        fmt, width = info

        data = bytearray()
        for i in range(count):
            if type_str.startswith("f"):
                val = float(i) * 1.5
                if type_str == "f8":
                    val = float(i) * 1.5e6
            else:
                val = (i * 2654435761) & ((1 << (width * 8)) - 1)
                if type_str.startswith("i"):
                    max_signed = 1 << (width * 8 - 1)
                    if val >= max_signed:
                        val -= 1 << (width * 8)
            data.extend(struct.pack(fmt, val))
        self._enqueue(bytes(data))
        return b""

    def _uptime_str(self) -> str:
        """Return formatted uptime string."""
        elapsed = int(time.time() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins}m {secs}s"

    # -- NMEA / GPS handlers ------------------------------------------------

    def _nmea_checksum(self, sentence: str) -> str:
        """Compute NMEA XOR checksum for the content between $ and *.

        Args:
            sentence: NMEA sentence content (without $ and *XX).

        Returns:
            Two-character hex checksum.
        """
        cs = 0
        for ch in sentence:
            cs ^= ord(ch)
        return f"{cs:02X}"

    def _nmea_sentence(self, body: str) -> bytes:
        """Build a complete NMEA sentence with checksum and CRLF.

        Args:
            body: Sentence content without $ prefix or *XX suffix.

        Returns:
            Complete NMEA sentence as bytes.
        """
        cs = self._nmea_checksum(body)
        return f"${body}*{cs}\r\n".encode()

    def _nmea_time(self) -> str:
        """Return current UTC time in NMEA HHMMSS.SS format."""
        t = time.gmtime()
        return f"{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}.00"

    def _nmea_date(self) -> str:
        """Return current UTC date in NMEA DDMMYY format."""
        t = time.gmtime()
        return f"{t.tm_mday:02d}{t.tm_mon:02d}{t.tm_year % 100:02d}"

    def _handle_nmea_query(self, cmd: str) -> bytes:
        """Handle NMEA $GPxxx query commands.

        Returns simulated GPS data for the 50-yard line of
        Lumen Field, Seattle (47°35.712'N, 122°19.896'W).

        Args:
            cmd: The NMEA command string.

        Returns:
            NMEA sentence(s) as bytes.
        """
        upper = cmd.upper().strip()

        # Position: 47.5952°N, 122.3316°W
        # NMEA format: DDMM.MMMM
        lat = "4735.7120"
        lat_dir = "N"
        lon = "12219.8960"
        lon_dir = "W"
        utc = self._nmea_time()
        date = self._nmea_date()
        sats = self._gps_sats
        # Add slight jitter to altitude
        alt = round(4.5 + random.uniform(-0.3, 0.3), 1)

        if upper == "$GPGGA":
            # GGA - Global Positioning System Fix Data
            fix = 1 if self._gps_fix else 0
            body = (
                f"GPGGA,{utc},{lat},{lat_dir},{lon},{lon_dir},"
                f"{fix},{sats:02d},0.9,{alt},M,-17.0,M,,"
            )
            return self._nmea_sentence(body)

        if upper == "$GPRMC":
            # RMC - Recommended Minimum Navigation Information
            status = "A" if self._gps_fix else "V"
            speed = round(random.uniform(0.0, 0.2), 1)
            course = round(random.uniform(0, 360), 1)
            body = (
                f"GPRMC,{utc},{status},{lat},{lat_dir},{lon},{lon_dir},"
                f"{speed},{course},{date},,,A"
            )
            return self._nmea_sentence(body)

        if upper == "$GPGSA":
            # GSA - DOP and Active Satellites
            fix_3d = 3 if self._gps_fix else 1
            # Report satellite PRNs (simulated)
            prns = ",".join(f"{i:02d}" for i in range(3, 3 + min(sats, 12)))
            # Pad to 12 slots
            empty = ",".join("" for _ in range(12 - min(sats, 12)))
            if empty:
                prns = prns + "," + empty
            body = f"GPGSA,A,{fix_3d},{prns},1.5,0.9,1.2"
            return self._nmea_sentence(body)

        if upper == "$GPGSV":
            # GSV - Satellites in View (one message for simplicity)
            lines: list[bytes] = []
            total_msgs = (sats + 3) // 4
            for msg_num in range(1, total_msgs + 1):
                parts = [f"GPGSV,{total_msgs},{msg_num},{sats:02d}"]
                start = (msg_num - 1) * 4
                for i in range(4):
                    idx = start + i
                    if idx >= sats:
                        break
                    prn = 3 + idx
                    elev = 20 + idx * 7
                    azim = (45 + idx * 40) % 360
                    snr = random.randint(30, 45)
                    parts.append(f"{prn:02d},{elev},{azim:03d},{snr:02d}")
                body = ",".join(parts)
                lines.append(self._nmea_sentence(body))
            return b"".join(lines)

        return f"ERROR: Unknown NMEA query '{cmd}'\r\n".encode()

    def _handle_pmtk(self, cmd: str) -> bytes:
        """Handle $PMTK configuration commands (simulated).

        Always acknowledges with $PMTK001. Configuration is accepted
        but has no effect on the simulated device.

        Args:
            cmd: The PMTK command string.

        Returns:
            PMTK acknowledgement sentence as bytes.
        """
        # Extract command number from $PMTKnnn,...
        stripped = cmd.strip()
        if len(stripped) < 8:
            return b"ERROR: Invalid PMTK command\r\n"
        try:
            cmd_id = stripped[5:].split(",")[0].split("*")[0]
            int(cmd_id)  # validate it's a number
        except (ValueError, IndexError):
            return b"ERROR: Invalid PMTK command\r\n"
        # $PMTK001,cmd,3 = success acknowledgement
        body = f"PMTK001,{cmd_id},3"
        return self._nmea_sentence(body)

    # -- XMODEM handlers ----------------------------------------------------

    def _handle_xmodem_cmd(self, cmd: str, upper: str) -> bytes:
        """Handle AT+XMODEM=RECV and AT+XMODEM=SEND commands.

        Args:
            cmd: Original command string.
            upper: Uppercased command string.

        Returns:
            Response bytes — OK + initial protocol byte, or error.
        """
        if "=" not in upper:
            return b"ERROR: Usage: AT+XMODEM=RECV or AT+XMODEM=SEND\r\n"

        mode = upper.split("=", 1)[1].strip()

        if mode == "RECV":
            self._xmodem_state = "recv"
            self._xmodem_recv_buf = bytearray()
            self._xmodem_block_num = 0
            self._xmodem_crc_mode = False
            # OK response, then NAK to initiate transfer (checksum mode)
            return b"OK\r\n" + bytes([self._NAK])

        if mode == "SEND":
            self._xmodem_state = "send"
            self._xmodem_send_data = self._XMODEM_SEND_PAYLOAD
            self._xmodem_block_num = 1
            self._xmodem_crc_mode = False
            # OK response — device waits for NAK or 'C' from host
            return b"OK\r\n"

        return b"ERROR: Usage: AT+XMODEM=RECV or AT+XMODEM=SEND\r\n"

    def _process_xmodem(self) -> None:
        """Handle XMODEM protocol bytes while in transfer mode.

        Called from _process_input when _xmodem_state is set.
        Routes to recv or send state machine based on current mode.
        """
        if self._xmodem_state == "recv":
            self._process_xmodem_recv()
        elif self._xmodem_state == "send":
            self._process_xmodem_send()

    def _process_xmodem_recv(self) -> None:
        """XMODEM receive state machine (device receives file from host).

        Expects SOH blocks (133 bytes: SOH + blk + ~blk + 128 data + cksum)
        or CRC blocks (134 bytes: SOH + blk + ~blk + 128 data + crc_hi + crc_lo).
        ACKs valid blocks, NAKs invalid ones. EOT ends the transfer.
        """
        buf = self._input_buf

        if not buf:
            return

        # EOT = end of transmission
        if buf[0] == self._EOT:
            del buf[0]
            self._output_buf.extend(bytes([self._ACK]))
            self._xmodem_state = None
            return

        # Wait for a complete block: SOH + blk + ~blk + 128 data + check
        # Checksum mode: 1 byte check (132 total), CRC mode: 2 byte check (133 total)
        block_size = 133 if self._xmodem_crc_mode else 132
        if buf[0] == self._SOH and len(buf) >= block_size:
            block = bytes(buf[:block_size])
            del buf[:block_size]

            blk_num = block[1]
            blk_inv = block[2]
            data = block[3:131]

            # Validate block number complement
            if (blk_num + blk_inv) & 0xFF != 0xFF:
                self._output_buf.extend(bytes([self._NAK]))
                return

            # Validate checksum or CRC
            if self._xmodem_crc_mode:
                expected_crc = (block[131] << 8) | block[132]
                actual_crc = _xmodem_crc16(data)
                if actual_crc != expected_crc:
                    self._output_buf.extend(bytes([self._NAK]))
                    return
            else:
                expected_cksum = block[131]
                actual_cksum = sum(data) & 0xFF
                if actual_cksum != expected_cksum:
                    self._output_buf.extend(bytes([self._NAK]))
                    return

            self._xmodem_block_num += 1
            self._xmodem_recv_buf.extend(data)
            self._output_buf.extend(bytes([self._ACK]))
            return

        # Discard unexpected bytes if not SOH or EOT
        if buf[0] not in (self._SOH, self._EOT):
            del buf[0]

    def _process_xmodem_send(self) -> None:
        """XMODEM send state machine (device sends file to host).

        Waits for NAK (checksum mode) or 'C' (CRC mode) to start.
        Sends 128-byte blocks, waits for ACK after each.
        Sends EOT after all data is sent.
        """
        buf = self._input_buf

        if not buf:
            return

        byte = buf[0]
        del buf[0]

        # Initial handshake: NAK = checksum mode, 'C' = CRC mode
        if self._xmodem_block_num == 1 and not self._xmodem_crc_mode and byte in (self._NAK, self._CRC_START):
            self._xmodem_crc_mode = byte == self._CRC_START
            self._enqueue_xmodem_block()
            return

        if byte == self._ACK:
            # Advance to next block
            offset = (self._xmodem_block_num - 1) * 128
            if offset >= len(self._xmodem_send_data):
                # All data sent, send EOT
                self._output_buf.extend(bytes([self._EOT]))
                self._xmodem_state = None
                return
            self._enqueue_xmodem_block()
            return

        if byte == self._NAK:
            # Retransmit current block
            self._enqueue_xmodem_block()
            return

    def _enqueue_xmodem_block(self) -> None:
        """Build and enqueue the current XMODEM data block.

        Pads the last block to 128 bytes with 0x1A (SUB/CPMEOF).
        Uses checksum or CRC depending on negotiated mode.
        """
        offset = (self._xmodem_block_num - 1) * 128
        chunk = self._xmodem_send_data[offset:offset + 128]

        if not chunk:
            # No more data — send EOT
            self._output_buf.extend(bytes([self._EOT]))
            self._xmodem_state = None
            return

        # Pad last block to 128 bytes
        if len(chunk) < 128:
            chunk = chunk + b"\x1a" * (128 - len(chunk))

        blk = self._xmodem_block_num & 0xFF
        header = bytes([self._SOH, blk, 0xFF - blk])

        if self._xmodem_crc_mode:
            crc = _xmodem_crc16(chunk)
            trailer = bytes([crc >> 8, crc & 0xFF])
        else:
            trailer = bytes([sum(chunk) & 0xFF])

        self._output_buf.extend(header + chunk + trailer)
        self._xmodem_block_num += 1

    @property
    def xmodem_received_data(self) -> bytes:
        """Data received via XMODEM (for testing)."""
        return bytes(self._xmodem_recv_buf)

    # -- YMODEM handlers ----------------------------------------------------

    def _handle_ymodem_cmd(self, cmd: str, upper: str) -> bytes:
        """Handle AT+YMODEM=RECV and AT+YMODEM=SEND commands.

        Args:
            cmd: Original command string.
            upper: Uppercased command string.

        Returns:
            Response bytes.
        """
        if "=" not in upper:
            return b"ERROR: Usage: AT+YMODEM=RECV or AT+YMODEM=SEND\r\n"

        mode = upper.split("=", 1)[1].strip()

        if mode == "RECV":
            self._ymodem_state = "recv"
            self._ymodem_phase = "header"
            self._ymodem_recv_buf = bytearray()
            self._ymodem_recv_name = ""
            self._ymodem_recv_size = 0
            self._ymodem_block_num = 0
            self._ymodem_eot_count = 0
            # OK, then 'C' to request CRC mode header
            return b"OK\r\n" + bytes([self._CRC_START])

        if mode == "SEND":
            self._ymodem_state = "send"
            self._ymodem_phase = "header"
            self._ymodem_send_data = self._YMODEM_SEND_PAYLOAD
            self._ymodem_send_name = self._YMODEM_SEND_FILENAME
            self._ymodem_block_num = 0
            self._ymodem_eot_count = 0
            # OK, wait for 'C' from host
            return b"OK\r\n"

        return b"ERROR: Usage: AT+YMODEM=RECV or AT+YMODEM=SEND\r\n"

    def _process_ymodem(self) -> None:
        """Handle YMODEM protocol bytes while in transfer mode."""
        if self._ymodem_state == "recv":
            self._process_ymodem_recv()
        elif self._ymodem_state == "send":
            self._process_ymodem_send()

    def _process_ymodem_recv(self) -> None:
        """YMODEM receive state machine (device receives file from host).

        Phases: header (block 0 with filename/size), data (file content),
        eot (end of transmission), batch_end (empty block 0).
        """
        buf = self._input_buf

        if not buf:
            return

        # EOT handling: first EOT gets NAK, second gets ACK then 'C' for next file
        if buf[0] == self._EOT:
            del buf[0]
            self._ymodem_eot_count += 1
            if self._ymodem_eot_count == 1:
                self._output_buf.extend(bytes([self._NAK]))
            else:
                self._output_buf.extend(bytes([self._ACK]))
                self._ymodem_phase = "batch_end"
                self._ymodem_eot_count = 0
                # Send 'C' to request next file header (or batch end)
                self._output_buf.extend(bytes([self._CRC_START]))
            return

        # Determine block size from header byte
        if buf[0] == self._SOH:
            block_size = 128
            frame_size = 133  # SOH + blk + ~blk + 128 + CRC(2)
        elif buf[0] == self._STX:
            block_size = 1024
            frame_size = 1029  # STX + blk + ~blk + 1024 + CRC(2)
        elif buf[0] == self._CAN:
            del buf[0]
            self._ymodem_state = None
            return
        else:
            # Discard unexpected byte
            del buf[0]
            return

        if len(buf) < frame_size:
            return  # Wait for complete block

        block = bytes(buf[:frame_size])
        del buf[:frame_size]

        blk_num = block[1]
        blk_inv = block[2]
        data = block[3:3 + block_size]

        # Validate block number complement
        if (blk_num + blk_inv) & 0xFF != 0xFF:
            self._output_buf.extend(bytes([self._NAK]))
            return

        # Validate CRC-16
        expected_crc = (block[3 + block_size] << 8) | block[4 + block_size]
        actual_crc = _xmodem_crc16(data)
        if actual_crc != expected_crc:
            self._output_buf.extend(bytes([self._NAK]))
            return

        if self._ymodem_phase == "header" or self._ymodem_phase == "batch_end":
            # Block 0: filename\0filesize\0 (or empty = batch end)
            if data[0] == 0x00:
                # Empty filename = end of batch
                self._output_buf.extend(bytes([self._ACK]))
                self._ymodem_state = None
                return

            # Parse filename and size (format: filename\0filesize[ mtime ...]\0)
            null_idx = data.index(0x00)
            self._ymodem_recv_name = data[:null_idx].decode("ascii", errors="replace")
            rest = data[null_idx + 1:]
            size_end = rest.index(0x00) if 0x00 in rest else len(rest)
            fields_str = rest[:size_end].decode("ascii", errors="replace").strip()
            # Filesize is the first space-separated field (decimal)
            size_str = fields_str.split(" ")[0] if fields_str else ""
            self._ymodem_recv_size = int(size_str) if size_str else 0
            self._ymodem_recv_buf = bytearray()
            self._ymodem_block_num = 1
            self._ymodem_phase = "data"
            self._ymodem_eot_count = 0
            # ACK the header, then 'C' to start data
            self._output_buf.extend(bytes([self._ACK, self._CRC_START]))
            return

        # Data block
        self._ymodem_block_num += 1
        self._ymodem_recv_buf.extend(data)
        self._output_buf.extend(bytes([self._ACK]))

    def _process_ymodem_send(self) -> None:
        """YMODEM send state machine (device sends file to host).

        Phases: header (send block 0), data (send file blocks),
        eot (end of transmission), batch_end (send empty block 0).
        """
        buf = self._input_buf

        if not buf:
            return

        byte = buf[0]
        del buf[0]

        if byte == self._CAN:
            self._ymodem_state = None
            return

        if self._ymodem_phase == "header":
            # Waiting for 'C' to send header block 0
            if byte == self._CRC_START or byte == self._NAK:
                self._enqueue_ymodem_header_block()
                return

        if self._ymodem_phase == "data_wait_c":
            # After header ACK, wait for 'C' to start data
            if byte == self._CRC_START:
                self._ymodem_block_num = 0
                self._enqueue_ymodem_data_block()
                self._ymodem_phase = "data"
                return

        if self._ymodem_phase == "data":
            if byte == self._ACK:
                offset = self._ymodem_block_num * 1024
                if offset >= len(self._ymodem_send_data):
                    # All data sent, send EOT
                    self._output_buf.extend(bytes([self._EOT]))
                    self._ymodem_phase = "eot"
                    self._ymodem_eot_count = 1
                    return
                self._enqueue_ymodem_data_block()
                return
            if byte == self._NAK:
                # Retransmit current block
                self._ymodem_block_num -= 1
                self._enqueue_ymodem_data_block()
                return

        if self._ymodem_phase == "eot":
            if byte == self._NAK:
                # First NAK after EOT, send EOT again
                self._output_buf.extend(bytes([self._EOT]))
                self._ymodem_eot_count += 1
                return
            if byte == self._ACK:
                # EOT acknowledged, wait for 'C' for batch end
                self._ymodem_phase = "batch_end"
                return
            if byte == self._CRC_START:
                # Some implementations send 'C' after ACK
                self._ymodem_phase = "batch_end"
                # Fall through to batch_end handling
                self._enqueue_ymodem_empty_header()
                return

        if self._ymodem_phase == "batch_end":
            if byte == self._CRC_START or byte == self._NAK:
                self._enqueue_ymodem_empty_header()
                return
            if byte == self._ACK:
                # Batch end acknowledged, done
                self._ymodem_state = None
                return

        if self._ymodem_phase == "header":
            if byte == self._ACK:
                # Header ACKed, wait for 'C' to start data
                self._ymodem_phase = "data_wait_c"
                return

    def _enqueue_ymodem_header_block(self) -> None:
        """Build and enqueue YMODEM block 0 (filename + filesize + mtime)."""
        name_bytes = self._ymodem_send_name.encode("ascii")
        size_bytes = str(len(self._ymodem_send_data)).encode("ascii")
        # filename\0filesize<space>mtime\0 padded to 128 bytes
        # mtime is octal seconds since epoch; 0 = unknown
        payload = name_bytes + b"\x00" + size_bytes + b" 0\x00"
        payload = payload.ljust(128, b"\x00")

        crc = _xmodem_crc16(payload)
        header = bytes([self._SOH, 0x00, 0xFF])
        trailer = bytes([crc >> 8, crc & 0xFF])
        self._output_buf.extend(header + payload + trailer)

    def _enqueue_ymodem_data_block(self) -> None:
        """Build and enqueue the current YMODEM 1024-byte data block.

        _ymodem_block_num is a 0-based data index. The actual YMODEM
        sequence number is block_num + 1 (block 0 was the header).
        """
        offset = self._ymodem_block_num * 1024
        chunk = self._ymodem_send_data[offset:offset + 1024]

        if not chunk:
            self._output_buf.extend(bytes([self._EOT]))
            self._ymodem_phase = "eot"
            self._ymodem_eot_count = 1
            return

        # Pad last block to 1024 bytes
        if len(chunk) < 1024:
            chunk = chunk + b"\x1a" * (1024 - len(chunk))

        blk = (self._ymodem_block_num + 1) & 0xFF  # header was block 0
        header = bytes([self._STX, blk, 0xFF - blk])
        crc = _xmodem_crc16(chunk)
        trailer = bytes([crc >> 8, crc & 0xFF])
        self._output_buf.extend(header + chunk + trailer)
        self._ymodem_block_num += 1

    def _enqueue_ymodem_empty_header(self) -> None:
        """Build and enqueue an empty YMODEM block 0 (batch end signal)."""
        payload = b"\x00" * 128
        crc = _xmodem_crc16(payload)
        header = bytes([self._SOH, 0x00, 0xFF])
        trailer = bytes([crc >> 8, crc & 0xFF])
        self._output_buf.extend(header + payload + trailer)

    @property
    def ymodem_received_data(self) -> bytes:
        """Data received via YMODEM (for testing)."""
        return bytes(self._ymodem_recv_buf)

    @property
    def ymodem_received_name(self) -> str:
        """Filename received via YMODEM block 0 (for testing)."""
        return self._ymodem_recv_name

    @property
    def ymodem_received_size(self) -> int:
        """File size from YMODEM block 0 (for testing)."""
        return self._ymodem_recv_size

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
            # Write single register - store value and echo back
            if len(frame) >= 6:
                reg_addr = struct.unpack(">H", frame[2:4])[0]
                reg_val = struct.unpack(">H", frame[4:6])[0]
                self._registers[reg_addr] = reg_val
            return _modbus_add_crc(frame[:-2])
        else:
            # Unsupported function - exception code 0x01
            return _modbus_exception(slave_id, func_code, 0x01)

    def _modbus_read_registers(self, slave_id: int, frame: bytes) -> bytes:
        """Handle Modbus function 0x03 - read holding registers.

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


def _xmodem_crc16(data: bytes) -> int:
    """Compute XMODEM CRC-16 (polynomial 0x1021, init 0x0000).

    Args:
        data: Payload bytes.

    Returns:
        16-bit CRC value.
    """
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


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
