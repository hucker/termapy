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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from termapy.folders import PLUGIN, VIZ
from termapy.plugins.capabilities import CapabilitySet, MissingCapability

# The folders termapy imports Python from, in the case a typed path may use.
_CODE_FOLDERS = frozenset(name.casefold() for name in (PLUGIN, VIZ))


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
                ``gui_apps``.  Declare ``needs=CapabilitySet.GUI_APPS``
                on your Command to gate dispatch instead.
        """
        if not self.capabilities.gui_apps:
            raise MissingCapability(
                "ctx.fs.open_file requires gui_apps capability; "
                "declare needs=CapabilitySet.GUI_APPS on your Command"
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
            f"Path {name!r} escapes the {base.name or folder}/ sandbox. The MCP host is "
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

    def guard_code_folder(self, folder: Path) -> None:
        """Refuse a write into a folder termapy imports code from, with no person present.

        Python in ``plugin/`` or ``viz/`` runs at the next load, so an MCP
        client writing there would step around every other gate.  Unlike the
        sandbox above, no flag lifts this (registry ``mcp-no-code-writes``).

        Below the config root every part of the path counts, because
        ``plugin/sub/`` is still in ``plugin/``.  Above the root a folder named
        ``plugin`` is only where a project happens to live, so a path outside
        the root is judged by its last part.

        Args:
            folder: The folder the file lands in.

        Raises:
            MissingCapability: The folder is or sits in one that holds code, and
                the host has no interactive session.
        """
        if self.capabilities.interactive:
            return
        resolved = folder.resolve()
        config_folder = self._config_folder()
        cfg_root = config_folder.parent if config_folder else None
        parts = (
            resolved.relative_to(cfg_root).parts
            if cfg_root and resolved.is_relative_to(cfg_root)
            else (resolved.name,)
        )
        for part in parts:
            # Windows makes plugin. and plugin::$INDEX_ALLOCATION the folder plugin/.
            if part.split(":")[0].rstrip(". ").casefold() in _CODE_FOLDERS:
                raise MissingCapability(
                    f"Folder {part!r} holds code termapy imports: an MCP client cannot write there."
                )

    def guard_replace(self, path: Path) -> None:
        """Refuse replacing a file outside the config's folder, with no person present.

        Inside its folder an MCP client may write over a file, as a person can;
        outside it the file is not the config's to change, and no flag lifts
        this (registry ``mcp-no-code-writes``, decision 2026-11-05).  A code
        folder inside it is ``guard_code_folder``'s answer.

        Args:
            path: The file about to be written.

        Raises:
            MissingCapability: The file exists outside the config's folder, or
                no config is loaded, and the host has no interactive session.
        """
        if self.capabilities.interactive or not path.exists():
            return
        config_folder = self._config_folder()
        if config_folder is None or not path.resolve().is_relative_to(config_folder):
            raise MissingCapability(
                f"File exists: {path.name} "
                "(an MCP client never replaces a file outside the config's folder)"
            )

    def _config_folder(self) -> Path | None:
        """The active config's folder, or ``None`` with no config, when the data folders are ``.``."""
        cap_dir = Path(self.cap_dir)
        return None if cap_dir == Path(".") else cap_dir.resolve().parent
