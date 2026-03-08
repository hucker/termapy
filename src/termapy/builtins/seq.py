"""Built-in plugin: show or reset sequence counters."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "seq"
ARGS = "{reset}"
HELP = "Show sequence counters, or reset them."


def handler(ctx: PluginContext, args: str) -> None:
    """Show current sequence counters or reset them."""
    if args.strip().lower() == "reset":
        ctx.engine.reset_seq()
        ctx.write("Sequence counters reset.")
    else:
        counters = ctx.engine.get_seq_counters()
        if counters:
            parts = [f"seq{k}={v}" for k, v in sorted(counters.items())]
            ctx.write(f"Counters: {', '.join(parts)}")
        else:
            ctx.write("No counters set.")
