"""Config-path resolution helpers.

Pure stdlib (``pathlib``) logic for finding and resolving termapy
config files.  Extracted from ``app.py`` so callers that must not
import Textual -- the CLI entry point, the ``--check`` flag -- can
share one implementation instead of duplicating it.

Public API:

- ``find_config()`` -- scan ``cfg_dir()`` for ``termapy_cfg/<name>/<name>.cfg``
  files and decide whether to auto-load, show a name picker, or show a
  file picker.
- ``resolve_config(name)`` -- turn a user-supplied name, path, or
  directory into a concrete ``.cfg`` path via a 5-rule chain.
- ``infer_config_from_run_file(path)`` -- walk up from a ``.run`` (or
  ``.pro``) script path to find the enclosing config.

All three are Textual-free and import ``cfg_dir`` lazily so importing
this module doesn't force the rest of ``termapy.config`` to load.
"""

from __future__ import annotations

from pathlib import Path


def find_config() -> tuple[str | None, bool]:
    """Find config in ``termapy_cfg/<name>/<name>.cfg``.

    Returns ``(path, show_picker)``:

    - 1 cfg file:  ``(path, False)`` -- auto-load
    - 0 cfg files: ``(None, False)`` -- show name picker for new config
    - 2+ cfg files: ``(None, True)`` -- show file picker

    Also performs the legacy ``.json`` -> ``.cfg`` migration on first
    scan so old installs see their configs after upgrade.
    """
    from termapy.config import cfg_dir, migrate_json_to_cfg

    d = cfg_dir()
    migrate_json_to_cfg(d)
    cfg_files = sorted(d.glob("*/*.cfg"))
    if len(cfg_files) == 1:
        return str(cfg_files[0]), False
    if len(cfg_files) > 1:
        return None, True
    return None, False


def resolve_config(name: str) -> str | None:
    """Resolve a config name, path, or directory to a ``.cfg`` file.

    Resolution chain (first match wins):

    1. Exact file -- path exists and is a file.
    2. Directory -- look for ``<dirname>.cfg`` inside.
    3. ``cfg_dir()/<name>/<name>.cfg`` -- bare name via configured cfg dir.
    4. ``./termapy_cfg/<name>/<name>.cfg`` -- bare name via cwd.
    5. ``<name>.cfg`` appended -- in case the extension was omitted.

    Returns ``None`` if nothing matched.
    """
    from termapy.config import cfg_dir

    p = Path(name)
    # 1. Exact file.
    if p.is_file():
        return str(p)
    # 2. Directory -- look for <dirname>.cfg inside.
    if p.is_dir():
        candidate = p / f"{p.name}.cfg"
        if candidate.exists():
            return str(candidate)
    # 3. cfg_dir/<name>/<name>.cfg (configured cfg dir).
    stem = p.stem
    try:
        candidate = Path(cfg_dir()) / stem / f"{stem}.cfg"
        if candidate.exists():
            return str(candidate)
    except SystemExit:
        pass  # cfg_dir doesn't exist yet -- skip this rule.
    # 4. ./termapy_cfg/<name>/<name>.cfg (cwd fallback).
    candidate = Path("termapy_cfg") / stem / f"{stem}.cfg"
    if candidate.exists():
        return str(candidate)
    # 5. Append .cfg.
    if not name.endswith(".cfg"):
        candidate = Path(f"{name}.cfg")
        if candidate.exists():
            return str(candidate)
    return None


def infer_config_from_run_file(run_path: str) -> str | None:
    """Infer config path from a ``.run`` (or ``.pro``) script path.

    If the script is at ``termapy_cfg/<name>/scripts/foo.run``, the
    config is ``termapy_cfg/<name>/<name>.cfg``.  Walks up the parent
    chain looking for any ``*.cfg`` file, stopping short of the
    ``termapy_cfg`` root so we don't mistakenly pick a sibling config.
    """
    p = Path(run_path).resolve()
    for parent in p.parents:
        cfgs = list(parent.glob("*.cfg"))
        if cfgs and parent.name != "termapy_cfg":
            return str(cfgs[0])
    return None


def proto_dir_for(config_path: str) -> Path:
    """The ``proto/`` folder that sits beside a config file.

    Args:
        config_path: Path to a ``.cfg`` file.

    Returns:
        The sibling ``proto/`` directory (which may not exist).
    """
    return Path(config_path).parent / "proto"


def proto_script_name(name: str) -> str:
    """Normalize a ``.pro`` script name, appending the extension if absent.

    Exposed separately so an error message can report the name that was
    actually searched for (``smoke.pro``) rather than what the user typed
    (``smoke``), without the caller re-implementing the rule.

    Args:
        name: User-supplied script name, with or without ``.pro``.

    Returns:
        The name with a single ``.pro`` extension.
    """
    return name if name.endswith(".pro") else name + ".pro"


def resolve_proto_path(name: str, config_path: str) -> Path | None:
    """Resolve a ``.pro`` script name to an existing file, or None.

    Two locations are tried, in order: the name as given (so a path
    typed relative to the shell's cwd wins), then inside the config's
    ``proto/`` folder.  A missing ``.pro`` extension is appended first,
    so ``smoke`` and ``smoke.pro`` behave identically.

    Returns None rather than exiting so the caller owns the error
    message and exit code -- this stays a pure lookup.

    Args:
        name: User-supplied script name or path, with or without ``.pro``.
        config_path: Path to the active ``.cfg`` file.

    Returns:
        The resolved path, or None when neither location has the file.
    """
    name = proto_script_name(name)
    direct = Path(name)
    if direct.exists():
        return direct
    in_proto_dir = proto_dir_for(config_path) / name
    if in_proto_dir.exists():
        return in_proto_dir
    return None
