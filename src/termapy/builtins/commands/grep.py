"""Built-in plugin: search the scrollback for matching lines."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from termapy.defaults import cmd_prefix
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.plugins.params import ParamSpec
from termapy.scripting import ANSI_RE as _ANSI_RE

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def find_matches(
    text: str,
    pattern: str,
    *,
    is_noise: Callable[[str], bool] | None = None,
) -> tuple[list[tuple[int, str]], str | None]:
    """Find regex matches in scrollback text.

    Case-insensitive.  Returns ``(matches, error_message)`` where
    matches is ``[(line_no_1_based, ANSI_stripped_line), ...]`` and
    error_message is ``None`` on success or a user-facing string on
    pattern compile failure / empty input.

    Shared by ``/grep`` (prints the list) and ``/find`` (navigates
    interactively) so both have identical match semantics.

    Args:
        text: Full scrollback text (from ``ctx.ui.get_screen_text()``).
        pattern: Regex pattern.  Empty -> error.
        is_noise: Optional per-line predicate to skip lines (used to
            exclude the command's own output from recursive matches).
    """
    if not pattern:
        return [], "Pattern required"
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return [], f"Invalid pattern: {e}"
    if not text:
        return [], "No scrollback (CLI mode?)"

    noise = is_noise or (lambda _line: False)
    matches: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines()):
        if noise(line):
            continue
        if rx.search(line):
            clean = _ANSI_RE.sub("", line)
            matches.append((i + 1, clean))
    return matches, None


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Search the scrollback for lines matching a regex pattern.

    Performs a case-insensitive regex search across all visible terminal
    output. Skips its own output and echoed grep commands to avoid
    recursive matches. Results are limited by ``max_grep_lines`` config.

    Args:
        ctx: Plugin context for screen text access and output.
        args: Unused -- the pattern arrives via ``ctx.arg`` (see ``params``).
    """
    pattern = ctx.arg("pattern")
    prefix = cmd_prefix(ctx.cfg)
    grep_cmd = f"{prefix}grep"

    def _is_grep_noise(line: str) -> bool:
        stripped = line.lstrip()
        return stripped.startswith("grep:") or grep_cmd in line

    text = ctx.ui.get_screen_text()
    matches, err = find_matches(text, pattern, is_noise=_is_grep_noise)
    if err is not None:
        # Preserve /grep's historical messages where they differ from
        # the helper's generic ones.
        if "No scrollback" in err:
            return CmdResult.fail(
                msg="Grep not available: no scrollback in CLI mode",
            )
        if "Pattern required" in err:
            return CmdResult.fail(msg="Usage: /grep <pattern>")
        return CmdResult.fail(msg=err)

    if not matches:
        ctx.io.output(f"  grep: '{pattern}' - no matches")
        return CmdResult.ok(value="0")
    max_matches = ctx.cfg.get("max_grep_lines", 100)
    total = len(matches)
    shown = matches[:max_matches]
    if total > max_matches:
        ctx.io.output(f"  grep: '{pattern}' - showing first {max_matches} of {total} matches")
    else:
        ctx.io.output(f"  grep: '{pattern}' - {total} match(es)")
    for lineno, line in shown:
        ctx.io.output(f"  grep: {lineno:>5} | {line}")
    return CmdResult.ok(value=str(total))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="grep",
    help="Search the scrollback for lines matching a pattern (case-insensitive regex).",
    long_help="""\
Searches all visible terminal output using Python regex syntax.
Matching is case-insensitive. ANSI escape codes are stripped
before display. Grep's own output is excluded from results.

Max results controlled by max_grep_lines config (default 100).

Examples:
  /grep error          - find lines containing 'error'
  /grep ^OK            - lines starting with 'OK'
  /grep temp.*\\d+      - 'temp' followed by digits

See also: /find (interactive navigation through matches with
highlighted lines and arrow-button paging), /search (search the
command-help corpus rather than the scrollback).""",
    handler=_handler,
    needs=CapabilitySet(interactive=True),  # scrollback only exists interactively
    params=[
        ParamSpec("pattern", "str", positional=True, rest=True, required=True,
                  help="case-insensitive regex to search the scrollback for"),
    ],
)
