"""Tests for termapy.profile and termapy.response_parsers (Phase 0).

Covers schema validation, profile load/save, precedence comparator,
and parse_response across all five formats.  No serial or Textual.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.profile import (
    Profile,
    SCHEMA_PATH,
    load_profile,
    precedence,
    save_profile,
    validate_profile,
)
from termapy.response_parsers import parse_response


FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
REFERENCE_PROFILES = [
    "at_modem.profile.json",
    "register_psu.profile.json",
    "smart_sensor.profile.json",
    "typed_modem.profile.json",
]


# ── schema file basics ──────────────────────────────────────────────────────


class TestSchemaFile:
    def test_schema_file_exists(self):
        # Arrange / Act
        actual = SCHEMA_PATH.exists()
        # Assert
        assert actual is True, f"schema must exist at {SCHEMA_PATH}"

    def test_schema_file_is_valid_json(self):
        # Arrange / Act
        actual = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Assert
        assert isinstance(actual, dict), "schema is a JSON object"
        assert "$schema" in actual, "must declare $schema"

    def test_schema_top_level_has_expected_blocks(self):
        # Arrange
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Act
        actual = set(schema["properties"].keys())
        expected_required = {
            "profile_version", "profile_revision", "profile_date",
            "device", "error_detection", "commands",
        }
        # Assert
        assert expected_required.issubset(actual), (
            f"schema missing top-level blocks: {expected_required - actual}"
        )


# ── reference profiles validate ─────────────────────────────────────────────


@pytest.mark.parametrize("name", REFERENCE_PROFILES)
class TestReferenceProfiles:
    def test_reference_profile_loads(self, name):
        # Arrange
        path = FIXTURES / name
        # Act
        actual = load_profile(path)
        # Assert
        assert isinstance(actual, dict), f"{name} loads as dict"
        assert "commands" in actual, f"{name} has commands"

    def test_reference_profile_validates(self, name):
        # Arrange
        path = FIXTURES / name
        profile = load_profile(path)
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, (
            f"{name} should validate cleanly, got errors: {result.errors}"
        )

    def test_reference_profile_round_trip(self, tmp_path, name):
        # Arrange
        path = FIXTURES / name
        original = load_profile(path)
        out = tmp_path / name
        # Act
        save_profile(original, out)
        round_tripped = load_profile(out)
        # Assert
        assert round_tripped == original, f"{name} round-trips byte-equivalent"


# ── load: format detection ──────────────────────────────────────────────────


class TestLoadFormatDetection:
    def test_load_json_by_extension(self, tmp_path):
        # Arrange
        path = tmp_path / "p.json"
        path.write_text('{"profile_version": 2, "commands": {}}', encoding="utf-8")
        # Act
        actual = load_profile(path)
        # Assert
        assert actual["profile_version"] == 2, "json loaded"

    def test_load_toml_by_extension(self, tmp_path):
        # Arrange
        path = tmp_path / "p.toml"
        path.write_text(
            'profile_version = 2\n[commands.AT]\nhelp = "test"\n',
            encoding="utf-8",
        )
        # Act
        actual = load_profile(path)
        # Assert
        assert actual["profile_version"] == 2, "toml profile_version loaded"
        assert actual["commands"]["AT"]["help"] == "test", "toml command parsed"

    def test_load_unknown_extension_tries_toml_first(self, tmp_path):
        # Arrange — TOML content with no extension
        path = tmp_path / "p"
        path.write_text(
            'profile_version = 2\n[commands.AT]\nhelp = "test"\n',
            encoding="utf-8",
        )
        # Act
        actual = load_profile(path)
        # Assert
        assert actual["profile_version"] == 2, "fallback to toml works"

    def test_load_unknown_extension_falls_back_to_json(self, tmp_path):
        # Arrange — JSON content with no extension
        path = tmp_path / "p"
        path.write_text('{"profile_version": 2}', encoding="utf-8")
        # Act
        actual = load_profile(path)
        # Assert
        assert actual["profile_version"] == 2, "fallback to json works"

    def test_load_invalid_content_raises(self, tmp_path):
        # Arrange — neither JSON nor TOML
        path = tmp_path / "p.json"
        path.write_text("this is not valid {", encoding="utf-8")
        # Act / Assert
        with pytest.raises((ValueError, json.JSONDecodeError)):
            load_profile(path)


# ── save always JSON ────────────────────────────────────────────────────────


class TestSaveAlwaysJson:
    def test_save_writes_json_for_json_path(self, tmp_path):
        # Arrange
        profile = {"profile_version": 2, "commands": {}}
        out = tmp_path / "p.json"
        # Act
        save_profile(profile, out)
        # Assert
        assert json.loads(out.read_text("utf-8")) == profile, "saved as parseable json"

    def test_save_writes_json_even_for_toml_path(self, tmp_path):
        # Arrange — save targets a .toml extension; result is still JSON content
        profile = {"profile_version": 2, "commands": {}}
        out = tmp_path / "p.toml"
        # Act
        save_profile(profile, out)
        # Assert — must parse as JSON (not TOML), per spec: save is always JSON
        actual = json.loads(out.read_text("utf-8"))
        assert actual == profile, "save ignores extension and writes JSON"


# ── built-in validator ──────────────────────────────────────────────────────


class TestBuiltinValidator:
    def test_empty_dict_validates(self):
        # Arrange / Act
        result = validate_profile({})
        # Assert — empty profile is structurally valid (all fields optional)
        assert result.ok is True, "empty dict has no errors"

    def test_non_dict_root_fails(self):
        # Arrange / Act
        result = validate_profile("not a dict")  # type: ignore[arg-type]
        # Assert
        assert result.ok is False, "non-dict root rejected"
        assert len(result.errors) >= 1, "at least one error"

    def test_invalid_profile_version_fails(self):
        # Arrange
        profile = {"profile_version": 99}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "version 99 is not in {1,2}"
        assert any("profile_version" in e for e in result.errors), (
            "error mentions profile_version"
        )

    def test_transport_block_rejected(self):
        # Arrange -- transport block retired in v18 cfg / drop-transport
        # refactor; wire-level settings live in cfg now.
        profile = {"profile_version": 2, "transport": {"baud_rate": 9600}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "transport block must fail validation"
        assert any("transport" in e for e in result.errors), (
            "error mentions transport so authors can find what to remove"
        )

    def test_command_missing_help_fails(self):
        # Arrange
        profile = {"commands": {"BAD": {}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "command requires help"
        assert any("BAD" in e and "help" in e for e in result.errors), (
            "error mentions command name and field"
        )

    def test_unknown_safety_warns_but_loads(self):
        # Arrange -- compatibility policy: a tier from a future spec
        # revision must degrade (gate like destructive), not fail load.
        profile = {
            "commands": {"X": {"help": "h", "safety": "kinda-mostly-safe"}}
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "unknown safety tier never blocks a load"
        assert any(
            "safety" in w and "destructive" in w for w in result.warnings
        ), "warning names the field and states the degrade rule"

    def test_unknown_response_format_warns_but_loads(self):
        # Arrange -- same policy: unknown format degrades to 'text'.
        profile = {
            "commands": {"X": {"help": "h", "response": {"format": "yaml"}}}
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "unknown response format never blocks a load"
        assert any(
            "format" in w and "text" in w for w in result.warnings
        ), "warning names the field and states the degrade rule"

    def test_valid_minimal_profile_passes(self):
        # Arrange — only required field is help per command
        profile = {"commands": {"AT": {"help": "test"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "minimal profile validates"


# ── types block (profile-local user-defined types) ──────────────────────────


class TestTypesBlockSchema:
    """Schema-rejection tests for the v2 ``types`` block.

    These assertions require the optional ``jsonschema`` package.  The
    loader's ``_builtin_validate`` fallback only enforces the high-
    leverage v1/v2 rules and doesn't know how to validate the
    ``types``-block discriminated union, so these tests skip cleanly
    when ``jsonschema`` isn't installed (tox envs that don't pull it
    in, slim install profiles, etc.).
    """

    pytestmark = pytest.mark.skipif(
        __import__("importlib.util", fromlist=[""]).find_spec("jsonschema") is None,
        reason="strict types-block validation requires the optional jsonschema package",
    )

    def test_empty_types_block_validates(self):
        # Arrange
        profile = {"commands": {}, "types": {}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "empty types block is valid"

    def test_all_six_kinds_validate(self):
        # Arrange — one of each kind, minimally well-formed.
        profile = {
            "commands": {},
            "types": {
                "a": {"kind": "enum", "values": ["x"]},
                "b": {"kind": "int_range", "min": 0, "max": 10},
                "c": {"kind": "float_range", "min": 0.0, "max": 1.0},
                "d": {"kind": "str_length", "min_len": 1, "max_len": 4},
                "e": {"kind": "pattern", "regex": "^x$"},
                "f": {"kind": "format_spec", "spec": "Val:H1"},
            },
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, (
            f"all six kinds should validate; errors: {result.errors}"
        )

    def test_unknown_kind_warns_but_loads(self):
        # Arrange -- compatibility policy: unknown kind loads with a
        # warning; args of that type refuse at dispatch (fail-closed).
        profile = {"commands": {}, "types": {"x": {"kind": "totally_made_up"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "unknown kind never blocks a load"
        assert any("kind" in w for w in result.warnings), "warning names kind"

    def test_enum_without_values_fails(self):
        # Arrange — schema requires `values` when kind=enum.
        profile = {"commands": {}, "types": {"x": {"kind": "enum"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "enum without values is invalid"

    def test_int_range_without_min_fails(self):
        # Arrange
        profile = {"commands": {}, "types": {"x": {"kind": "int_range", "max": 10}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "int_range requires both min and max"

    def test_pattern_without_regex_fails(self):
        # Arrange
        profile = {"commands": {}, "types": {"x": {"kind": "pattern"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "pattern requires regex"

    def test_str_length_with_only_max_len_validates(self):
        # Arrange — anyOf: at least one of min_len / max_len.
        profile = {"commands": {}, "types": {
            "n": {"kind": "str_length", "max_len": 16},
        }}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, (
            f"str_length with only max_len is valid; errors: {result.errors}"
        )

    def test_str_length_with_neither_bound_fails(self):
        # Arrange
        profile = {"commands": {}, "types": {"n": {"kind": "str_length"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "str_length requires at least one bound"

    def test_typed_arg_type_accepts_custom_name(self):
        # Arrange — relaxed enum lets typed_arg.type reference a custom type.
        profile = {
            "types": {"speed": {"kind": "enum", "values": ["slow", "fast"]}},
            "commands": {
                "MOVE": {
                    "help": "Move at a speed.",
                    "typed_args": [
                        {"name": "rate", "type": "speed", "required": True}
                    ],
                }
            },
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, (
            f"custom type name allowed; errors: {result.errors}"
        )

    def test_typed_arg_type_still_accepts_builtins(self):
        # Arrange — backward compat: existing profiles use builtin names.
        profile = {
            "commands": {
                "AT": {
                    "help": "Test.",
                    "typed_args": [
                        {"name": "pin", "type": "str", "required": True}
                    ],
                }
            }
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "builtins still accepted in typed_arg.type"


# ── precedence comparator ───────────────────────────────────────────────────


class TestPrecedence:
    def test_higher_revision_wins(self):
        # Arrange
        a = {"profile_revision": "2.0.0"}
        b = {"profile_revision": "1.5.0"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == 1, "a > b when a has higher rev"

    def test_lower_revision_loses(self):
        # Arrange
        a = {"profile_revision": "0.1.0"}
        b = {"profile_revision": "1.0.0"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == -1, "a < b when b has higher rev"

    def test_equal_revision_newer_date_wins(self):
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-04-01"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == 1, "newer date wins on revision tie"

    def test_equal_revision_older_date_loses(self):
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-04-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == -1, "older date loses on revision tie"

    def test_equal_revision_and_date_device_wins(self):
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        # Act
        actual = precedence(a, b, a_source="hand", b_source="device")
        # Assert
        assert actual == -1, "device-fetched wins on full tie"

    def test_equal_revision_and_date_no_source_hint_returns_zero(self):
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == 0, "no tiebreaker = equal"

    def test_missing_fields_treated_as_epoch(self):
        # Arrange
        a = {}
        b = {"profile_revision": "0.0.1"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == -1, "missing rev loses to any versioned candidate"


# ── parse_response: none ────────────────────────────────────────────────────


class TestParseNone:
    def test_none_returns_none(self):
        # Arrange / Act
        actual = parse_response("anything", "none")
        # Assert
        assert actual is None, "none format always returns None"


# ── parse_response: literal ─────────────────────────────────────────────────


class TestParseLiteral:
    def test_literal_match(self):
        # Arrange / Act
        actual = parse_response("OK", "literal", pattern="OK")
        # Assert
        assert actual == "OK", "exact match returns the text"

    def test_literal_match_with_whitespace_strip(self):
        # Arrange / Act
        actual = parse_response("  OK  \n", "literal", pattern="OK")
        # Assert
        assert actual == "OK", "strips whitespace before compare"

    def test_literal_no_match_returns_none(self):
        # Arrange / Act
        actual = parse_response("ERROR", "literal", pattern="OK")
        # Assert
        assert actual is None, "non-match returns None"


# ── parse_response: regex ───────────────────────────────────────────────────


class TestParseRegex:
    def test_regex_named_groups_become_typed_dict(self):
        # Arrange
        text = "VER=1.4.7"
        # Act
        actual = parse_response(
            text, "regex",
            pattern=r"VER=(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)",
            types={"major": "int", "minor": "int", "patch": "int"},
        )
        # Assert
        assert actual == {"major": 1, "minor": 4, "patch": 7}, (
            "named groups coerced to typed dict"
        )

    def test_regex_no_match_returns_none(self):
        # Arrange / Act
        actual = parse_response("garbage", "regex", pattern=r"^OK$")
        # Assert
        assert actual is None, "no match returns None"

    def test_regex_no_groups_returns_match_text(self):
        # Arrange / Act
        actual = parse_response("hello world", "regex", pattern=r"\w+")
        # Assert
        assert actual == "hello", "no-group regex returns matched substring"

    def test_regex_unnamed_groups_returns_list(self):
        # Arrange / Act
        actual = parse_response("a=1 b=2", "regex", pattern=r"(\w+)=(\d+)")
        # Assert
        assert actual == ["a", "1"], "unnamed groups returned as list"

    def test_regex_invalid_pattern_returns_none(self):
        # Arrange — unbalanced paren
        actual = parse_response("anything", "regex", pattern=r"(unclosed")
        # Assert
        assert actual is None, "invalid pattern fails gracefully"

    def test_regex_failed_int_coercion_returns_raw(self):
        # Arrange / Act
        actual = parse_response(
            "X=hello", "regex",
            pattern=r"X=(?P<v>\w+)",
            types={"v": "int"},
        )
        # Assert
        assert actual == {"v": "hello"}, (
            "failed coercion preserves raw string, doesn't raise"
        )


# ── parse_response: lines ───────────────────────────────────────────────────


class TestParseLines:
    def test_lines_split_only(self):
        # Arrange
        text = "line1\nline2\nline3"
        # Act
        actual = parse_response(text, "lines")
        # Assert
        assert actual == ["line1", "line2", "line3"], "splits on newlines"

    def test_lines_with_terminator_truncates(self):
        # Arrange
        text = "a\nb\nEND\nc"
        # Act
        actual = parse_response(text, "lines", terminator=r"^END$")
        # Assert
        assert actual == ["a", "b"], "stops at terminator (exclusive)"

    def test_lines_with_line_pattern_returns_typed_dicts(self):
        # Arrange
        text = "CH1 V=12.5 I=0.500 ON\nCH2 V=5.0 I=0.100 OFF\nEND"
        # Act
        actual = parse_response(
            text, "lines",
            terminator=r"^END$",
            line_pattern=(
                r"CH(?P<id>\d+)\s+V=(?P<v>-?\d+\.\d+)"
                r"\s+I=(?P<i>-?\d+\.\d+)\s+(?P<state>ON|OFF)"
            ),
            line_types={"id": "int", "v": "float", "i": "float", "state": "str"},
        )
        expected = [
            {"id": 1, "v": 12.5, "i": 0.5, "state": "ON"},
            {"id": 2, "v": 5.0, "i": 0.1, "state": "OFF"},
        ]
        # Assert
        assert actual == expected, "lines parsed into typed dicts"

    def test_lines_filter_drops_non_matching(self):
        # Arrange
        text = "header\ndata1\ndata2\nfooter"
        # Act — filter pattern keeps lines matching "data"
        actual = parse_response(text, "lines", pattern=r"^data")
        # Assert
        assert actual == ["data1", "data2"], "filter drops non-matching lines"


# ── parse_response: json ────────────────────────────────────────────────────


class TestParseJson:
    def test_json_object_parses(self):
        # Arrange / Act
        actual = parse_response('{"temp": 23.5}', "json")
        # Assert
        assert actual == {"temp": 23.5}, "JSON object parsed"

    def test_json_array_parses(self):
        # Arrange / Act
        actual = parse_response("[1, 2, 3]", "json")
        # Assert
        assert actual == [1, 2, 3], "JSON array parsed"

    def test_json_invalid_returns_none(self):
        # Arrange / Act
        actual = parse_response("not json {", "json")
        # Assert
        assert actual is None, "invalid JSON returns None, doesn't raise"


# ── parse_response: text ────────────────────────────────────────────────────


class TestParseText:
    def test_text_returns_raw_string(self):
        # Arrange
        text = "line one\nline two"
        # Act
        actual = parse_response(text, "text")
        # Assert
        assert actual == text, "text format hands back the response unchanged"

    def test_text_needs_no_pattern(self):
        # Arrange / Act
        actual = parse_response("hello", "text", pattern="ignored")
        # Assert
        assert actual == "hello", "pattern is irrelevant to text format"


# ── unknown format ──────────────────────────────────────────────────────────


class TestUnknownFormat:
    def test_unknown_format_degrades_to_text(self):
        # Arrange / Act -- compatibility policy: an unrecognized format
        # from a newer spec revision returns the raw string, not a
        # failure, so the data stays usable on this host.
        actual = parse_response("anything", "yaml")
        # Assert
        assert actual == "anything", "unknown format degrades to text"


# ── forward-compat lint warnings ────────────────────────────────────────────


class TestCompatibilityWarnings:
    def test_clean_profile_has_no_warnings(self):
        # Arrange
        profile = {
            "profile_version": 2,
            "commands": {"AT": {"help": "test", "safety": "readonly",
                                "response": {"format": "text"}}},
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "canonical profile validates"
        assert result.warnings == [], "no warnings on a fully-canonical profile"

    def test_unknown_command_field_warns(self):
        # Arrange
        profile = {"commands": {"AT": {"help": "h", "retry_count": 3}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "unknown field is tolerated"
        assert any(
            "retry_count" in w and "commands/AT" in w for w in result.warnings
        ), "warning names the field and its location"

    def test_unknown_root_field_warns(self):
        # Arrange
        profile = {"profile_version": 2, "commmands": {}}
        # Act
        result = validate_profile(profile)
        # Assert -- the classic typo: warned, not silently ignored
        assert result.ok is True, "unknown root field tolerated"
        assert any("commmands" in w for w in result.warnings), (
            "root-level typo surfaces as a warning"
        )

    def test_extension_keys_never_warn(self):
        # Arrange -- $schema convention plus both extension prefixes,
        # at root and command level.
        profile = {
            "$schema": "https://termapy.org/profile.schema.json",
            "x_vendor_blob": {"anything": True},
            "commands": {"AT": {"help": "h", "x-notes": "internal",
                                "x_trace_id": 7}},
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "extension keys are valid"
        assert result.warnings == [], "extension namespace never warns"

    def test_unknown_coercion_name_warns(self):
        # Arrange
        profile = {
            "commands": {"V": {"help": "h", "response": {
                "format": "regex", "pattern": "(?P<v>\\d+)",
                "types": {"v": "decimal128"},
            }}}
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "unknown coercion tolerated"
        assert any(
            "decimal128" in w and "str" in w for w in result.warnings
        ), "warning names the coercion and the degrade-to-str rule"

    def test_unit_metadata_is_canonical_not_warned(self):
        # Arrange -- unit / units / line_units are first-class fields.
        profile = {
            "commands": {"volt": {
                "help": "Read voltage.",
                "send_template": "volt {ch}",
                "typed_args": [{"name": "ch", "type": "str",
                                "unit": "channel#"}],
                "response": {"format": "regex",
                             "pattern": "(?P<mv>-?\\d+)",
                             "types": {"mv": "int"},
                             "units": {"mv": "mV"}},
            }}
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, f"unit metadata validates: {result.errors}"
        assert result.warnings == [], "unit fields are canonical, not warned"

    def test_builtin_validator_is_structural_only(self):
        # Arrange -- direct call to the no-jsonschema fallback: vocab
        # deviations must NOT be errors there either (they are warnings
        # via collect_warnings, attached by validate_profile).
        from termapy.profile.loader import _builtin_validate

        profile = {
            "commands": {"X": {"help": "h", "safety": "wild",
                               "response": {"format": "yaml"}}}
        }
        # Act
        result = _builtin_validate(profile)
        # Assert
        assert result.ok is True, (
            "builtin fallback no longer hard-fails on vocabulary; "
            "structure only"
        )


# ── Profile dataclass wrapper ───────────────────────────────────────────────


class TestProfileDataclass:
    def test_load_returns_profile_with_path(self):
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert profile.path == path, "path attached"
        assert profile.source == "hand", "default source"

    def test_revision_accessor(self):
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert profile.revision == "1.0.0", "revision exposed"

    def test_date_accessor(self):
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert profile.date == "2026-05-01", "date exposed"

    def test_commands_accessor_returns_dict(self):
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert "AT" in profile.commands, "AT command present"

    def test_save_uses_attached_path(self, tmp_path):
        # Arrange
        path = tmp_path / "p.json"
        profile = Profile(data={"profile_version": 2}, path=path)
        # Act
        profile.save()
        # Assert
        assert path.exists(), "saved to attached path"

    def test_save_no_path_raises(self):
        # Arrange
        profile = Profile(data={"profile_version": 2})
        # Act / Assert
        with pytest.raises(ValueError):
            profile.save()


# ── --validate-profile CLI smoke ────────────────────────────────────────────


@pytest.mark.slow  # subprocess-spawning --validate-profile smoke tests
class TestValidateProfileCli:
    def test_cli_validates_reference_profile(self, tmp_path):
        # Arrange
        profile_path = FIXTURES / "at_modem.profile.json"
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--validate-profile", str(profile_path)],
            capture_output=True,
            text=True,
        )
        # Assert
        assert result.returncode == 0, (
            f"valid profile exits 0; stderr: {result.stderr}"
        )
        assert "OK" in result.stdout, "OK printed on success"

    def test_cli_rejects_invalid_profile(self, tmp_path):
        # Arrange -- structural violation (command without help): still
        # a hard error under the compatibility policy.
        bad = tmp_path / "bad.profile.json"
        bad.write_text(
            json.dumps({
                "profile_version": 2,
                "commands": {"X": {"safety": "readonly"}},
            }),
            encoding="utf-8",
        )
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--validate-profile", str(bad)],
            capture_output=True,
            text=True,
        )
        # Assert
        assert result.returncode == 1, "invalid profile exits 1"
        assert "FAIL" in result.stderr, "FAIL printed on rejection"
        assert "help" in result.stderr, "specific field cited in error"

    def test_cli_warns_but_passes_on_unknown_vocabulary(self, tmp_path):
        # Arrange -- unknown safety tier: degrades per the compat
        # policy, so the CLI validator warns on stderr and exits 0.
        newer = tmp_path / "newer.profile.json"
        newer.write_text(
            json.dumps({
                "profile_version": 2,
                "commands": {"X": {"help": "h", "safety": "made-up"}},
            }),
            encoding="utf-8",
        )
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--validate-profile", str(newer)],
            capture_output=True,
            text=True,
        )
        # Assert
        assert result.returncode == 0, "warnings never fail validation"
        assert "OK" in result.stdout, "OK verdict on stdout"
        assert "warning" in result.stderr, "warning surfaced on stderr"
        assert "safety" in result.stderr, "warning names the field"

    def test_cli_missing_file_exits_1(self):
        # Arrange
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy",
             "--validate-profile", "/no/such/file.json"],
            capture_output=True,
            text=True,
        )
        # Assert
        assert result.returncode == 1, "missing file exits 1"
        assert "not found" in result.stderr, "useful error message"
