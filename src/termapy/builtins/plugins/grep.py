"""Built-in plugin: search the scrollback for matching lines."""

from __future__ import annotations

import re

from typing import TYPE_CHECKING

from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _handler(ctx: PluginContext, args: str) -> None:
    """Search the scrollback for lines matching a regex pattern.

    Performs a case-insensitive regex search across all visible terminal
    output. Skips its own output and echoed grep commands to avoid
    recursive matches. Results are limited by ``max_grep_lines`` config.

    Args:
        ctx: Plugin context for screen text access and output.
        args: Regex pattern string to search for.
    """
    pattern = args.strip()
    if not pattern:
        ctx.write("Usage: /grep <pattern>", "red")
        return
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        ctx.write(f"  grep: invalid pattern: {e}", "red")
        return
    max_matches = ctx.cfg.get("max_grep_lines", 100)
    prefix = ctx.cfg.get("cmd_prefix", "/")
    grep_cmd = f"{prefix}grep"
    text = ctx.get_screen_text()
    if not text:
        ctx.write("  grep: not available (no scrollback in CLI mode)", "yellow")
        return
    lines = text.splitlines()

    def _is_grep_noise(line: str) -> bool:
        """Check if a line is grep's own output to avoid recursive matches.

        Args:
            line: Terminal output line to check.

        Returns:
            True if the line is grep output or an echoed grep command.
        """
        stripped = line.lstrip()
        return stripped.startswith("grep:") or grep_cmd in line

    matches = [
        (i + 1, line) for i, line in enumerate(lines)
        if rx.search(line) and not _is_grep_noise(line)
    ]
    if not matches:
        ctx.write(f"  grep: '{pattern}' — no matches")
        return
    total = len(matches)
    shown = matches[:max_matches]
    if total > max_matches:
        ctx.write(f"  grep: '{pattern}' — showing first {max_matches} of {total} matches")
    else:
        ctx.write(f"  grep: '{pattern}' — {total} match(es)")
    for lineno, line in shown:
        clean = _ANSI_RE.sub("", line)
        ctx.write(f"  grep: {lineno:>5} | {clean}")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="grep",
    args="<pattern>",
    help="Search the scrollback for lines matching a pattern (case-insensitive regex).",
    long_help="""\
Searches all visible terminal output using Python regex syntax.
Matching is case-insensitive. ANSI escape codes are stripped
before display. Grep's own output is excluded from results.

Max results controlled by max_grep_lines config (default 100).

Examples:
  /grep error          — find lines containing 'error'
  /grep ^OK            — lines starting with 'OK'
  /grep temp.*\\d+      — 'temp' followed by digits""",
    handler=_handler,
)
