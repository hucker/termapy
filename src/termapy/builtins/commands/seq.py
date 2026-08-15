"""Built-in plugin: show or reset sequence counters.

This plugin owns the ``seq`` namespace.  Layout:

- Integer keys ``0..9`` hold the counter values used by ``{seqN}`` and
  ``{seqN+}`` template expansions in scripts.
- String key ``"_start_time"`` holds the timestamp substituted for the
  ``{starttime}`` placeholder.  Set on ``on_app_start`` and refreshed
  on every ``on_script_start``.

The leading underscore on ``_start_time`` is a convention signaling
"plugin-internal, don't walk this as a counter."
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.help_dynamic import compose, green
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import filename_timestamp

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _now() -> str:
    """Return a filename-safe timestamp (YYYYmmdd_HHMMSS)."""
    return filename_timestamp()


def on_app_start(ctx: PluginContext) -> None:
    """Seed the seq namespace with a start-time timestamp.

    Fires once after plugins load and the context is wired.  Counters
    start empty; they are created on first ``{seqN+}`` expansion.
    ``_start_time`` is the frozen string for ``{starttime}``; ``_start_perf``
    is the raw monotonic clock behind ``{elapsed}``.
    """
    seq = ctx.ns("seq")
    seq["_start_time"] = _now()
    seq["_start_perf"] = time.monotonic()


def on_script_start(ctx: PluginContext) -> None:
    """Reset counters and refresh the start-time timestamp.

    Fires only at the outermost script boundary.  Nested ``/run`` does
    not re-fire, so inner scripts inherit the outer script's counters
    and start time.
    """
    seq = ctx.ns("seq")
    seq.clear()
    seq["_start_time"] = _now()
    seq["_start_perf"] = time.monotonic()


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Show current sequence counter values.

    Sequence counters are auto-incremented by ``{seq}`` template
    expansions in scripts. Displays all current counter values.

    Args:
        ctx: Plugin context for state and output.
        args: Unused.
    """
    # Integer keys only -- _start_time is plugin-internal, not a counter.
    counters = {k: v for k, v in ctx.ns("seq").items() if isinstance(k, int)}
    if counters:
        parts = [f"seq{k}={v}" for k, v in sorted(counters.items())]
        line = ", ".join(parts)
        ctx.io.output(f"Counters: {line}")
        return CmdResult.ok(value=line)
    ctx.io.output("No counters set.")
    return CmdResult.ok(value="")


def _handler_reset(ctx: PluginContext, args: str) -> CmdResult:
    """Reset all sequence counters to zero.

    Preserves ``_start_time`` and ``_start_perf`` so ``{starttime}`` and
    ``{elapsed}`` keep working until the next script starts.

    Args:
        ctx: Plugin context for state and output.
        args: Unused.
    """
    seq = ctx.ns("seq")
    start_time = seq.get("_start_time", "")
    start_perf = seq.get("_start_perf")
    seq.clear()
    seq["_start_time"] = start_time
    if start_perf is not None:
        seq["_start_perf"] = start_perf
    ctx.io.output("Sequence counters reset.")
    return CmdResult.ok(value="reset")


# ── Dynamic long_help ─────────────────────────────────────────────────────────

_SEQ_PROSE = """\
Sequence counters are used in script templates for auto-numbering.

Placeholders:
  {seq1+}  - increment counter 1, then substitute its value
  {seq1}   - substitute counter 1 without incrementing
  {seq2+}  - deeper counter 2 (any digit 0-9; seq1 is the top level)

Counters start at 0. seq1 is the top level, seq2 the next, and so on.
Incrementing {seqN+} resets every deeper (higher-numbered) counter to 0,
so bumping an outer level restarts the inner ones (e.g. {seq1+} resets
seq2, seq3, ...). Always use + on the level you emit: a fresh level
counts 0 -> 1 on its first +.

Use cases:
  Test numbering (seq1 = section, seq2 = step):
    {seq1+}.{seq2+}   -> 1.1  (new section, first step)
    {seq1}.{seq2+}    -> 1.2  (same section, next step)
    {seq1+}.{seq2+}   -> 2.1  (new section resets the step, then +)

  Automatic file naming (e.g. screenshots in a script):
    /ss.txt capture_{seq1+}  -> capture_1.txt, capture_2.txt, ..."""


def _seq_state_line(ctx: PluginContext) -> str:
    """Green line showing current counter count + start-time stamp."""
    seq = ctx.ns("seq")
    counters = sum(1 for k in seq if isinstance(k, int))
    start = seq.get("_start_time", "(none)")
    word = "counter" if counters == 1 else "counters"
    return green(f"Currently set: {counters} {word}; start_time = {start}")


def _seq_long_help(ctx: PluginContext) -> str:
    return compose(_seq_state_line(ctx), _SEQ_PROSE)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="seq",
    help="Print sequence counters.",
    long_help=_seq_long_help,
    handler=_handler,
    needs=CapabilitySet(interactive=True),  # script-state primitive
    sub_commands={
        "reset": Command(
            help="Reset all counters to zero.",
            long_help=_seq_state_line,
            handler=_handler_reset,
            needs=CapabilitySet(interactive=True),
        ),
    },
)
