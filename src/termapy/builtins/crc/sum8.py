"""Byte checksum - sum of all bytes mod 256."""

NAME = "sum8"
WIDTH = 1


def compute(data: bytes) -> int:
    """Compute byte checksum (sum mod 256).

    Args:
        data: Payload bytes.

    Returns:
        8-bit checksum.
    """
    return sum(data) & 0xFF
