"""The profile ``memory`` block: schema-known, lint-warned, never load-blocking.

The block declares how termapy talks to the device's memory (dialect,
max_block, address_bits, endian).  Its validation is the same
``termapy.memory.validate_block`` the runtime resolver uses, so a warning
here always describes what ``/mem.*`` will actually do.
"""

from __future__ import annotations

import json
from pathlib import Path

from termapy.profile import validate_profile

DEMO_PROFILE = (
    Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo" / "demo.profile.json"
)


def _memory_warnings(profile: dict) -> list[str]:
    result = validate_profile(profile)
    assert result.ok is True, "the memory block never blocks a load"
    return [warning for warning in result.warnings if warning.startswith("memory")]


class TestMemoryBlock:

    def test_clean_block_is_silent(self):
        profile = {"memory": {"dialect": "termapy", "max_block": 64, "address_bits": 32, "endian": "le"}}
        assert _memory_warnings(profile) == [], "canonical values raise nothing"

    def test_unknown_key_warns(self):
        warnings = _memory_warnings({"memory": {"row_pattern": "x"}})
        assert any("memory" in warning and "row_pattern" in warning for warning in warnings), (
            "forward-compat lint names the unknown key"
        )

    def test_unknown_dialect_warns_with_the_refusal_rule(self):
        warnings = _memory_warnings({"memory": {"dialect": "uboot"}})
        assert any("dialect" in warning and "refuse" in warning for warning in warnings), (
            "states that /mem.* refuse rather than guess a grammar"
        )

    def test_bad_endian_degrades_to_le(self):
        warnings = _memory_warnings({"memory": {"endian": "big"}})
        assert any("endian" in warning and "treated as le" in warning for warning in warnings)

    def test_bad_max_block_degrades_to_default(self):
        # A wrong VALUE degrades; a wrong TYPE ("64") is a schema error like
        # every other typed profile field, and that is the schema's job.
        warnings = _memory_warnings({"memory": {"max_block": 0}})
        assert any("max_block" in warning and "default 64" in warning for warning in warnings)

    def test_template_block_without_read_warns_with_the_refusal(self):
        warnings = _memory_warnings({"memory": {"dialect": "template"}})
        assert any("memory/read: required" in warning and "refuse" in warning for warning in warnings)

    def test_template_bad_regex_warns(self):
        warnings = _memory_warnings({"memory": {"dialect": "template", "read": "r {addr} {len}", "ack": "("}})
        assert any("memory/ack: invalid regex" in warning for warning in warnings)

    def test_demo_legacy_profile_lints_clean(self):
        profile = json.loads(DEMO_PROFILE.with_name("demo_legacy.profile.json").read_text(encoding="utf-8"))
        assert profile["memory"]["dialect"] == "template"
        assert _memory_warnings(profile) == [], "the shipped template block is canonical"

    def test_demo_profile_declares_the_block_cleanly(self):
        # Arrange
        profile = json.loads(DEMO_PROFILE.read_text(encoding="utf-8"))

        # Assert
        assert profile["memory"]["dialect"] == "termapy", "the demo device speaks the native spec"
        assert _memory_warnings(profile) == [], "and its block lints clean"
