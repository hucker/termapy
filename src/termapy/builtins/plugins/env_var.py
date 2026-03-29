"""Built-in transform + command: expand $(env.NAME) placeholders in CLI commands."""

from __future__ import annotations

import fnmatch
import os
import re
from typing import TYPE_CHECKING

from termapy.plugins import Command, Transform
from termapy.scripting import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# Snapshot environment at plugin load time - frozen for the session.
_ENV = dict(os.environ)


def _cli_transform(text: str) -> str:
    """Replace $(env.NAME) placeholders with environment variable values.

    Syntax:
        $(env.NAME)          - expand; error if not set
        $(env.NAME|default)  - expand; use 'default' if not set

    Variable names must be word characters (a-z, A-Z, 0-9, _).

    Args:
        text: CLI command string potentially containing $(env.NAME).

    Returns:
        String with known placeholders replaced.
    """
    def _replace(m: re.Match) -> str:
        name = m.group(1)
        fallback = m.group(2)
        val = _ENV.get(name)
        if val is not None:
            return val
        if fallback is not None:
            return fallback
        raise ValueError(f"$(env.{name}) - variable not set (use |fallback for a default)")

    return re.sub(
        r"\$\(env\.(\w+)(?:\|([^)]*))?\)",
        _replace,
        text,
    )


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List environment variables available for $(env.NAME) expansion.

    With no arguments, lists all captured variables (sorted).
    With a glob pattern (e.g. USER*), lists matching variables.
    With an exact name, shows that single variable's value.

    Args:
        ctx: Plugin context for output.
        args: Optional variable name or glob pattern to filter by.
    """
    pattern = args.strip()
    if pattern:
        if any(c in pattern for c in "*?[]"):
            matches = {k: v for k, v in _ENV.items()
                       if fnmatch.fnmatch(k, pattern)}
            if matches:
                for k in sorted(matches):
                    ctx.write(f"  {k}={matches[k]}")
            else:
                return CmdResult.fail(msg=f"  No variables matching {pattern}")
            return CmdResult.ok()
        val = _ENV.get(pattern)
        if val is not None:
            ctx.write(f"  {pattern}={val}")
        else:
            return CmdResult.fail(msg=f"  {pattern} - not set")
        return CmdResult.ok()
    ctx.write(f"Environment snapshot ({len(_ENV)} vars):")
    for k in sorted(_ENV):
        ctx.write(f"  {k}={_ENV[k]}")
    return CmdResult.ok()


def _handler_set(ctx: PluginContext, args: str) -> CmdResult:
    """Set a session-scoped environment variable.

    Updates only the in-memory snapshot - does not modify the OS
    environment.  The value is available immediately for $(env.NAME)
    expansion and persists until the app is restarted or /env.reload
    is called.

    Args:
        ctx: Plugin context for output.
        args: ``"NAME value"`` string (both required).
    """
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        return CmdResult.fail(msg="Usage: /env.set <name> <value>")
    name, value = parts
    _ENV[name] = value
    ctx.write(f"  {name}={value}", "green")
    return CmdResult.ok()


def _handler_reload(ctx: PluginContext, args: str) -> CmdResult:
    """Re-snapshot the process environment.

    Replaces the frozen environment dict with a fresh copy of
    os.environ.  Useful if environment variables were changed
    after the application started.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    _ENV.clear()
    _ENV.update(os.environ)
    ctx.write(f"Environment reloaded ({len(_ENV)} vars).", "green")
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="env",
    help="Manage $(env.NAME) expansion for CLI commands.",
    long_help="""\
The $(env.NAME) transform expands placeholders in REPL commands
using a snapshot of the process environment taken at startup.

Syntax:
  $(env.PORT)            - expand, error if not set
  $(env.PORT|COM3)       - expand, use COM3 as fallback

Commands:
  /env.list              - list all captured variables
  /env.list PATH         - show a single variable's value
  /env.set PORT COM7     - set a session-scoped variable
  /env.reload            - re-snapshot from OS environment""",
    sub_commands={
        "list": Command(
            args="{pattern}",
            help="Show environment variables (all, by name, or glob pattern).",
            handler=_handler_list,
        ),
        "set": Command(
            args="<name> <value>",
            help="Set a session-scoped variable (in-memory only).",
            handler=_handler_set,
        ),
        "reload": Command(
            help="Re-snapshot the process environment.",
            handler=_handler_reload,
        ),
    },
)

TRANSFORM = Transform(
    name="env_var",
    help="Expand $(env.NAME) placeholders from the process environment.",
    repl=_cli_transform,
)
