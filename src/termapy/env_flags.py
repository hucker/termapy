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

Truthy vocabulary (case-insensitive): ``1``, ``true``, ``yes``,
``on``.  Anything else, including unset and empty string, is false.
"""

from __future__ import annotations

import os


def _truthy(value: str | None) -> bool:
    """Parse an env-var value into a boolean using the standard vocabulary.

    Standalone (not just a closure inside the constants below) so tests
    can exercise the parser with explicit input rather than environment
    manipulation.
    """
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Module-level constants, frozen at import time.  Re-evaluating these
# would defeat the design -- they are intentionally NOT functions.
TRUSTED_PLUGINS_ONLY: bool = _truthy(os.environ.get("TERMAPY_TRUSTED_PLUGINS_ONLY"))
OS_CMD_ENABLED: bool = _truthy(os.environ.get("TERMAPY_OS_CMD_ENABLED"))
MCP_ENV_ENABLED: bool = _truthy(os.environ.get("TERMAPY_MCP_ENV_ENABLED"))

# Per-class opt-ins that lift the MCP host sandbox.  Default off: an MCP
# client is a remote/automated peer, so host access beyond the device is
# refused unless the operator opts in from the server's shell.  Each maps
# to one restrictive capability the MCP host grants only when set (see
# mcp/server.py); interactive hosts (CLI/TUI) grant both unconditionally.
#   MCP_FS_UNCONFINED -> filesystem_unconfined (paths outside the cfg dir)
#   MCP_NET_EGRESS    -> network_egress (serial-over-URL socket://, rfc2217://)
MCP_FS_UNCONFINED: bool = _truthy(os.environ.get("TERMAPY_MCP_FS_UNCONFINED"))
MCP_NET_EGRESS: bool = _truthy(os.environ.get("TERMAPY_MCP_NET_EGRESS"))


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
    "mark_under_mcp",
    "env_access_blocked",
    "_truthy",
]
