"""Built-in plugin: run a shell command."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "os"
ARGS = "<cmd>"
HELP = "Run a shell command and show output (10s timeout). e.g. !!os dir"


def handler(ctx: PluginContext, args: str) -> None:
    """Run a shell command and display its output.

    Requires ``os_cmd_enabled: true`` in the config. Runs the command
    via ``subprocess.run()`` with a 10-second timeout. Stdout is
    displayed in white, stderr in red.

    Args:
        ctx: Plugin context for config access and output.
        args: Shell command string to execute.
    """
    if not ctx.cfg.get("os_cmd_enabled"):
        ctx.write("!!os is disabled. Set os_cmd_enabled: true in config.", "red")
        return
    if not args.strip():
        ctx.write("Usage: !!os <command>", "red")
        return
    try:
        result = subprocess.run(
            args, shell=True, capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            ctx.write(line, "white")
        for line in result.stderr.splitlines():
            ctx.write(line, "red")
    except subprocess.TimeoutExpired:
        ctx.write("Command timed out (10s limit)", "red")
