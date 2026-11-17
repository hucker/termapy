"""The C command monitor, ``termapy/firmware/c/termapy_monitor.c``, on the host.

A fake device (``tests/fixtures/firmware/monitor_host.c``) installs a small
command table over stdout as the serial line; each stdin line is fed to the
monitor byte by byte, and each directive's output is delimited by a ``--``
line.  The same lines also go through the MicroPython monitor the board
starters run, and the two must answer byte for byte alike: the C is that
monitor's twin on the wire.

Needs a C compiler on PATH; skips otherwise (the precedent of
test_firmware_mem.py and test_firmware_json.py).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.boards import find_board, firmware_paths
from termapy.firmware import TARGETS, find_target, source_path

# The shipped modules, from the package: what ``/firmware.copy`` writes.
FIRMWARE = source_path(find_target("monitor", targets=TARGETS), "termapy_monitor.c").parent
MODULE = FIRMWARE / "termapy_monitor.c"
HARNESS = Path(__file__).parent / "fixtures" / "firmware" / "monitor_host.c"
STRICT = ["-std=c99", "-pedantic", "-Wall", "-Wextra", "-Werror"]

# One compiler at a time on one worker (test_firmware_json.py says why).
pytestmark = pytest.mark.xdist_group("c_toolchain")
COMPILE_TIMEOUT_S = 120
RUN_TIMEOUT_S = 30


def _compiler() -> str:
    found = next((path for path in map(shutil.which, ("cc", "gcc", "clang")) if path), None)
    if found is None:
        pytest.skip("no C compiler on PATH")
    return found


@pytest.fixture(scope="module")
def monitor_host(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The harness, built after a strict C99 pass over the module alone."""
    compiler = _compiler()
    strict = subprocess.run(
        [compiler, *STRICT, "-fsyntax-only", "-I", str(FIRMWARE), str(MODULE)],
        capture_output=True,
        text=True,
        check=False,
        timeout=COMPILE_TIMEOUT_S,
    )
    assert strict.returncode == 0, strict.stderr
    exe = tmp_path_factory.mktemp("monitor_host") / (
        "monitor_host.exe" if sys.platform == "win32" else "monitor_host"
    )
    link = subprocess.run(
        [
            compiler,
            "-std=gnu99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(FIRMWARE),
            str(MODULE),
            str(FIRMWARE / "termapy_json.c"),
            str(HARNESS),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=COMPILE_TIMEOUT_S,
    )
    assert link.returncode == 0, link.stderr
    return exe


def _run(exe: Path, directives: list[str]) -> list[str]:
    """One output chunk per directive, exact bytes, line endings kept."""
    result = subprocess.run(
        [str(exe)],
        input="\n".join(directives).encode("ascii") + b"\n",
        capture_output=True,
        check=True,
        timeout=RUN_TIMEOUT_S,
    )
    chunks = result.stdout.decode("ascii").split("--\n")
    assert len(chunks) == len(directives) + 1, "one chunk per directive, then a trailing empty"
    return chunks[:-1]


def _micropython_monitor():
    """The MicroPython starters' monitor, imported from the package as a board imports it."""
    path = next(
        path for path in firmware_paths(find_board("pico")) if path.name == "termapy_json.py"
    )
    spec = importlib.util.spec_from_file_location("board_termapy_json", path)
    assert spec is not None and spec.loader is not None, "the board's module loads"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _micropython_answers(lines: list[str]) -> list[str]:
    """The same device as the harness, its table in MicroPython, fed each line byte by byte."""
    board = _micropython_monitor()
    state = {"led": False}

    def led(args):
        if len(args) > 1:
            raise board.CommandError("usage: led {on|off|1|0}")
        if args:
            state["led"] = board.on_off_arg(args[0], "state")
        return "on" if state["led"] else "off"

    def pair(args):
        if len(args) != 2:
            raise board.CommandError("usage: pair <a> <b>")
        return list(args)

    commands = {
        "led": ("the LED: led on|off|1|0 sets it, bare led reads it", led),
        "level": (
            "a level from 0 to 255, decimal or 0x hex",
            lambda args: board.int_arg(args[0], "level", 0, 255),
        ),
        "pair": ("two words as an array", pair),
        "rgb": ("three levels as an array", lambda args: [255, 0, 32]),
        "temp": ("a temperature in degrees C", lambda args: 21.5),
    }
    out: list[str] = []
    monitor = board.Monitor(commands, out.append)
    answers = []
    for line in lines:
        start = len(out)
        wire = "x" * int(line[6:]) if line.startswith("#long ") else line
        for char in wire + "\n":
            monitor.feed(char)
        answers.append("".join(out[start:]))
    return answers


# The lines both monitors take alike: everything but the listing (the
# MicroPython one also answers xmodem) and a native command's JSON form.
SHARED_LINES = [
    "led",
    "led on",
    "led maybe",
    "led on off",
    "level 0x1F",
    "level 256",
    "level -1",
    "level 12abc",
    "pair a b",
    "rgb",
    "temp",
    "nosuch",
    "help led",
    "help nosuch",
    "help led extra",
    "json",
    "json on",
    "led",
    "rgb",
    '{"cmd":"led","args":"off","id":7}',
    "led",
    '{"method":"pair","params":["x","y"],"id":8}',
    '{"cmd":"nosuch","id":9}',
    '{"cmd":',
    '{"jsonrpc":"2.0","method":"temp","id":10}',
    "help led",
    "nosuch",
    "#long 513",
    "json off",
    "json bogus",
    "led",
]


class TestWire:
    @pytest.mark.rule("monitor-c-wire")
    def test_the_c_and_micropython_monitors_answer_alike(self, monitor_host):
        """Every shared line gets the same bytes from the C monitor and the MicroPython one."""
        # Act
        actual = _run(monitor_host, SHARED_LINES)
        expected = _micropython_answers(SHARED_LINES)

        # Assert
        differ = [
            (line, c, python)
            for line, c, python in zip(SHARED_LINES, actual, expected, strict=True)
            if c != python
        ]
        assert differ == [], "line, C, MicroPython"

    @pytest.mark.rule("monitor-c-wire")
    def test_a_request_s_id_is_echoed_once(self, monitor_host):
        """A JSON request's id comes back on its reply and on no reply after it, twice over."""
        actual = _run(
            monitor_host,
            ['{"cmd":"led","args":"on","id":7}', "led", '{"cmd":"rgb","id":8}', "rgb"],
        )
        assert actual == [
            '{"result":"on","id":7}\n',
            '{"result":"on"}\n',
            '{"result":[255,0,32],"id":8}\n',
            '{"result":[255,0,32]}\n',
        ], "a plain line after a request carries no id; the framing stays JSON"

    def test_text_replies_end_crlf_and_json_replies_lf(self, monitor_host):
        """Text framing ends each line CRLF, an array one item per line; JSON is one LF line."""
        actual = _run(monitor_host, ["temp", "pair a b", "json on", "temp", "pair a b"])
        assert actual == [
            "21.5\r\n",
            "a\r\nb\r\n",
            '{"result":"on"}\n',
            '{"result":21.5}\n',
            '{"result":["a","b"]}\n',
        ]


class TestHelp:
    @pytest.mark.rule("monitor-c-wire")
    def test_help_lists_every_command_sorted_and_aligned(self, monitor_host):
        """Bare help in text is one aligned line per command, the monitor's own among them, in name order."""
        actual = _run(monitor_host, ["help"])[0]
        expected = (
            "MEM.R  read memory: MEM.R <addr> <len>, the address in hex\r\n"
            "help   list the commands; help <command> for one\r\n"
            "json   the reply framing: on for JSON-RPC, off for text\r\n"
            "led    the LED: led on|off|1|0 sets it, bare led reads it\r\n"
            "level  a level from 0 to 255, decimal or 0x hex\r\n"
            "pair   two words as an array\r\n"
            "rgb    three levels as an array\r\n"
            "temp   a temperature in degrees C\r\n"
        )
        assert actual == expected

    def test_help_in_json_is_an_object_of_name_to_help(self, monitor_host):
        """In JSON, help is one object, its keys in name order."""
        actual = _run(monitor_host, ["json on", "help"])[1]
        assert actual.startswith('{"result":{"MEM.R":"read memory'), "uppercase sorts first"
        assert '"help":"list the commands; help <command> for one","json":' in actual
        assert actual.endswith('"temp":"a temperature in degrees C"}}\n'), "one line, then LF"


class TestNative:
    def test_a_plain_line_gets_the_text_whatever_the_framing(self, monitor_host):
        """A native command answers a plain line in its own text, in text and in JSON framing alike, twice."""
        text = "MEM.R 0 2\r\n00000000: 01 02\r\nOK\r\n"
        actual = _run(monitor_host, ["MEM.R 0 2", "json on", "MEM.R 0 2", "MEM.R 0 2", "led"])
        assert actual[0] == text, "the handler gets the whole line, its name included"
        assert actual[2:4] == [text, text], "JSON framing does not wrap a plain line"
        assert actual[4] == '{"result":"off"}\n', "and the framing is JSON again after it"

    def test_a_json_request_gets_the_text_as_its_result(self, monitor_host):
        """A JSON request for a native command gets the text as one string, LF line breaks, with its id."""
        actual = _run(monitor_host, ['{"cmd":"MEM.R","args":"0 2","id":3}'])[0]
        assert actual == '{"result":"MEM.R 0 2\\n00000000: 01 02\\nOK","id":3}\n'


class TestLines:
    def test_crlf_and_blank_lines_answer_once(self, monitor_host):
        """CR LF ends one line, not two; a blank or all-space line answers nothing."""
        actual = _run(monitor_host, ["#cr led", "", "   ", "#cr temp"])
        assert actual == ["off\r\n", "", "", "21.5\r\n"]

    def test_two_lines_in_one_batch_are_both_answered(self, monitor_host):
        """Lines arriving together are answered in order, none dropped."""
        actual = _run(monitor_host, ["#batch led on|temp|led"])[0]
        assert actual == "on\r\n21.5\r\non\r\n"

    def test_a_line_split_across_reads_is_one_line(self, monitor_host):
        """Bytes without a line ending wait for the rest of the line."""
        actual = _run(monitor_host, ["#raw le", "d on"])
        assert actual == ["", "on\r\n"], "nothing until the line ends"

    def test_a_line_too_long_is_refused_and_the_next_answered(self, monitor_host):
        """A line past 512 bytes is one refusal however long it runs, and the monitor carries on, twice."""
        actual = _run(monitor_host, ["#long 512", "#long 100000", "led", "#long 600", "temp"])
        assert actual[0].startswith("ERR unknown command: xxx"), "512 bytes is a line"
        assert len(actual[0]) == len("ERR ") + 127 + 2, "the message is cut at 127 characters"
        assert actual[1:] == [
            "ERR line too long\r\n",
            "off\r\n",
            "ERR line too long\r\n",
            "21.5\r\n",
        ]

    def test_answer_trims_the_line(self, monitor_host):
        """A line handed to Answer whole is trimmed as a fed one is."""
        actual = _run(monitor_host, ["#answer   led on  "])
        assert actual == ["on\r\n"]


class TestArguments:
    def test_a_number_is_decimal_or_hex_and_bounded(self, monitor_host):
        """IntArg takes decimal and 0x hex in range, and refuses an overflow and an empty word."""
        actual = _run(
            monitor_host, ["level 255", "level 0xff", "level 99999999999999999999", "level"]
        )
        assert actual == [
            "255\r\n",
            "255\r\n",
            "ERR invalid level: 99999999999999999999 (use 0 to 255)\r\n",
            "ERR invalid level:  (use 0 to 255)\r\n",
        ]


class TestInit:
    def test_a_table_reusing_a_monitor_name_is_refused_whole(self, monitor_host):
        """A table that names help is not installed: only the monitor's own commands answer."""
        actual = _run(monitor_host, ["#reserved", "led", "help"])
        assert actual[0] == "refused\n"
        assert actual[1] == "ERR unknown command: led\r\n", "no row of the refused table runs"
        assert actual[2] == (
            "help  list the commands; help <command> for one\r\n"
            "json  the reply framing: on for JSON-RPC, off for text\r\n"
        )
