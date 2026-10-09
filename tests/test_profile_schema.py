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
    SCHEMA_PATH,
    Profile,
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
        """The profile JSON Schema ships at SCHEMA_PATH; a packaging slip would drop it."""
        # Arrange / Act
        actual = SCHEMA_PATH.exists()
        # Assert
        assert actual is True, f"schema must exist at {SCHEMA_PATH}"

    def test_schema_file_is_valid_json(self):
        """The schema parses as a JSON object that declares its $schema dialect."""
        # Arrange / Act
        actual = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Assert
        assert isinstance(actual, dict), "schema is a JSON object"
        assert "$schema" in actual, "must declare $schema"

    def test_schema_top_level_has_expected_blocks(self):
        """The schema still defines the core blocks, from profile_version through commands."""
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
        """Each shipped reference profile loads as a dict with a commands block."""
        # Arrange
        path = FIXTURES / name
        # Act
        actual = load_profile(path)
        # Assert
        assert isinstance(actual, dict), f"{name} loads as dict"
        assert "commands" in actual, f"{name} has commands"

    def test_reference_profile_validates(self, name):
        """Each reference profile validates with no errors -- they are the copy-from examples."""
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
        """Save then load returns an equal profile, so saving drops nothing from a reference."""
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
        """A .json extension selects the JSON parser."""
        # Arrange
        path = tmp_path / "p.json"
        path.write_text('{"profile_version": 2, "commands": {}}', encoding="utf-8")
        # Act
        actual = load_profile(path)
        # Assert
        assert actual["profile_version"] == 2, "json loaded"

    def test_load_toml_by_extension(self, tmp_path):
        """A .toml extension selects the TOML parser, nested command tables included."""
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
        """With no extension, TOML content loads -- TOML is tried before JSON."""
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
        """With no extension, JSON content still loads once the TOML attempt fails."""
        # Arrange — JSON content with no extension
        path = tmp_path / "p"
        path.write_text('{"profile_version": 2}', encoding="utf-8")
        # Act
        actual = load_profile(path)
        # Assert
        assert actual["profile_version"] == 2, "fallback to json works"

    def test_load_invalid_content_raises(self, tmp_path):
        """Content that parses as neither JSON nor TOML raises instead of loading."""
        # Arrange — neither JSON nor TOML
        path = tmp_path / "p.json"
        path.write_text("this is not valid {", encoding="utf-8")
        # Act / Assert
        with pytest.raises((ValueError, json.JSONDecodeError)):
            load_profile(path)


# ── save always JSON ────────────────────────────────────────────────────────


class TestSaveAlwaysJson:
    def test_save_writes_json_for_json_path(self, tmp_path):
        """Saving to a .json path writes JSON that parses back to the same dict."""
        # Arrange
        profile = {"profile_version": 2, "commands": {}}
        out = tmp_path / "p.json"
        # Act
        save_profile(profile, out)
        # Assert
        assert json.loads(out.read_text("utf-8")) == profile, "saved as parseable json"

    def test_save_writes_json_even_for_toml_path(self, tmp_path):
        """A .toml target still gets JSON content -- save ignores the extension by spec."""
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
        """An empty profile is structurally valid, since every top-level field is optional."""
        # Arrange / Act
        result = validate_profile({})
        # Assert — empty profile is structurally valid (all fields optional)
        assert result.ok is True, "empty dict has no errors"

    def test_non_dict_root_fails(self):
        """A root that is not an object fails validation with at least one error."""
        # Arrange / Act
        result = validate_profile("not a dict")  # type: ignore[arg-type]
        # Assert
        assert result.ok is False, "non-dict root rejected"
        assert len(result.errors) >= 1, "at least one error"

    def test_invalid_profile_version_fails(self):
        """A profile_version outside 1 and 2 is an error, and the message names the field."""
        # Arrange
        profile = {"profile_version": 99}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "version 99 is not in {1,2}"
        assert any("profile_version" in error for error in result.errors), (
            "error mentions profile_version"
        )

    def test_transport_block_rejected(self):
        """The retired transport block is a hard error naming it, so authors know to remove it."""
        # Arrange -- transport block retired in v18 cfg / drop-transport
        # refactor; wire-level settings live in cfg now.
        profile = {"profile_version": 2, "transport": {"baud_rate": 9600}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "transport block must fail validation"
        assert any("transport" in error for error in result.errors), (
            "error mentions transport so authors can find what to remove"
        )

    def test_command_missing_help_fails(self):
        """A command without help is an error naming both the command and the missing field."""
        # Arrange
        profile = {"commands": {"BAD": {}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "command requires help"
        assert any("BAD" in error and "help" in error for error in result.errors), (
            "error mentions command name and field"
        )

    def test_unknown_safety_warns_but_loads(self):
        """An unknown safety tier warns that it degrades to destructive, but never blocks a load."""
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
        """An unknown response format warns that it degrades to text, but never blocks a load."""
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
        """A command carrying only help is a complete profile -- help is the one required field."""
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
        """An empty types map is valid; declaring no custom types is not an error."""
        # Arrange
        profile = {"commands": {}, "types": {}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "empty types block is valid"

    def test_all_six_kinds_validate(self):
        """One minimal well-formed type of each of the six kinds validates cleanly."""
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
        """An unknown kind loads with a warning; args of that type refuse later at dispatch."""
        # Arrange -- compatibility policy: unknown kind loads with a
        # warning; args of that type refuse at dispatch (fail-closed).
        profile = {"commands": {}, "types": {"x": {"kind": "totally_made_up"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "unknown kind never blocks a load"
        assert any("kind" in warning for warning in result.warnings), (
            "warning names kind"
        )

    def test_enum_without_values_fails(self):
        """An enum type with no values list is rejected."""
        # Arrange — schema requires `values` when kind=enum.
        profile = {"commands": {}, "types": {"x": {"kind": "enum"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "enum without values is invalid"

    def test_int_range_without_min_fails(self):
        """An int_range with max but no min is rejected; both bounds are required."""
        # Arrange
        profile = {"commands": {}, "types": {"x": {"kind": "int_range", "max": 10}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "int_range requires both min and max"

    def test_pattern_without_regex_fails(self):
        """A pattern type with no regex is rejected."""
        # Arrange
        profile = {"commands": {}, "types": {"x": {"kind": "pattern"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "pattern requires regex"

    def test_str_length_with_only_max_len_validates(self):
        """A str_length needs only one bound; max_len alone is enough."""
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
        """A str_length with neither min_len nor max_len is rejected."""
        # Arrange
        profile = {"commands": {}, "types": {"n": {"kind": "str_length"}}}
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is False, "str_length requires at least one bound"

    def test_typed_arg_type_accepts_custom_name(self):
        """A typed_arg type may name a profile-local type, not only a builtin."""
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
        """Builtin type names keep working in typed_args now that custom names are allowed."""
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
        """The candidate with the higher profile_revision ranks first (returns 1)."""
        # Arrange
        a = {"profile_revision": "2.0.0"}
        b = {"profile_revision": "1.5.0"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == 1, "a > b when a has higher rev"

    def test_lower_revision_loses(self):
        """The comparison is symmetric: a lower profile_revision returns -1."""
        # Arrange
        a = {"profile_revision": "0.1.0"}
        b = {"profile_revision": "1.0.0"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == -1, "a < b when b has higher rev"

    def test_equal_revision_newer_date_wins(self):
        """On a revision tie, the newer profile_date wins."""
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-04-01"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == 1, "newer date wins on revision tie"

    def test_equal_revision_older_date_loses(self):
        """On a revision tie, the older profile_date loses (returns -1)."""
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-04-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == -1, "older date loses on revision tie"

    def test_equal_revision_and_date_device_wins(self):
        """On a full revision and date tie, a device-fetched copy beats a hand-written one."""
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        # Act
        actual = precedence(a, b, a_source="hand", b_source="device")
        # Assert
        assert actual == -1, "device-fetched wins on full tie"

    def test_equal_revision_and_date_no_source_hint_returns_zero(self):
        """On a full tie with no source hints, the two profiles rank equal (0)."""
        # Arrange
        a = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        b = {"profile_revision": "1.0.0", "profile_date": "2026-05-01"}
        # Act
        actual = precedence(a, b)
        # Assert
        assert actual == 0, "no tiebreaker = equal"

    def test_missing_fields_treated_as_epoch(self):
        """A profile with no revision or date loses to any versioned candidate."""
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
        """The none format discards the response, whatever its text."""
        # Arrange / Act
        actual = parse_response("anything", "none")
        # Assert
        assert actual is None, "none format always returns None"


# ── parse_response: literal ─────────────────────────────────────────────────


class TestParseLiteral:
    def test_literal_match(self):
        """An exact literal match returns the matched text."""
        # Arrange / Act
        actual = parse_response("OK", "literal", pattern="OK")
        # Assert
        assert actual == "OK", "exact match returns the text"

    def test_literal_match_with_whitespace_strip(self):
        """Surrounding whitespace and the line ending are stripped before the literal compare."""
        # Arrange / Act
        actual = parse_response("  OK  \n", "literal", pattern="OK")
        # Assert
        assert actual == "OK", "strips whitespace before compare"

    def test_literal_no_match_returns_none(self):
        """A response that differs from the literal yields None, not an error."""
        # Arrange / Act
        actual = parse_response("ERROR", "literal", pattern="OK")
        # Assert
        assert actual is None, "non-match returns None"


# ── parse_response: regex ───────────────────────────────────────────────────


class TestParseRegex:
    def test_regex_named_groups_become_typed_dict(self):
        """Named groups become dict keys, each coerced by its declared type."""
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
        """A regex that does not match yields None."""
        # Arrange / Act
        actual = parse_response("garbage", "regex", pattern=r"^OK$")
        # Assert
        assert actual is None, "no match returns None"

    def test_regex_no_groups_returns_match_text(self):
        """A group-free regex returns the first matched substring, not the whole response."""
        # Arrange / Act
        actual = parse_response("hello world", "regex", pattern=r"\w+")
        # Assert
        assert actual == "hello", "no-group regex returns matched substring"

    def test_regex_unnamed_groups_returns_list(self):
        """Unnamed groups come back as a list of strings from the first match."""
        # Arrange / Act
        actual = parse_response("a=1 b=2", "regex", pattern=r"(\w+)=(\d+)")
        # Assert
        assert actual == ["a", "1"], "unnamed groups returned as list"

    def test_regex_invalid_pattern_returns_none(self):
        """A malformed pattern yields None instead of raising re.error into the caller."""
        # Arrange — unbalanced paren
        actual = parse_response("anything", "regex", pattern=r"(unclosed")
        # Assert
        assert actual is None, "invalid pattern fails gracefully"

    def test_regex_failed_int_coercion_returns_raw(self):
        """A group that fails int coercion keeps its raw string instead of raising."""
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
        """With no options, the lines format just splits the response on newlines."""
        # Arrange
        text = "line1\nline2\nline3"
        # Act
        actual = parse_response(text, "lines")
        # Assert
        assert actual == ["line1", "line2", "line3"], "splits on newlines"

    def test_lines_with_terminator_truncates(self):
        """The terminator line ends the list and is excluded, as is everything after it."""
        # Arrange
        text = "a\nb\nEND\nc"
        # Act
        actual = parse_response(text, "lines", terminator=r"^END$")
        # Assert
        assert actual == ["a", "b"], "stops at terminator (exclusive)"

    def test_lines_with_line_pattern_returns_typed_dicts(self):
        """With line_pattern and line_types, each row becomes a typed dict -- a table read."""
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
        """A pattern on the lines format filters, keeping only lines that match."""
        # Arrange
        text = "header\ndata1\ndata2\nfooter"
        # Act — filter pattern keeps lines matching "data"
        actual = parse_response(text, "lines", pattern=r"^data")
        # Assert
        assert actual == ["data1", "data2"], "filter drops non-matching lines"


# ── parse_response: json ────────────────────────────────────────────────────


class TestParseJson:
    def test_json_object_parses(self):
        """A JSON object response comes back as a dict."""
        # Arrange / Act
        actual = parse_response('{"temp": 23.5}', "json")
        # Assert
        assert actual == {"temp": 23.5}, "JSON object parsed"

    def test_json_array_parses(self):
        """A JSON array response comes back as a list; the root need not be an object."""
        # Arrange / Act
        actual = parse_response("[1, 2, 3]", "json")
        # Assert
        assert actual == [1, 2, 3], "JSON array parsed"

    def test_json_invalid_returns_none(self):
        """Malformed JSON yields None instead of raising."""
        # Arrange / Act
        actual = parse_response("not json {", "json")
        # Assert
        assert actual is None, "invalid JSON returns None, doesn't raise"


# ── parse_response: text ────────────────────────────────────────────────────


class TestParseText:
    def test_text_returns_raw_string(self):
        """The text format returns the response unchanged, newlines included."""
        # Arrange
        text = "line one\nline two"
        # Act
        actual = parse_response(text, "text")
        # Assert
        assert actual == text, "text format hands back the response unchanged"

    def test_text_needs_no_pattern(self):
        """A pattern passed with the text format is ignored, not applied."""
        # Arrange / Act
        actual = parse_response("hello", "text", pattern="ignored")
        # Assert
        assert actual == "hello", "pattern is irrelevant to text format"


# ── unknown format ──────────────────────────────────────────────────────────


class TestUnknownFormat:
    def test_unknown_format_degrades_to_text(self):
        """An unrecognized format from a newer spec returns the raw text so data stays usable."""
        # Arrange / Act -- compatibility policy: an unrecognized format
        # from a newer spec revision returns the raw string, not a
        # failure, so the data stays usable on this host.
        actual = parse_response("anything", "yaml")
        # Assert
        assert actual == "anything", "unknown format degrades to text"


# ── forward-compat lint warnings ────────────────────────────────────────────


class TestCompatibilityWarnings:
    def test_clean_profile_has_no_warnings(self):
        """A fully canonical profile draws zero warnings, so any warning means something."""
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
        """An unknown command field loads, and the warning names both field and command path."""
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
        """A root-level typo such as commmands warns instead of being silently ignored."""
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
        """The $schema key and x_ / x- extension keys never warn, at root or command level."""
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
        """An unknown coercion type loads, with a warning that the value degrades to str."""
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
        """Unit metadata (typed_arg unit, response units) is canonical and draws no warnings."""
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

    def test_misspelled_field_warning_suggests_correction(self):
        """A misspelled safety key silently un-gates a command, so its warning names the fix."""
        # Arrange -- the dangerous typo class: a misspelled `safety`
        # key silently un-gates a command, so the warning must point
        # straight at the fix.
        profile = {
            "commands": {"X": {"help": "h", "saftey": "destructive"}}
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, "typo'd field still loads (warn, not fail)"
        assert any(
            "saftey" in w and "did you mean 'safety'" in w
            for w in result.warnings
        ), "warning carries a did-you-mean suggestion"

    def test_misspelled_value_warning_suggests_correction(self):
        """A near-miss safety value gets a did-you-mean suggestion too, not only field names."""
        # Arrange
        profile = {
            "commands": {"X": {"help": "h", "safety": "destructiv"}}
        }
        # Act
        result = validate_profile(profile)
        # Assert
        assert any(
            "destructiv" in w and "did you mean 'destructive'" in w
            for w in result.warnings
        ), "value typos get suggestions too"

    def test_unrelated_unknown_field_has_no_suggestion(self):
        """A novel field with no close match warns without a spurious did-you-mean."""
        # Arrange -- a genuinely novel field (plausibly from a newer
        # spec) should not get a spurious did-you-mean.
        profile = {"commands": {"X": {"help": "h", "telemetry_channel": 2}}}
        # Act
        result = validate_profile(profile)
        # Assert
        warning = next(w for w in result.warnings if "telemetry_channel" in w)
        assert "did you mean" not in warning, (
            "no suggestion when nothing is edit-distance close"
        )

    def test_builtin_validator_is_structural_only(self):
        """Without jsonschema, the fallback checks structure only; vocabulary never fails."""
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
        """Profile.load keeps the file path and defaults the source to hand."""
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert profile.path == path, "path attached"
        assert profile.source == "hand", "default source"

    def test_revision_accessor(self):
        """The revision property reads profile_revision from the loaded data."""
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert profile.revision == "1.0.0", "revision exposed"

    def test_date_accessor(self):
        """The date property reads profile_date from the loaded data."""
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert profile.date == "2026-05-01", "date exposed"

    def test_commands_accessor_returns_dict(self):
        """The commands property exposes the commands block keyed by command name."""
        # Arrange
        path = FIXTURES / "at_modem.profile.json"
        # Act
        profile = Profile.load(path)
        # Assert
        assert "AT" in profile.commands, "AT command present"

    def test_save_uses_attached_path(self, tmp_path):
        """A bare save() writes to the path the Profile was built with."""
        # Arrange
        path = tmp_path / "p.json"
        profile = Profile(data={"profile_version": 2}, path=path)
        # Act
        profile.save()
        # Assert
        assert path.exists(), "saved to attached path"

    def test_save_no_path_raises(self):
        """Saving a Profile with no attached path raises instead of guessing a location."""
        # Arrange
        profile = Profile(data={"profile_version": 2})
        # Act / Assert
        with pytest.raises(ValueError):
            profile.save()


# ── --validate-profile CLI smoke ────────────────────────────────────────────


@pytest.mark.slow  # subprocess-spawning --validate-profile smoke tests
class TestValidateProfileCli:
    def test_cli_validates_reference_profile(self, tmp_path):
        """The --validate-profile CLI exits 0 and prints OK for a known-good profile."""
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
        """A structural error still exits 1, with FAIL and the offending field on stderr."""
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
        """Unknown vocabulary exits 0 with OK on stdout and the warning on stderr."""
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

    def test_cli_strict_escalates_warnings_to_failure(self, tmp_path):
        """Under --strict a warning alone exits 1, and the did-you-mean reaches the CI log."""
        # Arrange -- same warning-only profile as above; --strict is
        # the CI mode where a typo must stop the build.
        newer = tmp_path / "newer.profile.json"
        newer.write_text(
            json.dumps({
                "profile_version": 2,
                "commands": {"X": {"help": "h", "saftey": "destructive"}},
            }),
            encoding="utf-8",
        )
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy",
             "--validate-profile", str(newer), "--strict"],
            capture_output=True,
            text=True,
        )
        # Assert
        assert result.returncode == 1, "--strict turns warnings into failure"
        assert "FAIL" in result.stderr, "FAIL verdict under --strict"
        assert "did you mean 'safety'" in result.stderr, (
            "suggestion surfaced where CI logs show it"
        )

    def test_cli_strict_passes_clean_profile(self, tmp_path):
        """A warning-free profile still passes under --strict; strict only escalates warnings."""
        # Arrange
        clean = tmp_path / "clean.profile.json"
        clean.write_text(
            json.dumps({
                "profile_version": 2,
                "commands": {"X": {"help": "h", "safety": "readonly"}},
            }),
            encoding="utf-8",
        )
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy",
             "--validate-profile", str(clean), "--strict"],
            capture_output=True,
            text=True,
        )
        # Assert
        assert result.returncode == 0, "--strict passes a warning-free profile"
        assert "OK" in result.stdout, "OK verdict"

    def test_cli_missing_file_exits_1(self):
        """A missing profile file exits 1 with a not-found message on stderr."""
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
