"""sw_loopback: /proto.crc.find cmd= keeps a CRC byte of 0x0A, on pyserial's loop:// port.

``loop://`` hands back what was sent, so with ``eol`` set to LF the line
ending a trigger goes out with is the reply's last byte -- here, the last
byte of its CRC. In-process on a ``CLITerminal``, so fast, not slow.
"""

from __future__ import annotations

import json

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG

# The software loopback needs no hardware: it runs anywhere, CI included.
pytestmark = pytest.mark.sw_loopback

# A frame whose crc16-modbus trailer (big-endian) is ``5F 0A``: its last CRC
# byte is the LF a blind strip removed.  Sent as ``cmd=AT+X4290_`` with
# ``eol`` set to LF, the appended line ending IS that byte.
_PAYLOAD = b"AT+X4290"
_TRAILER = b"_\n"


@pytest.fixture
def loop_cli(tmp_path):
    """A CLI on pyserial's loop:// port with an LF line ending, so cmd= text comes back as sent."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "loop://", "baud_rate": 115200},
        "eol": "\n",
    }
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    assert terminal._connect() is True, "the loop:// port opens"
    yield terminal
    terminal._disconnect()


class TestFindCmdKeepsAnLfCrcByte:
    """/proto.crc.find cmd= on a reply whose last CRC byte is 0x0A."""

    def test_find_matches_a_frame_whose_crc_ends_in_lf(self, loop_cli):
        """When the stripped frame matches nothing, find detects on the frame whole and names the algorithm."""
        # Arrange
        trigger = (_PAYLOAD + _TRAILER[:1]).decode()

        # Act
        result = loop_cli.repl.dispatch_full(f"/proto.crc.find cmd={trigger}")

        # Assert
        assert result.success is True, "the capture came back"
        assert result.value == "crc16-modbus", (
            "the LF was stripped as a line ending, taking the CRC's last byte with it"
        )

    def test_find_still_strips_a_real_line_ending_twice_in_a_row(self, loop_cli):
        """A frame that matches without its LF is still reported that way, run after run."""
        # Arrange -- 123456789 + its crc16-modbus trailer 4B 37, then the LF
        trigger = "123456789K7"

        # Act
        first = loop_cli.repl.dispatch_full(f"/proto.crc.find cmd={trigger}")
        second = loop_cli.repl.dispatch_full(f"/proto.crc.find cmd={trigger}")

        # Assert
        assert first.value == "crc16-modbus", "the line ending came off before detection"
        assert second.value == "crc16-modbus", "a second capture does not see the first's bytes"
