"""Tests for the MCP prompts surface.

Prompts are user-invocable conversation recipes registered via the
SDK's @server.prompt() decorator.  Today: ``draft_profile``.
These tests exercise the prompt-body composition pure-functionally
(without spinning up an MCP session) since the integration with the
SDK is a thin decorator layer.
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
        """The authoring guide is read from disk at import, not left empty if the file moves."""
        # Arrange / Act / Assert -- non-empty content from disk
        assert isinstance(_AUTHORING_GUIDE, str), "guide is a string"
        assert len(_AUTHORING_GUIDE) > 500, (
            "guide should be substantial (loaded from authoring-profiles.md)"
        )

    def test_guide_includes_critical_rules(self):
        """The guide carries the enabled=false and destructive-tier rules every draft must obey."""
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
        """Every draft_profile prompt embeds the full authoring guide verbatim."""
        # Act
        body = _build_draft_profile_message("", "", "", "", "")
        # Assert
        assert _AUTHORING_GUIDE in body, "guide is embedded in every prompt"

    def test_body_lays_out_all_input_sections(self):
        """All five input sections appear even when every argument is empty."""
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
        """Empty help output reads as "(none provided" so the LLM is not left with a blank."""
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
        assert "(none provided" in body, "empty help section is explicitly marked"

    def test_help_output_wrapped_in_code_fence(self):
        """Pasted help sits verbatim in a code fence so the LLM reads it as data."""
        # Arrange
        help_text = "command  Description\nbat      Read battery"
        # Act
        body = _build_draft_profile_message(help_text, "", "", "", "")
        # Assert -- pasted help lives inside a fenced block so the LLM
        # parses it as data, not instructions
        assert "```" in body, "code fences present"
        assert help_text in body, "help text embedded verbatim"

    def test_device_name_included_when_provided(self):
        """A supplied device name surfaces in the prompt body."""
        # Act
        body = _build_draft_profile_message("", "", "", "MyDevice v3", "")
        # Assert
        assert "MyDevice v3" in body, "device name surfaces in body"

    def test_unspecified_device_name_marked(self):
        """A missing device name is stated as (unspecified) rather than omitted."""
        # Act
        body = _build_draft_profile_message("", "", "", "", "")
        # Assert
        assert "(unspecified)" in body, "missing device name explicitly noted"

    def test_engineer_notes_passed_through(self):
        """Engineer notes, such as safety caveats, reach the prompt body unaltered."""
        # Arrange
        notes = "gpio drives limit switches; do not mark mutable"
        # Act
        body = _build_draft_profile_message("", "", "", "", notes)
        # Assert
        assert notes in body, "engineer notes embedded verbatim"

    def test_sample_responses_warning_when_absent(self):
        """Without sample responses the prompt forbids inventing regex patterns."""
        # Act -- no samples provided
        body = _build_draft_profile_message("help text", "", "", "", "")
        # Assert -- LLM is told NOT to invent regex without samples
        assert "do not invent regex patterns" in body, (
            "absent samples trigger the no-invented-regex warning"
        )


# ── register_prompts attaches the decorator ─────────────────────────────────


class TestRegisterPrompts:
    """Registration is a thin call into the SDK.  Smoke-test it by
    building a real MCP server and confirming draft_profile
    appears in the prompt registry afterward."""

    def test_draft_profile_registered(self, tmp_path):
        """register_prompts adds draft_profile to a real MCP server's prompt listing."""
        # Arrange
        import asyncio

        from mcp.server import MCPServer

        from termapy.mcp.prompts import register_prompts

        server = MCPServer("test")
        # ``host`` is unused by today's prompts but the signature
        # requires it; pass a sentinel
        register_prompts(server, host=None)  # ty: ignore[invalid-argument-type]

        # Act -- introspect the prompt registry.  mcp 2.x exposes a
        # public async listing, so this no longer reaches into the
        # private _prompt_manager the way the 1.x version had to.
        names = [p.name for p in asyncio.run(server.list_prompts())]

        # Assert
        assert "draft_profile" in names, "draft_profile registered after register_prompts() call"
