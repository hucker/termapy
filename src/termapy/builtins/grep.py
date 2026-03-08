"""Built-in plugin: search the scrollback for matching lines."""

import re

NAME = "grep"
ARGS = "<pattern>"
HELP = "Search the scrollback for lines matching a pattern (case-insensitive regex)."

_MAX_MATCHES = 100
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
    text = ctx.get_screen_text()
    lines = text.splitlines()
    matches = [(i + 1, line) for i, line in enumerate(lines) if rx.search(line)]
    if not matches:
        ctx.write(f"  grep: '{pattern}' — no matches")
        return
    total = len(matches)
    shown = matches[:_MAX_MATCHES]
    if total > _MAX_MATCHES:
        ctx.write(f"  grep: '{pattern}' — showing first {_MAX_MATCHES} of {total} matches")
    else:
        ctx.write(f"  grep: '{pattern}' — {total} match(es)")
    for lineno, line in shown:
        clean = _ANSI_RE.sub("", line)
        ctx.write(f"  grep: {lineno:>5} | {clean}")
