"""Built-in plugin: /profile.* commands.

Phase 0 ships only the validator.  Loaders and info commands land in
later phases when the rest of the MCP plumbing is in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command
from termapy.profile import load_profile, validate_profile

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler_validate(ctx: PluginContext, args: str) -> CmdResult:
    """Validate a profile file against the schema and report errors.

    Args:
        ctx: Plugin context.
        args: Path to a ``.json`` or ``.toml`` profile file.
    """
    path_str = args.strip()
    if not path_str:
        return CmdResult.fail(msg="Usage: /profile.validate <path>")
    path = Path(path_str)
    if not path.exists():
        return CmdResult.fail(msg=f"Profile not found: {path}")
    try:
        profile = load_profile(path)
    except (OSError, ValueError) as e:
        return CmdResult.fail(msg=f"Parse error: {e}")
    result = validate_profile(profile)
    if result.ok:
        n = len(profile.get("commands", {})) if isinstance(profile, dict) else 0
        ctx.result(f"Valid profile.  {n} commands.", "green")
        return CmdResult.ok(value=str(n))
    ctx.write(f"  Profile has {len(result.errors)} error(s):", "red")
    for err in result.errors:
        ctx.write(f"    - {err}", "yellow")
    return CmdResult.fail(msg=f"{len(result.errors)} validation error(s)")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="profile",
    help="Device profile commands (MCP profile schema, validator).",
    long_help=(
        "A device profile declaratively describes how a serial device "
        "speaks: transport rules (baud, line endings, prompt, echo), "
        "command catalog (typed args, response shapes), error patterns. "
        "Profiles are the input the MCP server consumes to bridge LLMs "
        "to serial devices.  See docs/profile-v2-spec.md for the spec."
    ),
    handler=None,
    sub_commands={
        "validate": Command(
            args="<path>",
            help="Validate an MCP device profile (.json or .toml).",
            long_help=(
                "Schema-validate a profile against profile.schema.json. "
                "Prints OK + command count on success, or line-numbered "
                "errors on failure.  Equivalent to 'termapy "
                "--validate-profile <path>' from the shell."
            ),
            handler=_handler_validate,
        ),
    },
)
