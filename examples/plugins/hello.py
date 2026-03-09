"""Example plugin: a simple greeting command.

Drop this file into termapy_cfg/plugins/ (global) or
termapy_cfg/<config>/plugins/ (per-config) to make !hello available.
"""

NAME = "hello"
ARGS = "{name}"
HELP = "Say hello. Demonstrates a minimal plugin."


def handler(ctx, args):
    name = args.strip() or "world"
    ctx.write(f"Hello, {name}!")
