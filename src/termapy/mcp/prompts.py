"""MCP prompts: user-invocable conversation recipes.

Prompts are the third MCP primitive (alongside tools and resources).
A prompt is a server-defined template that the user picks from a menu
in the MCP client (Claude Desktop, VS Code MCP, etc.); when chosen,
the prompt's pre-baked text drops into chat with whatever arguments
the user filled in.

Termapy's prompts let a user lean on the connected LLM for things
termapy itself shouldn't (or can't) do well -- profile authoring
from a help dump being the canonical example.  The LLM does the
parsing/classification/drafting; termapy provides the schema, the
authoring rules, and the invocation surface.

Today: ``draft_profile`` only.  Future: ``audit_profile``,
``upgrade_to_typed``, ``add_command`` etc.

This module is registered from ``mcp.server._build_server`` via a
single ``register_prompts(server, host)`` call so server.py stays
focused on host lifecycle and tool/resource wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from termapy.mcp.server import MCPHost


# ── Authoring guide loading ─────────────────────────────────────────────────


_AUTHORING_GUIDE_PATH = (
    Path(__file__).parent.parent / "help" / "authoring-profiles.md"
)


def _load_authoring_guide() -> str:
    """Read the authoring guide once at module load.

    Lives in ``help/authoring-profiles.md`` so it's editable as a doc,
    viewable in the docs site, and embeddable in prompts.  If the file
    is missing (broken install), fall back to a brief inline message
    rather than crashing prompt registration -- the prompt is still
    useful even without the guide.
    """
    try:
        return _AUTHORING_GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "# Authoring device profiles\n\n"
            "(Authoring guide not found at "
            f"{_AUTHORING_GUIDE_PATH}; consult the termapy docs.)\n"
        )


_AUTHORING_GUIDE = _load_authoring_guide()


# ── draft_profile prompt ────────────────────────────────────────────────────


_DRAFT_PROFILE_INSTRUCTIONS = """\
You are drafting a v2 device profile for termapy from artifacts the
user has pasted in.  Follow the authoring guide above precisely.

Critical rules:

1. **Every command entry MUST have ``enabled: false``.**  No exceptions,
   not even for entries that look obviously safe (``version``, ``help``).
   The user will flip them to true one at a time as they audit.  This
   is the audit signature -- DO NOT skip it.

2. **When in doubt about safety, prefer ``destructive``.**  Friction is
   recoverable; data loss isn't.  Mark anything that resets, erases,
   factory-clears, drives external outputs, or talks to billable
   services as ``destructive``.

3. **Default ``response.format: "lines"``** with ``timeout_ms: 1000``
   unless you have evidence (a sample response) that justifies a
   stricter format.  A wrong regex is worse than ``lines``.

4. **Bump ``timeout_ms``** for slow commands: reset (5000), mfg/cal
   (10000), flash erase (10000+), self-test.

5. **Add a top-level ``_notes`` block** summarizing what was inferred
   and what needs human review.  Use ``needs_review`` for entries
   you're uncertain about.  Underscore-prefixed keys are accepted
   by the schema as metadata.

6. **Don't invent commands.**  Only include entries that appear in
   the help output the user pasted.

Return ONE JSON object matching the v2 profile schema.  Wrap it in a
fenced code block (```json ... ```) so the user can copy it.  After
the code block, briefly summarize: how many commands, how many you
flagged as needing review, and any commands you couldn't classify
with confidence.

---

# Inputs from the user
"""


def _build_draft_profile_message(
    help_output: str,
    sample_responses: str,
    startup_banner: str,
    device_name: str,
    notes: str,
) -> str:
    """Compose the user-facing prompt body.

    The structure is fixed so the LLM sees consistent input every
    time -- title sections in the same order, empty sections marked
    ``(none provided)`` so the LLM never has to guess what was
    omitted.
    """
    parts: list[str] = []
    parts.append(_AUTHORING_GUIDE)
    parts.append("")
    parts.append(_DRAFT_PROFILE_INSTRUCTIONS)
    parts.append("")
    parts.append(f"## Device name\n\n{device_name or '(unspecified)'}\n")
    parts.append("")
    parts.append("## Help output\n")
    if help_output.strip():
        parts.append("```")
        parts.append(help_output.rstrip())
        parts.append("```\n")
    else:
        parts.append("(none provided -- ask the user to paste the device's help output)\n")
    parts.append("## Startup banner\n")
    if startup_banner.strip():
        parts.append("```")
        parts.append(startup_banner.rstrip())
        parts.append("```\n")
    else:
        parts.append("(none provided)\n")
    parts.append("## Sample responses\n")
    if sample_responses.strip():
        parts.append("```")
        parts.append(sample_responses.rstrip())
        parts.append("```\n")
    else:
        parts.append(
            "(none provided -- only use ``response.format: \"lines\"`` for now; "
            "do not invent regex patterns without sample data)\n"
        )
    parts.append("## Engineer notes\n")
    if notes.strip():
        parts.append(notes.rstrip() + "\n")
    else:
        parts.append("(none provided)\n")
    return "\n".join(parts)


# ── Registration ────────────────────────────────────────────────────────────


def register_prompts(server: Any, host: MCPHost) -> None:
    """Register all termapy MCP prompts on the FastMCP server.

    Called once from ``_build_server``.  ``host`` is unused today
    but takes it as a parameter so future prompts can read host
    state (active profile, port, captured artifacts) when composing
    their template.
    """
    del host  # reserved for future prompts that need host state

    @server.prompt(
        name="draft_profile",
        title="Draft a termapy device profile",
        description=(
            "Draft a v2 termapy profile from a device's help output. "
            "Paste the help text (and optionally sample responses, a "
            "startup banner, and notes); the LLM returns a JSON profile "
            "with every command set to enabled=false for safe per-command "
            "review."
        ),
    )
    def draft_profile(
        help_output: str = "",
        sample_responses: str = "",
        startup_banner: str = "",
        device_name: str = "",
        notes: str = "",
    ) -> list[dict[str, Any]]:
        """Build a draft v2 profile from pasted device artifacts.

        Args:
            help_output: The device's help table (paste from the
                terminal).  Optional; if empty the LLM will ask.
            sample_responses: ``cmd: response`` pairs the user
                captured by hand.  Optional but enables typed regex
                upgrades.
            startup_banner: The boot banner the device emits on
                connect or reset.  Optional; useful for setting
                ``device.startup_banner``.
            device_name: Friendly name for ``device.name``.
                Optional.
            notes: Free-form context the user wants the LLM to
                consider (e.g. "this is a 3-axis stage; gpio drives
                limit switches").  Optional.
        """
        body = _build_draft_profile_message(
            help_output=help_output,
            sample_responses=sample_responses,
            startup_banner=startup_banner,
            device_name=device_name,
            notes=notes,
        )
        return [
            {
                "role": "user",
                "content": {"type": "text", "text": body},
            }
        ]
