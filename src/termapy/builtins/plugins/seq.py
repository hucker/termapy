"""Built-in plugin: show or reset sequence counters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Show current sequence counter values.

    Sequence counters are auto-incremented by ``{seq}`` template
    expansions in scripts. Displays all current counter values.

    Args:
        ctx: Plugin context for engine state and output.
        args: Unused.
    """
    counters = ctx.engine.get_seq_counters()
    if counters:
        parts = [f"seq{k}={v}" for k, v in sorted(counters.items())]
        ctx.write(f"Counters: {', '.join(parts)}")
    else:
        ctx.write("No counters set.")
    return CmdResult.ok()


def _handler_reset(ctx: PluginContext, args: str) -> CmdResult:
    """Reset all sequence counters to zero.

    Args:
        ctx: Plugin context for engine state and output.
        args: Unused.
    """
    ctx.engine.reset_seq()
    ctx.write("Sequence counters reset.")
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="seq",
    help="Show sequence counters.",
    long_help="""\
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
    /ss.txt capture_{seq1+}  -> capture_1.txt, capture_2.txt, ...""",
    handler=_handler,
    sub_commands={
        "reset": Command(
            help="Reset all counters to zero.",
            handler=_handler_reset,
        ),
    },
)
