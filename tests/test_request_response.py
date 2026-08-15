"""Tests for ``termapy.request_response.request_response``.

Pure plumbing helper — drain -> send -> wait -> collect.  Tests use
fake callables so we don't need real serial I/O.  The MCP profile
executor and the json_mode REPL fallthrough both depend on this
helper behaving correctly; regression here breaks both paths.
"""

from __future__ import annotations

import pytest

from termapy.request_response import request_response

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


def _fake_drain_factory(stale: list[str]) -> callable:
    """Return a drain callable that yields ``stale`` then ``[]`` thereafter."""
    state = {"first": True}

    def drain() -> list[str]:
        if state["first"]:
            state["first"] = False
            return list(stale)
        return []

    return drain


def _fake_wait_factory(lines: list[str]) -> callable:
    """Return a wait_for_lines callable that returns ``lines`` regardless of args."""

    def wait(*, timeout: float, terminator: str = "", idle_gap: float = 0.05):
        return list(lines)

    return wait


# ── Tests ────────────────────────────────────────────────────────────────────


def test_send_and_collect_basic():
    # Arrange — fake writer; fake wait returns 2 lines
    writer = _FakeWriter()
    drain = _fake_drain_factory([])
    wait = _fake_wait_factory(["OK", "VOLT=5.5"])

    # Act
    rr = request_response(
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="get_voltage",
        encoding="utf-8",
        line_ending="\r",
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
    # Arrange — drain yields 3 stale lines
    writer = _FakeWriter()
    drain = _fake_drain_factory(["leftover-1", "leftover-2", "leftover-3"])
    wait = _fake_wait_factory(["fresh"])
    recorded: list[str] = []

    # Act
    rr = request_response(
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        encoding="utf-8",
        line_ending="\n",
        timeout_s=1.0,
        on_drained_line=recorded.append,
    )

    # Assert — every stale line went to the callback; response unaffected
    actual_recorded = recorded
    expected_recorded = ["leftover-1", "leftover-2", "leftover-3"]
    assert actual_recorded == expected_recorded, "stale lines passed to callback"
    assert rr["text"] == "fresh", "fresh response after drain"


def test_drained_stale_lines_silently_discarded_when_no_callback():
    # Arrange — drain has stale lines but no callback supplied
    writer = _FakeWriter()
    drain = _fake_drain_factory(["junk"])
    wait = _fake_wait_factory([])

    # Act
    rr = request_response(
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        encoding="utf-8",
        line_ending="\r",
        timeout_s=1.0,
    )

    # Assert — no callback invoked; helper still functions normally
    assert rr["error"] == "", "no error from missing callback"
    assert rr["lines"] == [], "wait result returned"


def test_send_error_returns_error_field_no_wait():
    # Arrange — writer raises OSError; wait should NOT be called
    writer = _FakeWriter(raise_on_write=OSError("port closed"))
    drain = _fake_drain_factory([])
    wait_called = {"count": 0}

    def wait(*, timeout, terminator="", idle_gap=0.05):
        wait_called["count"] += 1
        return []

    # Act
    rr = request_response(
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        encoding="utf-8",
        line_ending="\r",
        timeout_s=1.0,
    )

    # Assert — error field populated; wait_for_lines never called
    assert "Send error" in rr["error"], "error field describes send failure"
    assert "port closed" in rr["error"], "wraps the underlying exception"
    assert wait_called["count"] == 0, "wait_for_lines skipped on send failure"
    assert rr["lines"] == [], "no lines collected on send error"
    assert rr["text"] == "", "no text on send error"


def test_wait_false_skips_collection():
    # Arrange
    writer = _FakeWriter()
    drain = _fake_drain_factory(["stale"])
    wait_called = {"count": 0}

    def wait(*, timeout, terminator="", idle_gap=0.05):
        wait_called["count"] += 1
        return ["should-not-be-collected"]

    # Act
    rr = request_response(
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="fire-and-forget",
        encoding="utf-8",
        line_ending="\r",
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
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        encoding="utf-8",
        line_ending="\r",
        timeout_s=2.5,
        terminator=r"^OK$",
        idle_gap_s=0.2,
    )

    # Assert
    actual = captured
    expected = {"timeout": 2.5, "terminator": r"^OK$", "idle_gap": 0.2}
    assert actual == expected, "terminator/timeout/idle_gap passed verbatim"


def test_encoding_used_for_send():
    # Arrange
    writer = _FakeWriter()
    drain = _fake_drain_factory([])
    wait = _fake_wait_factory([])

    # Act — latin-1 encoded "café"
    request_response(
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="café",
        encoding="latin-1",
        line_ending="",
        timeout_s=1.0,
    )

    # Assert — latin-1 caf\xe9 not utf-8 caf\xc3\xa9
    actual = writer.writes
    expected = [b"caf\xe9"]
    assert actual == expected, "encoding used to encode command"


@pytest.mark.parametrize("eol", ["\r", "\n", "\r\n", ""])
def test_line_ending_appended(eol):
    # Arrange
    writer = _FakeWriter()
    drain = _fake_drain_factory([])
    wait = _fake_wait_factory([])

    # Act
    request_response(
        serial_write=writer,
        drain_recent_lines=drain,
        wait_for_lines=wait,
        command="cmd",
        encoding="ascii",
        line_ending=eol,
        timeout_s=1.0,
    )

    # Assert
    actual = writer.writes[0]
    expected = ("cmd" + eol).encode("ascii")
    assert actual == expected, f"line ending {eol!r} appended"
