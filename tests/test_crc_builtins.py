"""Tests for built-in CRC checksum modules (sum8, sum16)."""

from termapy.builtins.crc.sum8 import NAME as SUM8_NAME, WIDTH as SUM8_WIDTH, compute as sum8
from termapy.builtins.crc.sum16 import NAME as SUM16_NAME, WIDTH as SUM16_WIDTH, compute as sum16

# ── sum8 module attributes ──────────────────────────────────────────────────


class TestSum8Attributes:
    def test_name(self):
        """NAME is sum8, the key the CRC plugin loader registers this module under."""
        assert SUM8_NAME == "sum8", "module NAME should be 'sum8'"

    def test_width(self):
        """WIDTH is 1: a sum8 checksum occupies a single byte in a frame."""
        assert SUM8_WIDTH == 1, "WIDTH should be 1 byte"


# ── sum8 compute ────────────────────────────────────────────────────────────


class TestSum8Compute:
    def test_empty(self):
        """An empty payload sums to 0 rather than raising."""
        # Act
        actual = sum8(b"")

        # Assert - empty input sums to zero
        assert actual == 0, f"expected 0 for empty input, got {actual}"

    def test_single_byte(self):
        """A lone byte comes back as itself, with no seed or inversion applied."""
        # Act
        actual = sum8(b"\x42")

        # Assert - single byte returns itself
        assert actual == 0x42, f"expected 0x42, got {actual:#x}"

    def test_known_sum(self):
        """Bytes with no carry sum plainly: 1 through 5 gives 15."""
        # Arrange
        data = bytes([1, 2, 3, 4, 5])

        # Act
        actual = sum8(data)

        # Assert - 1+2+3+4+5 = 15
        expected = 15
        assert actual == expected, f"expected {expected}, got {actual}"

    def test_wraps_at_256(self):
        """A carry out of 8 bits is discarded: 0xFF + 0x01 gives 0x00."""
        # Arrange - 0xFF + 0x01 = 0x100, should wrap to 0x00
        data = bytes([0xFF, 0x01])

        # Act
        actual = sum8(data)

        # Assert - wraps mod 256
        expected = 0x00
        assert actual == expected, f"expected {expected:#x}, got {actual:#x}"

    def test_all_ff(self):
        """Repeated carries are all discarded: three 0xFF bytes give 0xFD."""
        # Arrange - 3 * 0xFF = 765 = 0x2FD, mod 256 = 0xFD
        data = bytes([0xFF, 0xFF, 0xFF])

        # Act
        actual = sum8(data)

        # Assert
        expected = 0xFD
        assert actual == expected, f"expected {expected:#x}, got {actual:#x}"

    def test_ascii_hello(self):
        """Text sums by byte value: Hello totals 500 and truncates to 244."""
        # Arrange
        data = b"Hello"

        # Act
        actual = sum8(data)

        # Assert - H(72) + e(101) + l(108) + l(108) + o(111) = 500, mod 256 = 244
        expected = 500 & 0xFF
        assert actual == expected, f"expected {expected}, got {actual}"


# ── sum16 module attributes ─────────────────────────────────────────────────


class TestSum16Attributes:
    def test_name(self):
        """NAME is sum16, the key the CRC plugin loader registers this module under."""
        assert SUM16_NAME == "sum16", "module NAME should be 'sum16'"

    def test_width(self):
        """WIDTH is 2: a sum16 checksum occupies two bytes in a frame."""
        assert SUM16_WIDTH == 2, "WIDTH should be 2 bytes"


# ── sum16 compute ───────────────────────────────────────────────────────────


class TestSum16Compute:
    def test_empty(self):
        """An empty payload sums to 0 in the 16-bit variant too."""
        # Act
        actual = sum16(b"")

        # Assert - empty input sums to zero
        assert actual == 0, f"expected 0 for empty input, got {actual}"

    def test_single_byte(self):
        """A lone byte comes back as itself in the low half of the 16-bit sum."""
        # Act
        actual = sum16(b"\x42")

        # Assert - single byte returns itself
        assert actual == 0x42, f"expected 0x42, got {actual:#x}"

    def test_known_sum(self):
        """Below 8 bits of total, sum16 agrees with sum8: 1 through 5 gives 15."""
        # Arrange
        data = bytes([1, 2, 3, 4, 5])

        # Act
        actual = sum16(data)

        # Assert - 1+2+3+4+5 = 15
        expected = 15
        assert actual == expected, f"expected {expected}, got {actual}"

    def test_does_not_wrap_at_256(self):
        """The carry out of 8 bits is kept: 0xFF + 0x01 gives 0x100, not 0x00."""
        # Arrange - 0xFF + 0x01 = 0x100, should NOT wrap at 8 bits
        data = bytes([0xFF, 0x01])

        # Act
        actual = sum16(data)

        # Assert - 16-bit sum preserves the carry
        expected = 0x100
        assert actual == expected, f"expected {expected:#x}, got {actual:#x}"

    def test_wraps_at_65536(self):
        """A 257-byte payload totaling 65281 comes back unchanged; despite the name, no wrap."""
        # Arrange - 0xFFFF + 0x01 = 0x10000, should wrap to 0x0000
        data = bytes([0xFF] * 256 + [0x01])

        # Act
        actual = sum16(data)

        # Assert - 256 * 255 + 1 = 65281, mod 65536 = 65281 (no wrap yet)
        expected = (256 * 255 + 1) & 0xFFFF
        assert actual == expected, f"expected {expected:#x}, got {actual:#x}"

    def test_large_wraps(self):
        """257 bytes of 0xFF land exactly on 0xFFFF; despite the name, nothing wraps."""
        # Arrange - 257 * 0xFF = 65535 = 0xFFFF
        data = bytes([0xFF] * 257)

        # Act
        actual = sum16(data)

        # Assert - 257 * 255 = 65535
        expected = (257 * 255) & 0xFFFF
        assert actual == expected, f"expected {expected:#x}, got {actual:#x}"

    def test_ascii_hello(self):
        """Hello keeps its full total of 500, the value sum8 truncates to 244."""
        # Arrange
        data = b"Hello"

        # Act
        actual = sum16(data)

        # Assert - H(72) + e(101) + l(108) + l(108) + o(111) = 500
        expected = 500
        assert actual == expected, f"expected {expected}, got {actual}"

    def test_sum8_vs_sum16_differ_on_overflow(self):
        """Three 0x80 bytes: sum8 drops the carry (0x80) while sum16 keeps it (0x180)."""
        # Arrange - data that overflows 8 bits but not 16
        data = bytes([0x80, 0x80, 0x80])

        # Act
        actual_8 = sum8(data)
        actual_16 = sum16(data)

        # Assert - sum8 wraps, sum16 doesn't
        assert actual_8 == 0x80, f"sum8 expected 0x80, got {actual_8:#x}"
        assert actual_16 == 0x180, f"sum16 expected 0x180, got {actual_16:#x}"
