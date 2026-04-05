"""Built-in plugin: file transfer settings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the file transfer root directory.

    With no argument, shows the current root. With a path argument,
    sets ``file_xfer_root`` in the config. Both XMODEM and YMODEM
    resolve relative paths against this directory.

    Args:
        ctx: Plugin context.
        args: Optional directory path to set.
    """
    arg = args.strip()

    if not arg:
        root = ctx.cfg.get("file_xfer_root", "")
        if root:
            resolved = Path(root).resolve()
            ctx.result(str(resolved))
        else:
            ctx.result(f"{ctx.cap_dir}  (default)")
        return CmdResult.ok()

    # Set the root
    path = Path(arg)
    if not path.is_dir():
        return CmdResult.fail(msg=f"Directory not found: {path.resolve()}")

    resolved = str(path.resolve())
    ctx.engine.apply_cfg("file_xfer_root", resolved)
    ctx.result(f"Transfer root: {resolved}")
    return CmdResult.ok(value=resolved)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="xfer",
    help="File transfer settings.",
    handler=None,
    sub_commands={
        "root": Command(
            args="{path}",
            help="Show or set the file transfer root directory.",
            handler=_handler_root,
        ),
    },
)
