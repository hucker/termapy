"""Tests for Phase 2: TargetCommand v2 fields + /include ingestion.

Verifies that:
- v1 manifests load unchanged (defaults for all v2 fields).
- v2 manifests populate the new fields correctly.
- _to_json_dict round-trips: v1 byte-stable, v2 round-trips its fields.
- Sanitization: malformed v2 values fall back to defaults.

No serial / no Textual.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.builtins.commands.include import (
    _build_commands,
    _to_json_dict,
)
from termapy.plugins import TargetCommand


FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


# ── v1 manifests: unchanged by the v2 extension ─────────────────────────────


class TestV1ManifestsUnchanged:
    def test_v1_manifest_loads_with_default_v2_fields(self):
        # Arrange
        v1 = {"AT": {"help": "Connectivity test.", "args": ""}}
        # Act
        cmds = _build_commands(v1)
        # Assert
        actual = cmds["AT"]
        assert actual.help == "Connectivity test.", "help preserved"
        assert actual.args == "", "args preserved"
        assert actual.typed_args == [], "typed_args defaults empty"
        assert actual.send_template == "", "send_template defaults empty"
        assert actual.response == {}, "response defaults empty"
        assert actual.safety == "safe", "safety defaults safe"
        assert actual.rate_limit_hz == 0.0, "rate_limit_hz defaults 0"
        assert actual.timeout_ms == 0, "timeout_ms defaults 0"
        assert actual.subcommands == {}, "subcommands default empty"

    def test_v1_round_trip_byte_stable(self):
        # Arrange — minimal v1 manifest
        v1_input = {"AT": {"help": "Connectivity test.", "args": ""}}
        # Act
        cmds = _build_commands(v1_input)
        round_tripped = _to_json_dict(cmds)["commands"]
        # Assert
        assert round_tripped == v1_input, (
            "v1 manifest round-trips byte-stable; v2 defaults omitted"
        )

    def test_v1_with_long_help_and_flags_round_trips(self):
        # Arrange
        v1_input = {
            "AT": {
                "help": "Connectivity test.",
                "args": "",
                "long_help": "Returns OK if device is responsive.",
                "flags": {"--quiet": "Suppress output."},
            }
        }
        # Act
        cmds = _build_commands(v1_input)
        round_tripped = _to_json_dict(cmds)["commands"]
        # Assert
        assert round_tripped == v1_input, "v1 with all v1 fields round-trips"


# ── v2 fields populate correctly ────────────────────────────────────────────


class TestV2FieldsPopulate:
    def test_typed_args_list_of_dicts(self):
        # Arrange
        entry = {
            "help": "Set voltage.",
            "typed_args": [
                {"name": "mv", "type": "int", "required": True, "min": 0, "max": 30000},
            ],
        }
        # Act
        cmd = _build_commands({"AT+VOLT": entry})["AT+VOLT"]
        # Assert
        assert len(cmd.typed_args) == 1, "one typed arg"
        assert cmd.typed_args[0]["name"] == "mv", "name preserved"
        assert cmd.typed_args[0]["type"] == "int", "type preserved"
        assert cmd.typed_args[0]["min"] == 0, "min preserved"

    def test_send_template_string(self):
        # Arrange
        entry = {"help": "Set X.", "send_template": "AT+X={v}"}
        # Act
        cmd = _build_commands({"AT+X": entry})["AT+X"]
        # Assert
        assert cmd.send_template == "AT+X={v}", "send_template preserved"

    def test_response_dict(self):
        # Arrange
        entry = {
            "help": "Read version.",
            "response": {
                "format": "regex",
                "pattern": r"VER=(\d+)",
                "types": {"v": "int"},
                "timeout_ms": 500,
            },
        }
        # Act
        cmd = _build_commands({"AT+VER": entry})["AT+VER"]
        # Assert
        assert cmd.response["format"] == "regex", "format preserved"
        assert cmd.response["pattern"] == r"VER=(\d+)", "pattern preserved"
        assert cmd.response["timeout_ms"] == 500, "timeout preserved"

    def test_safety_enum(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "safety": "destructive"}}
        )["X"]
        # Assert
        assert cmd.safety == "destructive", "safety preserved"

    def test_rate_limit_hz(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "rate_limit_hz": 5.0}}
        )["X"]
        # Assert
        assert cmd.rate_limit_hz == 5.0, "rate_limit preserved"

    def test_timeout_ms(self):
        # Arrange / Act
        cmd = _build_commands({"X": {"help": "h", "timeout_ms": 1000}})["X"]
        # Assert
        assert cmd.timeout_ms == 1000, "timeout_ms preserved"

    def test_subcommands_recursive(self):
        # Arrange — nested subcommands
        entry = {
            "help": "Port commands.",
            "subcommands": {
                "open": {"help": "Open the port."},
                "close": {"help": "Close the port."},
            },
        }
        # Act
        cmd = _build_commands({"port": entry})["port"]
        # Assert
        assert "open" in cmd.subcommands, "open subcommand present"
        assert "close" in cmd.subcommands, "close subcommand present"
        assert cmd.subcommands["open"].help == "Open the port.", "nested help"


# ── Sanitization: malformed values fall back to defaults ────────────────────


class TestSanitization:
    def test_typed_args_non_list_falls_back_to_empty(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "typed_args": "not-a-list"}}
        )["X"]
        # Assert
        assert cmd.typed_args == [], "non-list typed_args defaults empty"

    def test_typed_args_drops_unknown_keys(self):
        # Arrange — extra keys not in the keep-list
        entry = {
            "help": "h",
            "typed_args": [
                {"name": "x", "type": "int", "secret_field": "hax"},
            ],
        }
        # Act
        cmd = _build_commands({"X": entry})["X"]
        # Assert
        assert "secret_field" not in cmd.typed_args[0], (
            "unknown keys dropped during sanitization"
        )
        assert cmd.typed_args[0]["name"] == "x", "known keys kept"

    def test_invalid_safety_falls_back_to_safe(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "safety": "kinda-mostly-safe"}}
        )["X"]
        # Assert
        assert cmd.safety == "safe", "invalid safety defaults to safe"

    def test_non_numeric_rate_limit_falls_back_to_zero(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "rate_limit_hz": "fast"}}
        )["X"]
        # Assert
        assert cmd.rate_limit_hz == 0.0, "non-numeric rate_limit defaults 0"

    def test_non_int_timeout_falls_back_to_zero(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "timeout_ms": "soon"}}
        )["X"]
        # Assert
        assert cmd.timeout_ms == 0, "non-int timeout defaults 0"

    def test_non_string_send_template_falls_back_to_empty(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "send_template": ["not", "a", "string"]}}
        )["X"]
        # Assert
        assert cmd.send_template == "", "non-string send_template defaults empty"

    def test_non_dict_response_falls_back_to_empty(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "response": "not-a-dict"}}
        )["X"]
        # Assert
        assert cmd.response == {}, "non-dict response defaults empty"

    def test_non_dict_subcommands_falls_back_to_empty(self):
        # Arrange / Act
        cmd = _build_commands(
            {"X": {"help": "h", "subcommands": ["not", "a", "dict"]}}
        )["X"]
        # Assert
        assert cmd.subcommands == {}, "non-dict subcommands defaults empty"


# ── Round-trip stability for v2 manifests ───────────────────────────────────


class TestV2RoundTrip:
    def test_v2_response_round_trips(self):
        # Arrange
        original = {
            "AT+VER": {
                "help": "Read version.",
                "args": "",
                "response": {
                    "format": "regex",
                    "pattern": r"VER=(\d+)",
                    "timeout_ms": 500,
                },
            }
        }
        # Act
        cmds = _build_commands(original)
        round_tripped = _to_json_dict(cmds)["commands"]
        # Assert — response field present, content preserved
        assert round_tripped["AT+VER"]["response"]["format"] == "regex", (
            "response.format round-trips"
        )
        assert (
            round_tripped["AT+VER"]["response"]["pattern"] == r"VER=(\d+)"
        ), "response.pattern round-trips"

    def test_v2_safety_destructive_round_trips(self):
        # Arrange
        original = {
            "RESET": {"help": "Reset.", "args": "", "safety": "destructive"}
        }
        # Act
        round_tripped = _to_json_dict(_build_commands(original))["commands"]
        # Assert
        assert round_tripped["RESET"]["safety"] == "destructive", (
            "safety field round-trips"
        )

    def test_v2_safety_safe_omitted_in_round_trip(self):
        # Arrange — safe is the default; should be omitted from output
        original = {"X": {"help": "h", "args": "", "safety": "safe"}}
        # Act
        round_tripped = _to_json_dict(_build_commands(original))["commands"]
        # Assert
        assert "safety" not in round_tripped["X"], (
            "default safety omitted to keep v1 round-trip byte-stable"
        )

    def test_v2_subcommands_round_trip_recursive(self):
        # Arrange
        original = {
            "port": {
                "help": "Port commands.",
                "args": "",
                "subcommands": {
                    "open": {"help": "Open.", "args": ""},
                },
            }
        }
        # Act
        round_tripped = _to_json_dict(_build_commands(original))["commands"]
        # Assert
        assert round_tripped["port"]["subcommands"]["open"]["help"] == "Open.", (
            "nested subcommand help round-trips"
        )

    def test_v2_full_round_trip_via_reference_profile(self):
        # Arrange — load the smart_sensor reference and ingest its commands
        profile_path = FIXTURES / "smart_sensor.profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        original_commands = profile["commands"]
        # Act
        cmds = _build_commands(original_commands)
        # Assert — v2 fields populated for at least one command
        assert "get_temp" in cmds, "smart_sensor get_temp loaded"
        set_threshold = cmds["set_threshold"]
        assert set_threshold.safety == "destructive", "safety populated from v2"
        assert set_threshold.rate_limit_hz == 10.0, "rate_limit populated"
        assert len(set_threshold.typed_args) == 1, "typed_args populated"
        assert set_threshold.response.get("format") == "json", (
            "response.format populated"
        )


# ── Forward-compat: unknown keys still ignored ──────────────────────────────


class TestForwardCompat:
    def test_unknown_top_level_key_in_command_ignored(self):
        # Arrange — entry has a future field termapy doesn't know
        entry = {"help": "h", "future_field_v9": {"complicated": "structure"}}
        # Act — should not raise; should produce a valid TargetCommand
        cmd = _build_commands({"X": entry})["X"]
        # Assert
        assert cmd.help == "h", "command loaded"
        # The unknown key is dropped on round-trip (forward-compat = lossy)
        round_tripped = _to_json_dict({"X": cmd})["commands"]["X"]
        assert "future_field_v9" not in round_tripped, (
            "unknown keys dropped on round-trip (acceptable forward-compat)"
        )


# ── Bare TargetCommand construction still works (v1 callers) ────────────────


class TestTargetCommandBareConstruction:
    def test_v1_constructor_with_only_required_args(self):
        # Arrange / Act — code that constructs TargetCommand the old way
        tc = TargetCommand(name="X", help="h")
        # Assert — all v2 fields at defaults
        assert tc.typed_args == [], "v2 default"
        assert tc.send_template == "", "v2 default"
        assert tc.safety == "safe", "v2 default"
        assert tc.subcommands == {}, "v2 default"

    def test_v1_constructor_with_all_v1_args(self):
        # Arrange / Act — full v1 shape
        tc = TargetCommand(
            name="X",
            help="h",
            args="<v>",
            long_help="Longer prose.",
            flags={"--q": "Quiet."},
        )
        # Assert
        assert tc.long_help == "Longer prose.", "v1 long_help preserved"
        assert tc.flags == {"--q": "Quiet."}, "v1 flags preserved"
        assert tc.safety == "safe", "v2 default still applies"
