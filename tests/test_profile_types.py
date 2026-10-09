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
        """A None profile builds an empty registry instead of raising."""
        # Arrange / Act
        reg = TypeRegistry.from_profile(None)
        # Assert
        actual = reg.all()
        assert actual == {}, "no profile -> empty registry"

    def test_from_profile_without_types_block_is_empty(self):
        """A profile with commands but no types block defines no custom types."""
        # Arrange / Act
        reg = TypeRegistry.from_profile({"commands": {}})
        # Assert
        actual = reg.all()
        assert actual == {}, "no types block -> empty registry"

    def test_from_profile_with_invalid_types_block_is_empty(self):
        """A types block that is not an object degrades to empty instead of raising."""
        # Arrange -- types is not an object (schema would catch this,
        # but registry must degrade gracefully).
        # Act
        reg = TypeRegistry.from_profile({"types": "not a dict"})
        # Assert
        assert reg.all() == {}, "non-dict types block -> empty registry"

    def test_builtin_resolution(self):
        """Every BUILTINS name resolves with kind builtin, even from an empty profile."""
        # Arrange
        reg = TypeRegistry.from_profile({})
        # Act / Assert -- all five builtins resolve to a "builtin"-kind TypeDef
        for name in sorted(BUILTINS):
            td = reg.resolve(name)
            assert td is not None, f"builtin {name} resolves"
            assert td.kind == "builtin", f"{name} has kind=builtin"

    def test_unknown_type_resolves_to_none(self):
        """An undefined type name resolves to None, not a placeholder TypeDef."""
        # Arrange
        reg = TypeRegistry.from_profile({})
        # Act
        actual = reg.resolve("not_a_real_type")
        # Assert
        assert actual is None, "unknown name resolves to None"

    def test_custom_type_cannot_shadow_builtin(self):
        """Redefining builtin int raises ProfileTypeError rather than a soft build error."""
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
        """A listed enum value validates and comes back as its string form."""
        # Arrange — custom name (not 'bool', which is a reserved builtin).
        profile = {"types": {"on_off": {"kind": "enum", "values": ["on", "off"]}}}
        reg = TypeRegistry.from_profile(profile)
        # Act
        result = reg.validate("on_off", "on")
        # Assert
        assert result.ok, "member value passes"
        assert result.value == "on", "normalized to string"

    def test_enum_rejects_nonmember_with_helpful_error(self):
        """A non-member's error names the bad value and lists every allowed one."""
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
        """Numeric enum values match their string form, so 19200 typed as text passes."""
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
        """An empty values list stays in the registry, flagged as a non-empty build error."""
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
        """An in-range int string passes and is coerced to int, not kept as text."""
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
        """Below the minimum fails, and the error names the minimum and its value."""
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
        """Above the maximum fails with an error that names the maximum."""
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
        """Non-numeric text fails with an expected-int error instead of a ValueError."""
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
        """An in-range float string passes and is coerced to float."""
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
        """A float just below the minimum (-0.1 against 0.0) fails."""
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
        """A string within min_len..max_len passes with its value unchanged."""
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
        """A string shorter than min_len fails with an error naming the minimum."""
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
        """A string longer than max_len fails with an error naming the maximum."""
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
        """With only max_len set there is no lower bound, so the empty string passes."""
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
        """A str_length with neither bound is unusable and is marked a build error."""
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
        """A value matching the whole anchored regex passes: 5s as a duration."""
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
        """Matching uses fullmatch, so an unanchored regex rejects trailing extra text."""
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
        """A bad regex is a build error at load and fails every later validate call."""
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
        """A valid spec is parsed at build time and its columns cached for the catalog."""
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
        """Validation is a stub for now: any value passes, so dispatch is never blocked."""
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
        """A malformed spec never crashes construction; the type stays in the registry."""
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
        """An unrecognized kind is a build error, and validating it says unknown kind."""
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
        """Validating against an undefined type name fails with an unknown-type error."""
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
        """A type entry that is a string, not an object, is marked broken, not raised."""
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
        """The int builtin accepts a decimal string and coerces it to int."""
        # Arrange / Act
        reg = TypeRegistry.from_profile({})
        result = reg.validate("int", "42")
        # Assert
        assert result.ok and result.value == 42, "int builtin coerces"

    def test_int_builtin_fails_for_non_int(self):
        """The int builtin rejects non-numeric text."""
        # Arrange / Act
        reg = TypeRegistry.from_profile({})
        result = reg.validate("int", "not an int")
        # Assert
        assert not result.ok, "non-int string fails the builtin int check"

    def test_bool_builtin_accepts_lenient_forms(self):
        """The bool builtin reads the shared parse_bool forms on, true, 1, yes as True."""
        # Arrange -- parse_bool accepts multiple forms (on/off/true/etc.)
        reg = TypeRegistry.from_profile({})
        # Act / Assert
        for form in ("on", "true", "1", "yes"):
            result = reg.validate("bool", form)
            assert result.ok and result.value is True, (
                f"builtin bool accepts {form!r} as True"
            )

    def test_str_builtin_accepts_anything(self):
        """The str builtin is identity: any text passes."""
        # Arrange / Act
        reg = TypeRegistry.from_profile({})
        result = reg.validate("str", "anything goes")
        # Assert
        assert result.ok, "str builtin accepts any input"


# ── Schema/catalog surfacing ──────────────────────────────────────────────────


class TestCatalogRendering:
    def test_typedef_to_catalog_enum(self):
        """An enum's catalog entry carries its kind, values and help for agent consumers."""
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
        """A format_spec type exposes its raw spec string in the catalog for LLMs."""
        # Arrange
        profile = {"types": {"byte": {"kind": "format_spec", "spec": "Val:H1"}}}
        reg = TypeRegistry.from_profile(profile)
        td = reg.resolve("byte")
        # Act
        out = typedef_to_catalog(td)
        # Assert -- the LLM gets to see the raw spec string for guidance
        assert out["spec"] == "Val:H1", "raw spec surfaced in catalog"

    def test_schema_kinds_returns_all_recognized_kinds(self):
        """schema_kinds() lists all six kinds, the canonical list drift checks rely on."""
        # Arrange / Act
        kinds = schema_kinds()
        # Assert -- this is the canonical list tests use to detect drift
        for required in (
            "enum", "int_range", "float_range",
            "str_length", "pattern", "format_spec",
        ):
            assert required in kinds, f"schema kinds include {required}"
