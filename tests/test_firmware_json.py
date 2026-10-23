"""The drop-in JSON wrapper, ``examples/firmware/termapy_json.c``, on the host.

A fake platform (stdout as the serial line) is installed with
``TermapyJson_Init``; each stdin line is a directive (mode / reply / extract /
line / ...) and each directive's output is delimited by a ``--`` line
(``tests/fixtures/firmware/json_host.c``).  This is the one place the device
side of the JSON envelope runs end to end against the real C -- the envelope
is asserted byte for byte against termapy's NDJSON contract
(src/termapy/demo_ndjson.py).

Needs a C compiler on PATH; skips otherwise (the CRC-codegen precedent,
shared with test_firmware_mem.py).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIRMWARE = Path(__file__).parent.parent / "examples" / "firmware"
MODULE = FIRMWARE / "termapy_json.c"
HARNESS = Path(__file__).parent / "fixtures" / "firmware" / "json_host.c"
# The module alone must be strict C99; the harness needs POSIX/Windows I/O.
STRICT = ["-std=c99", "-pedantic", "-Wall", "-Wextra", "-Werror"]


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
    exe = folder / ("json_host.exe" if sys.platform == "win32" else "json_host")
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
def json_host(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(_compiler(), tmp_path_factory.mktemp("json_host"))


def _run(exe: Path, directives: list[str]) -> list[str]:
    """One output chunk per directive (exact bytes, EOLs kept), split on ``--``."""
    result = subprocess.run(
        [str(exe)],
        input="\n".join(directives).encode("ascii") + b"\n",
        capture_output=True,
        check=True,
    )
    chunks = result.stdout.decode("ascii").split("--\n")
    assert len(chunks) == len(directives) + 1, "one chunk per directive, then a trailing empty"
    return chunks


def _joined(exe: Path, directives: list[str], start: int) -> str:
    """The output of directives[start:] concatenated: one reply built by several calls."""
    return "".join(_run(exe, directives)[start:-1])


# ── silence before Init ──────────────────────────────────────────────────────


class TestInit:
    def test_reply_before_init_is_silent(self, json_host):
        """pre-Init: there is no output path, so a reply must emit nothing."""
        # Act -- reply BEFORE the init directive
        out = _run(json_host, ["reply nope", "init"])

        # Assert
        assert out[0] == "", "a reply before Init prints nothing"

    def test_builder_and_passthrough_before_init_are_silent(self, json_host):
        """Every writer, not just Reply, is silent without a platform."""
        # Act
        out = _run(
            json_host, ["mode json", "abegin", "iint 1", "aend", "pass x", "passend", "init"]
        )

        # Assert
        assert "".join(out[:-2]) == "", "nothing reaches a NULL put"


# ── text mode: the puts replacement, unchanged behaviour ──────────────────────


class TestTextMode:
    def test_ver_is_bare_plus_crlf(self, json_host):
        """ver in text mode is the bare string and CRLF, exactly like puts."""
        # Act
        out = _run(json_host, ["init", "mode text", "reply 1.23"])

        # Assert
        assert out[2] == "1.23\r\n", "bare value, CRLF line ending"

    def test_error_is_bare_message(self, json_host):
        """A text-mode error is the bare message -- the integrator adds ERR if wanted."""
        # Act
        out = _run(json_host, ["init", "mode text", "error -1 nope"])

        # Assert
        assert out[2] == "nope\r\n", "no ERR prefix, no envelope"

    def test_raw_and_string_are_identical_in_text(self, json_host):
        """raw vs string differ only in JSON mode; text mode emits the value bare."""
        # Act
        out = _run(json_host, ["init", "mode text", "reply 42", "replyraw 42"])

        # Assert
        assert out[2] == "42\r\n" and out[3] == "42\r\n", "same bare output either way"

    def test_typed_values_print_bare(self, json_host):
        """int, uint and float replies are the plain number and CRLF in text mode."""
        # Act
        out = _run(
            json_host,
            ["init", "mode text", "replyint -7", "replyuint 4294967295", "replyfloat 3.14159 2"],
        )

        # Assert
        assert out[2] == "-7\r\n", "signed"
        assert out[3] == "4294967295\r\n", "unsigned above LONG_MAX on 32-bit"
        assert out[4] == "3.14\r\n", "the injected formatter's output, bare"


# ── JSON mode: the NDJSON envelope, byte for byte ─────────────────────────────


class TestJsonMode:
    def test_success_with_id(self, json_host):
        """A string result with an id: result quoted, id last; compact, LF."""
        # Act
        out = _run(json_host, ["init", "mode json", "setid 7", "reply 1.23"])

        # Assert
        assert out[3] == '{"result":"1.23","id":7}\n', "matches demo_ndjson _ok"

    def test_success_without_id_omits_id(self, json_host):
        """No id: the id field is OMITTED entirely (not 0, not null) -- the parity case."""
        # Act
        out = _run(json_host, ["init", "mode json", "clearid", "reply 1.23"])

        # Assert
        assert out[3] == '{"result":"1.23"}\n', "id omitted when the request had none"

    def test_raw_value_is_unquoted(self, json_host):
        """A raw result is spliced verbatim: a number is a JSON number, not a string."""
        # Act
        out = _run(json_host, ["init", "mode json", "clearid", "replyraw 42"])

        # Assert
        assert out[3] == '{"result":42}\n', "raw value unquoted"

    def test_error_envelope(self, json_host):
        """An error: the JSON-RPC error object (code, then message), id last."""
        # Act
        out = _run(json_host, ["init", "mode json", "setid 3", "error -11 unknown mode"])

        # Assert
        assert out[3] == '{"error":{"code":-11,"message":"unknown mode"},"id":3}\n'

    def test_string_escaping(self, json_host):
        """A quote and a tab in a result are JSON-escaped inside the string."""
        # Act -- the harness passes the arg literally; a literal tab is hard to
        # send, so assert the quote escape, which the parser preserves.
        out = _run(json_host, ["init", "mode json", "clearid", 'reply he said "hi"'])

        # Assert
        assert out[3] == '{"result":"he said \\"hi\\""}\n', "inner quotes escaped"


# ── typed scalar replies ──────────────────────────────────────────────────────


class TestTypedReplies:
    def test_int_is_a_json_number(self, json_host):
        """ReplyInt writes a bare number, negative sign included."""
        # Act
        out = _run(json_host, ["init", "mode json", "clearid", "replyint -7", "replyint 0"])

        # Assert
        assert out[3] == '{"result":-7}\n', "signed number, not a string"
        assert out[4] == '{"result":0}\n', "zero prints as 0"

    def test_uint_covers_the_full_32_bit_range(self, json_host):
        """ReplyUInt is for ticks and counters that exceed LONG_MAX on a 32-bit long."""
        # Act
        out = _run(json_host, ["init", "mode json", "setid 1", "replyuint 4294967295"])

        # Assert
        assert out[3] == '{"result":4294967295,"id":1}\n'

    def test_float_uses_the_injected_formatter(self, json_host):
        """ReplyFloat hands value and decimals to the platform's formatter."""
        # Act
        out = _run(
            json_host, ["init", "mode json", "clearid", "replyfloat 3.14159 2", "replyfloat -0.5 1"]
        )

        # Assert
        assert out[3] == '{"result":3.14}\n', "two decimals as asked"
        assert out[4] == '{"result":-0.5}\n', "negative float"

    def test_float_without_a_formatter_is_null(self, json_host):
        """A platform with fmtFloat NULL links no float code and answers null / empty."""
        # Act
        out = _run(
            json_host,
            [
                "initnofloat",
                "mode json",
                "clearid",
                "replyfloat 1.5 1",
                "mode text",
                "replyfloat 1.5 1",
            ],
        )

        # Assert
        assert out[3] == '{"result":null}\n', "JSON has null for 'no value'"
        assert out[5] == "\r\n", "text mode: an empty value line"

    def test_non_finite_float_is_null(self, json_host):
        """nan / inf have no JSON spelling: the module replaces them with null."""
        # Act -- strtod parses "nan" and "inf" on the harness side
        out = _run(
            json_host, ["init", "mode json", "clearid", "replyfloat nan 2", "replyfloat inf 2"]
        )

        # Assert
        assert out[3] == '{"result":null}\n', "nan -> null"
        assert out[4] == '{"result":null}\n', "inf -> null"


# ── the streaming builder ─────────────────────────────────────────────────────


class TestBuilder:
    def test_array_of_mixed_items(self, json_host):
        """ArrayBegin..ArrayEnd is one envelope; items are comma-separated and typed."""
        # Act
        actual = _joined(
            json_host,
            [
                "init",
                "mode json",
                "clearid",
                "abegin",
                "iint 1",
                "istr two",
                "ifloat 3.5 1",
                "iraw true",
                "aend",
            ],
            3,
        )

        # Assert
        assert actual == '{"result":[1,"two",3.5,true]}\n'

    def test_object_of_fields_with_id(self, json_host):
        """ObjectBegin..ObjectEnd: "key":value pairs, id last on the envelope."""
        # Act
        actual = _joined(
            json_host,
            [
                "init",
                "mode json",
                "setid 9",
                "obegin",
                "fstr name stack",
                "fuint used 812",
                "fuint max 4096",
                "oend",
            ],
            3,
        )

        # Assert
        assert actual == '{"result":{"name":"stack","used":812,"max":4096},"id":9}\n'

    def test_nested_array_of_objects_is_a_table(self, json_host):
        """The hwm shape: an array of row objects, commas placed at every level."""
        # Act
        actual = _joined(
            json_host,
            [
                "init",
                "mode json",
                "clearid",
                "abegin",
                "obegin",
                "fstr name a",
                "fint v 1",
                "oend",
                "obegin",
                "fstr name b",
                "fint v 2",
                "oend",
                "aend",
            ],
            3,
        )

        # Assert
        assert actual == '{"result":[{"name":"a","v":1},{"name":"b","v":2}]}\n'

    def test_keyed_containers_inside_an_object(self, json_host):
        """FieldArrayBegin / FieldObjectBegin open a named child, like MEM.INFO's regions."""
        # Act
        actual = _joined(
            json_host,
            [
                "init",
                "mode json",
                "clearid",
                "obegin",
                "fint n 2",
                "fabegin regions",
                "istr sram",
                "istr flash",
                "aend",
                "fobegin caps",
                "fraw modify true",
                "oend",
                "oend",
            ],
            3,
        )

        # Assert
        assert actual == '{"result":{"n":2,"regions":["sram","flash"],"caps":{"modify":true}}}\n'

    def test_empty_containers(self, json_host):
        """No items: [] and {} with no stray comma."""
        # Act
        actual = _joined(
            json_host, ["init", "mode json", "clearid", "abegin", "aend", "obegin", "oend"], 3
        )

        # Assert
        assert actual == '{"result":[]}\n{"result":{}}\n'

    def test_text_mode_prints_one_line_per_value(self, json_host):
        """In text mode containers print nothing; items are 'value', fields 'key value'."""
        # Act
        actual = _joined(
            json_host,
            [
                "init",
                "mode text",
                "abegin",
                "iint 1",
                "istr two",
                "aend",
                "obegin",
                "fuint used 812",
                "oend",
            ],
            2,
        )

        # Assert
        assert actual == "1\r\ntwo\r\nused 812\r\n", "a plain fallback layout"

    def test_item_outside_a_container_is_a_scalar_reply(self, json_host):
        """ItemInt at depth 0 is ReplyInt; a Field at depth 0 drops its key."""
        # Act
        out = _run(json_host, ["init", "mode json", "clearid", "iint 5", "fstr k v"])

        # Assert
        assert out[3] == '{"result":5}\n'
        assert out[4] == '{"result":"v"}\n', "no key where there is no object"

    def test_extra_end_is_ignored(self, json_host):
        """An unbalanced End emits nothing and does not corrupt the next reply."""
        # Act
        out = _run(json_host, ["init", "mode json", "clearid", "aend", "reply ok"])

        # Assert
        assert out[3] == "", "nothing to close"
        assert out[4] == '{"result":"ok"}\n'


# ── the passthrough for unconverted commands ──────────────────────────────────


class TestPassthrough:
    def test_text_mode_forwards_bytes_unchanged(self, json_host):
        """In text mode the passthrough is the plain putc: nothing added."""
        # Act
        actual = _joined(json_host, ["init", "mode text", "pass hello", "passend"], 2)

        # Assert
        assert actual == "hello\n", "verbatim, newline included, no envelope"

    def test_json_mode_wraps_printed_text_as_a_string(self, json_host):
        """The first byte opens the envelope, End closes it; one trailing newline is dropped."""
        # Act
        actual = _joined(json_host, ["init", "mode json", "setid 4", "pass SB_0_8", "passend"], 3)

        # Assert
        assert actual == '{"result":"SB_0_8","id":4}\n', "a puts-style value\\n becomes the value"

    def test_inner_newlines_are_kept_escaped(self, json_host):
        """Multi-line output is one string with \\n inside; only the last newline goes."""
        # Act
        actual = _joined(
            json_host,
            ["init", "mode json", "clearid", "pass line one", "pass line two", "passend"],
            3,
        )

        # Assert
        assert actual == '{"result":"line one\\nline two"}\n'

    def test_quotes_and_controls_are_escaped(self, json_host):
        """Handler text with quotes stays valid JSON."""
        # Act
        actual = _joined(
            json_host, ["init", "mode json", "clearid", 'passraw say "hi"', "passend"], 3
        )

        # Assert
        assert actual == '{"result":"say \\"hi\\""}\n'

    def test_end_without_output_is_a_no_op(self, json_host):
        """A converted handler prints nothing through the passthrough, so End emits nothing."""
        # Act
        out = _run(json_host, ["init", "mode json", "clearid", "reply done", "passend"])

        # Assert
        assert out[3] == '{"result":"done"}\n'
        assert out[4] == "", "nothing was open"


# ── the input extractor ───────────────────────────────────────────────────────


class TestExtract:
    def test_plain_line_is_text(self, json_host):
        """A line that is not a JSON object is left alone for the dispatcher."""
        # Act
        out = _run(json_host, ["init", "extract ver 1 2"])

        # Assert
        assert out[1] == "TEXT\n", "not JSON: dispatch as typed"

    def test_plain_line_clears_a_stale_id(self, json_host):
        """A text line carries no id, so the reply to it must not echo the previous one."""
        # Act
        out = _run(
            json_host,
            ["init", "mode json", 'extract {"cmd":"ver","id":7}', "extract ver", "reply 1.23"],
        )

        # Assert
        assert out[3] == "TEXT\n"
        assert out[4] == '{"result":"1.23"}\n', "no id after a text line"

    def test_json_command_extracts_the_name(self, json_host):
        """{\"cmd\":\"ver\"} yields the bare command name."""
        # Act
        out = _run(json_host, ["init", 'extract {"cmd":"ver"}'])

        # Assert
        assert out[1] == "JSON|ver\n", "the cmd name is extracted"

    def test_args_append_after_a_space(self, json_host):
        """A string args field becomes a space-separated token for the dispatcher."""
        # Act
        out = _run(json_host, ["init", 'extract {"cmd":"echo","args":"hi there"}'])

        # Assert
        assert out[1] == "JSON|echo hi there\n", "cmd + args, space-joined"

    def test_json_with_spaces(self, json_host):
        """Whitespace around the colons and commas is tolerated."""
        # Act
        out = _run(json_host, ["init", 'extract { "cmd" : "set_x" , "args" : "5" }'])

        # Assert
        assert out[1] == "JSON|set_x 5\n", "spaced JSON still parses"

    def test_missing_cmd_is_bad(self, json_host):
        """A JSON object with no cmd is malformed for our purposes."""
        # Act
        out = _run(json_host, ["init", 'extract {"nope":"x"}'])

        # Assert
        assert out[1] == "BAD\n", "no cmd key -> BAD"

    def test_unterminated_string_is_bad_not_a_crash(self, json_host):
        """An unterminated cmd string is BAD, and the program does not crash."""
        # Act
        out = _run(json_host, ["init", 'extract {"cmd":"ver'])

        # Assert
        assert out[1] == "BAD\n", "unterminated string -> BAD"

    def test_over_long_args_is_bad_not_silently_dropped(self, json_host):
        """args longer than TERMAPY_JSON_ARGS_MAX is rejected, never run without its args."""
        # Act
        long_args = "x" * 70
        out = _run(json_host, ["init", f'extract {{"cmd":"echo","args":"{long_args}"}}'])

        # Assert
        assert out[1] == "BAD\n", "the request is refused, not truncated"

    def test_non_string_args_is_bad(self, json_host):
        """args must be a string; a number there is a malformed request."""
        # Act
        out = _run(json_host, ["init", 'extract {"cmd":"echo","args":5}'])

        # Assert
        assert out[1] == "BAD\n"

    def test_extract_flips_output_to_json(self, json_host):
        """Seeing a JSON command line sets JSON output mode for the following reply."""
        # Act -- start in text, extract a JSON line, then reply
        out = _run(
            json_host,
            ["init", "mode text", 'extract {"cmd":"ver","id":9}', "reply 1.23"],
        )

        # Assert
        assert out[2] == "JSON|ver\n", "extracted"
        assert out[3] == '{"result":"1.23","id":9}\n', "extract flipped to JSON and captured id 9"

    def test_bad_json_still_flips_to_json(self, json_host):
        """A '{' line that is malformed still switches to JSON, so the error is an envelope."""
        # Act
        out = _run(
            json_host, ["init", "mode text", 'extract {"nope":"x"}', "error -32700 malformed json"]
        )

        # Assert
        assert out[2] == "BAD\n", "malformed"
        assert out[3] == '{"error":{"code":-32700,"message":"malformed json"}}\n', (
            "the error after a bad JSON line is itself JSON"
        )


# ── the mode command and the full OnLine flow ─────────────────────────────────


class TestModeCmdAndLine:
    def test_modecmd_flips_the_boolean(self, json_host):
        """The mode command toggles text <-> json; a reply after it changes shape."""
        # Act -- start text, flip to json, reply; flip back, reply
        out = _run(
            json_host,
            ["init", "mode text", "clearid", "modecmd", "reply A", "modecmd", "reply B"],
        )

        # Assert
        assert out[4] == '{"result":"A"}\n', "flipped to JSON"
        assert out[6] == "B\r\n", "second reply is the text form of B"

    def test_line_runs_the_full_flow(self, json_host):
        """line {\"cmd\":\"echo\",\"args\":\"hi\"} extracts, dispatches, and replies in JSON."""
        # Act
        out = _run(json_host, ["init", 'line {"cmd":"echo","args":"hi","id":2}'])

        # Assert
        assert out[1] == '{"result":"hi","id":2}\n', "end to end: extract + dispatch + reply"

    def test_line_plain_text_dispatches_as_text(self, json_host):
        """A plain ver line (no JSON) dispatches and replies in the current (text) mode."""
        # Act
        out = _run(json_host, ["init", "mode text", "line ver"])

        # Assert
        assert out[2] == "1.23\r\n", "plain line, text reply"


# ── the mirrored JSON-RPC tag ─────────────────────────────────────────────────


class TestJsonRpcTag:
    def test_tagged_request_gets_a_tagged_reply_with_its_id(self, json_host):
        """A request carrying "jsonrpc" is answered as a full JSON-RPC response object."""
        # Act
        out = _run(json_host, ["init", 'line {"jsonrpc":"2.0","method":"ver","id":7}'])

        # Assert
        assert out[1] == '{"jsonrpc":"2.0","result":"1.23","id":7}\n', "tag first, id last"

    def test_tagged_request_without_id_answers_id_null(self, json_host):
        """Tagged and id-less: the id member is present and null, as the spec shapes it."""
        # Act
        out = _run(json_host, ["init", 'line {"jsonrpc":"2.0","method":"ver"}'])

        # Assert
        assert out[1] == '{"jsonrpc":"2.0","result":"1.23","id":null}\n'

    def test_untagged_request_without_id_omits_id(self, json_host):
        """Untagged and id-less stays short: no tag, no id member at all."""
        # Act
        out = _run(json_host, ["init", 'line {"cmd":"ver"}'])

        # Assert
        assert out[1] == '{"result":"1.23"}\n'

    def test_tagged_error_and_malformed_request(self, json_host):
        """The tag and id rules hold for an error object and for a malformed request."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                'line {"jsonrpc":"2.0","method":"nope","id":5}',
                'line {"jsonrpc":"2.0","method":5}',
                'line {"cmd":5,"id":3}',
            ],
        )

        # Assert
        assert (
            out[1] == '{"jsonrpc":"2.0","error":{"code":-2,"message":"unknown command"},"id":5}\n'
        )
        assert (
            out[2]
            == '{"jsonrpc":"2.0","error":{"code":-32700,"message":"malformed json"},"id":null}\n'
        )
        assert out[3] == '{"error":{"code":-32700,"message":"malformed json"},"id":3}\n', (
            "the id is captured before the request is judged, so a BAD request keeps it"
        )

    def test_tag_reaches_the_builder_and_the_passthrough(self, json_host):
        """Every head writer mirrors the tag: scalar, container and passthrough."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                'extract {"jsonrpc":"2.0","method":"x","id":2}',
                "pass SB_0_8",
                "passend",
                'extract {"jsonrpc":"2.0","method":"x"}',
                "abegin",
                "iint 1",
                "aend",
                'extract {"jsonrpc":"2.0","method":"x","id":3}',
                'error -11 he said "x"',
            ],
        )

        # Assert
        assert "".join(out[2:4]) == '{"jsonrpc":"2.0","result":"SB_0_8","id":2}\n', "passthrough"
        assert "".join(out[5:8]) == '{"jsonrpc":"2.0","result":[1],"id":null}\n', "builder"
        assert (
            out[9] == '{"jsonrpc":"2.0","error":{"code":-11,"message":"he said \\"x\\""},"id":3}\n'
        )

    def test_tag_is_dropped_with_the_id(self, json_host):
        """A text line, ClearId and Init all drop the tag; SetId alone keeps it."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                'extract {"jsonrpc":"2.0","method":"ver","id":1}',
                "extract ver",
                "reply 1.23",
                'extract {"jsonrpc":"2.0","method":"ver","id":1}',
                "clearid",
                "reply 1.23",
                'extract {"jsonrpc":"2.0","method":"ver","id":1}',
                "init",
                "reply 1.23",
                'extract {"jsonrpc":"2.0","method":"ver"}',
                "setid 9",
                "reply 1.23",
            ],
        )

        # Assert
        assert out[3] == '{"result":"1.23"}\n', "a text line clears tag and id"
        assert out[6] == '{"result":"1.23"}\n', "ClearId clears the tag too"
        assert out[9] == '{"result":"1.23"}\n', "Init drops a pending tag and id, keeps JSON mode"
        assert out[12] == '{"jsonrpc":"2.0","result":"1.23","id":9}\n', "SetId leaves the tag alone"

    def test_tag_means_nothing_in_text_mode(self, json_host):
        """Text mode output is untouched by the tag."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                'extract {"jsonrpc":"2.0","method":"ver","id":1}',
                "mode text",
                "reply 1.23",
                "error -1 nope",
            ],
        )

        # Assert
        assert out[3] == "1.23\r\n"
        assert out[4] == "nope\r\n"


# ── the JSON-RPC request spelling: method and params ──────────────────────────


class TestRequestKeys:
    def test_method_is_an_alias_of_cmd_and_cmd_wins(self, json_host):
        """{"method":...} extracts like {"cmd":...}; with both present, cmd is taken."""
        # Act
        out = _run(
            json_host, ["init", 'extract {"method":"ver"}', 'extract {"method":"a","cmd":"b"}']
        )

        # Assert
        assert out[1] == "JSON|ver\n"
        assert out[2] == "JSON|b\n"

    def test_params_string_and_array_join_with_spaces(self, json_host):
        """params as a string, or a flat array of strings and bare scalars, becomes one arg line."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                'extract {"method":"echo","params":"hi there"}',
                'extract {"method":"echo","params":["5ms","2s"]}',
                'extract {"method":"set","params":[1, true, -2.5, null, "x y"]}',
                'extract {"method":"ver","params":[]}',
                'extract {"method":"ver","params":[ ]}',
                'line {"jsonrpc":"2.0","method":"echo","params":["5ms","2s"],"id":1}',
            ],
        )

        # Assert
        assert out[1] == "JSON|echo hi there\n"
        assert out[2] == "JSON|echo 5ms 2s\n"
        assert out[3] == "JSON|set 1 true -2.5 null x y\n", "bare scalars copied verbatim"
        assert out[4] == "JSON|ver\n", "an empty array appends nothing"
        assert out[5] == "JSON|ver\n"
        assert out[6] == '{"jsonrpc":"2.0","result":"5ms 2s","id":1}\n', "end to end"

    def test_malformed_params_are_bad(self, json_host):
        """Objects, nested arrays, trailing commas, missing commas and bare non-strings are BAD."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                'extract {"method":"ver","params":{}}',
                'extract {"method":"ver","params":{"a":1}}',
                'extract {"method":"ver","params":[[1]]}',
                'extract {"method":"ver","params":["a",]}',
                'extract {"method":"ver","params":["a"',
                'extract {"method":"ver","params":["a',
                'extract {"method":"ver","params":[1 2]}',
                'extract {"method":"echo","params":5}',
                'extract {"method":"echo","params":null}',
                'extract {"cmd":"echo","args":["A"]}',
            ],
        )

        # Assert
        assert out[1:11] == ["BAD\n"] * 10, (
            "args stays string-only; params rejects anything not flat"
        )

    def test_args_wins_over_params(self, json_host):
        """With both present, args is taken; params is ignored."""
        # Act
        out = _run(json_host, ["init", 'extract {"cmd":"echo","args":"A","params":"B"}'])

        # Assert
        assert out[1] == "JSON|echo A\n"

    def test_params_bounds(self, json_host):
        """Each element must fit TERMAPY_JSON_ARGS_MAX; the whole line must fit the caller's buffer."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                f'extract {{"method":"echo","params":["{"x" * 63}"]}}',
                f'extract {{"method":"echo","params":["{"x" * 64}"]}}',
                f'extract {{"method":"echo","params":["{"a" * 40}","{"b" * 40}"]}}',
                f'extract {{"method":"echo","params":["{"a" * 40}","{"b" * 40}","{"c" * 40}"]}}',
            ],
        )

        # Assert
        assert out[1] == f"JSON|echo {'x' * 63}\n", "63 chars fit the 64-byte element copy"
        assert out[2] == "BAD\n", "64 do not"
        assert out[3] == f"JSON|echo {'a' * 40} {'b' * 40}\n", "86 bytes fit the 96-byte line"
        assert out[4] == "BAD\n", "a third element overflows the line"

    def test_a_value_equal_to_a_key_name_is_not_a_key(self, json_host):
        """The colon check lets {"cmd":"params"} run and keeps "jsonrpc" in args from tagging."""
        # Act
        out = _run(
            json_host,
            [
                "init",
                'extract {"cmd":"params"}',
                'extract {"cmd":"id","id":5}',
                "reply x",
                'extract {"cmd":"echo","args":"jsonrpc"}',
                "reply x",
            ],
        )

        # Assert
        assert out[1] == "JSON|params\n"
        assert out[2] == "JSON|id\n"
        assert out[3] == '{"result":"x","id":5}\n'
        assert out[4] == "JSON|echo jsonrpc\n"
        assert out[5] == '{"result":"x"}\n', "not tagged"


# ── compile-time knobs ────────────────────────────────────────────────────────


class TestKnob:
    def test_a_bad_id_digit_knob_refuses_to_compile(self, tmp_path):
        """TERMAPY_JSON_ID_MAX_DIGITS out of range triggers the #error, naming the knob."""
        # Act -- strict pass on the module alone with a bad knob
        result = _strict(_compiler(), "-DTERMAPY_JSON_ID_MAX_DIGITS=0")

        # Assert
        assert result.returncode != 0, "a zero digit bound must not compile"
        assert "TERMAPY_JSON_ID_MAX_DIGITS" in result.stderr, "the #error names the knob"

    def test_extract_can_be_compiled_out(self):
        """TERMAPY_JSON_HAS_EXTRACT=0 builds the module alone cleanly under the strict flags."""
        # Act
        result = _strict(_compiler(), "-DTERMAPY_JSON_HAS_EXTRACT=0")

        # Assert
        assert result.returncode == 0, result.stderr

    def test_a_tiny_args_knob_refuses_to_compile(self):
        """TERMAPY_JSON_ARGS_MAX below 2 cannot hold a NUL and a char."""
        # Act
        result = _strict(_compiler(), "-DTERMAPY_JSON_ARGS_MAX=1")

        # Assert
        assert result.returncode != 0
        assert "TERMAPY_JSON_ARGS_MAX" in result.stderr
