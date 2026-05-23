"""Built-in plugin: run a shell command.

Named os_cmd.py because 'os.py' would shadow Python's os module.

Gated by the ``TERMAPY_OS_CMD_ENABLED`` env var, NOT a cfg key.
The cfg cannot grant /os to itself -- a hostile cfg flipping its
own gate would defeat the policy.  Set the env var once in your
shell session (or never, leaving /os disabled).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from termapy.env_flags import OS_CMD_ENABLED
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Run a shell command and display its output.

    Gated by the ``TERMAPY_OS_CMD_ENABLED`` env var (truthy ``1`` /
    ``true`` / ``yes`` / ``on``).  Runs the command via
    ``subprocess.run()`` with a 10-second timeout.  Stdout is
    displayed in white, stderr in red.

    Args:
        ctx: Plugin context for output.
        args: Shell command string to execute.
    """
    if not OS_CMD_ENABLED:
        return CmdResult.fail(
            msg=(
                "/os is disabled.  Set TERMAPY_OS_CMD_ENABLED=1 in "
                "your shell to enable.  (Was a cfg key through v0.65; "
                "retired to env-var-only because the cfg cannot defend "
                "against the cfg.)"
            )
        )
    if not args.strip():
        return CmdResult.fail(msg="Usage: /os <command>")
    try:
        result = subprocess.run(
            args, shell=True, capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            ctx.io.output(line, "white")
        for line in result.stderr.splitlines():
            ctx.io.output(line, "red")
    except subprocess.TimeoutExpired:
        return CmdResult.fail(msg="Command timed out (10s limit)")
    # Return stdout so scripts can capture command output via
    # ``$(OUT) <- /os hostname`` etc.
    return CmdResult.ok(value=result.stdout)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="os",
    args="<cmd>",
    help="Run a shell command and show output (10s timeout). e.g. {prefix}os dir",
    long_help="""\
Runs a shell command via the system shell and displays its output.
Stdout is shown in white, stderr in red.

Gated by the TERMAPY_OS_CMD_ENABLED env var (disabled by default
for safety).  Set it to 1 in your shell to enable:

    # bash/zsh
    export TERMAPY_OS_CMD_ENABLED=1
    # PowerShell
    $env:TERMAPY_OS_CMD_ENABLED = "1"

Commands time out after 10 seconds.

Examples:
  /os dir                - list files (Windows)
  /os ls -la             - list files (Unix)
  /os python --version   - check Python version
  /os ping -c 1 host     - network test""",
    handler=_handler,
    needs=CapabilitySet(interactive=True),  # shell exec; interactive only
)
