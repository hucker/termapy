"""The drop-in MEM handler, ``termapy/firmware/c/termapy_mem.c``, run on the host.

A fake platform (256 bytes of RAM at address 0, mapped as a writable
"ram" region and a read-only "rom" region, counting lock/unlock, stdout as
the serial line) is installed with ``TermapyMem_Init``; each wire command
is one stdin line and each reply is delimited by a ``--`` line
(``tests/fixtures/firmware/mem_host.c``).  This is the one place the wire
grammar runs end to end against the real C -- the FakeSerial suite
exercises only termapy's half of the exchange.

Needs a C compiler on PATH; skips otherwise (the CRC-codegen precedent).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mem_wire_cases import WIRE_ERRORS, masked, row

from termapy.firmware import TARGETS, find_target, source_path

# The shipped module, from the package: what ``/firmware.copy mem`` writes.
FIRMWARE = source_path(find_target("mem", targets=TARGETS), "termapy_mem.c").parent
MODULE = FIRMWARE / "termapy_mem.c"
HARNESS = Path(__file__).parent / "fixtures" / "firmware" / "mem_host.c"
ENDIAN = "le" if sys.byteorder == "little" else "be"
# The module alone must be strict C99; the harness needs POSIX/Windows I/O.
STRICT = ["-std=c99", "-pedantic", "-Wall", "-Wextra", "-Werror"]
# The harness's map: "ram" 0x00-0x7F writable, "rom" 0x80-0xFF read-only.
REGIONS = (
    '"regions": [{"name": "ram", "start": "0x00000000", "size": 128, "w": true}, '
    '{"name": "rom", "start": "0x00000080", "size": 128, "w": false}]'
)


def _compiler() -> str:
    found = next((path for path in map(shutil.which, ("cc", "gcc", "clang")) if path), None)
    if found is None:
        pytest.skip("no C compiler on PATH")
    return found


def _strict(compiler: str, *defines: str) -> subprocess.CompletedProcess[str]:
    """The module alone through the strict C99 flags."""
    return subprocess.run(
        [compiler, *STRICT, *defines, "-fsyntax-only", "-I", str(FIRMWARE), str(MODULE)],
        capture_output=True,
        text=True,
        check=False,
    )


def _build(compiler: str, folder: Path, *defines: str) -> Path:
    """Compile module + harness into ``folder``, after a strict pass on the module alone."""
    exe = folder / ("mem_host.exe" if sys.platform == "win32" else "mem_host")
    strict = _strict(compiler, *defines)
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


def _replies(exe: Path, commands: list[str], mode: str = "") -> list[str]:
    """One reply per command, exact bytes (CRLF kept), then the harness's lock line.

    ``mode`` picks the harness's map (``noregions``, ``nullname``); empty
    is the two-region table.
    """
    result = subprocess.run(
        [str(exe), *([mode] if mode else [])],
        input="\n".join(commands).encode("ascii") + b"\n",
        capture_output=True,
        check=True,
    )
    chunks = result.stdout.decode("ascii").split("--\n")
    assert len(chunks) == len(commands) + 1, "one reply per command, then the lock line"
    return chunks


@pytest.mark.slow
class TestWire:
    def test_info_and_silence_before_init(self, mem_host):
        """MEM.INFO is one JSON line with the map; before Init the module says nothing."""
        # Act -- the harness sends INFO before TermapyMem_Init; any reply
        # there would land in front of this one and break the exact match.
        reply, _lock = _replies(mem_host, ["INFO"])

        # Assert
        expected = (
            f'{{"max_block": 64, "address_bits": 32, "endian": "{ENDIAN}", "modify": true, '
            f"{REGIONS}}}\r\nOK\r\n"
        )
        assert reply == expected

    def test_read_rows_of_sixteen(self, mem_host):
        """A read is rows of sixteen bytes; the remainder is a shorter row."""
        # Act
        short, longer, _lock = _replies(mem_host, ["R 0 16", "R 0 20"])

        # Assert
        assert short == row(0, bytes(16)) + "OK\r\n"
        assert longer == row(0, bytes(16)) + row(0x10, bytes(4)) + "OK\r\n", (
            "the remainder is a second row"
        )

    def test_write_then_read_back(self, mem_host):
        """Written bytes read back; lower-case and spaced hex pairs are accepted."""
        # Act
        commands = ["W 10 DEADBEEF", "R 10 4", "W 20 de ad", "R 20 2"]
        _ok, back, _ok2, spaced, _lock = _replies(mem_host, commands)

        # Assert
        assert back == row(0x10, b"\xde\xad\xbe\xef") + "OK\r\n"
        assert spaced == row(0x20, b"\xde\xad") + "OK\r\n", "lower-case, spaced pairs accepted"

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
        """MEM.M masks the word as the core loads it, replies the old word, locks once."""
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
        assert pre == row(addr, old) + "OK\r\n", "MEM.M replies the PRE-modify word"
        assert after == row(addr, masked(old, and_mask, or_mask)) + "OK\r\n"
        assert lock == "locks=1 depth=0\n", "locked once around the read-modify-write, unlocked"

    def test_a_refusal_inside_the_lock_still_unlocks(self, mem_host):
        """The map allows the faulting word, the read op refuses it: locked, then unlocked."""
        # Act
        reply, lock = _replies(mem_host, ["M 40 FFFFFFFF 00000000"])

        # Assert
        assert reply == "ERR range\r\n", "the platform's own refusal is still ERR range"
        assert lock == "locks=1 depth=0\n", "a refused read must not leave the lock held"

    @pytest.mark.parametrize(
        ("command", "reason"),
        [
            ("", "usage"),
            ("X", "usage"),
            ("R F0 32", "range"),  # runs past the end of the map
            *WIRE_ERRORS,
        ],
    )
    def test_errors(self, mem_host, command, reason):
        """Malformed, oversized and out-of-map commands answer one ERR line with the reason."""
        # Act
        reply, _lock = _replies(mem_host, [command])

        # Assert
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
        """Whatever the dispatcher leaves -- the whole line, ".R", "R", any case -- dispatches."""
        # Act
        reply, _lock = _replies(mem_host, [command])

        # Assert
        assert reply == row(0, b"\x00") + "OK\r\n"


@pytest.mark.slow
class TestRegions:
    """The platform's memory map gates every verb in the wire layer."""

    def test_a_read_only_region_reads(self, mem_host):
        """Reading "rom" is allowed; only writing it is not."""
        # Act
        reply, _lock = _replies(mem_host, ["R 80 4"])

        # Assert
        assert reply == row(0x80, bytes(4)) + "OK\r\n"

    @pytest.mark.parametrize(
        "command",
        ["R 7C 8", "R 100 1", "W 7F 0102", "W 80 01", "M 80 FF 01"],
        ids=[
            "read-across-two",
            "read-outside",
            "write-across-two",
            "write-read-only",
            "modify-read-only",
        ],
    )
    def test_the_map_refuses_as_a_whole_reply(self, mem_host, command):
        """Spanning two regions, outside every region, or writing a read-only one: one ERR range."""
        # Act
        reply, _lock = _replies(mem_host, [command])

        # Assert
        assert reply == "ERR range\r\n", "no rows, no partial write, just the refusal"

    def test_a_refused_write_changes_nothing(self, mem_host):
        """A write into "rom" is refused before the write op runs: the bytes stay."""
        # Act
        refused, after, _lock = _replies(mem_host, ["W 80 AB", "R 80 1"])

        # Assert
        assert refused == "ERR range\r\n"
        assert after == row(0x80, b"\x00") + "OK\r\n", "the read-only byte is untouched"

    def test_a_modify_the_map_refuses_never_takes_the_lock(self, mem_host):
        """MEM.M on "rom" is refused before the critical section is entered."""
        # Act
        reply, lock = _replies(mem_host, ["M 80 FF 01"])

        # Assert
        assert reply == "ERR range\r\n"
        assert lock == "locks=0 depth=0\n", "the map's refusal comes before lock()"

    def test_an_empty_map_refuses_everything(self, mem_host):
        """regionCount 0: every verb answers ERR range, and MEM.INFO lists no regions."""
        # Act
        *replies, lock = _replies(mem_host, ["R 0 1", "W 0 00", "M 0 FF 00", "INFO"], "noregions")

        # Assert
        assert replies[:3] == ["ERR range\r\n"] * 3, "read, write and modify all refused"
        assert '"regions": []' in replies[3], "the map is reported empty, not omitted"
        assert lock == "locks=0 depth=0\n", "nothing reached the critical section"

    def test_a_null_name_prints_empty(self, mem_host):
        """A region with a NULL name is reported as "" rather than crashing or skipping."""
        # Act
        reply, _lock = _replies(mem_host, ["INFO"], "nullname")

        # Assert
        assert '"regions": [{"name": "", "start": "0x00000000", "size": 256, "w": true}]' in reply


@pytest.mark.slow
class TestConfiguration:
    def test_address_bits_sets_the_row_and_region_width(self, tmp_path):
        """A 16-bit build prints four address digits, in rows and in the map."""
        # Arrange
        exe = _build(_compiler(), tmp_path, "-DTERMAPY_MEM_ADDRESS_BITS=16")

        # Act
        info, row, _lock = _replies(exe, ["INFO", "R 0 1"])

        # Assert
        assert '"address_bits": 16' in info
        assert '"start": "0x0080"' in info, "region starts use the same digit count"
        assert row == "0000: 00\r\nOK\r\n", "four hex digits of address"

    def test_row_bytes_sets_the_row_length(self, tmp_path):
        """TERMAPY_MEM_ROW_BYTES=8 splits a 16-byte read into two rows of eight."""
        # Arrange
        exe = _build(_compiler(), tmp_path, "-DTERMAPY_MEM_ROW_BYTES=8")

        # Act
        reply, _lock = _replies(exe, ["R 0 16"])

        # Assert
        assert reply == row(0, bytes(8)) + row(8, bytes(8)) + "OK\r\n"

    @pytest.mark.parametrize(
        ("define", "knob"),
        [
            ("-DTERMAPY_MEM_ADDRESS_BITS=64", "TERMAPY_MEM_ADDRESS_BITS"),
            ("-DTERMAPY_MEM_MAX_BLOCK=0", "TERMAPY_MEM_MAX_BLOCK"),
            ("-DTERMAPY_MEM_ROW_BYTES=0", "TERMAPY_MEM_ROW_BYTES"),
        ],
        ids=["address-bits-over-32", "max-block-zero", "row-bytes-zero"],
    )
    def test_a_bad_knob_refuses_to_compile(self, define, knob):
        """An impossible setting is a #error naming the knob, never a silent build."""
        # Act
        result = _strict(_compiler(), define)

        # Assert
        assert result.returncode != 0, "the build must fail"
        assert knob in result.stderr, "the #error names the knob"
