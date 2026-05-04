"""Termapy MCP (Model Context Protocol) server package.

This package isolates everything that pulls in the ``mcp`` SDK or the
asyncio stdio loop.  Normal termapy usage never imports it -- ``termapy
--mcp`` is the only entry point.

The package is the *reference implementation* of the device-profile
spec.  Other tools can build their own MCP bridges from the same
schema (see ``docs/profile-v2-spec.md``).

Phase 1 ships only the stub: ``run_mcp_stdio`` checks for the SDK and
exits with a clean install hint when missing.  Real server code lands
in Phase 3.
"""
