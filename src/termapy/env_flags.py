"""Security-policy env-var gates, read once at process start.

Two policy flags live outside the cfg file because the cfg is exactly
what the policy needs to defend against -- a hostile cfg cannot flip
its own gates if the gates live in the process environment.  This
mirrors npm's ``--ignore-scripts`` / ``NPM_CONFIG_IGNORE_SCRIPTS``
shape and Python's own ``PYTHONNOUSERSITE``.

The two flags:

- ``TERMAPY_TRUSTED_PLUGINS_ONLY``: when truthy, the plugin loader
  skips the two filesystem-discovery passes (global plugin folder and
  per-cfg plugin folder).  Built-in plugins -- which ship with the
  wheel in ``src/termapy/builtins/commands/`` -- always load
  regardless.  Trust boundary becomes "your Python site-packages,"
  identical to every other Python tool.

- ``TERMAPY_OS_CMD_ENABLED``: when truthy, the ``/os`` shell-escape
  built-in is enabled.  Was a cfg key (``os_cmd_enabled``) through
  v0.65; retired to env-var-only in v0.66 because cfg-level policy
  cannot defend against a cfg that simply flips its own flag.

- ``TERMAPY_MCP_ENV_ENABLED``: when truthy, process-environment access
  (``/env`` / ``/env.list`` and the ``$(env.NAME)`` transform) is
  allowed while running as an MCP server.  Default off: an MCP client
  is a remote/automated peer, and environment variables routinely hold
  secrets (API tokens, credentials), so a bare ``/env`` would dump them
  all in one call.  Interactive hosts (CLI/TUI) are the user's own
  shell and are never gated -- the gate applies only under ``--mcp``.
  Same env-var-not-cfg rationale as ``/os``: a hostile cfg must not be
  able to grant itself the read.

The ``*_ENABLED`` / ``*_ONLY`` policy flags are evaluated **once at
import time** and cached as module-level constants.  Plugin code
mutating ``os.environ`` at runtime cannot retroactively flip a decision
the loader already made -- the result has been captured in a local
Python variable.

Every variable here is a boolean a user typed, so it is read through
``scripting.parse_bool`` (registry ``bool-one-vocabulary``): any token of
the one vocabulary, case-insensitive.  Unset or blank is the declared
default -- off for a gate, "no override" for ``TERMAPY_GUI``.  A present
value outside the vocabulary is a startup error: the reader records it
and ``termapy`` refuses to start, naming the variable (see
``startup_error``), because the alternative is a typo that silently
means off.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from termapy.scripting import parse_bool

# Errors found while reading the environment at import, one message per
# variable.  A reader never raises here: ``plugins.capabilities`` builds
# its tables at import, so a raise would surface as a traceback from a
# plain ``import termapy.cli``.  Instead the entry point asks
# ``startup_error()`` right after parsing its arguments and refuses to
# start, naming the variable.
STARTUP_ERRORS: list[str] = []


def _read_gate(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    errors: list[str] | None = None,
) -> bool:
    """Read a policy gate: unset or blank is off; garbage fails closed and is recorded.

    ``environ`` and ``errors`` default to the process environment and
    ``STARTUP_ERRORS``; tests pass their own so the reader is exercised
    with explicit input rather than environment manipulation.
    """
    source = os.environ if environ is None else environ
    sink = STARTUP_ERRORS if errors is None else errors
    try:
        return parse_bool(source.get(name), default=False, what=name)
    except ValueError as e:
        sink.append(str(e))
        return False


def _read_override(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    errors: list[str] | None = None,
) -> bool | None:
    """Read an override: unset or blank is no override (None); garbage is recorded and ignored."""
    source = os.environ if environ is None else environ
    sink = STARTUP_ERRORS if errors is None else errors
    try:
        return parse_bool(source.get(name), default=None, what=name)
    except ValueError as e:
        sink.append(str(e))
        return None


def startup_error() -> str | None:
    """The first environment error, or None.  The entry point refuses to start on one."""
    return STARTUP_ERRORS[0] if STARTUP_ERRORS else None


# Module-level constants, frozen at import time.  Re-evaluating these
# would defeat the design -- they are intentionally NOT functions.
TRUSTED_PLUGINS_ONLY: bool = _read_gate("TERMAPY_TRUSTED_PLUGINS_ONLY")
OS_CMD_ENABLED: bool = _read_gate("TERMAPY_OS_CMD_ENABLED")
MCP_ENV_ENABLED: bool = _read_gate("TERMAPY_MCP_ENV_ENABLED")

# Per-class opt-ins that lift the MCP host sandbox.  Default off: an MCP
# client is a remote/automated peer, so host access beyond the device is
# refused unless the operator opts in from the server's shell.  Each maps
# to one restrictive capability the MCP host grants only when set (see
# mcp/server.py); interactive hosts (CLI/TUI) grant both unconditionally.
#   MCP_FS_UNCONFINED -> filesystem_unconfined (paths outside the cfg dir)
#   MCP_NET_EGRESS    -> network_egress (serial-over-URL socket://, rfc2217://)
MCP_FS_UNCONFINED: bool = _read_gate("TERMAPY_MCP_FS_UNCONFINED")
MCP_NET_EGRESS: bool = _read_gate("TERMAPY_MCP_NET_EGRESS")

# Not a policy gate: the GUI-apps detection override (see
# ``plugins.capabilities.detect_gui_apps``).  Read here with the other
# environment booleans so a bad value is refused at startup instead of
# raising inside the capability tables that are built at import.  None
# means "no override, use the heuristic".
GUI_OVERRIDE: bool | None = _read_override("TERMAPY_GUI")


# Runtime marker (NOT a frozen policy constant): set once by the MCP
# server entry point so ctx-less code -- specifically the $(env.NAME)
# transform, a pure string function with no PluginContext -- can tell it
# is running under --mcp.  Command handlers that DO have a ctx can read
# ``ctx.capabilities.interactive`` instead; this exists for the paths
# that can't.  Left False for CLI/TUI/tests, so they are never gated.
_under_mcp: bool = False


def mark_under_mcp() -> None:
    """Record that this process is serving MCP.  Called once at startup.

    Idempotent.  Deliberately a one-way latch: nothing clears it, because
    a process that has begun speaking the MCP wire protocol never becomes
    an interactive session.
    """
    global _under_mcp
    _under_mcp = True


def env_access_blocked() -> bool:
    """True when process-environment access must be refused right now.

    The policy: block iff we are under MCP and the operator has not
    opted in via ``TERMAPY_MCP_ENV_ENABLED``.  Interactive hosts
    (``_under_mcp`` False) are never blocked.  Consulted by both the
    ``/env*`` command handlers and the ``$(env.NAME)`` transform so the
    two share one decision.
    """
    return _under_mcp and not MCP_ENV_ENABLED


__all__ = [
    "TRUSTED_PLUGINS_ONLY",
    "OS_CMD_ENABLED",
    "MCP_ENV_ENABLED",
    "MCP_FS_UNCONFINED",
    "MCP_NET_EGRESS",
    "GUI_OVERRIDE",
    "STARTUP_ERRORS",
    "startup_error",
    "mark_under_mcp",
    "env_access_blocked",
]
