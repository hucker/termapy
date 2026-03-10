"""Built-in plugin: show or change config values."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "cfg"
ARGS = "{key {value}}"
HELP = "No args: show config. Key only: show value. Key+value: confirm dialog."
LONG_HELP = """\
Three modes:
  !cfg              — show all config key/value pairs
  !cfg baudrate     — show current value of 'baudrate'
  !cfg baudrate 115200 — change with confirmation dialog

Type is auto-detected from the existing value (int, float,
bool, string). Bool accepts: true/false, yes/no, on/off, 1/0.
Changes are saved to the JSON config file.

Use !cfg_auto for the same thing without confirmation."""


def handler(ctx: PluginContext, args: str) -> None:
    """Show all config, a single key, or set a key with confirmation.

    With no arguments, prints every key/value pair. With a key only,
    prints that key's current value. With key and value, validates the
    type against the existing value and delegates to the confirmation
    dialog (or applies directly if no dialog is configured).

    Args:
        ctx: Plugin context for config access and output.
        args: Optional ``"key"`` or ``"key value"`` string.
    """
    parts = args.strip().split(None, 1)
    # !cfg — show all
    if not parts:
        for k, v in ctx.cfg.items():
            ctx.write(f"  {k}: {v!r}")
        return
    key = parts[0]
    if key not in ctx.cfg:
        ctx.write(f"Unknown config key: {key}", "red")
        return
    # !cfg key — show value
    if len(parts) == 1:
        ctx.write(f"  {key}: {ctx.cfg[key]!r}")
        return
    # !cfg key value — validate and delegate for confirmation
    value_str = parts[1]
    try:
        new_val = ctx.engine.coerce_type(value_str, ctx.cfg[key])
    except (ValueError, TypeError) as e:
        ctx.write(f"Type error: {e}", "red")
        return
    old_val = ctx.cfg[key]
    if new_val == old_val:
        ctx.write(f"{key} is already {old_val!r}", "dim")
        return
    if ctx.engine.save_cfg:
        ctx.engine.save_cfg(key, new_val)
    else:
        ctx.engine.apply_cfg(key, new_val)
