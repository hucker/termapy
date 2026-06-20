"""Tests for the demo's AT+RND random-packet emitter.

Exercises three handlers added to ``FakeSerial`` for the CRC API
walkthrough:

* ``AT+RND`` -- one packet using a random catalogue algorithm from a
  small curated set; payload + CRC bytes.
* ``AT+RND.CUSTOM`` -- one packet using a secret non-catalogue
  Rocksoft polynomial; payload length follows a deterministic cycle.
* ``AT+RND.CUSTOM.REVEAL`` -- prints the secret params.

The point: ``/proto.crc.find cmd=AT+RND`` identifies the algorithm in
the response, and ``/proto.crc.reverse cmd=AT+RND.CUSTOM count=13``
recovers the secret polynomial algebraically.  These tests confirm
the demo emits packets the engine layer can identify and reverse.
"""

from __future__ import annotations

from crcglot import ALGORITHMS, Crc, compute, generic_crc

from termapy.demo import FakeSerial


def _drain(s: FakeSerial, max_bytes: int = 256) -> bytes:
    """Read everything currently in the demo's output buffer."""
    return bytes(s.read(max_bytes))


class TestAtRnd:
    """``AT+RND`` emits one packet using a random catalogue algorithm."""

    def test_response_is_payload_plus_catalogue_crc(self):
        # Arrange
        s = FakeSerial()

        # Act
        s.write(b"AT+RND\r")
        response = _drain(s)

        # Assert -- trailing CRLF stripped, the rest is payload + CRC of
        # SOME curated algorithm.  We don't pin the specific algorithm
        # (it's random), but the packet must verify against AT LEAST one
        # of the curated set.
        assert response.endswith(b"\r\n"), (
            f"AT+RND response should end with CRLF; got {response!r}"
        )
        packet = response[:-2]
        curated = {
            "crc8", "crc8-maxim",
            "crc16-modbus", "crc16-xmodem",
            "crc32", "crc32-bzip2",
        }
        matched = False
        for name in curated:
            algo = ALGORITHMS[name]
            width_bytes = (algo.width + 7) // 8
            if len(packet) <= width_bytes:
                continue
            payload, crc_bytes = packet[:-width_bytes], packet[-width_bytes:]
            endian = "little" if algo.refout else "big"
            actual_crc = int.from_bytes(crc_bytes, endian)
            expected_crc = compute(payload, name)
            if actual_crc == expected_crc:
                matched = True
                break
        assert matched, (
            f"AT+RND packet should verify against one of {curated}; "
            f"got packet hex {packet.hex()}"
        )


class TestAtRndCustom:
    """``AT+RND.CUSTOM`` emits packets using a secret Rocksoft poly with
    a deterministic length cycle."""

    def test_length_cycle_matches_documented_pattern(self):
        # Arrange -- the cycle is [8, 16, 8, 16, 24] payload bytes;
        # each packet adds 2 CRC bytes and 2 CRLF bytes.
        s = FakeSerial()
        expected_payload_lengths = [8, 16, 8, 16, 24, 8, 16]

        # Act
        observed: list[int] = []
        for _ in range(len(expected_payload_lengths)):
            s.write(b"AT+RND.CUSTOM\r")
            resp = _drain(s)
            # Strip CRLF and 2-byte CRC to get payload length
            assert resp.endswith(b"\r\n"), f"CRLF expected; got {resp!r}"
            payload = resp[:-2][:-2]
            observed.append(len(payload))

        # Assert
        assert observed == expected_payload_lengths, (
            f"AT+RND.CUSTOM length cycle should be "
            f"{expected_payload_lengths}; got {observed}"
        )

    def test_crc_matches_reveal_params(self):
        # Arrange -- capture one packet, recompute its CRC against the
        # REVEAL'd secret params, and confirm they match.
        s = FakeSerial()
        s.write(b"AT+RND.CUSTOM\r")
        resp = _drain(s)
        assert resp.endswith(b"\r\n"), f"CRLF expected; got {resp!r}"
        packet = resp[:-2]
        payload, crc_bytes = packet[:-2], packet[-2:]
        actual_crc = int.from_bytes(crc_bytes, "big")

        # Act -- recompute via crcglot.generic_crc with the secret params
        secret = Crc(
            width=16, poly=0x1A2B, init=0xABCD,
            refin=False, refout=False, xorout=0x0000,
        )
        expected_crc = generic_crc(payload, secret)

        # Assert
        assert actual_crc == expected_crc, (
            f"AT+RND.CUSTOM packet CRC ({actual_crc:#06x}) should match "
            f"the secret Rocksoft params (expected {expected_crc:#06x}); "
            f"packet hex {packet.hex()}"
        )


class TestAtRndCustomReveal:
    """``AT+RND.CUSTOM.REVEAL`` prints the secret params in kv form."""

    def test_reveal_contains_documented_params(self):
        # Arrange
        s = FakeSerial()

        # Act
        s.write(b"AT+RND.CUSTOM.REVEAL\r")
        response = _drain(s).decode(errors="replace").strip()

        # Assert -- each documented param appears in the line
        expected_fragments = [
            "width=16",
            "poly=0x1A2B",
            "init=0xABCD",
            "refin=false",
            "refout=false",
            "xorout=0x0000",
        ]
        for fragment in expected_fragments:
            assert fragment in response, (
                f"REVEAL output should contain {fragment!r}; "
                f"got {response!r}"
            )
