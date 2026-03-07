"""Built-in plugin: abort a running script."""

NAME = "stop"
ARGS = ""
HELP = "Abort a running script."


def handler(ctx, args):
    if ctx.engine.in_script():
        ctx.engine.script_stop()
        ctx.write("Stopping script...")
    else:
        ctx.write("No script running.")
