"""Example plugin: print the current timestamp.

Demonstrates using the config and writing output.
"""

from datetime import datetime

NAME = "ts"
ARGS = ""
HELP = "Print the current date and time."


def handler(ctx, args):
    fmt = "%Y-%m-%d %H:%M:%S"
    ctx.write(f"{datetime.now().strftime(fmt)}", "green")
