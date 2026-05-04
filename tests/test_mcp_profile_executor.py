"""Tests for the profile-aware MCP request/response executor.

The executor turns bare device commands (e.g. ``AT+TEMP``) into a
shaped JSON value when the active profile declares a response schema.
This is the bridge from "termapy sent some bytes" to "the LLM gets
{'celsius': 23.4}".

These tests build an MCPHost with no real serial port and stub the
plumbing:
  - ``host.engine`` is monkeypatched to report ``is_connected=True``.
  - ``host._serial_write`` records outbound bytes.
  - Inbound device replies are simulated by feeding lines into
    ``host.repl.feed_lines()`` from a helper thread (so the
    ``wait_for_lines`` blocking loop sees them as fresh arrivals).

Each format (none/literal/regex/lines/json), error_detection, the
fall-through (unmapped bare line, slash command, no profile loaded),
and the stale-line archival are covered.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from termapy.defaults import DEFAULT_CFG

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.mcp.server import MCPHost  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def host(tmp_path, monkeypatch):
    """Build an MCPHost with no port, with a fake-connected engine.

    ``engine.is_connected`` is a property; we override it on the
    instance's class for the duration of the test.  ``_serial_write``
    is captured so tests can assert on outbound bytes.
    """
    cfg = dict(DEFAULT_CFG)
    cfg["port"] = ""
    cfg["echo_input"] = False
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    h = MCPHost(cfg, str(config_path), verbose=False)

    # Force is_connected=True on the engine.  The property lives on
    # the class; patch at the class level for this test only.
    monkeypatch.setattr(
        type(h.engine), "is_connected", property(lambda _self: True)
    )

    # Capture outbound bytes; suppress the real port write (there is no port).
    sent: list[bytes] = []
    monkeypatch.setattr(h, "_serial_write", lambda data: sent.append(data))
    h._sent = sent  # convenience handle for tests
    return h


def _load_profile(host: MCPHost, profile: dict) -> None:
    """Install a profile into the active_profile namespace directly.

    Bypasses /profile.load (which also applies transport rules).  Tests
    that don't care about transport mutation use this; tests that need
    the transport-applied side-effect can call /profile.load.
    """
    ns = host.ctx.ns("active_profile")
    ns.clear()
    ns.update(profile)


def _reply_after_send(host: MCPHost, lines: list[str]) -> None:
    """Schedule ``lines`` to be fed to the engine AFTER the next serial_write.

    The race we need to avoid: if we push lines before _dispatch_via_profile
    drains the recent-lines buffer, those lines get archived as stale
    pre_send_drain events instead of being treated as the response.
    Hooking into the captured _serial_write so the push happens after
    the executor has sent its bytes guarantees correct ordering.
    """
    pushed = threading.Event()
    original_write = host._serial_write

    def _wrapped(data: bytes) -> None:
        original_write(data)
        if pushed.is_set():
            return
        pushed.set()

        def _push() -> None:
            host.repl.feed_lines(lines)

        threading.Thread(target=_push, daemon=True).start()

    host._serial_write = _wrapped


# ── Profile fixtures ────────────────────────────────────────────────────────


_BASE_TRANSPORT = {
    "protocol": "text",
    "baud_rate": 115200,
    "encoding": "utf-8",
    "line_ending_send": "\r\n",
    "line_ending_recv": "\r\n",
    "default_response_timeout_ms": 500,
}


def _profile_with(commands: dict, *, error_detection: dict | None = None) -> dict:
    p = {
        "profile_version": 2,
        "profile_revision": "1.0.0",
        "transport": dict(_BASE_TRANSPORT),
        "commands": commands,
    }
    if error_detection is not None:
        p["error_detection"] = error_detection
    return p


# ── Format coverage ─────────────────────────────────────────────────────────


class TestLiteralFormat:
    def test_returns_matched_when_response_equals_pattern(self, host):
        # Arrange
        _load_profile(host, _profile_with({
            "AT": {
                "help": "Connectivity test.",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ["OK"])
        # Act
        result = asyncio.run(host.run_command_async("AT", "normal", 5.0))
        # Assert
        actual_value = result["value"]
        expected_value = "OK"
        assert result["success"] is True, "literal hit succeeds"
        assert actual_value == expected_value, "literal returns the matched text"
        assert host._sent == [b"AT\r\n"], "wire bytes match transport eol"

    def test_returns_failure_when_response_differs(self, host):
        # Arrange
        _load_profile(host, _profile_with({
            "AT": {
                "help": "Connectivity test.",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ["NOPE"])
        # Act
        result = asyncio.run(host.run_command_async("AT", "normal", 5.0))
        # Assert
        assert result["success"] is False, "non-matching literal fails"
        assert "did not match" in result["error"], "error names format mismatch"


class TestRegexFormat:
    def test_named_groups_with_type_coercion(self, host):
        # Arrange
        _load_profile(host, _profile_with({
            "AT+TEMP": {
                "help": "Read temperature.",
                "response": {
                    "format": "regex",
                    "pattern": r"(?P<celsius>-?\d+\.\d+)C",
                    "types": {"celsius": "float"},
                    "timeout_ms": 200,
                },
            },
        }))
        _reply_after_send(host, ["23.4C"])
        # Act
        result = asyncio.run(host.run_command_async("AT+TEMP", "normal", 5.0))
        # Assert
        actual = result["value"]
        expected = {"celsius": 23.4}
        assert result["success"] is True, "regex hit succeeds"
        assert actual == expected, "named group coerced to float"

    def test_regex_no_match_fails_with_raw(self, host):
        # Arrange
        _load_profile(host, _profile_with({
            "AT+TEMP": {
                "help": "Read temperature.",
                "response": {
                    "format": "regex",
                    "pattern": r"(?P<celsius>-?\d+\.\d+)C",
                    "types": {"celsius": "float"},
                    "timeout_ms": 200,
                },
            },
        }))
        _reply_after_send(host, ["bogus"])
        # Act
        result = asyncio.run(host.run_command_async("AT+TEMP", "normal", 5.0))
        # Assert
        assert result["success"] is False, "non-matching regex fails"
        assert result["value"]["raw"] == "bogus", "raw text preserved on parse failure"


class TestLinesFormat:
    def test_collects_until_terminator(self, host):
        # Arrange — multi-line reply ending with OK terminator
        _load_profile(host, _profile_with({
            "AT+INFO": {
                "help": "Multi-line info.",
                "response": {
                    "format": "lines",
                    "terminator": "^OK$",
                    "timeout_ms": 200,
                },
            },
        }))
        _reply_after_send(host, ["model=DEMO", "version=1.0", "OK", "stale_after_ok"])
        # Act
        result = asyncio.run(host.run_command_async("AT+INFO", "normal", 5.0))
        # Assert
        actual = result["value"]
        expected = ["model=DEMO", "version=1.0"]
        assert result["success"] is True, "lines collection succeeds"
        assert actual == expected, "terminator excluded; later lines dropped"


class TestJsonFormat:
    def test_returns_parsed_object(self, host):
        # Arrange
        _load_profile(host, _profile_with({
            "GET_VOLT": {
                "help": "Voltage reading (JSON).",
                "response": {"format": "json", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ['{"voltage": 1.2, "unit": "V"}'])
        # Act
        result = asyncio.run(host.run_command_async("GET_VOLT", "normal", 5.0))
        # Assert
        actual = result["value"]
        expected = {"voltage": 1.2, "unit": "V"}
        assert result["success"] is True, "json parse succeeds"
        assert actual == expected, "json passed through as dict"


class TestNoneFormat:
    def test_fire_and_forget_returns_sent_marker(self, host):
        # Arrange — no reply scheduled; format=none means we don't wait
        _load_profile(host, _profile_with({
            "RESET": {
                "help": "Reset; no reply.",
                "response": {"format": "none"},
            },
        }))
        # Act
        result = asyncio.run(host.run_command_async("RESET", "normal", 5.0))
        # Assert
        actual = result["value"]
        assert result["success"] is True, "fire-and-forget succeeds without waiting"
        assert actual == {"sent": True, "cmd": "RESET"}, "sent-marker shape"
        assert host._sent == [b"RESET\r\n"], "bytes still went out"


# ── Error detection ─────────────────────────────────────────────────────────


class TestErrorDetection:
    def test_error_pattern_dominates_response(self, host):
        # Arrange — even though regex would match, error_detection wins
        _load_profile(host, _profile_with(
            {
                "AT+TEMP": {
                    "help": "Read temperature.",
                    "response": {
                        "format": "regex",
                        "pattern": r"(?P<celsius>-?\d+\.\d+)C",
                        "types": {"celsius": "float"},
                    },
                },
            },
            error_detection={
                "pattern": r"^ERROR(?::\s*(?P<message>.+))?$",
            },
        ))
        _reply_after_send(host, ["ERROR: sensor unavailable"])
        # Act
        result = asyncio.run(host.run_command_async("AT+TEMP", "normal", 5.0))
        # Assert
        assert result["success"] is False, "error pattern fails the call"
        assert result["error"] == "sensor unavailable", (
            "captured message group surfaces as error"
        )


# ── Destructive-command confirmation gate ───────────────────────────────────


class TestDestructiveGate:
    """Profile entries with ``safety: destructive`` need an explicit
    ``confirm=True`` on the MCP tool call.  Safe/readonly are unaffected."""

    def test_destructive_without_confirm_refuses(self, host):
        # Arrange — RESET marked destructive
        _load_profile(host, _profile_with({
            "RESET": {
                "help": "Soft reset; clears RAM.",
                "safety": "destructive",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        # Act — call without confirm
        result = asyncio.run(host.run_command_async("RESET", "normal", 5.0))
        # Assert — fails BEFORE sending; surfaces an LLM-actionable shape
        actual_marker = result["value"]
        expected_marker = {
            "needs_confirmation": True,
            "command": "RESET",
            "safety": "destructive",
            "help": "Soft reset; clears RAM.",
        }
        assert result["success"] is False, "destructive blocks without confirm"
        assert "Confirmation required" in result["error"], (
            "error names the gate so the LLM client can elicit"
        )
        assert actual_marker == expected_marker, (
            "value carries structured marker for client UIs"
        )
        assert host._sent == [], "no bytes hit the wire when gate fires"

    def test_destructive_with_confirm_runs(self, host):
        # Arrange
        _load_profile(host, _profile_with({
            "RESET": {
                "help": "Soft reset.",
                "safety": "destructive",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ["OK"])
        # Act — confirm=True
        result = asyncio.run(
            host.run_command_async("RESET", "normal", 5.0, confirm=True)
        )
        # Assert
        assert result["success"] is True, "confirm=True bypasses the gate"
        assert result["value"] == "OK", "command ran and parsed normally"
        assert host._sent == [b"RESET\r\n"], "bytes went out after confirmation"

    def test_safe_command_ignores_confirm(self, host):
        # Arrange — safe (default) command; confirm should be a no-op
        _load_profile(host, _profile_with({
            "AT": {
                "help": "Connectivity test.",
                "safety": "readonly",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ["OK"])
        # Act — call with confirm=False (the gate must not fire on readonly)
        result = asyncio.run(host.run_command_async("AT", "normal", 5.0))
        # Assert
        assert result["success"] is True, "readonly runs without confirm"
        assert host._sent == [b"AT\r\n"], "readonly hit the wire normally"

    def test_mutable_runs_without_confirm(self, host):
        # Arrange — mutable: changes state but reversible (LED toggle).
        # The gate must NOT fire; only destructive does.
        _load_profile(host, _profile_with({
            "AT+LED": {
                "help": "Set LED state.",
                "safety": "mutable",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ["OK"])
        # Act
        result = asyncio.run(host.run_command_async("AT+LED", "normal", 5.0))
        # Assert
        assert result["success"] is True, "mutable runs without confirm"
        assert host._sent == [b"AT+LED\r\n"], "mutable bytes hit the wire"


# ── Enabled gate (per-command audit toggle) ─────────────────────────────────


class TestEnabledGate:
    """Profile entries with ``enabled: false`` are hidden from the
    executor and fall through to /term.send.  Default-true preserves
    existing curated profiles unchanged.  This is the second line of
    defense after the catalog filter (which hides them from the LLM
    in the first place); the gate exists so even a hand-typed bare
    command can't run a draft entry."""

    def test_disabled_command_refuses(self, host):
        # Arrange — entry exists in profile but enabled=false.  The
        # executor MUST refuse (defense in depth: catalog filter
        # already hid it, but if the LLM somehow learned the name
        # we still won't let it run).
        _load_profile(host, _profile_with({
            "AT": {
                "help": "Connection test.",
                "enabled": False,
                "safety": "readonly",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        # Act
        result = asyncio.run(host.run_command_async("AT", "normal", 5.0))
        # Assert -- refused; no bytes on the wire; structured marker
        actual_marker = result["value"]
        expected_marker = {
            "disabled": True,
            "command": "AT",
            "help": "Connection test.",
        }
        assert result["success"] is False, "disabled commands refuse to run"
        assert "disabled" in result["error"], "error names the gate reason"
        assert actual_marker == expected_marker, "structured marker for client UI"
        assert host._sent == [], "no bytes hit the wire when entry is disabled"

    def test_enabled_default_true_runs_normally(self, host):
        # Arrange -- entry omits ``enabled`` entirely.  Default true.
        _load_profile(host, _profile_with({
            "AT": {
                "help": "Connection test.",
                "safety": "readonly",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ["OK"])
        # Act
        result = asyncio.run(host.run_command_async("AT", "normal", 5.0))
        # Assert
        assert result["success"] is True, "enabled-by-default runs normally"
        assert result["value"] == "OK", "executor shaped the response"

    def test_disabled_takes_precedence_over_destructive(self, host):
        # Arrange -- destructive AND disabled.  The disabled gate
        # checks first; failure carries the disabled marker, NOT the
        # needs_confirmation marker.  Either way the user gets a
        # refusal -- but the message is "audit and enable" rather
        # than "approve once" because that's the right action.
        _load_profile(host, _profile_with({
            "RESET": {
                "help": "Soft reset.",
                "enabled": False,
                "safety": "destructive",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        # Act -- even with confirm=True, disabled refuses
        result = asyncio.run(
            host.run_command_async("RESET", "normal", 5.0, confirm=True)
        )
        # Assert
        assert result["success"] is False, "disabled refuses regardless of confirm"
        assert result["value"].get("disabled") is True, "disabled marker, not needs_confirmation"
        assert host._sent == [], "no bytes go out for a disabled entry"


# ── Fall-through (no profile / no match / slash) ────────────────────────────


class TestFallThrough:
    def test_slash_command_bypasses_profile(self, host):
        # Arrange — load a profile, but dispatch a slash command
        _load_profile(host, _profile_with({
            "AT": {
                "help": "x",
                "response": {"format": "literal", "pattern": "OK"},
            },
        }))
        # Act
        result = asyncio.run(host.run_command_async("/help", "normal", 5.0))
        # Assert — /help dispatched normally, no serial write
        assert result["success"] is True, "/help still works"
        assert host._sent == [], "slash commands don't hit the wire"

    def test_unmapped_bare_line_falls_through(self, host):
        # Arrange — profile loaded but RANDOM not in it.  Bare line
        # should fall through to /term.send (writes bytes, value="").
        _load_profile(host, _profile_with({
            "AT": {
                "help": "x",
                "response": {"format": "literal", "pattern": "OK"},
            },
        }))
        # Act
        result = asyncio.run(host.run_command_async("RANDOM", "normal", 5.0))
        # Assert — /term.send returned ok, default cfg line_ending="\r"
        # (transport eol is for profile commands; fallthrough uses cfg).
        assert result["success"] is True, "fall-through send succeeds"
        assert host._sent and host._sent[0].startswith(b"RANDOM"), (
            "bytes for unmapped command still went out"
        )

    def test_no_profile_loaded_falls_through(self, host):
        # Arrange — never call _load_profile.  Bare AT goes to /term.send.
        # Act
        result = asyncio.run(host.run_command_async("AT", "normal", 5.0))
        # Assert
        assert result["success"] is True, "no-profile send succeeds"
        assert host._sent and host._sent[0].startswith(b"AT"), "AT bytes sent"
        # value should be empty -- /term.send doesn't return data
        assert result["value"] == "", "no profile means no shaped value"


# ── Stale-line archival ─────────────────────────────────────────────────────


class TestStaleArchival:
    def test_pre_send_drain_moves_buffer_to_async_events(self, host):
        # Arrange — load a profile, push some "between calls" lines into
        # the recent-lines buffer, then dispatch a profile command.
        _load_profile(host, _profile_with({
            "AT": {
                "help": "x",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        host.repl.feed_lines(["leftover_1", "leftover_2"])
        _reply_after_send(host, ["OK"])
        # Act
        result = asyncio.run(host.run_command_async("AT", "normal", 5.0))
        # Assert
        assert result["success"] is True, "command itself succeeds"
        archived = [
            e for e in host._async_events if e.get("source") == "pre_send_drain"
        ]
        actual_lines = [e["line"] for e in archived]
        expected_lines = ["leftover_1", "leftover_2"]
        assert actual_lines == expected_lines, (
            "stale recent-lines moved to async_events with pre_send_drain source"
        )


# ── Send-template binding ───────────────────────────────────────────────────


class TestSendTemplate:
    def test_send_template_command_matches_rendered_text(self, host):
        # Arrange — AT+LED={state} entry; LLM types the rendered form.
        _load_profile(host, _profile_with({
            "AT+LED": {
                "help": "Toggle LED.",
                "send_template": "AT+LED={state}",
                "response": {"format": "literal", "pattern": "OK", "timeout_ms": 200},
            },
        }))
        _reply_after_send(host, ["OK"])
        # Act
        result = asyncio.run(host.run_command_async("AT+LED=on", "normal", 5.0))
        # Assert
        assert result["success"] is True, "send_template entry matched"
        assert host._sent == [b"AT+LED=on\r\n"], "literal text sent through"
