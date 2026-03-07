"""Built-in plugin: show or reset sequence counters."""

NAME = "seq"
ARGS = "{reset}"
HELP = "Show sequence counters, or reset them."


def handler(ctx, args):
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
