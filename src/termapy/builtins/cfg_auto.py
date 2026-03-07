"""Built-in plugin: set a config key immediately (no confirmation)."""

NAME = "cfg_auto"
ARGS = "<key> <value>"
HELP = "Set a config key immediately (no confirmation)."


def handler(ctx, args):
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
