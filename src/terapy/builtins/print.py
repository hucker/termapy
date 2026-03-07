"""Built-in plugin: print a message to the terminal."""

NAME = "print"
ARGS = "<text>"
HELP = "Print a message to the terminal."


def handler(ctx, args):
    ctx.write(args)
