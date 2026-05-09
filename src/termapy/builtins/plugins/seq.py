"""Built-in plugin: show or reset sequence counters.

This plugin owns the ``seq`` namespace.  Layout:

- Integer keys ``0..9`` hold the counter values used by ``{seqN}`` and
  ``{seqN+}`` template expansions in scripts.
- String key ``"_start_time"`` holds the timestamp substituted for the
  ``{starttime}`` placeholder.  Set on ``on_app_start`` and refreshed
  on every ``on_script_start``.

The leading underscore on ``_start_time`` is a convention signalling
"plugin-internal, don't walk this as a counter."
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from termapy.help_dynamic import compose, green
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _now() -> str:
    """Return a filename-safe timestamp (YYYYmmdd_HHMMSS)."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def on_app_start(ctx: PluginContext) -> None:
    """Seed the seq namespace with a start-time timestamp.

    Fires once after plugins load and the context is wired.  Counters
    start empty; they are created on first ``{seqN+}`` expansion.
    """
    ctx.ns("seq")["_start_time"] = _now()


def on_script_start(ctx: PluginContext) -> None:
    """Reset counters and refresh the start-time timestamp.

    Fires only at the outermost script boundary.  Nested ``/run`` does
    not re-fire, so inner scripts inherit the outer script's counters
    and start time.
    """
    seq = ctx.ns("seq")
    seq.clear()
    seq["_start_time"] = _now()


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Show current sequence counter values.

    Sequence counters are auto-incremented by ``{seq}`` template
    expansions in scripts. Displays all current counter values.

    Args:
        ctx: Plugin context for engine state and output.
        args: Unused.
    """
    # Integer keys only -- _start_time is plugin-internal, not a counter.
    counters = {k: v for k, v in ctx.ns("seq").items() if isinstance(k, int)}
    if counters:
        parts = [f"seq{k}={v}" for k, v in sorted(counters.items())]
        ctx.io.write(f"Counters: {', '.join(parts)}")
    else:
        ctx.io.write("No counters set.")
    return CmdResult.ok()


def _handler_reset(ctx: PluginContext, args: str) -> CmdResult:
    """Reset all sequence counters to zero.

    Preserves ``_start_time`` so ``{starttime}`` keeps working until
    the next script starts.

    Args:
        ctx: Plugin context for engine state and output.
        args: Unused.
    """
    seq = ctx.ns("seq")
    start_time = seq.get("_start_time", "")
    seq.clear()
    seq["_start_time"] = start_time
    ctx.io.write("Sequence counters reset.")
    return CmdResult.ok()


# ── Dynamic long_help ─────────────────────────────────────────────────────────

_SEQ_PROSE = """\
Sequence counters are used in script templates for auto-numbering.

Placeholders:
  {seq1+}  - increment counter 1, then substitute its value
  {seq1}   - substitute counter 1 without incrementing
  {seq2+}  - independent counter 2 (any digit 0-9)

Counters start at 0. Incrementing a higher-level counter resets
all lower-level counters (e.g. {seq1+} resets seq2, seq3, etc.).

Use cases:
  Automatic test numbering in scripts:
    Test {seq1+}           -> Test 1, Test 2, Test 3, ...
    Test {seq1}.{seq2+}    -> Test 1.1, Test 1.2, ...
    Test {seq1+}.{seq2+}   -> Test 2.1 (seq2 resets on seq1 increment)

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
    help="Show sequence counters.",
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
