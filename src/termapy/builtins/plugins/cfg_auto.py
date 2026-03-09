"""Built-in plugin: set a config key immediately (no confirmation)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "cfg_auto"
ARGS = "<key> <value>"
HELP = "Set a config key immediately (no confirmation)."


def handler(ctx: PluginContext, args: str) -> None:
    """Set a config key immediately without confirmation dialog.

    Validates the key exists and coerces the value to match the
    existing type, then applies and saves in one step. Useful in
    scripts where interactive confirmation is not possible.

    Args:
        ctx: Plugin context for config access and output.
        args: ``"key value"`` string (both required).
    """
    parts = args.strip().split(None, 1)
    if not parts or len(parts) < 2:
        ctx.write("Usage: !!cfg_auto <key> <value>", "red")
        return
    key, value_str = parts[0], parts[1]
    if key not in ctx.cfg:
        ctx.write(f"Unknown config key: {key}", "red")
        return
    try:
        new_val = ctx.engine.coerce_type(value_str, ctx.cfg[key])
    except (ValueError, TypeError) as e:
        ctx.write(f"Type error: {e}", "red")
        return
    ctx.engine.apply_cfg(key, new_val)
