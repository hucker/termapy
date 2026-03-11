"""Example plugin: a simple greeting command.

Drop this file into termapy_cfg/plugins/ (global) or
termapy_cfg/<config>/plugins/ (per-config) to make !hello available.
"""


def _handler(ctx, args):
    name = args.strip() or "world"
    ctx.write(f"Hello, {name}!")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = {
    "name": "hello",
    "args": "{name}",
    "help": "Say hello. Demonstrates a minimal plugin.",
    "handler": _handler,
}
