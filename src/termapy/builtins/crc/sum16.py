"""16-bit checksum — sum of all bytes mod 65536."""

NAME = "sum16"
WIDTH = 2


def compute(data: bytes) -> int:
    """Compute 16-bit checksum (sum mod 65536).

    Args:
        data: Payload bytes.

    Returns:
        16-bit checksum.
    """
    return sum(data) & 0xFFFF
