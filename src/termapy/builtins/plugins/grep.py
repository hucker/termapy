"""Built-in plugin: search the scrollback for matching lines."""

from __future__ import annotations

import re

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "grep"
ARGS = "<pattern>"
HELP = "Search the scrollback for lines matching a pattern (case-insensitive regex)."

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def handler(ctx: PluginContext, args: str) -> None:
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
        ctx.write("Usage: !!grep <pattern>", "red")
        return
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        ctx.write(f"  grep: invalid pattern: {e}", "red")
        return
    max_matches = ctx.cfg.get("max_grep_lines", 100)
    prefix = ctx.cfg.get("repl_prefix", "!!")
    grep_cmd = f"{prefix}grep"
    text = ctx.get_screen_text()
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
