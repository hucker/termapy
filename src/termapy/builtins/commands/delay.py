"""Built-in plugin: wait for a duration, the one /delay in every frontend.

Until 2026-10-16 /delay had four implementations -- a TUI hook, a CLI hook,
an MCP hook and the script runner's own -- and the TUI's ``/delay.silent``
returned without waiting (review 2026-10-15, D2).  This handler is the only
one: it waits through ``termapy.plugins.waiting``, on the shared stop event,
so Escape (TUI) and Ctrl+C (CLI) cancel it, and a frontend contributes only
how progress is drawn (``ctx.internal.delay_progress``).  ``.silent`` and
``.quiet`` are the universal level suffixes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command
from termapy.plugins.params import ParamSpec
from termapy.plugins.waiting import wait

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Wait the duration, cancellable by Escape or a script stop; ``msg=`` says what for, first."""
    seconds = ctx.arg("duration")
    # The duration as typed (200ms), for the done line; msg= is the rest.
    label = args.split(None, 1)[0]
    msg = ctx.arg("msg")
    if msg:
        ctx.io.output(msg)
    if wait(ctx, seconds) == "stopped":
        # Inside a script the stop belongs to the script, as /expect says too.
        if ctx.internal.in_script():
            return CmdResult.fail(msg="Script stopped.")
        return CmdResult.fail(msg=f"Delay {label} canceled.")
    ctx.io.output(f"Delay {label} done.")
    return CmdResult.ok(value=seconds)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="terminal",
    name="delay",
    help="Wait for a duration (e.g. 500ms, 1.5s); Escape or Ctrl+C cancels.",
    long_help=(
        "Waits for <duration>, showing progress for a second or more where\n"
        "the frontend draws it: the bottom bar or the script overlay in the\n"
        "TUI, a bar in the CLI, nothing over MCP.  Escape (TUI) or Ctrl+C\n"
        "(CLI) cancels the wait and the script it is in, and the command\n"
        "fails.  {prefix}delay.silent waits with no progress and no output.\n"
        "\n"
        "msg= says what the wait is for, printed when it starts.  It takes\n"
        "the rest of the line as written, spaces and all, so it comes last\n"
        "and needs no quotes.\n"
        "\n"
        "Example:\n"
        "  AT+RESET\n"
        "  {prefix}delay 2s msg=Waiting for the board to restart..."
    ),
    handler=_handler,
    params=[
        ParamSpec(
            "duration",
            "duration",
            positional=True,
            required=True,
            help="how long to wait, e.g. 500ms, 1.5s, 2m",
        ),
        ParamSpec(
            "msg",
            "str",
            rest=True,
            hint="<text>",
            help="what the wait is for, the rest of the line",
        ),
    ],
)
