"""Example plugin: print the current timestamp.

Demonstrates using the config and writing output.
"""

from datetime import datetime

from termapy.plugins import Command


def _handler(ctx, args):
    fmt = "%Y-%m-%d %H:%M:%S"
    ctx.write(f"{datetime.now().strftime(fmt)}", "green")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Print the current date and time.",
    name="ts",
    handler=_handler,
)
