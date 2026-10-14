"""Example plugin: a simple greeting command.

Drop this file into termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config) to make /hello available.

The shape every built-in command has: a typed ``_handler`` that returns a
``CmdResult`` with a scriptable value, and the ``COMMAND`` declaration last.
"""

from termapy.plugins import CmdResult, Command, PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    name = args.strip() or "world"
    greeting = f"Hello, {name}!"
    ctx.io.result(greeting)
    # The value is what a script captures with ``$(HI) <- /hello you``.
    return CmdResult.ok(value=greeting)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="hello",
    args="{name}",
    help="Say hello. Demonstrates a minimal plugin.",
    handler=_handler,
)
