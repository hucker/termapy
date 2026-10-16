"""Built-in plugin: ``/demo`` -- switch to the built-in demo device.

One handler in every frontend (review 2026-10-15, D6).  The TUI used to
switch on a background worker and answer before the switch happened, so
a script line after ``/demo`` ran against the old config; the CLI switched
first.  Now this writes the demo files and hands the switch to
``ctx.internal.load_config``, the path ``/cfg.load`` takes, which returns
after the switch in every frontend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.config import cfg_dir, setup_demo_config
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _switch_to_demo(ctx: PluginContext, args: str, force: bool) -> CmdResult:
    """Write the demo config (all of it again with ``force``), then load it.

    Arguments are refused rather than ignored: ``/demo --force`` once
    meant the reset, and silently switching without it would mislead.
    """
    if args.strip():
        raise UsageError()
    try:
        path = setup_demo_config(cfg_dir(), force=force)
    except OSError as e:
        return CmdResult.fail(msg=f"Demo setup error: {e}")
    return ctx.internal.load_config(str(path))


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    return _switch_to_demo(ctx, args, force=False)


def _handler_force(ctx: PluginContext, args: str) -> CmdResult:
    return _switch_to_demo(ctx, args, force=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="demo",
    help="Switch to the built-in demo device (creates its config if needed).",
    handler=_handler,
    needs=CapabilitySet.INTERACTIVE,
    sub_commands={
        "force": Command(
            help="Reset the demo config to its defaults and switch to it.",
            handler=_handler_force,
            needs=CapabilitySet.INTERACTIVE,
        ),
    },
)
