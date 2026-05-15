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

Both are evaluated **once at import time** and cached as
module-level constants.  Plugin code mutating ``os.environ`` at
runtime cannot retroactively flip a decision the loader already made
-- the result has been captured in a local Python variable.

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


__all__ = ["TRUSTED_PLUGINS_ONLY", "OS_CMD_ENABLED", "_truthy"]
