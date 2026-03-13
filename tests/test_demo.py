"""Tests for FakeSerial demo device — ASCII commands, Modbus RTU, and config setup."""

import struct
import time

import pytest

from termapy.demo import FakeSerial, _modbus_add_crc, _modbus_crc


@pytest.fixture
def dev() -> FakeSerial:
    """Create a FakeSerial instance for testing."""
    return FakeSerial(baudrate=9600)


def _send_cmd(dev: FakeSerial, cmd: str) -> str:
    """Send an ASCII command and return the response as a string."""
    dev.write(cmd.encode() + b"\r")
    # Skip simulated delay
    time.sleep(0.01)
    return dev.read(4096).decode()


# -- FakeSerial basics -----------------------------------------------------


class TestFakeSerialBasics:
    def test_initial_state(self, dev: FakeSerial) -> None:
        assert dev.is_open is True  # starts open
        assert dev.port == "DEMO"  # default port name
        assert dev.baudrate == 9600  # matches constructor arg
        assert dev.in_waiting == 0  # no data yet

    def test_close(self, dev: FakeSerial) -> None:
        dev.close()
        assert dev.is_open is False  # closed after close()

    def test_dtr_rts(self, dev: FakeSerial) -> None:
        assert dev.dtr is True  # default DTR
        assert dev.rts is True  # default RTS
        dev.dtr = False
        dev.rts = False
        assert dev.dtr is False  # DTR toggled off
        assert dev.rts is False  # RTS toggled off

    def test_send_break(self, dev: FakeSerial) -> None:
        dev.send_break()  # no-op, should not raise

    def test_port_setter(self, dev: FakeSerial) -> None:
        dev.port = "COM99"
        assert dev.port == "COM99"  # port name updated

    def test_baudrate_setter(self, dev: FakeSerial) -> None:
        dev.baudrate = 115200
        assert dev.baudrate == 115200  # baudrate updated


# -- ASCII commands --------------------------------------------------------


class TestAsciiCommands:
    def test_at_ok(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "AT")
        assert "OK" in actual  # AT returns OK

    def test_at_info(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "AT+INFO")
        assert "Bassomatic v77" in actual  # contains device name
        assert "Uptime" in actual  # contains uptime

    def test_at_temp(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "AT+TEMP")
        assert "+TEMP:" in actual  # contains temp marker
        assert "C" in actual  # contains unit

    def test_at_led_on(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "AT+LED on")
        assert "OK" in actual  # LED on accepted

    def test_at_led_off(self, dev: FakeSerial) -> None:
        _send_cmd(dev, "AT+LED on")
        actual = _send_cmd(dev, "AT+LED off")
        assert "OK" in actual  # LED off accepted

    def test_at_led_toggle_affects_status(self, dev: FakeSerial) -> None:
        # Assign
        _send_cmd(dev, "AT+LED on")
        # Act
        actual = _send_cmd(dev, "AT+STATUS")
        # Assert
        assert "LED: ON" in actual  # status shows LED on

    def test_at_led_no_arg(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "AT+LED")
        assert "ERROR" in actual  # missing arg is an error

    def test_at_status(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "AT+STATUS")
        assert "LED:" in actual  # contains LED state
        assert "Uptime:" in actual  # contains uptime

    def test_at_reset(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "AT+RESET")
        assert "Boot" in actual  # contains boot marker
        assert "Ready" in actual  # contains ready marker

    def test_mem_dump(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "mem 0x1000 32")
        assert "00001000:" in actual  # contains address

    def test_mem_deterministic(self, dev: FakeSerial) -> None:
        # Assign
        actual_first = _send_cmd(dev, "mem 0x100 16")
        # Act
        actual_second = _send_cmd(dev, "mem 0x100 16")
        # Assert
        assert actual_first == actual_second  # same addr produces same output

    def test_mem_no_addr(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "mem")
        assert "00000000:" in actual  # defaults to address 0

    def test_help_list(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "help")
        assert "AT" in actual  # lists AT command
        assert "mem" in actual  # lists mem command

    def test_unknown_cmd(self, dev: FakeSerial) -> None:
        actual = _send_cmd(dev, "BOGUS")
        assert "ERROR" in actual  # unknown command is an error
        assert "BOGUS" in actual  # includes the bad command

    def test_partial_write(self, dev: FakeSerial) -> None:
        """Writing bytes in chunks still produces a response."""
        dev.write(b"AT")
        time.sleep(0.005)
        assert dev.in_waiting == 0  # no response yet (incomplete)
        dev.write(b"\r")
        time.sleep(0.01)
        actual = dev.read(4096).decode()
        assert "OK" in actual  # got response after line ending

    def test_empty_line(self, dev: FakeSerial) -> None:
        dev.write(b"\r")
        time.sleep(0.01)
        actual = dev.read(4096).decode()
        assert actual == "\r\n"  # empty line returns just CRLF


# -- Binary Modbus --------------------------------------------------------


class TestModbus:
    def _build_read_request(
        self, slave_id: int, start_reg: int, num_regs: int
    ) -> bytes:
        """Build a Modbus read holding registers request (func 0x03)."""
        payload = struct.pack(">BBH H", slave_id, 0x03, start_reg, num_regs)
        return _modbus_add_crc(payload)

    def _build_write_request(
        self, slave_id: int, reg_addr: int, value: int
    ) -> bytes:
        """Build a Modbus write single register request (func 0x06)."""
        payload = struct.pack(">BBH H", slave_id, 0x06, reg_addr, value)
        return _modbus_add_crc(payload)

    def test_modbus_read_registers(self, dev: FakeSerial) -> None:
        # Assign
        request = self._build_read_request(slave_id=1, start_reg=0, num_regs=2)
        # Act
        dev.write(request)
        time.sleep(0.01)
        response = dev.read(4096)
        # Assert
        assert len(response) >= 9  # header(3) + data(4) + crc(2)
        assert response[0] == 1  # slave id echoed
        assert response[1] == 0x03  # function code
        assert response[2] == 4  # byte count (2 regs * 2 bytes)
        # Verify CRC
        payload = response[:-2]
        expected_crc = _modbus_crc(payload)
        actual_crc = struct.unpack("<H", response[-2:])[0]
        assert actual_crc == expected_crc  # valid CRC

    def test_modbus_write_register(self, dev: FakeSerial) -> None:
        # Assign
        request = self._build_write_request(slave_id=1, reg_addr=10, value=0x1234)
        # Act
        dev.write(request)
        time.sleep(0.01)
        response = dev.read(4096)
        # Assert — write single register echoes the request
        assert len(response) == len(request)  # same length as request
        # Verify the payload matches (excluding CRC)
        assert response[0] == 1  # slave id
        assert response[1] == 0x06  # function code
        reg = struct.unpack(">H", response[2:4])[0]
        val = struct.unpack(">H", response[4:6])[0]
        assert reg == 10  # register address echoed
        assert val == 0x1234  # value echoed

    def test_modbus_bad_crc(self, dev: FakeSerial) -> None:
        # Assign — valid frame but corrupt the CRC
        request = self._build_read_request(slave_id=1, start_reg=0, num_regs=1)
        bad_request = request[:-2] + b"\xFF\xFF"
        # Act
        dev.write(bad_request)
        time.sleep(0.01)
        response = dev.read(4096)
        # Assert — exception response
        assert len(response) == 5  # slave + func|0x80 + exception + crc(2)
        assert response[1] == (0x03 | 0x80)  # error flag set on func code

    def test_modbus_unknown_function(self, dev: FakeSerial) -> None:
        # Assign — function code 0x10 not supported
        payload = struct.pack(">BB", 1, 0x10) + b"\x00\x00"
        request = _modbus_add_crc(payload)
        # Act
        dev.write(request)
        time.sleep(0.01)
        response = dev.read(4096)
        # Assert — exception response with illegal function code
        assert response[1] == (0x10 | 0x80)  # error flag on original func
        assert response[2] == 0x01  # illegal function exception


# -- Modbus CRC helper -----------------------------------------------------


class TestModbusCrc:
    def test_known_value(self) -> None:
        """Verify CRC against a known Modbus example."""
        # Standard example: slave=1, func=3, start=0, count=1
        data = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x01])
        actual = _modbus_crc(data)
        assert actual == 0x0A84  # known CRC for this payload

    def test_add_crc_roundtrip(self) -> None:
        data = b"\x01\x03\x00\x00\x00\x01"
        framed = _modbus_add_crc(data)
        # Verify the CRC of the full frame (excluding last 2 bytes) matches
        actual = _modbus_crc(framed[:-2])
        expected = struct.unpack("<H", framed[-2:])[0]
        assert actual == expected  # roundtrip CRC matches


# -- Demo config setup -----------------------------------------------------


class TestDemoConfigSetup:
    def test_setup_creates_files(self, tmp_path) -> None:
        from termapy.config import setup_demo_config

        # Act
        config_path = setup_demo_config(tmp_path)
        # Assert
        assert config_path.exists()  # config file created
        assert (tmp_path / "demo" / "scripts").is_dir()  # scripts dir created
        assert (tmp_path / "demo" / "proto").is_dir()  # proto dir created
        assert (tmp_path / "demo" / "plugins" / "probe.py").exists()  # demo plugin copied

    def test_setup_idempotent(self, tmp_path) -> None:
        from termapy.config import setup_demo_config

        # Assign
        config_path = setup_demo_config(tmp_path)
        first_mtime = config_path.stat().st_mtime
        # Act
        time.sleep(0.05)
        setup_demo_config(tmp_path)
        # Assert
        second_mtime = config_path.stat().st_mtime
        assert second_mtime == first_mtime  # file not overwritten
