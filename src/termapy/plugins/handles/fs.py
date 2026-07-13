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

    # The one place caller-supplied file names become paths.  Every
    # command that reads or writes a user-named file MUST route through
    # here so containment is a single checkable invariant, not a
    # per-command discipline (which historically some commands got and
    # some missed).  See docs and the security audit.
    _FOLDER_ATTRS = {
        "cap": "cap_dir",
        "scripts": "scripts_dir",
        "proto": "proto_dir",
        "prof": "prof_dir",
        "ss": "ss_dir",
    }

    def resolve(self, name: str, folder: str) -> Path:
        """Resolve a caller-supplied file name against a per-config folder.

        A plain name lands inside ``folder``.  An absolute path or ``..``
        that escapes the folder is refused UNLESS the host grants
        ``filesystem_unconfined`` -- so an MCP client (a remote/automated
        peer) is confined to the config sandbox, while the operator at a
        CLI/TUI keeps host-wide paths.  Reuses the same
        ``resolve()`` + ``is_relative_to`` containment as the ``.dump``
        commands and the MCP capture resource.

        Args:
            name: Caller-supplied file name or path.
            folder: Which per-config folder -- one of ``"cap"``,
                ``"scripts"``, ``"proto"``, ``"prof"``, ``"ss"``.

        Returns:
            The resolved absolute ``Path``.

        Raises:
            MissingCapability: ``name`` escapes ``folder`` and the host
                lacks ``filesystem_unconfined`` (dispatch converts this to
                a clean ``CmdResult.fail``).
            ValueError: unknown ``folder``.
        """
        attr = self._FOLDER_ATTRS.get(folder)
        if attr is None:
            raise ValueError(f"Unknown folder: {folder!r}")
        base = Path(getattr(self, attr))
        target = (base / name).resolve()
        try:
            inside = target.is_relative_to(base.resolve())
        except OSError:
            inside = False
        if inside or self.capabilities.filesystem_unconfined:
            return target
        raise MissingCapability(
            f"Path {name!r} escapes the {folder}/ sandbox. The MCP host is "
            f"confined to the config directory; set TERMAPY_MCP_FS_UNCONFINED=1 "
            f"in the server's shell to allow host-wide paths."
        )

    def guard_external_path(self, arg: str, what: str = "Path") -> None:
        """Refuse a free-path argument that points outside the sandbox.

        For commands whose argument is a whole path rather than a
        folder-relative name (e.g. ``/profile.save <path>``,
        ``/profile.load <path>``, ``/cfg.load <path>``): an absolute path
        or a ``..`` traversal is refused UNLESS the host grants
        ``filesystem_unconfined``.  A plain name or in-sandbox relative
        path is always allowed, so name-based loads (``/cfg.load mydevice``)
        keep working under the MCP sandbox.

        Raises:
            MissingCapability: ``arg`` escapes the sandbox and the host
                lacks ``filesystem_unconfined``.
        """
        p = Path(arg)
        escapes = p.is_absolute() or ".." in p.parts
        if escapes and not self.capabilities.filesystem_unconfined:
            raise MissingCapability(
                f"{what} {arg!r} is outside the config sandbox. The MCP host "
                f"is confined to the config directory; set "
                f"TERMAPY_MCP_FS_UNCONFINED=1 in the server's shell to allow "
                f"host-wide paths."
            )
