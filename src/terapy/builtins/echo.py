"""Built-in plugin: toggle REPL command echo."""

NAME = "echo"
ARGS = "{on | off}"
HELP = "Toggle REPL command echo, or set on/off. Output is not affected."


def handler(ctx, args):
    arg = args.strip().lower()
    if arg == "on":
        ctx.engine.set_echo(True)
    elif arg == "off":
        ctx.engine.set_echo(False)
    else:
        ctx.engine.set_echo(not ctx.engine.get_echo())
    state = "on" if ctx.engine.get_echo() else "off"
    ctx.write(f"REPL echo {state}.", "green")
