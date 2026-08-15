"""Tests for the MCP prompts surface.

Prompts are user-invocable conversation recipes registered via
FastMCP's @server.prompt() decorator.  Today: ``draft_profile``.
These tests exercise the prompt-body composition pure-functionally
(without spinning up an MCP session) since the integration with
FastMCP is a thin decorator layer.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.mcp.prompts import (  # noqa: E402
    _AUTHORING_GUIDE,
    _build_draft_profile_message,
)

# ── Authoring guide loaded at module init ───────────────────────────────────


class TestAuthoringGuide:
    def test_guide_loaded_at_import(self):
        # Arrange / Act / Assert -- non-empty content from disk
        assert isinstance(_AUTHORING_GUIDE, str), "guide is a string"
        assert len(_AUTHORING_GUIDE) > 500, (
            "guide should be substantial (loaded from authoring-profiles.md)"
        )

    def test_guide_includes_critical_rules(self):
        # Assert -- the LLM-targeted rules ARE in the guide so they
        # ride along inside every prompt body
        assert "enabled: false" in _AUTHORING_GUIDE, (
            "authoring guide must instruct enabled=false on drafts"
        )
        assert "destructive" in _AUTHORING_GUIDE, (
            "authoring guide must explain the destructive safety tier"
        )


# ── Prompt body composition ─────────────────────────────────────────────────


class TestDraftProfileMessage:
    """The prompt body composer is pure -- given the user's pasted
    artifacts it returns a deterministic string.  Tests anchor on
    structure (sections present, empty handling) rather than wording."""

    def test_body_includes_authoring_guide(self):
        # Act
        body = _build_draft_profile_message("", "", "", "", "")
        # Assert
        assert _AUTHORING_GUIDE in body, "guide is embedded in every prompt"

    def test_body_lays_out_all_input_sections(self):
        # Act
        body = _build_draft_profile_message("", "", "", "", "")
        # Assert -- five named input sections always appear
        for header in (
            "## Device name",
            "## Help output",
            "## Startup banner",
            "## Sample responses",
            "## Engineer notes",
        ):
            assert header in body, f"section header missing: {header!r}"

    def test_empty_help_output_marked_explicitly(self):
        # Arrange / Act
        body = _build_draft_profile_message(
            help_output="",
            sample_responses="",
            startup_banner="",
            device_name="",
            notes="",
        )
        # Assert -- LLM sees a clear "no help provided" message, not
        # a confusingly empty section
        assert "(none provided" in body, (
            "empty help section is explicitly marked"
        )

    def test_help_output_wrapped_in_code_fence(self):
        # Arrange
        help_text = "command  Description\nbat      Read battery"
        # Act
        body = _build_draft_profile_message(help_text, "", "", "", "")
        # Assert -- pasted help lives inside a fenced block so the LLM
        # parses it as data, not instructions
        assert "```" in body, "code fences present"
        assert help_text in body, "help text embedded verbatim"

    def test_device_name_included_when_provided(self):
        # Act
        body = _build_draft_profile_message("", "", "", "MyDevice v3", "")
        # Assert
        assert "MyDevice v3" in body, "device name surfaces in body"

    def test_unspecified_device_name_marked(self):
        # Act
        body = _build_draft_profile_message("", "", "", "", "")
        # Assert
        assert "(unspecified)" in body, "missing device name explicitly noted"

    def test_engineer_notes_passed_through(self):
        # Arrange
        notes = "gpio drives limit switches; do not mark mutable"
        # Act
        body = _build_draft_profile_message("", "", "", "", notes)
        # Assert
        assert notes in body, "engineer notes embedded verbatim"

    def test_sample_responses_warning_when_absent(self):
        # Act -- no samples provided
        body = _build_draft_profile_message("help text", "", "", "", "")
        # Assert -- LLM is told NOT to invent regex without samples
        assert "do not invent regex patterns" in body, (
            "absent samples trigger the no-invented-regex warning"
        )


# ── register_prompts attaches the decorator ─────────────────────────────────


class TestRegisterPrompts:
    """Registration is a thin call into FastMCP.  Smoke-test it by
    building a real FastMCP server and confirming draft_profile
    appears in the prompt registry afterward."""

    def test_draft_profile_registered(self, tmp_path):
        # Arrange
        from mcp.server.fastmcp import FastMCP

        from termapy.mcp.prompts import register_prompts

        server = FastMCP("test")
        # ``host`` is unused by today's prompts but the signature
        # requires it; pass a sentinel
        register_prompts(server, host=None)  # type: ignore[arg-type]

        # Act -- introspect the prompt registry
        # FastMCP doesn't expose a public listing, so we check via
        # the underlying _prompt_manager attribute (private but
        # stable in current SDK).
        manager = server._prompt_manager
        names = list(manager._prompts.keys())

        # Assert
        assert "draft_profile" in names, (
            "draft_profile registered after register_prompts() call"
        )
