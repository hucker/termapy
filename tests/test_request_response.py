"""Tests for ``termapy.request_response.request_response``.

Pure plumbing helper — drain -> send -> wait -> collect.  Tests use
fake callables so we don't need real serial I/O, and send through the
real line sender (``SerialHandle.send``) over a fake writer.  The MCP
profile executor depends on this helper behaving correctly.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from termapy.plugins.handles.serial import SerialHandle
from termapy.request_response import id_field, json_request, pick_reply, request_response

# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeWriter:
    """Records bytes handed to it; raises on demand."""

    def __init__(self, raise_on_write: Exception | None = None):
        self.writes: list[bytes] = []
        self._raise = raise_on_write

    def __call__(self, payload: bytes) -> None:
        if self._raise is not None:
            raise self._raise
        self.writes.append(payload)


def _sender(writer: _FakeWriter, encoding: str, eol: str) -> Callable[[str], None]:
    """The real line sender, ``ctx.serial.send``, writing to ``writer``."""
    return SerialHandle(write=writer, _cfg=lambda: {"eol": eol, "encoding": encoding}).send


def _fake_drain_factory(stale: list[str]) -> Callable[[], list[str]]:
    """Return a drain callable that yields ``stale`` then ``[]`` thereafter."""
    state = {"first": True}

    def drain() -> list[str]:
        if state["first"]:
            state["first"] = False
            return list(stale)
        return []

    return drain


def _fake_wait_factory(lines: list[str]) -> Callable[..., list[str]]:
    """Return a wait_for_lines callable that returns ``lines`` regardless of args."""

    def wait(*, timeout: float, terminator: str = "", idle_gap: float = 0.05) -> list[str]:
        return list(lines)

    return wait


# ── Tests ────────────────────────────────────────────────────────────────────


def test_unencodable_command_is_a_send_error():
    """A character outside the configured encoding comes back as error=, not as an exception on the caller's thread."""
    # Arrange
    writer = _FakeWriter()

    # Act
    rr = request_response(
        send=_sender(writer, "ascii", "\r"),
        drain_recent_lines=_fake_drain_factory([]),
        wait_for_lines=_fake_wait_factory([]),
        command="café",
        timeout_s=1.0,
    )

    # Assert
    assert rr["error"].startswith("Send error:"), "reported, not raised"
    assert writer.writes == [], "nothing reached the port"


def test_send_and_collect_basic():
    """One round trip sends command + line ending and returns lines, joined text, and timing."""
    # Arrange — fake writer; fake wait returns 2 lines
    writer = _FakeWriter()
    drain = _fake_drain_factory([])
    wait = _fake_wait_factory(["OK", "VOLT=5.5"])

    # Act
    rr = request_response(
        send=_sender(writer, "utf-8", "\r"),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="get_voltage",
        timeout_s=1.0,
    )

    # Assert — write happened with line ending appended; both lines collected
    actual_payload = writer.writes
    expected_payload = [b"get_voltage\r"]
    assert actual_payload == expected_payload, "command + line ending sent"
    actual_lines = rr["lines"]
    expected_lines = ["OK", "VOLT=5.5"]
    assert actual_lines == expected_lines, "wait_for_lines result returned"
    assert rr["text"] == "OK\nVOLT=5.5", "lines joined with newline"
    assert rr["error"] == "", "no send error"
    assert rr["elapsed_s"] >= 0, "elapsed time recorded"


def test_drained_stale_lines_passed_to_callback():
    """Lines left over before the send go to on_drained_line, never into the response."""
    # Arrange — drain yields 3 stale lines
    writer = _FakeWriter()
    drain = _fake_drain_factory(["leftover-1", "leftover-2", "leftover-3"])
    wait = _fake_wait_factory(["fresh"])
    recorded: list[str] = []

    # Act
    rr = request_response(
        send=_sender(writer, "utf-8", "\n"),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        timeout_s=1.0,
        on_drained_line=recorded.append,
    )

    # Assert — every stale line went to the callback; response unaffected
    actual_recorded = recorded
    expected_recorded = ["leftover-1", "leftover-2", "leftover-3"]
    assert actual_recorded == expected_recorded, "stale lines passed to callback"
    assert rr["text"] == "fresh", "fresh response after drain"


def test_drained_stale_lines_silently_discarded_when_no_callback():
    """Without on_drained_line, stale lines are dropped and the call still succeeds."""
    # Arrange — drain has stale lines but no callback supplied
    writer = _FakeWriter()
    drain = _fake_drain_factory(["junk"])
    wait = _fake_wait_factory([])

    # Act
    rr = request_response(
        send=_sender(writer, "utf-8", "\r"),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        timeout_s=1.0,
    )

    # Assert — no callback invoked; helper still functions normally
    assert rr["error"] == "", "no error from missing callback"
    assert rr["lines"] == [], "wait result returned"


def test_send_error_returns_error_field_no_wait():
    """A failed write is reported in the error field and the reply wait is skipped."""
    # Arrange — writer raises OSError; wait should NOT be called
    writer = _FakeWriter(raise_on_write=OSError("port closed"))
    drain = _fake_drain_factory([])
    wait_called = {"count": 0}

    def wait(*, timeout, terminator="", idle_gap=0.05):
        wait_called["count"] += 1
        return []

    # Act
    rr = request_response(
        send=_sender(writer, "utf-8", "\r"),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        timeout_s=1.0,
    )

    # Assert — error field populated; wait_for_lines never called
    assert "Send error" in rr["error"], "error field describes send failure"
    assert "port closed" in rr["error"], "wraps the underlying exception"
    assert wait_called["count"] == 0, "wait_for_lines skipped on send failure"
    assert rr["lines"] == [], "no lines collected on send error"
    assert rr["text"] == "", "no text on send error"


def test_wait_false_skips_collection():
    """wait=False still drains and sends but returns an empty envelope without waiting."""
    # Arrange
    writer = _FakeWriter()
    drain = _fake_drain_factory(["stale"])
    wait_called = {"count": 0}

    def wait(*, timeout, terminator="", idle_gap=0.05):
        wait_called["count"] += 1
        return ["should-not-be-collected"]

    # Act
    rr = request_response(
        send=_sender(writer, "utf-8", "\r"),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="fire-and-forget",
        timeout_s=1.0,
        wait=False,
    )

    # Assert — drain happened; wait skipped; envelope is empty
    assert wait_called["count"] == 0, "wait_for_lines skipped when wait=False"
    assert rr["text"] == "", "no text in fire-and-forget"
    assert rr["lines"] == [], "no lines in fire-and-forget"
    assert rr["error"] == "", "no error in fire-and-forget"
    assert writer.writes == [b"fire-and-forget\r"], "send still happened"


def test_terminator_passed_to_wait_for_lines():
    """The timeout, terminator regex and idle gap reach wait_for_lines unchanged."""
    # Arrange — capture wait kwargs to verify terminator threading
    writer = _FakeWriter()
    drain = _fake_drain_factory([])
    captured: dict = {}

    def wait(*, timeout, terminator="", idle_gap=0.05):
        captured["timeout"] = timeout
        captured["terminator"] = terminator
        captured["idle_gap"] = idle_gap
        return []

    # Act
    request_response(
        send=_sender(writer, "utf-8", "\r"),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        timeout_s=2.5,
        terminator=r"^OK$",
        idle_gap_s=0.2,
    )

    # Assert
    actual = captured
    expected = {"timeout": 2.5, "terminator": r"^OK$", "idle_gap": 0.2}
    assert actual == expected, "terminator/timeout/idle_gap passed verbatim"


def test_encoding_used_for_send():
    """The command is encoded with the caller's encoding, not always as UTF-8."""
    # Arrange
    writer = _FakeWriter()
    drain = _fake_drain_factory([])
    wait = _fake_wait_factory([])

    # Act — latin-1 encoded "café"
    request_response(
        send=_sender(writer, "latin-1", ""),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="café",
        timeout_s=1.0,
    )

    # Assert — latin-1 caf\xe9 not utf-8 caf\xc3\xa9
    actual = writer.writes
    expected = [b"caf\xe9"]
    assert actual == expected, "encoding used to encode command"


@pytest.mark.parametrize("eol", ["\r", "\n", "\r\n", ""])
def test_line_ending_appended(eol):
    """Each line ending, including none, is appended to the command byte for byte."""
    # Arrange
    writer = _FakeWriter()
    drain = _fake_drain_factory([])
    wait = _fake_wait_factory([])

    # Act
    request_response(
        send=_sender(writer, "ascii", eol),
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        timeout_s=1.0,
    )

    # Assert
    actual = writer.writes[0]
    expected = ("cmd" + eol).encode("ascii")
    assert actual == expected, f"line ending {eol!r} appended"


# ── JSON-mode ids (registry json-mode-ids) ──────────────────────────────────


def _windows(*windows: list[str]) -> Callable[..., list[str]]:
    """A wait_for_lines that answers each call with the next window, then nothing."""
    pending = list(windows)

    def wait(*, timeout: float, terminator: str = "", idle_gap: float = 0.05) -> list[str]:
        return pending.pop(0) if pending else []

    return wait


@pytest.mark.rule("json-mode-ids")
class TestJsonModeIds:
    """A device that takes JSON lines gets an id, and only the line carrying it answers."""

    def test_only_a_json_line_config_gets_ids(self):
        """protocol ndjson names the id field; a text device, the default, gets none."""
        assert id_field({"protocol": "ndjson"}) == "id", "the default field"
        assert (
            id_field({"protocol": "ndjson", "ndjson_field_routing": {"response_id": "tag"}})
            == "tag"
        )
        assert id_field({}) == "", "a text device is sent the line as typed"
        assert id_field({"protocol": "text"}) == ""

    def test_the_request_splits_the_command_from_its_args(self):
        """The first word is the command, the rest its args with inner spacing kept, then the id."""
        assert json_request("machine.Pin.value 2 1", 7, "id") == (
            '{"cmd":"machine.Pin.value","args":"2 1","id":7}'
        )
        assert json_request("temp", 8, "id") == '{"cmd":"temp","id":8}', "no args, no args key"
        assert json_request("echo 'a  b'", 9, "tag") == '{"cmd":"echo","args":"\'a  b\'","tag":9}'

    def test_the_reply_is_the_line_carrying_the_id(self):
        """A late reply, an event and a text line are the others; only id 7 answers, and only once."""
        lines = [
            '{"result":1,"id":6}',
            "tick",
            '{"event":"ready"}',
            '{"result":2,"id":7}',
            '{"result":3,"id":7}',
        ]
        reply, others = pick_reply(lines, 7, "id")
        assert reply == '{"result":2,"id":7}'
        assert others == [lines[0], lines[1], lines[2], lines[4]], "every other line, in order"
        assert pick_reply(['{"result":true,"id":true}'], 1, "id")[0] == "", "true is not the id 1"
        assert pick_reply(["not json {"], 1, "id") == ("", ["not json {"])

    def test_collection_goes_on_past_a_stray_line_until_the_reply(self):
        """A stray line ends the first idle window; the wait goes on and finds the reply in the next."""
        # Arrange
        writer = _FakeWriter()
        wait = _windows(['{"result":1,"id":6}'], ["print from the board"], ['{"result":2,"id":7}'])

        # Act
        rr = request_response(
            send=_sender(writer, "utf-8", "\n"),
            drain_recent_lines=_fake_drain_factory([]),
            wait_for_lines=wait,
            command=json_request("temp", 7, "id"),
            timeout_s=1.0,
            reply_id=7,
            field="id",
        )

        # Assert
        assert writer.writes == [b'{"cmd":"temp","id":7}\n'], "the request carries its id"
        assert pick_reply(rr["lines"], 7, "id")[0] == '{"result":2,"id":7}', (
            "the reply was waited for"
        )
        assert len(rr["lines"]) == 3, "every line that came is kept for the caller to sort"

    def test_without_an_id_one_window_is_the_answer(self):
        """With no id, the first idle window ends collection, as before."""
        rr = request_response(
            send=_sender(_FakeWriter(), "utf-8", "\r"),
            drain_recent_lines=_fake_drain_factory([]),
            wait_for_lines=_windows(["first"], ["second"]),
            command="temp",
            timeout_s=1.0,
        )
        assert rr["lines"] == ["first"]
