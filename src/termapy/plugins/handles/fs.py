"""FilesystemHandle -- per-config directories and file-open primitive.

Reachable as ``ctx.fs.*``.  Domain: the five per-config directories
(``ss``, ``scripts``, ``proto``, ``cap``, ``prof``) and the system
file-open primitive.

Field names map cleanly:

  - ``ctx.ss_dir``        -> ``ctx.fs.ss_dir``
  - ``ctx.scripts_dir``   -> ``ctx.fs.scripts_dir``
  - ``ctx.proto_dir``     -> ``ctx.fs.proto_dir``
  - ``ctx.cap_dir``       -> ``ctx.fs.cap_dir``
  - ``ctx.prof_dir``      -> ``ctx.fs.prof_dir``
  - ``ctx.open_file()``   -> ``ctx.fs.open_file()``  (capability-gated)

``open_file`` requires the ``gui_apps`` capability -- on a headless
or SSH-without-X11 host the system opener succeeds silently while
opening on the wrong machine, which is worse than a clear refusal.
Plugins that genuinely need to open files (system editor for
``/edit.cfg``, file viewer for ``/show.cfg``) declare
``needs=CapabilitySet(gui_apps=True)`` and the dispatcher gates them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins.capabilities import MissingCapability

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext


class FilesystemHandle:
    """Per-config directory paths + system file opener (gated by ``gui_apps``)."""

    def __init__(self, ctx: "PluginContext") -> None:
        self._ctx = ctx

    # ── Directory paths (always available) ───────────────────────────

    @property
    def ss_dir(self) -> Path:
        """Per-config screenshots directory.  Auto-created on first access."""
        return self._ctx.ss_dir

    @property
    def scripts_dir(self) -> Path:
        """Per-config ``.run`` scripts directory.  Auto-created on first access."""
        return self._ctx.scripts_dir

    @property
    def proto_dir(self) -> Path:
        """Per-config protocol test scripts directory.  Auto-created on first access."""
        return self._ctx.proto_dir

    @property
    def cap_dir(self) -> Path:
        """Per-config capture directory.  Auto-created on first access."""
        return self._ctx.cap_dir

    @property
    def prof_dir(self) -> Path:
        """Per-config profile-output directory.  Auto-created on first access."""
        return self._ctx.prof_dir

    # ── System file opener (gated) ───────────────────────────────────

    def open_file(self, path) -> None:
        """Open a file or folder in the system viewer/editor.

        Requires the ``gui_apps`` capability -- without it, the call
        would silently open on the wrong machine (SSH without X11).

        Raises:
            MissingCapability: if the environment doesn't provide
                ``gui_apps``.  Declare ``needs=CapabilitySet(gui_apps=True)``
                on your Command to gate dispatch instead.
        """
        if not self._ctx.capabilities.gui_apps:
            raise MissingCapability(
                "ctx.fs.open_file requires gui_apps capability; "
                "declare needs=CapabilitySet(gui_apps=True) on your Command"
            )
        self._ctx.open_file(path)
