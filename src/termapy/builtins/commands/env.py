"""Built-in transform + command: expand $(env.NAME) placeholders in CLI commands."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from termapy.env_flags import env_access_blocked
from termapy.help_dynamic import compose, green
from termapy.matching import matches
from termapy.plugins import CmdResult, Column, Command, Transform, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# The namespace holding the session's environment, name -> value.
_ENV_NS = "env"

# Shared refusal message so the command handlers and the transform speak
# with one voice about why env access is off.
_MCP_BLOCKED_MSG = (
    "Environment access is disabled under MCP.  Set "
    "TERMAPY_MCP_ENV_ENABLED=1 in the server's shell to enable "
    "$(env.NAME) expansion and the env commands."
)


def _env(ctx: PluginContext) -> dict[str, str]:
    """The session's environment: a snapshot of the process's, taken at first use.

    A load empties the namespace, which drops what ``/env.set`` added, and the
    next read takes the snapshot again.

    Args:
        ctx: Plugin context holding the snapshot.

    Returns:
        The live name -> value store.
    """
    env = ctx.ns(_ENV_NS)
    if not env:
        env.update(os.environ)
    return env


def _cli_transform(ctx: PluginContext, text: str) -> str:
    """Replace $(env.NAME) placeholders with environment variable values.

    Syntax:
        $(env.NAME)          - expand; error if not set
        $(env.NAME|default)  - expand; use 'default' if not set

    Variable names must be word characters (a-z, A-Z, 0-9, _).

    Args:
        ctx: Plugin context holding the snapshot.
        text: CLI command string potentially containing $(env.NAME).

    Returns:
        String with known placeholders replaced.
    """
    # Under MCP without opt-in, refuse to expand rather than leak a
    # single variable at a time (/print $(env.AWS_SECRET_ACCESS_KEY)).
    # Only raise when the text actually contains a placeholder, so
    # ordinary commands pass through untouched.
    if env_access_blocked() and "$(env." in text:
        raise ValueError(_MCP_BLOCKED_MSG)

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        fallback = m.group(2)
        val = _env(ctx).get(name)
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


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List environment variables available for $(env.NAME) expansion.

    With no arguments, lists all captured variables (sorted).  A name
    pattern filters them (registry ``name-one-matcher``): ``path`` lists
    every name containing it, ``USER*`` is a glob, and none matching is
    an empty table.  A script reads one value with ``$(env.NAME)``.

    Args:
        ctx: Plugin context for output.
        args: Optional name pattern to filter by.
    """
    if env_access_blocked():
        return CmdResult.fail(msg=_MCP_BLOCKED_MSG)
    pattern = args.strip()
    # A list answer (registry list-one-format).  The data is the
    # name -> value mapping, so a consumer looks a name up.
    env = _env(ctx)
    names = sorted(name for name in env if matches(name, pattern))
    records = [{"name": name, "value": env[name]} for name in names]
    return CmdResult.table(
        "Environment snapshot",
        "variable",
        _ENV_COLUMNS,
        records,
        data={name: env[name] for name in names},
    )


_ENV_COLUMNS = (
    Column("NAME", lambda record: record["name"]),
    Column("VALUE", lambda record: record["value"]),
)


def _handler_set(ctx: PluginContext, args: str) -> CmdResult:
    """Set a session-scoped environment variable.

    Updates only the in-memory snapshot - does not modify the OS
    environment.  The value is available immediately for $(env.NAME)
    expansion and persists until the next config load, /env.reload or
    the app restarting.

    Args:
        ctx: Plugin context for output.
        args: ``"NAME value"`` string (both required).
    """
    if env_access_blocked():
        return CmdResult.fail(msg=_MCP_BLOCKED_MSG)
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        raise UsageError()
    name, value = parts
    _env(ctx)[name] = value
    ctx.io.output(f"  {name}={value}", "green")
    return CmdResult.ok(value=value)


def _handler_reload(ctx: PluginContext, args: str) -> CmdResult:
    """Re-snapshot the process environment.

    Replaces the frozen environment dict with a fresh copy of
    os.environ.  Useful if environment variables were changed
    after the application started.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    if env_access_blocked():
        return CmdResult.fail(msg=_MCP_BLOCKED_MSG)
    env = ctx.ns(_ENV_NS)
    env.clear()
    env.update(os.environ)
    ctx.io.output(f"Environment reloaded ({len(env)} vars).", "green")
    return CmdResult.ok(value=len(env))


# ── Dynamic long_help ─────────────────────────────────────────────────────────

_ENV_PROSE = """\
The $(env.NAME) transform expands placeholders in REPL commands
using a snapshot of the process environment, taken at first use and again
after a config load.

Syntax:
  $(env.PORT)            - expand, error if not set
  $(env.PORT|COM3)       - expand, use COM3 as fallback

Commands:
  {prefix}env.list              - list all captured variables
  {prefix}env.list path         - the names containing path (USER* globs)
  {prefix}env.set PORT COM7     - set a variable until the next config load
  {prefix}env.reload            - re-snapshot from OS environment"""


def _env_state_line(ctx: PluginContext) -> str:
    """Green one-liner: size of the frozen environment snapshot."""
    return green(f"Snapshot size = {len(_env(ctx))} variables")


def _env_long_help(ctx: PluginContext) -> str:
    return compose(_env_state_line(ctx), _ENV_PROSE)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="advanced",
    name="env",
    args="{pattern}",
    handler=_handler_list,
    shows=True,
    help="Print environment variables for $(env.NAME) expansion (alias: {prefix}env.list).",
    intro=(
        "Use the process environment in commands and configs as $(env.NAME), so a "
        "checked-in config stays portable between machines."
    ),
    long_help=_env_long_help,
    sub_commands={
        "list": Command(
            args="{pattern}",
            help="Explicit alias for bare {prefix}env.",
            long_help=_env_state_line,
            handler=_handler_list,
            shows=True,
        ),
        "set": Command(
            args="<name> <value>",
            help="Set a variable until the next config load (in-memory only).",
            long_help=_env_state_line,
            handler=_handler_set,
        ),
        "reload": Command(
            help="Re-snapshot the process environment.",
            long_help=_env_state_line,
            handler=_handler_reload,
        ),
    },
)

TRANSFORM = Transform(
    name="env_var",
    help="Expand $(env.NAME) placeholders from the process environment.",
    repl=_cli_transform,
)
