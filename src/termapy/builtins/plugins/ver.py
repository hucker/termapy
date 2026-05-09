"""Built-in plugin: show termapy version."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    # PackageNotFoundError fires when termapy is being run from a
    # git clone without `pip install .` -- common during development.
    # That's the only exception this path can legitimately raise;
    # anything else is a bug worth surfacing.
    from importlib.metadata import PackageNotFoundError, version
    try:
        ver = version("termapy")
    except PackageNotFoundError:
        ver = "unknown"
    ver_str = f"termapy v{ver}"
    ctx.io.result(ver_str)
    return CmdResult.ok(value=ver_str)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ver",
    help="Show termapy version.",
    handler=_handler,
)
