"""Built-in plugin: wait for a device line, the one /expect in every frontend.

Until 2026-10-16 the script runner had its own /expect (0.25 s default, an
abort on timeout, an uncompiled regex, a ``quiet=`` keyword) beside this one
(5 s, no abort), so one .run file got both behaviors depending on how a line
was spelled (review 2026-10-15, D1).  Decision 6 settled it: 5 s by default
everywhere, and a timeout inside a .run script stops the script on every
path, a ``$(L) <- /expect ...`` capture included.  ``.quiet`` hides the
match line.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.plugins.params import ParamSpec
from termapy.scripting import format_duration

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    return _expect(ctx, use_regex=False)


def _handler_regex(ctx: PluginContext, args: str) -> CmdResult:
    return _expect(ctx, use_regex=True)


def _expect(ctx: PluginContext, *, use_regex: bool) -> CmdResult:
    """Wait for a line containing (or matching) ``match=``; its value is the line."""
    pattern = ctx.arg("match").strip()
    timeout_s = ctx.arg("timeout")
    if use_regex:
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return CmdResult.fail(msg=f"Expect: invalid regex: {e}")

        def predicate(line: str) -> bool:
            return bool(compiled.search(line))
    else:

        def predicate(line: str) -> bool:
            return pattern in line

    match = ctx.wait_for_match(predicate, timeout=timeout_s)
    if match is None:
        if ctx.internal.in_script():
            stop = ctx.internal.script_stop_event
            if stop is not None and stop.is_set():
                return CmdResult.fail(msg="Script stopped.")
            # A missed expect is a failed assertion: the script stops here.
            ctx.internal.script_stop()
        return CmdResult.fail(msg=f'Expect "{pattern}" timeout after {format_duration(timeout_s)}')
    ctx.io.output(f'Expect "{pattern}" matched', "green")
    return CmdResult.ok(value=match)


_PARAMS = [
    ParamSpec(
        "timeout", "duration", default=5.0,
        help="how long to wait, e.g. 2s (default 5s)",
    ),
    ParamSpec(
        "match", "str", required=True, rest=True,
        help="text to find; runs to end of line, so it comes last",
    ),
]

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="expect",
    help="Wait for text in serial output (blocks until match or timeout).",
    long_help=(
        "Block until the device outputs a line containing <text>.\n"
        "Returns the matched line as the result value; fails on\n"
        "timeout (default 5s).  Inside a .run script a timeout also\n"
        "stops the script.\n"
        "\n"
        "Lines that arrived before {prefix}expect ran are searched\n"
        "too.  A match consumes the output up to and including the\n"
        "matched line, so the next {prefix}expect waits for new\n"
        "output instead of finding the same reply again.\n"
        "\n"
        "Available wherever ``block_until`` is provided: inside\n"
        "a .run script (always), and from the MCP server (the\n"
        "MCPHost opts in).  The interactive REPL prompt does\n"
        "not provide it -- type {prefix}expect there and you'll\n"
        "get a clear capability error.\n"
        "\n"
        "**match= must be the LAST keyword** because it consumes\n"
        "to end of line (text can contain spaces).\n"
        "\n"
        "Example:\n"
        "  AT+CONNECT\n"
        "  {prefix}expect timeout=5s match=CONNECTED"
    ),
    handler=_handler,
    params=_PARAMS,
    needs=CapabilitySet.BLOCK_UNTIL,
    raw_args=True,
    sub_commands={
        "regex": Command(
            help="Wait for regex match in serial output (blocks until match or timeout).",
            long_help=(
                "Block until the device outputs a line matching <pattern>.\n"
                "<pattern> is a Python regex.  See {prefix}expect for the\n"
                "non-regex variant, how a match consumes earlier output,\n"
                "and the capability gate that decides where this command\n"
                "runs.\n"
                "\n"
                "**match= must be the LAST keyword.**\n"
                "\n"
                "Example:\n"
                "  AT+STATUS\n"
                "  {prefix}expect.regex timeout=2s match=^\\+STATUS: \\d+$"
            ),
            handler=_handler_regex,
            params=_PARAMS,
            needs=CapabilitySet.BLOCK_UNTIL,
            raw_args=True,
        ),
    },
)
