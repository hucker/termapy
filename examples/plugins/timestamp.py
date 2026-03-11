"""Example plugin: print the current timestamp.

Demonstrates using the config and writing output.
"""

from datetime import datetime


def _handler(ctx, args):
    fmt = "%Y-%m-%d %H:%M:%S"
    ctx.write(f"{datetime.now().strftime(fmt)}", "green")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = {
    "name": "ts",
    "args": "",
    "help": "Print the current date and time.",
    "handler": _handler,
}
