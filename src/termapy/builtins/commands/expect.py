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
from collections.abc import Callable
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.plugins.params import ParamSpec
from termapy.scripting import format_duration

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Wait for a received line containing ``match=``; that line is the value."""
    return _expect(ctx, use_regex=False)


def _handler_regex(ctx: PluginContext, args: str) -> CmdResult:
    """Wait for a received line matching the ``match=`` regex; that line is the value."""
    return _expect(ctx, use_regex=True)


def _line_test(pattern: str, use_regex: bool) -> Callable[[str], bool]:
    """``pattern`` as a test of one line: a regex search, or plain containment.

    Raises:
        re.error: ``use_regex`` is set and ``pattern`` is not a valid regex.
    """
    if use_regex:
        compiled = re.compile(pattern)

        def searches(line: str) -> bool:
            return bool(compiled.search(line))

        return searches

    def contains(line: str) -> bool:
        return pattern in line

    return contains


def _expect(ctx: PluginContext, *, use_regex: bool) -> CmdResult:
    """Wait for a line containing (or matching) ``match=``; its value is the line.

    ``fail=`` is tested the same way and ends the wait at once: an error
    reply fails the expect when it arrives instead of after the timeout.
    A line that passes both tests fails, since an error reply that
    mentions the expected text is still an error.
    """
    pattern = ctx.arg("match").strip()
    fail_pattern = ctx.arg("fail").strip()
    timeout_s = ctx.arg("timeout")
    try:
        matches = _line_test(pattern, use_regex)
        fails = _line_test(fail_pattern, use_regex) if fail_pattern else None
    except re.error as e:
        return CmdResult.fail(msg=f"Expect: invalid regex: {e}")

    def predicate(line: str) -> bool:
        return matches(line) or (fails is not None and fails(line))

    line = ctx.wait_for_match(predicate, timeout=timeout_s)
    if line is None:
        return _missed(ctx, f'Expect "{pattern}" timeout after {format_duration(timeout_s)}')
    if fails is not None and fails(line):
        return _missed(ctx, f'Expect "{pattern}" failed: {line}')
    ctx.io.output(f'Expect "{pattern}" matched', "green")
    return CmdResult.ok(value=line)


def _missed(ctx: PluginContext, msg: str) -> CmdResult:
    """The failure for an expect that timed out or met its ``fail=`` line.

    Inside a .run script a missed expect is a failed assertion, so the
    script stops here; a stop the user already asked for answers as such.
    """
    if ctx.internal.in_script():
        stop = ctx.internal.script_stop_event
        if stop is not None and stop.is_set():
            return CmdResult.fail(msg="Script stopped.")
        ctx.internal.script_stop()
    return CmdResult.fail(msg=msg)


_PARAMS = [
    ParamSpec(
        "timeout",
        "duration",
        default=5.0,
        help="how long to wait, e.g. 2s (default 5s)",
    ),
    ParamSpec(
        "fail",
        "str",
        default="",
        help="text that fails the wait as soon as it arrives, e.g. ERROR (no spaces)",
    ),
    ParamSpec(
        "match",
        "str",
        required=True,
        rest=True,
        help="text to find; runs to end of line, so it comes last",
    ),
]

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="advanced",
    name="expect",
    help="Wait for text in serial output (blocks until match or timeout).",
    intro=(
        "Wait for a line from the device before going on, which is how a script "
        "pauses for a reply and fails when the reply is wrong."
    ),
    long_help=(
        "Block until the device outputs a line containing <text>.\n"
        "Returns the matched line as the result value; fails on\n"
        "timeout (default 5s).  Inside a .run script a timeout also\n"
        "stops the script.\n"
        "\n"
        "fail=<text> fails at once when a line containing it arrives,\n"
        "instead of waiting out the timeout: an error reply.  A line\n"
        "containing both fails.  It stops a script the same way.  Its\n"
        "text cannot hold a space; {prefix}expect.regex can (\\s).\n"
        "\n"
        "Each line is compared as the device sent it, without its line\n"
        "ending.\n"
        "\n"
        "Lines that arrived before {prefix}expect ran are searched\n"
        "too, back to the start of the script: output from before the\n"
        "script never answers it.  A match consumes the output up to\n"
        "and including the matched line, so the next {prefix}expect\n"
        "waits for new output instead of finding the same reply again.\n"
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
        "  {prefix}expect timeout=5s fail=ERROR match=CONNECTED"
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
                "<pattern> is a Python regex, searched in the line: ^OK$ is\n"
                "the whole line, OK$ its end.  fail=<pattern> is a regex too\n"
                "(^ERROR|^FAIL), and \\s stands for a space it cannot hold.\n"
                "See {prefix}expect for fail=, how a match consumes earlier\n"
                "output, and the capability gate that decides where this\n"
                "command runs.\n"
                "\n"
                "**match= must be the LAST keyword.**\n"
                "\n"
                "Example:\n"
                "  AT+STATUS\n"
                "  {prefix}expect.regex timeout=2s fail=^ERROR match=^\\+STATUS: \\d+$"
            ),
            handler=_handler_regex,
            params=_PARAMS,
            needs=CapabilitySet.BLOCK_UNTIL,
            raw_args=True,
        ),
    },
)
