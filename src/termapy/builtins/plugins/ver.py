"""Built-in plugin: show termapy version."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> None:
    from importlib.metadata import version
    try:
        ver = version("termapy")
    except Exception:
        ver = "unknown"
    ctx.write(f"termapy v{ver}")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ver",
    help="Show termapy version.",
    handler=_handler,
)
