"""The drop-in MEM handler, ``examples/firmware/termapy_mem.c``, run on the host.

A fake platform (256 bytes of RAM at address 0, counting lock/unlock,
stdout as the serial line) is installed with ``TermapyMem_Init``; each
wire command is one stdin line and each reply is delimited by a ``--``
line (``tests/fixtures/firmware/mem_host.c``).  This is the one place the
wire grammar runs end to end against the real C -- the FakeSerial suite
exercises only termapy's half of the exchange.

Needs a C compiler on PATH; skips otherwise (the CRC-codegen precedent).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIRMWARE = Path(__file__).parent.parent / "examples" / "firmware"
MODULE = FIRMWARE / "termapy_mem.c"
HARNESS = Path(__file__).parent / "fixtures" / "firmware" / "mem_host.c"
ENDIAN = "le" if sys.byteorder == "little" else "be"
# The module alone must be strict C99; the harness needs POSIX/Windows I/O.
STRICT = ["-std=c99", "-pedantic", "-Wall", "-Wextra", "-Werror"]


def _compiler() -> str:
    found = next((path for path in map(shutil.which, ("cc", "gcc", "clang")) if path), None)
    if found is None:
        pytest.skip("no C compiler on PATH")
    return found


def _build(compiler: str, folder: Path, *defines: str) -> Path:
    """Compile module + harness into ``folder``, after a strict pass on the module alone."""
    exe = folder / ("mem_host.exe" if sys.platform == "win32" else "mem_host")
    strict = subprocess.run(
        [compiler, *STRICT, *defines, "-fsyntax-only", "-I", str(FIRMWARE), str(MODULE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert strict.returncode == 0, strict.stderr
    link = subprocess.run(
        [
            compiler,
            "-std=gnu99",
            "-Wall",
            "-Wextra",
            "-Werror",
            *defines,
            "-I",
            str(FIRMWARE),
            str(MODULE),
            str(HARNESS),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert link.returncode == 0, link.stderr
    return exe


@pytest.fixture(scope="module")
def mem_host(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(_compiler(), tmp_path_factory.mktemp("mem_host"))


def _replies(exe: Path, commands: list[str]) -> list[str]:
    """One reply per command, exact bytes (CRLF kept), then the harness's lock line."""
    result = subprocess.run(
        [str(exe)],
        input="\n".join(commands).encode("ascii") + b"\n",
        capture_output=True,
        check=True,
    )
    chunks = result.stdout.decode("ascii").split("--\n")
    assert len(chunks) == len(commands) + 1, "one reply per command, then the lock line"
    return chunks


def _row(addr: int, data: bytes) -> str:
    return f"{addr:08X}: " + " ".join(f"{byte:02X}" for byte in data) + "\r\n"


def _masked(old: bytes, and_mask: int, or_mask: int) -> bytes:
    """What MEM.M leaves behind, computed the way the core sees the word."""
    value = int.from_bytes(old, sys.byteorder)
    return ((value & and_mask) | or_mask).to_bytes(len(old), sys.byteorder)


@pytest.mark.slow
class TestWire:
    def test_info_and_silence_before_init(self, mem_host):
        # The harness sends INFO before TermapyMem_Init; any reply there
        # would land in front of this one and break the exact match.
        reply, _lock = _replies(mem_host, ["INFO"])
        expected = (
            f'{{"max_block": 64, "address_bits": 32, "endian": "{ENDIAN}", "modify": true}}'
            "\r\nOK\r\n"
        )
        assert reply == expected

    def test_read_rows_of_sixteen(self, mem_host):
        short, longer, _lock = _replies(mem_host, ["R 0 16", "R 0 20"])
        assert short == _row(0, bytes(16)) + "OK\r\n"
        assert longer == _row(0, bytes(16)) + _row(0x10, bytes(4)) + "OK\r\n", (
            "the remainder is a second row"
        )

    def test_write_then_read_back(self, mem_host):
        commands = ["W 10 DEADBEEF", "R 10 4", "W 20 de ad", "R 20 2"]
        _ok, back, _ok2, spaced, _lock = _replies(mem_host, commands)
        assert back == _row(0x10, b"\xde\xad\xbe\xef") + "OK\r\n"
        assert spaced == _row(0x20, b"\xde\xad") + "OK\r\n", "lower-case, spaced pairs accepted"

    @pytest.mark.parametrize(
        ("addr", "old", "and_mask", "or_mask"),
        [
            (0x10, b"\xde\xad\xbe\xef", 0xFFFFFF00, 0x00000001),
            (0x20, b"\x34\x12", 0xFF00, 0x0001),
            (0x30, b"\x12", 0xF0, 0x0F),
        ],
        ids=["u32", "u16", "u8"],
    )
    def test_modify_masks_the_word_the_core_sees(self, mem_host, addr, old, and_mask, or_mask):
        # Arrange: the masks' digit count picks the width, as on the wire.
        digits = len(old) * 2
        commands = [
            f"W {addr:X} {old.hex().upper()}",
            f"M {addr:X} {and_mask:0{digits}X} {or_mask:0{digits}X}",
            f"R {addr:X} {len(old)}",
        ]

        # Act
        _ok, pre, after, lock = _replies(mem_host, commands)

        # Assert
        assert pre == _row(addr, old) + "OK\r\n", "MEM.M replies the PRE-modify word"
        assert after == _row(addr, _masked(old, and_mask, or_mask)) + "OK\r\n"
        assert lock == "locks=1 depth=0\n", "locked once around the read-modify-write, unlocked"

    def test_failed_modify_still_unlocks(self, mem_host):
        reply, lock = _replies(mem_host, ["M 100 FFFFFFFF 00000000"])
        assert reply == "ERR range\r\n", "the fake RAM ends at 0x100"
        assert lock == "locks=1 depth=0\n", "a refused read must not leave the lock held"

    @pytest.mark.parametrize(
        ("command", "reason"),
        [
            ("", "usage"),
            ("X", "usage"),
            ("R", "usage"),
            ("R 0", "usage"),
            ("R 0 0", "length"),
            ("R 0 65", "length"),
            ("R F0 32", "range"),  # runs past the fake RAM
            ("R 0 16 junk", "usage"),  # a verb takes exactly its arguments
            ("INFO junk", "usage"),
            ("R 100000000 1", "usage"),  # nine hex digits: wider than an address
            ("R 0 4294967296", "usage"),  # past uint32_t, refused rather than wrapped
            ("W 0", "usage"),
            ("W 0 ABC", "usage"),  # odd digit count
            ("W 0 " + "00" * 65, "length"),
            ("M 3 FFFF 0000", "usage"),  # misaligned for a 16-bit word
            ("M 0 FF 0000", "usage"),  # mask widths disagree
            ("M 0 FF 00 junk", "usage"),
        ],
    )
    def test_errors(self, mem_host, command, reason):
        reply, _lock = _replies(mem_host, [command])
        assert reply == f"ERR {reason}\r\n"

    @pytest.mark.parametrize(
        "command",
        [
            "R 0 1",
            "r 0 1",
            ".R 0 1",
            "MEM.R 0 1",
            "mem.r 0 1",
            "  R 0 1",
        ],
    )
    def test_dispatcher_prefix_forms(self, mem_host, command):
        reply, _lock = _replies(mem_host, [command])
        assert reply == _row(0, b"\x00") + "OK\r\n"


@pytest.mark.slow
class TestConfiguration:
    def test_address_bits_sets_the_row_width(self, tmp_path):
        exe = _build(_compiler(), tmp_path, "-DTERMAPY_MEM_ADDRESS_BITS=16")
        info, row, _lock = _replies(exe, ["INFO", "R 0 1"])
        assert '"address_bits": 16' in info
        assert row == "0000: 00\r\nOK\r\n", "four hex digits of address"

    def test_address_bits_over_32_refuses_to_compile(self):
        # Addresses are uint32_t; a wider setting used to compile into an
        # undefined shift in the hex printer.
        result = subprocess.run(
            [
                _compiler(),
                *STRICT,
                "-DTERMAPY_MEM_ADDRESS_BITS=64",
                "-fsyntax-only",
                "-I",
                str(FIRMWARE),
                str(MODULE),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "TERMAPY_MEM_ADDRESS_BITS" in result.stderr, "the #error names the knob"
