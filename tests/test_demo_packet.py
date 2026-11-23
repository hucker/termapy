"""Tests for the demo's AT+PKT sample packets and the readings that change.

The demo device answers the same on every run (registry
``demo-deterministic``): a reply that changes steps through a fixed cycle.
Three handlers drive the CRC tour:

* ``AT+PKT`` -- one of four fixed packets under ``crc16-xmodem``;
  ``/proto.crc.find cmd=AT+PKT`` names that algorithm and no other.
* ``AT+PKT.CUSTOM`` -- one of thirteen fixed packets under a secret
  non-catalog Rocksoft polynomial, which
  ``/proto.crc.reverse cmd=AT+PKT.CUSTOM count=13`` recovers.
* ``AT+PKT.CUSTOM.REVEAL`` -- prints the secret params.
"""

from __future__ import annotations

import pytest
from crcglot import ALGORITHMS, Crc, compute, detect, generic_crc

from termapy.demo import FakeSerial


def _ask(device: FakeSerial, command: str) -> bytes:
    """Send ``command`` and return everything the demo answered."""
    device.write(command.encode() + b"\r")
    return bytes(device.read(256))


def _packet(device: FakeSerial, command: str) -> bytes:
    """One reply to ``command`` with its CRLF terminator removed."""
    reply = _ask(device, command)
    assert reply.endswith(b"\r\n"), f"{command} ends its reply with CRLF; got {reply!r}"
    return reply[:-2]


class TestAtPkt:
    """``AT+PKT`` emits one of four fixed packets under crc16-xmodem."""

    def test_every_packet_verifies_under_the_demo_algorithm(self):
        """Each packet in the cycle is a payload followed by its crc16-xmodem, big-endian."""
        # Arrange
        device = FakeSerial()
        algo = ALGORITHMS["crc16-xmodem"]

        # Act
        packets = [_packet(device, "AT+PKT") for _ in range(len(FakeSerial._PKT_LENGTHS))]

        # Assert
        for packet in packets:
            payload, crc_bytes = packet[:-2], packet[-2:]
            actual = int.from_bytes(crc_bytes, "little" if algo.refout else "big")
            expected = compute(payload, "crc16-xmodem")
            assert actual == expected, f"packet {packet.hex()} carries its crc16-xmodem"

    @pytest.mark.rule("demo-deterministic")
    def test_every_packet_names_one_algorithm(self):
        """No catalog CRC fits any packet by chance, so find names crc16-xmodem and nothing else."""
        # Arrange
        device = FakeSerial()

        # Act -- the call /proto.crc.find makes on a captured binary packet
        found = [
            [match.algorithm for match in detect(packet, mode="binary", match="all").candidates]
            for packet in (_packet(device, "AT+PKT") for _ in range(len(FakeSerial._PKT_LENGTHS)))
        ]

        # Assert
        expected = [["crc16-xmodem"]] * len(FakeSerial._PKT_LENGTHS)
        assert found == expected, "each packet identifies as exactly one algorithm"

    @pytest.mark.rule("demo-deterministic")
    def test_the_packets_cycle_through_a_fixed_set(self):
        """The fifth packet is the first again, and a fresh device sends the same nine packets."""
        # Arrange
        first, second = FakeSerial(), FakeSerial()
        period = len(FakeSerial._PKT_LENGTHS)

        # Act
        sent = [_packet(first, "AT+PKT") for _ in range(2 * period + 1)]
        again = [_packet(second, "AT+PKT") for _ in range(2 * period + 1)]

        # Assert
        assert len(set(sent[:period])) == period, "the packets in one cycle all differ"
        assert sent[period:] == sent[: period + 1], "the cycle repeats, so no packet is untested"
        assert again == sent, "the same on every run"

    def test_unknown_pkt_command_is_refused(self):
        """A misspelled AT+PKT subcommand answers with an error naming it."""
        # Arrange
        device = FakeSerial()

        # Act
        reply = _ask(device, "AT+PKT.NOPE").decode()

        # Assert
        assert reply == "ERROR: Unknown AT+PKT command 'AT+PKT.NOPE'\r\n", reply


class TestAtPktCustom:
    """``AT+PKT.CUSTOM`` emits one of thirteen fixed packets under a secret polynomial."""

    @pytest.mark.rule("demo-deterministic")
    def test_length_cycle_matches_documented_pattern(self):
        """Payload lengths follow the thirteen-packet cycle the reverse walkthrough relies on, then repeat."""
        # Arrange
        device = FakeSerial()
        cycle = list(FakeSerial._PKT_CUSTOM_LENGTHS)

        # Act -- each packet is payload + 2 CRC bytes
        observed = [len(_packet(device, "AT+PKT.CUSTOM")) - 2 for _ in range(len(cycle) + 1)]

        # Assert
        expected = [8, 16, 8, 16, 24, 8, 16, 8, 16, 24, 8, 16, 8, 8]
        assert observed == expected, "thirteen lengths, then the first again"

    def test_crc_matches_reveal_params(self):
        """Every packet's CRC uses the secret params, so a recovered poly can be checked against them."""
        # Arrange
        device = FakeSerial()
        secret = Crc(width=16, poly=0x1A2B, init=0xABCD, refin=False, refout=False, xorout=0x0000)

        # Act
        packets = [_packet(device, "AT+PKT.CUSTOM") for _ in FakeSerial._PKT_CUSTOM_LENGTHS]

        # Assert
        for packet in packets:
            payload, crc_bytes = packet[:-2], packet[-2:]
            assert int.from_bytes(crc_bytes, "big") == generic_crc(payload, secret), (
                f"packet {packet.hex()} carries the secret CRC"
            )

    @pytest.mark.rule("demo-deterministic")
    def test_the_packets_are_the_same_on_every_run(self):
        """Two fresh devices send the same thirteen packets, all different."""
        # Arrange
        first, second = FakeSerial(), FakeSerial()
        count = len(FakeSerial._PKT_CUSTOM_LENGTHS)

        # Act
        sent = [_packet(first, "AT+PKT.CUSTOM") for _ in range(count)]
        again = [_packet(second, "AT+PKT.CUSTOM") for _ in range(count)]

        # Assert
        assert len(set(sent)) == count, "thirteen distinct packets for reverse to solve from"
        assert again == sent, "the same on every run"


class TestAtPktCustomReveal:
    """``AT+PKT.CUSTOM.REVEAL`` prints the secret params in kv form."""

    def test_reveal_contains_documented_params(self):
        """REVEAL prints all six Rocksoft params with the values the docs promise."""
        # Arrange
        device = FakeSerial()

        # Act
        response = _ask(device, "AT+PKT.CUSTOM.REVEAL").decode(errors="replace").strip()

        # Assert
        expected = "width=16 poly=0x1A2B init=0xABCD refin=false refout=false xorout=0x0000"
        assert response == expected, "the six params the tour compares against"


class TestReadingsThatChange:
    """A reading that changes steps through a fixed cycle instead of a random draw."""

    @pytest.mark.rule("demo-deterministic")
    def test_temperature_changes_and_is_the_same_on_every_run(self):
        """AT+TEMP moves between readings, within 22-25 C, and a fresh device reads the same sequence."""
        # Arrange
        first, second = FakeSerial(), FakeSerial()
        count = len(FakeSerial._TEMPS) + 1

        # Act
        readings = [_ask(first, "AT+TEMP") for _ in range(count)]
        again = [_ask(second, "AT+TEMP") for _ in range(count)]

        # Assert
        values = [
            float(reading.decode().removeprefix("+TEMP: ").removesuffix("C\r\n"))
            for reading in readings
        ]
        assert len(set(values)) > 1, "the temperature changes, so a plot of it is not flat"
        assert all(22.0 <= value <= 25.0 for value in values), values
        assert again == readings, "the same on every run"

    @pytest.mark.rule("demo-deterministic")
    def test_free_memory_is_fixed(self):
        """AT+INFO reports the same free memory every time."""
        # Arrange
        device = FakeSerial()

        # Act
        replies = [_ask(device, "AT+INFO").decode() for _ in range(3)]

        # Assert
        assert all("Free memory: 29135 bytes" in reply for reply in replies), replies
