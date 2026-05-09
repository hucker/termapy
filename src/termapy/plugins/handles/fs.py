"""FilesystemHandle -- per-config directories and file-open primitive.

Reachable as ``ctx.fs.*``.  Domain: the five per-config directories
(``ss``, ``scripts``, ``proto``, ``cap``, ``prof``) and the system
file-open primitive.

Self-contained dataclass: paths and the ``open_file`` callable are
fields.  Capability gating for ``open_file`` reads from the
``capabilities`` field (a snapshot of the host's CapabilitySet,
populated by ``PluginContext.__post_init__``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from termapy.plugins.capabilities import CapabilitySet, MissingCapability


@dataclass
class FilesystemHandle:
    """Per-config directory paths + system file opener (gated by ``gui_apps``).

    Plugins read paths via ``ctx.fs.cap_dir`` (etc.) and open files
    via ``ctx.fs.open_file(path)``.  ``open_file`` requires the
    ``gui_apps`` capability -- on a headless or SSH-without-X11 host
    the system opener succeeds silently while opening on the wrong
    machine, which is worse than a clear refusal.
    """

    # Per-config directory paths.
    ss_dir: Path = field(default_factory=lambda: Path("."))
    scripts_dir: Path = field(default_factory=lambda: Path("."))
    proto_dir: Path = field(default_factory=lambda: Path("."))
    cap_dir: Path = field(default_factory=lambda: Path("."))
    prof_dir: Path = field(default_factory=lambda: Path("."))

    # Backing callable for the gated open_file.
    _open_file_impl: Callable = lambda path: None

    # Capability snapshot for the open_file gate.  PluginContext sets
    # this in __post_init__ to its own CapabilitySet.
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)

    def open_file(self, path) -> None:
        """Open a file or folder in the system viewer/editor.

        Requires the ``gui_apps`` capability -- without it, the call
        would silently open on the wrong machine (SSH without X11).

        Raises:
            MissingCapability: if the environment doesn't provide
                ``gui_apps``.  Declare ``needs=CapabilitySet(gui_apps=True)``
                on your Command to gate dispatch instead.
        """
        if not self.capabilities.gui_apps:
            raise MissingCapability(
                "ctx.fs.open_file requires gui_apps capability; "
                "declare needs=CapabilitySet(gui_apps=True) on your Command"
            )
        self._open_file_impl(path)
