"""MCP stdio server entry point (Phase 1: stub).

Phase 1 ships only the dependency check.  When ``termapy --mcp`` is
invoked, this module verifies that the ``mcp`` SDK is installed; if
not, it prints a one-line install hint to stderr and exits 1.

**MCP state directory.** When the real server lands in Phase 3, all
MCP-specific runtime state (session log, cached profiles, scratch)
will live under ``<cfg_dir>/mcp/`` -- a first-class plumbing folder
distinct from ``cap/`` (user capture artifacts) and other per-config
data folders.  Architecturally MCP is a peer of CLI and TUI, not a
consumer of capture infrastructure.

Real server construction (MCPHost, ``run_command`` tool, catalog and
device_state resources, capture-as-resource, NDJSON pipeline) lands
in Phase 3.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


_INSTALL_HINT = (
    "termapy: --mcp requires the 'mcp' optional dependency.\n"
    "Install with:  pip install termapy[mcp]\n"
    "             (or with uv:  uv pip install termapy[mcp])"
)


def run_mcp_stdio(args: argparse.Namespace) -> None:
    """Run the MCP stdio server.  Phase 1 stub: verify SDK, exit cleanly.

    Args:
        args: Parsed argparse namespace.  Phase 1 ignores all flags
            except checking that we got here at all.  Phase 3 will
            consume ``args.config``, ``args.cfg_dir``, and
            ``args.mcp_verbose``.

    Behavior:
        - If the ``mcp`` SDK is not importable, print the install hint
          to stderr and exit 1.
        - If ``mcp_verbose`` is set, log a one-line "stub" notice to
          stderr so devs see they hit the right path.
        - Then exit 0 (Phase 1 has no real server to run).

    Stdout is reserved for MCP protocol frames once the real server
    lands; even the stub avoids ``print()`` to stdout.
    """
    try:
        import mcp  # noqa: F401  -- import-only check
    except ImportError:
        print(_INSTALL_HINT, file=sys.stderr)
        sys.exit(1)

    if getattr(args, "mcp_verbose", False):
        print(
            "termapy --mcp (Phase 1 stub): SDK detected, real server in Phase 3.",
            file=sys.stderr,
        )

    # Phase 1: nothing to serve yet.  Exit cleanly so the entry-flag
    # plumbing is verifiable.
    sys.exit(0)
