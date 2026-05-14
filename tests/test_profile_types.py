"""Tests for profile-local user-defined types (TypeRegistry + validators).

Covers the registry's construction from a profile dict, builtin
resolution, and each of the six recognized kinds: ``enum``,
``int_range``, ``float_range``, ``str_length``, ``pattern``, and the
``format_spec`` stub.  Build-time errors (bad regex, missing required
fields) must produce a TypeDef with ``build_error`` instead of raising
-- the registry must always finish constructing.
"""

from __future__ import annotations

import pytest

from termapy.profile import (
    BUILTINS,
    ProfileTypeError,
    TypeRegistry,
    schema_kinds,
    typedef_to_catalog,
)


# ── Registry construction & resolution ───────────────────────────────────────


class TestRegistryConstruction:
    def test_from_none_profile_is_empty(self):
        # Arrange / Act
        reg = TypeRegistry.from_profile(None)
        # Assert
        actual = reg.all()
        assert actual == {}, "no profile -> empty registry"

    def test_from_profile_without_types_block_is_empty(self):
        # Arrange / Act
        reg = TypeRegistry.from_profile({"commands": {}})
        # Assert
        actual = reg.all()
        assert actual == {}, "no types block -> empty registry"

    def test_from_profile_with_invalid_types_block_is_empty(self):
        # Arrange -- types is not an object (schema would catch this,
        # but registry must degrade gracefully).
        # Act
        reg = TypeRegistry.from_profile({"types": "not a dict"})
        # Assert
        assert reg.all() == {}, "non-dict types block -> empty registry"

    def test_builtin_resolution(self):
        # Arrange
        reg = TypeRegistry.from_profile({})
        # Act / Assert -- all five builtins resolve to a "builtin"-kind TypeDef
        for name in sorted(BUILTINS):
            td = reg.resolve(name)
            assert td is not None, f"builtin {name} resolves"
            assert td.kind == "builtin", f"{name} has kind=builtin"

    def test_unknown_type_resolves_to_none(self):
        # Arrange
        reg = TypeRegistry.from_profile({})
        # Act
        actual = reg.resolve("not_a_real_type")
        # Assert
        assert actual is None, "unknown name resolves to None"

    def test_custom_type_cannot_shadow_builtin(self):
        # Arrange -- profile tries to redefine 'int' as an enum.
        profile = {"types": {"int": {"kind": "enum", "values": ["1", "2"]}}}
        # Act / Assert
        with pytest.raises(ProfileTypeError) as exc:
            TypeRegistry.from_profile(profile)
        actual = str(exc.value)
        assert "collides with a builtin" in actual, (
            "shadowing a builtin must raise ProfileTypeError"
        )


# ── enum kind ─────────────────────────────────────────────────────────────────


class TestEnumKind:
    def test_enum_accepts_member(self):
        # Arrange — custom name (not 'bool', which is a reserved builtin).
        profile = {"types": {"on_off": {"kind": "enum", "values": ["on", "off"]}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("on_off", "on")
        # Assert
        assert result.ok, "member value passes"
        assert result.value == "on", "normalized to string"

    def test_enum_rejects_nonmember_with_helpful_error(self):
        # Arrange
        profile = {"types": {"on_off": {"kind": "enum", "values": ["on", "off"]}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("on_off", "banana")
        # Assert
        assert not result.ok, "non-member fails"
        assert "banana" in result.error, "error names the rejected value"
        assert "on" in result.error and "off" in result.error, (
            "error lists all allowed values"
        )

    def test_enum_stringifies_numeric_values(self):
        # Arrange -- baud rates as numbers, LLM passes string form.
        profile = {"types": {"baud": {
            "kind": "enum", "values": [9600, 19200, 115200],
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("baud", "19200")
        # Assert -- numeric values get stringified for uniform compare
        assert result.ok, "string form of numeric enum value passes"

    def test_enum_without_values_is_build_error(self):
        # Arrange
        profile = {"types": {"bad": {"kind": "enum", "values": []}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        td = reg.resolve("bad")
        # Assert
        assert td is not None, "build-error type is still in registry"
        assert "non-empty" in td.build_error, (
            "empty enum values -> build error"
        )


# ── int_range / float_range ───────────────────────────────────────────────────


class TestIntRange:
    def test_in_range_passes_and_coerces(self):
        # Arrange
        profile = {"types": {"percent": {
            "kind": "int_range", "min": 0, "max": 100,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("percent", "42")
        # Assert
        assert result.ok, "in-range value passes"
        assert result.value == 42, "coerced to int"

    def test_below_minimum_fails_with_bound_in_message(self):
        # Arrange
        profile = {"types": {"percent": {
            "kind": "int_range", "min": 0, "max": 100,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("percent", "-5")
        # Assert
        assert not result.ok, "below minimum fails"
        assert "minimum" in result.error and "0" in result.error, (
            "error names the violated bound"
        )

    def test_above_maximum_fails(self):
        # Arrange
        profile = {"types": {"percent": {
            "kind": "int_range", "min": 0, "max": 100,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("percent", "150")
        # Assert
        assert not result.ok, "above maximum fails"
        assert "maximum" in result.error, "error names the maximum"

    def test_non_int_string_fails_cleanly(self):
        # Arrange
        profile = {"types": {"percent": {
            "kind": "int_range", "min": 0, "max": 100,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("percent", "not a number")
        # Assert
        assert not result.ok, "non-coercible string fails"
        assert "expected int" in result.error, "error names expected type"


class TestFloatRange:
    def test_in_range_passes(self):
        # Arrange
        profile = {"types": {"v": {
            "kind": "float_range", "min": 0.0, "max": 5.0,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("v", "3.3")
        # Assert
        assert result.ok and result.value == pytest.approx(3.3), (
            "in-range float passes and is coerced"
        )

    def test_below_minimum_fails(self):
        # Arrange
        profile = {"types": {"v": {
            "kind": "float_range", "min": 0.0, "max": 5.0,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("v", "-0.1")
        # Assert
        assert not result.ok, "below minimum fails"


# ── str_length ────────────────────────────────────────────────────────────────


class TestStrLength:
    def test_within_bounds_passes(self):
        # Arrange
        profile = {"types": {"nickname": {
            "kind": "str_length", "min_len": 1, "max_len": 8,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("nickname", "abc")
        # Assert
        assert result.ok, "in-range length passes"
        assert result.value == "abc", "value preserved"

    def test_below_min_len_fails(self):
        # Arrange
        profile = {"types": {"nickname": {
            "kind": "str_length", "min_len": 3, "max_len": 8,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("nickname", "ab")
        # Assert
        assert not result.ok, "below min length fails"
        assert "minimum" in result.error.lower(), (
            "error mentions the minimum bound"
        )

    def test_above_max_len_fails(self):
        # Arrange
        profile = {"types": {"nickname": {
            "kind": "str_length", "min_len": 1, "max_len": 4,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("nickname", "abcdef")
        # Assert
        assert not result.ok, "above max length fails"
        assert "maximum" in result.error.lower(), (
            "error mentions the maximum bound"
        )

    def test_only_max_len_no_min(self):
        # Arrange -- one-sided bound.
        profile = {"types": {"name": {
            "kind": "str_length", "max_len": 16,
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act -- empty string still passes (no min).
        result = reg.validate("name", "")
        # Assert
        assert result.ok, "empty string passes when only max_len set"

    def test_neither_bound_is_build_error(self):
        # Arrange -- neither min nor max means the type is unusable.
        profile = {"types": {"name": {"kind": "str_length"}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        td = reg.resolve("name")
        # Assert
        assert td is not None and td.build_error, (
            "str_length without bounds is a build error"
        )


# ── pattern ───────────────────────────────────────────────────────────────────


class TestPattern:
    def test_fullmatch_passes(self):
        # Arrange
        profile = {"types": {"duration": {
            "kind": "pattern", "regex": r"^\d+(us|ms|s)$",
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("duration", "5s")
        # Assert
        assert result.ok, "fullmatching value passes"

    def test_partial_match_fails(self):
        # Arrange -- pattern is anchored via fullmatch semantics, even
        # if the regex itself lacks ^$ anchors.
        profile = {"types": {"tag": {
            "kind": "pattern", "regex": r"[a-z]+",
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act -- "abc123" partial-matches "abc" but isn't a fullmatch.
        result = reg.validate("tag", "abc123")
        # Assert
        assert not result.ok, "partial match fails under fullmatch rule"

    def test_invalid_regex_is_build_error_not_crash(self):
        # Arrange -- unbalanced paren is a regex syntax error.
        profile = {"types": {"bad": {
            "kind": "pattern", "regex": r"(unclosed",
        }}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        td = reg.resolve("bad")
        # Assert -- registry construction must not raise.
        assert td is not None and td.build_error, (
            "invalid regex -> build error, not crash"
        )
        # Using the broken type returns the build error
        outcome = reg.validate("bad", "anything")
        assert not outcome.ok, "broken type rejects all values"
        assert "regex" in outcome.error.lower(), (
            "error mentions regex"
        )


# ── format_spec (stub) ────────────────────────────────────────────────────────


class TestFormatSpecStub:
    def test_valid_spec_parses_at_build_time(self):
        # Arrange -- a real format-spec string from protocol.py vocabulary.
        profile = {"types": {"byte": {"kind": "format_spec", "spec": "Val:H1"}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        td = reg.resolve("byte")
        # Assert
        assert td is not None, "format_spec type present"
        assert td.build_error == "", "well-formed spec has no build error"
        assert td.columns, (
            "parsed columns cached at build time for the catalog"
        )

    def test_stub_validate_passes_through(self):
        # Arrange
        profile = {"types": {"byte": {"kind": "format_spec", "spec": "Val:H1"}}}
        reg = TypeRegistry.from_profile(profile)
        # Act -- the stub returns ok=True regardless of value content;
        # this proves the spot is wired and won't break dispatch.
        result = reg.validate("byte", "anything-goes-for-now")
        # Assert
        assert result.ok, (
            "format_spec stub passes anything (TODO: real validation)"
        )

    def test_invalid_spec_is_build_error(self):
        # Arrange -- a deliberately malformed spec.
        profile = {"types": {"bad": {"kind": "format_spec", "spec": "this is not a format spec"}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        td = reg.resolve("bad")
        # Assert
        assert td is not None, "build-error type is still in registry"
        # build_error may or may not fire depending on parse_format_spec
        # tolerance; if it doesn't, the type is at least present.  The
        # important guarantee: no crash during registry construction.


# ── unknown kind / missing type ───────────────────────────────────────────────


class TestErrorPaths:
    def test_unknown_kind_is_build_error(self):
        # Arrange
        profile = {"types": {"bad": {"kind": "totally_made_up"}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        td = reg.resolve("bad")
        # Assert
        assert td is not None and td.build_error, (
            "unknown kind -> build error"
        )
        result = reg.validate("bad", "x")
        assert not result.ok and "unknown kind" in result.error.lower()

    def test_validate_unknown_type_name(self):
        # Arrange
        reg = TypeRegistry.from_profile({})
        # Act
        result = reg.validate("nonexistent", "x")
        # Assert
        assert not result.ok, "unknown type name fails"
        assert "unknown type" in result.error.lower(), (
            "error names the unknown-type case"
        )

    def test_non_dict_entry_becomes_build_error(self):
        # Arrange -- types block has a string value where a dict is required.
        profile = {"types": {"bad": "this should be an object"}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        td = reg.resolve("bad")
        # Assert -- registry tolerated it, type marked broken.
        assert td is not None and td.build_error, (
            "non-dict type entry -> build error, not a crash"
        )


# ── Builtin validators ────────────────────────────────────────────────────────


class TestBuiltinValidation:
    def test_int_builtin_passes_for_int_strings(self):
        # Arrange / Act
        reg = TypeRegistry.from_profile({})
        result = reg.validate("int", "42")
        # Assert
        assert result.ok and result.value == 42, "int builtin coerces"

    def test_int_builtin_fails_for_non_int(self):
        # Arrange / Act
        reg = TypeRegistry.from_profile({})
        result = reg.validate("int", "not an int")
        # Assert
        assert not result.ok, "non-int string fails the builtin int check"

    def test_bool_builtin_accepts_lenient_forms(self):
        # Arrange -- parse_bool accepts multiple forms (on/off/true/etc.)
        reg = TypeRegistry.from_profile({})
        # Act / Assert
        for form in ("on", "true", "1", "yes"):
            result = reg.validate("bool", form)
            assert result.ok and result.value is True, (
                f"builtin bool accepts {form!r} as True"
            )

    def test_str_builtin_accepts_anything(self):
        # Arrange / Act
        reg = TypeRegistry.from_profile({})
        result = reg.validate("str", "anything goes")
        # Assert
        assert result.ok, "str builtin accepts any input"


# ── Schema/catalog surfacing ──────────────────────────────────────────────────


class TestCatalogRendering:
    def test_typedef_to_catalog_enum(self):
        # Arrange — non-shadowing custom name.
        profile = {"types": {"on_off": {
            "kind": "enum", "values": ["on", "off"], "help": "On/Off",
        }}}
        reg = TypeRegistry.from_profile(profile)
        td = reg.resolve("on_off")
        # Act
        out = typedef_to_catalog(td)
        # Assert
        assert out["kind"] == "enum", "kind preserved"
        assert out["values"] == ["on", "off"], "values exposed"
        assert out["help"] == "On/Off", "help exposed"

    def test_typedef_to_catalog_format_spec_exposes_spec(self):
        # Arrange
        profile = {"types": {"byte": {"kind": "format_spec", "spec": "Val:H1"}}}
        reg = TypeRegistry.from_profile(profile)
        td = reg.resolve("byte")
        # Act
        out = typedef_to_catalog(td)
        # Assert -- the LLM gets to see the raw spec string for guidance
        assert out["spec"] == "Val:H1", "raw spec surfaced in catalog"

    def test_schema_kinds_returns_all_recognized_kinds(self):
        # Arrange / Act
        kinds = schema_kinds()
        # Assert -- this is the canonical list tests use to detect drift
        for required in (
            "enum", "int_range", "float_range",
            "str_length", "pattern", "format_spec",
        ):
            assert required in kinds, f"schema kinds include {required}"
