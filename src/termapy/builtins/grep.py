"""Built-in plugin: search the scrollback for matching lines."""

import re

NAME = "grep"
ARGS = "<pattern>"
HELP = "Search the scrollback for lines matching a pattern (case-insensitive regex)."

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def handler(ctx, args):
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

    def _is_grep_noise(line):
        """Skip grep output lines and echoed grep commands."""
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
