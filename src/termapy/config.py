"""Config file management - paths, defaults, loading, and serial port setup.

Pure functions with no UI dependency. Used by app.py and tests.
"""

import codecs
import copy
import json
import os
import re
import shutil
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

import serial

from termapy import diag_log
from termapy.defaults import (
    CFG_CHOICES,
    CFG_KEYS,
    DEFAULT_CFG,
    STANDARD_BAUD_RATES,
    CfgKey,
    cfg_key,
    cfg_value,
    cmd_prefix,
    default_cfg,
)
from termapy.folders import (
    CAP,
    DEV,
    FOLDER_EXTS,
    FOLDER_MIGRATIONS,
    HISTORY_FILE,
    HISTORY_SUFFIX,
    LIB,
    LOG_SUFFIX,
    MCP_LOG_SUFFIX,
    PLUGIN,
    PROFILE_TMP_GLOB,
    RUN,
    SIDECARS,
    SYM,
    SYMBOLS_SUFFIX,
    default_file_name,
    ensure_folder,
    file_suggestion,
    inside,
    is_inside,
    prune_empty_folders,
)
from termapy.migration import (
    CURRENT_CONFIG_VERSION,
    DEPRECATED_CFG,
    migrate_config,
)
from termapy.port_allow import PortPolicy
from termapy.scripting import parse_bool, value_text

CFG_DIR: str | None = None  # set by --cfg-dir; None = use resolution chain

# Set beside CFG_DIR by an MCP server whose project has no config folder: CFG_DIR
# then names where one would be, so nothing falls back to the OS config directory.
CFG_DIR_MAY_BE_ABSENT: bool = False

_LOCAL_DIR_NAME = "termapy_cfg"

# Where a config sits under a config root: termapy_cfg/<name>/<name>.cfg.
CONFIG_GLOB = "*/*.cfg"

# Read timeout for every opened serial port.  Deliberately small so the
# reader loop's blocking ``read()`` returns promptly and can notice the
# stop signal.  This sets how long teardown takes: ``disconnect`` joins the
# reader thread, and the reader can only reach its stop check once ``read()``
# returns -- so this is the latency of a disconnect, not a race window.  It
# no longer needs to beat any other constant (the old timing contract against
# ``READER_STOP_WAIT_S`` is gone; teardown is a join).  Raising it slows
# disconnects proportionally.
SERIAL_READ_TIMEOUT_S = 0.05

# Driver-side RX buffer requested for every real port we open.
#
# pyserial hardcodes a 4 KB buffer on Windows (``SetupComm(handle, 4096,
# 4096)`` in ``serialwin32.Serial.open``).  That is small enough to matter:
# whenever the main thread stalls longer than ``4096 / (baud / 10)`` ms, the
# driver silently DISCARDS the overflow -- no exception, no gap marker,
# nothing visible in ``in_waiting``.  Measured on a hardware loopback, a
# 60 ms stall at 921600 lost 21% of the stream, and a 500 ms stall at
# 115200 lost 22%.  Both go to 0% at this size.  For reference, one
# 50-line RichLog batch costs ~40 ms, and 921600's threshold is ~44 ms.
#
# 256 KB buys ~22 s of slack at 115200 and ~2.8 s at 921600, costs a
# quarter-megabyte of non-paged pool per open port, and is not clamped by
# the drivers tested.  It is a REQUEST -- a driver may honor less.
#
# Windows-only: ``set_buffer_size`` exists solely on ``serialwin32``.  POSIX
# tty buffering is a different mechanism and is NOT addressed here.
SERIAL_RX_BUFFER_BYTES = 262144


def _os_default_cfg_dir() -> Path:
    """OS-standard config directory, which may not exist yet.

    A path, not a promise: the first write into it (a new config, the
    demo, the no-config history file) creates it.

    Windows: %APPDATA%/termapy
    macOS:   ~/Library/Application Support/termapy
    Linux:   ~/.config/termapy (XDG_CONFIG_HOME respected)
    """
    import platform

    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "termapy"


def cfg_dir() -> Path:
    """Resolve the config directory using precedence chain.

    1. --cfg-dir flag (CFG_DIR set explicitly) -- must exist
    2. TERMAPY_CFG_DIR env var -- must exist
    3. ./termapy_cfg if it exists in cwd -- never auto-created
    4. OS default (~/.config/termapy etc.) -- created by the first write into it
    """
    # 1. Explicit --cfg-dir
    if CFG_DIR is not None:
        d = Path(CFG_DIR)
        if d.suffix:
            raise SystemExit(
                f"termapy: config directory looks like a file: {CFG_DIR} "
                f"(has extension '{d.suffix}'). Use --cfg-dir for directories."
            )
        if not d.exists() and not CFG_DIR_MAY_BE_ABSENT:
            raise SystemExit(f"termapy: config directory does not exist: {d.resolve()}")
        return d

    # 2. Environment variable
    env = os.environ.get("TERMAPY_CFG_DIR")
    if env:
        d = Path(env)
        if not d.exists():
            raise SystemExit(f"termapy: TERMAPY_CFG_DIR does not exist: {d.resolve()}")
        return d

    # 3. Local termapy_cfg/ in cwd (check, never create)
    local = Path(_LOCAL_DIR_NAME)
    if local.is_dir():
        return local

    # 4. OS default (may not exist until something is written into it)
    return _os_default_cfg_dir()


def _is_bundled_path(p: Path) -> bool:
    """True if ``p`` lives inside the installed termapy package tree.

    Detection via ``importlib.resources.files("termapy")``: works for
    editable installs (``src/termapy/``) and wheels
    (``site-packages/termapy/``).  Fails open on any detection error so
    a path-comparison hiccup can't lock out a legitimate user.

    Used to refuse writes (cap/, ss/, prof/, .gitignore, ...) into the
    installed package -- a footgun that polluted the source tree during
    development before this guard existed.
    """
    try:
        import importlib.resources

        pkg_root = Path(str(importlib.resources.files("termapy"))).resolve()
        return is_inside(p, pkg_root)
    except (ImportError, OSError, TypeError, ValueError):
        return False


def cfg_data_dir(config_path: str) -> Path:
    """Return the per-config data directory (for logs, screenshots, etc.).

    Config files live at termapy_cfg/<name>/<name>.cfg, so the data dir
    is just the parent directory of the config file.  A read: it writes
    nothing, so a listing, a log viewer or an MCP query can call it on
    any config.  The folder's setup is ``prepare_cfg_folder``, run at load.

    Refuses to operate on cfg paths inside the installed termapy
    package tree -- those are read-only templates (e.g. the bundled
    ``builtins/demo/demo.cfg``).  Use ``--demo`` to copy the template
    into a writable cfg dir, or pass ``--cfg-dir <writable>``.
    """
    d = Path(config_path).parent
    if _is_bundled_path(d):
        raise RuntimeError(
            f"Refusing to activate bundled cfg as a runtime location: "
            f"{config_path}\n"
            f"This file is a read-only template inside the termapy "
            f"package.\n"
            f"Use `termapy --demo` to copy it to your cfg-dir, or pass\n"
            f"`--cfg-dir <writable-path>` pointing at a writable location."
        )
    return d


def prepare_cfg_folder(config_path: str) -> Path:
    """Set up a config's folder; the write half of ``cfg_data_dir``.

    Creates the folder and its ``.gitignore`` and runs the layout
    migrations (folder renames, sidecar moves).  Creates NO data folder:
    those appear on first write (``folders.ensure_folder``) and empty ones
    are pruned at load and stop (``folders.prune_empty_folders``).

    Called where a config is loaded (``load_config``) or created, never
    on a read path.  Idempotent: once a folder is set up, it changes
    nothing.

    Returns:
        The config folder.

    Raises:
        RuntimeError: ``config_path`` is the bundled read-only template.
    """
    d = cfg_data_dir(config_path)
    d.mkdir(parents=True, exist_ok=True)
    # One-time folder renames (migration)
    for old_name, new_name in FOLDER_MIGRATIONS:
        old = d / old_name
        new = d / new_name
        if old.is_dir() and not new.exists():
            old.rename(new)
    # One-time sidecar moves (migration): a stem-named file that once
    # lived beside the cfg and now has a folder.  Same rule as the folder
    # renames -- move only when the old exists and the new does not; the
    # folder is created only when there is something to move into it.
    stem = Path(config_path).stem
    for suffix, sub in SIDECARS:
        if sub is None:
            continue
        old_file = d / f"{stem}{suffix}"
        new_file = d / sub / f"{stem}{suffix}"
        if old_file.is_file() and not new_file.exists():
            ensure_folder(new_file.parent)
            old_file.rename(new_file)
    # One-time move (migration): the MCP session log lived in mcp/session.log
    # until 2026-11-05 and is now <stem>.mcp.log beside the TUI's <stem>.log.
    # Nothing else was ever written to mcp/, so an emptied folder goes too.
    old_mcp_log = d / _OLD_MCP_DIR / _OLD_MCP_LOG
    new_mcp_log = d / f"{stem}{MCP_LOG_SUFFIX}"
    if old_mcp_log.is_file() and not new_mcp_log.exists():
        old_mcp_log.rename(new_mcp_log)
        prune_empty_folders(d, (_OLD_MCP_DIR,))
    # Write .gitignore for transient data (only if it doesn't exist)
    gitignore = d / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Termapy - ignore transient/generated data\n"
            "*.log\n"
            ".cmd_history.txt\n"
            "*.history\n"
            ".cap_seq\n"
            ".target_menu.json\n"
            "_profile_tmp_*\n"
            "ss/\n"
            "cap/\n"
            "prof/\n",
            encoding="utf-8",
        )
    return d


def cfg_path_for_name(name: str) -> Path:
    """Return the config file path for a given name: termapy_cfg/<name>/<name>.cfg."""
    return cfg_dir() / name / f"{name}.cfg"


def is_config_file(path: Path) -> bool:
    """Whether a ``CONFIG_GLOB`` match is a config: ``<name>/<name>.cfg``, file and folder alike.

    A folder can hold another ``.cfg``: ``demo/.target_menu.cfg`` made one
    config count as two, so a lone config never loaded by itself (registry
    ``config-files-one-reader``).  Case is ignored, as the file systems
    that hold most configs ignore it.
    """
    return path.stem.casefold() == path.parent.name.casefold()


def config_files(root: Path) -> list[Path]:
    """The configs under ``root``, sorted by name; a missing folder has none."""
    return sorted(path for path in root.glob(CONFIG_GLOB) if is_config_file(path))


def validate_file_stem(name: str) -> str | None:
    """Why ``name`` cannot be a file or config name, or ``None`` if it can.

    One rule for every rename: a bare name, no path separators (so it
    cannot escape its folder), no whitespace (command arguments split on
    it), nothing hidden.
    """
    if not name:
        return "Name is required"
    if any(char in name for char in "/\\"):
        return f"Name may not contain path separators: {name}"
    if any(char.isspace() for char in name):
        return f"Name may not contain spaces: {name}"
    if name.startswith("."):
        return f"Name may not start with a dot: {name}"
    if name in (".", ".."):
        return f"Invalid name: {name}"
    return None


def new_cfg_root() -> Path:
    """Where ``termapy --new`` puts a config: the config directory, or a new ``./termapy_cfg``.

    ``--cfg-dir``, ``TERMAPY_CFG_DIR`` and an existing ``./termapy_cfg``
    win exactly as in ``cfg_dir``.  Otherwise a new config starts a project
    in the current folder instead of landing in the OS config directory.
    A read: nothing is created here; ``create_config`` makes the folder
    when it writes.
    """
    if CFG_DIR is not None or os.environ.get("TERMAPY_CFG_DIR") or Path(_LOCAL_DIR_NAME).is_dir():
        return cfg_dir()
    return Path(_LOCAL_DIR_NAME)


def new_config(
    name: str,
    *,
    port: str = "",
    baud: int | None = None,
    custom_baud: bool = False,
    mode: tuple[str, int, float] | None = None,
    eol: str | None = None,
    echo: bool | None = None,
    auto_connect: bool = False,
) -> dict:
    """A fresh config for ``name``: the defaults, titled ``name``, with these settings.

    The cfg half of making a config; ``create_config`` writes it.  Every
    creator builds through here -- the QuickSetup wizard and ``termapy
    --new`` -- so a new config is the same whichever frontend made it.  A
    setting left ``None`` keeps its default.

    Args:
        name: The config name, which is also its title.
        port: Port name, serial number or URL; ``""`` leaves it unset.
        baud: Baud rate.
        custom_baud: Whether ``baud`` was asked for as custom; a rate off the
            standard list needs it (``check_baud``).
        mode: ``(parity, byte_size, stop_bits)`` as ``port_control.parse_mode``
            returns it.
        eol: Line ending sent after each command.
        echo: Echo sent commands locally.
        auto_connect: Open the port when the config loads.

    Returns:
        The new cfg dict.

    Raises:
        ValueError: ``baud`` is off the standard list without ``custom_baud``,
            or under 300 with it.
    """
    cfg = default_cfg()
    cfg["title"] = name
    apply_open_settings(
        cfg, port=port, baud=baud, custom_baud=custom_baud, mode=mode, eol=eol, echo=echo
    )
    cfg["serial"]["custom_baud"] = custom_baud
    cfg["auto_connect"] = auto_connect
    return cfg


def apply_open_settings(
    cfg: dict,
    *,
    port: str = "",
    baud: int | None = None,
    custom_baud: bool = False,
    mode: tuple[str, int, float] | None = None,
    eol: str | None = None,
    echo: bool | None = None,
) -> None:
    """Lay ``/port.connect``'s settings over ``cfg``; one left ``None`` (or ``port`` ``""``) is kept.

    A new config (``new_config``) and a quick session's next open
    (``quick_config``) both write the settings here.  A baud rate given
    sets ``custom_baud`` with it, so a standard rate after a custom one
    clears it.

    Raises:
        ValueError: ``baud`` is off the standard list without ``custom_baud``,
            or under 300 with it.
    """
    if baud is not None:
        reason = check_baud(baud, custom=custom_baud, remedy="ask for it as custom")
        if reason:
            raise ValueError(f"Invalid baud rate: {baud} ({reason})")
    serial_cfg = cfg["serial"]
    if port:
        serial_cfg["port"] = port
    if baud is not None:
        serial_cfg["baud_rate"] = baud
        serial_cfg["custom_baud"] = custom_baud
    if mode is not None:
        serial_cfg["parity"], serial_cfg["byte_size"], serial_cfg["stop_bits"] = mode
    if eol is not None:
        cfg["eol"] = eol
    if echo is not None:
        cfg["echo"] = echo


def new_config_from_args(name: str, settings: str) -> dict:
    """``NAME PORT [SETTING ...]`` as a new config: what ``--new`` and ``/cfg.new`` read.

    One reading for the flag and the command.  The settings are
    ``/port.connect``'s grammar, read by its reader
    (``port_control.parse_open_args``): the port, which is required, then
    baud, frame, line ending, echo and ``custom=true`` in any order.  A rate
    off the standard list needs ``custom=true``, which also makes the
    config's ``custom_baud`` true.  The config connects when it loads -- a
    port was named, so connecting is the point.

    Args:
        name: The config name.
        settings: The settings text after the name.

    Returns:
        The new cfg, from ``new_config``.

    Raises:
        ValueError: The name fails ``validate_file_stem``, a setting is not
            ``/port.connect``'s, or there is no port.
    """
    from termapy.port_control import parse_open_args

    reason = validate_file_stem(name)
    if reason:
        raise ValueError(reason)
    port, baud, mode, line_ending, echo, custom, error = parse_open_args(settings)
    if error:
        raise ValueError(error)
    if not port:
        raise ValueError("A port is required")
    return new_config(
        name,
        port=port,
        baud=baud,
        custom_baud=custom,
        mode=mode,
        eol=line_ending,
        echo=echo,
        auto_connect=True,
    )


QUICK_PREFIX = "quick-"


def quick_cfg_root() -> Path:
    """Where a quick session's config lives: the OS folder, unless a config directory was named.

    ``--cfg-dir`` and ``TERMAPY_CFG_DIR`` win as in ``cfg_dir``; a
    ``./termapy_cfg`` does not, so typing a port inside a project never adds
    a config to it (registry ``quick-open-outside-project``).  A read:
    nothing is created here.
    """
    if CFG_DIR is not None or os.environ.get("TERMAPY_CFG_DIR"):
        return cfg_dir()
    return _os_default_cfg_dir()


def quick_config_path(port: str) -> Path:
    """The ``.cfg`` a quick session on ``port`` uses: ``quick-`` and the port, file-name safe.

    Every run of characters a file name may not hold becomes one dash:
    ``COM3`` is ``quick-COM3``, ``socket://localhost:7000`` is
    ``quick-socket-localhost-7000`` and ``/dev/ttyUSB0`` is
    ``quick-dev-ttyUSB0``.
    """
    name = QUICK_PREFIX + re.sub(r"[^A-Za-z0-9._]+", "-", port).strip("-.")
    return quick_cfg_root() / name / f"{name}.cfg"


def quick_config(port: str, settings: str) -> tuple[Path, str]:
    """The config ``termapy PORT [SETTING ...]`` opens, and what became of it: ``Created``, ``Updated`` or ``Using``.

    One config per port (``quick_config_path``), so sessions on two ports
    share nothing.  The first open creates it through ``new_config`` and
    ``create_config``, connecting when it loads.  A later open with
    settings lays them over it and saves it, so they are what the port
    opens with next time; one without settings leaves it as it is.  The
    settings are ``/port.connect``'s, read by its reader.

    Args:
        port: The port as the config should name it.
        settings: The words after the port.

    Returns:
        The ``.cfg`` path and the verb for the status line.

    Raises:
        ValueError: A word is not one of ``/port.connect``'s settings, or the
            baud rate is refused.
        RuntimeError, OSError: As ``create_config`` raises them.
    """
    from termapy.port_control import parse_open_args

    named, baud, mode, eol, echo, custom, error = parse_open_args(settings)
    if error:
        raise ValueError(error)
    if named is not None:
        raise ValueError(
            f"Unknown setting: {named} (the settings are baud, frame, cr|lf|crlf, "
            f"echo|noecho and custom=true)"
        )
    path = quick_config_path(port)
    if not path.is_file():
        cfg = new_config(
            path.stem,
            port=port,
            baud=baud,
            custom_baud=custom,
            mode=mode,
            eol=eol,
            echo=echo,
            auto_connect=True,
        )
        return create_config(path, cfg), "Created"
    if not settings.split():
        return path, "Using"
    # The file as written, not load_config's view: that one has its
    # $(env.X) values expanded, which saving would bake into the file.
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg.setdefault("serial", {})
    apply_open_settings(cfg, baud=baud, custom_baud=custom, mode=mode, eol=eol, echo=echo)
    write_config_file(path, cfg)
    return path, "Updated"


class ConfigExistsError(ValueError):
    """A new config's place is taken; overwriting its ``.cfg`` needs a yes.

    Every frontend asks ``question`` and, on yes, calls ``create_config``
    again with ``overwrite=True``.  Only the ``.cfg`` is replaced: the run
    scripts, profile, plugins, logs and captures beside it stay.

    Attributes:
        path: The ``.cfg`` that would be written.
        file_count: Files already in the config's folder.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.file_count = sum(1 for entry in path.parent.rglob("*") if entry.is_file())
        super().__init__(f"Config already exists: {path.stem}")

    @property
    def question(self) -> str:
        """The overwrite question, the same words in every frontend."""
        noun = "file" if self.file_count == 1 else "files"
        return (
            f"Config '{self.path.stem}' already exists ({self.path.parent}, "
            f"{self.file_count} {noun}). Overwrite {self.path.name}? Other files are kept."
        )


def ordered_cfg(cfg: Mapping) -> dict:
    """``cfg`` with its keys in ``DEFAULT_CFG``'s order, the layout of every config file.

    Known keys come in the defaults' order, and so do the keys inside a
    nested default (``serial``, ``ndjson_field_routing``); a key the
    defaults do not have (a typo, a retired key) follows in the order it
    had.  Values are untouched.
    """
    known = [key for key in DEFAULT_CFG if key in cfg]
    unknown = [key for key in cfg if key not in DEFAULT_CFG]
    ordered = {}
    for key in known + unknown:
        value = cfg[key]
        default = DEFAULT_CFG.get(key)
        if isinstance(default, dict) and isinstance(value, dict):
            # The default's keys first, in its order; the merge keeps those
            # places and appends any sub-key the default does not have.
            value = {**{sub: value[sub] for sub in default if sub in value}, **value}
        ordered[key] = value
    return ordered


def write_config_file(path: str | Path, cfg: Mapping) -> None:
    """Write ``cfg`` to ``path`` in the standard order (``ordered_cfg``).

    The one writer of a ``.cfg`` (registry ``cfg-written-in-order``):
    load's migration write-back, ``create_config`` and the config editor's
    Save, so a file reads group by group whichever of them wrote it.
    ``_`` keys are session state (migration steps, warnings) and are never
    saved.
    """
    saved = {key: value for key, value in cfg.items() if not key.startswith("_")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered_cfg(saved), f, indent=4)


def create_config(config_path: str | Path, cfg: dict, *, overwrite: bool = False) -> Path:
    """Write a new config -- the one way a ``.cfg`` comes into being.

    The place is taken when the ``.cfg`` exists, or when its own folder
    (``<name>/<name>.cfg``) does: the folder may hold another setup's
    scripts and profile.  Then it refuses unless ``overwrite`` is set,
    and an overwrite replaces only the ``.cfg``.  ``prepare_cfg_folder``
    sets the folder up, so a new config has the same folder and
    ``.gitignore`` in every frontend.

    Args:
        config_path: The ``.cfg`` to write, normally
            ``<cfg dir>/<name>/<name>.cfg``.
        cfg: The config, usually from ``new_config``.
        overwrite: Replace the ``.cfg`` of a config that already exists.

    Returns:
        The path written.

    Raises:
        ValueError: The name fails ``validate_file_stem``.
        ConfigExistsError: The place is taken and ``overwrite`` is False.
        RuntimeError: The path is inside the bundled package.
        OSError: The folder or the file could not be written.
    """
    path = Path(config_path)
    reason = validate_file_stem(path.stem)
    if reason:
        raise ValueError(reason)
    own_folder = path.parent.name == path.stem and path.parent.is_dir()
    if (path.exists() or own_folder) and not overwrite:
        raise ConfigExistsError(path)
    prepare_cfg_folder(str(path))
    write_config_file(path, cfg)
    return path


def rename_config(config_path: str, new_name: str) -> str:
    """Rename a config -- folder and ``.cfg`` together -- and return the new path.

    A config is ``termapy_cfg/<name>/<name>.cfg``: the folder holds the
    run/, proto/, cap/ ... data, so the two names must move together.
    When the folder is named after the config (the standard layout) it is
    renamed with the file; a non-standard layout (folder named differently)
    renames only the file.  Every stem-named sidecar (``folders.SIDECARS``:
    history, log, report, profile, symbol table) follows the file.  Nothing
    is overwritten: an existing target folder or file is
    an error, as is a name that fails ``validate_file_stem``.

    Args:
        config_path: The existing ``.cfg`` file.
        new_name: The new config name (no extension).

    Returns:
        The new ``.cfg`` path as a string.

    Raises:
        ValueError: Invalid name, unchanged name, or target exists.
        FileNotFoundError: ``config_path`` does not exist.
        OSError: The rename itself failed.
    """
    old = Path(config_path)
    if not old.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    # "xm.cfg" and "xm" mean the same thing; without this the user who
    # types the extension ends up with xm.cfg/xm.cfg.cfg.
    if new_name.endswith(old.suffix):
        new_name = new_name[: -len(old.suffix)]
    reason = validate_file_stem(new_name)
    if reason:
        raise ValueError(reason)
    if new_name == old.stem:
        raise ValueError(f"Config is already named {new_name}")
    folder = old.parent
    standard_layout = folder.name == old.stem
    if standard_layout:
        new_folder = folder.parent / new_name
        if new_folder.exists():
            raise ValueError(f"Config already exists: {new_name}")
    else:
        new_folder = folder
    new_path = new_folder / f"{new_name}.cfg"
    if new_path.exists():
        raise ValueError(f"Config already exists: {new_name}")
    # Rename the file (and its history sidecar) inside the old folder
    # first, then the folder: if the folder move fails the config is
    # still a consistent <folder>/<new>.cfg pair the resolver can find.
    old.rename(folder / f"{new_name}.cfg")
    # Every stem-named sidecar follows, from the ONE table that lists them
    # (folders.SIDECARS): history, log, info report, profile, and the symbol
    # table in its sym/ folder.  A sidecar left behind keeps the old name
    # forever -- a stale <old>.log beside a fresh <new>.log, or a profile
    # the server no longer finds by convention.
    for suffix, sub in SIDECARS:
        home = folder / sub if sub else folder
        sidecar = home / f"{old.stem}{suffix}"
        if sidecar.exists():
            sidecar.rename(home / f"{new_name}{suffix}")
    if standard_layout:
        folder.rename(new_folder)
    return str(new_path)


def _deletion(config_path: Path) -> tuple[Path | None, list[Path]]:
    """What ``delete_config`` removes: ``(folder, files)``.

    The standard layout (a folder named after the config) goes whole, so
    ``folder`` is set and ``files`` is everything in it.  A config in a
    folder of another name (a Save As to a path) shares it, so ``folder``
    is None and ``files`` is the ``.cfg`` and its stem-named sidecars
    (``folders.SIDECARS``), the same set ``rename_config`` moves.
    """
    folder = config_path.parent
    if folder.name == config_path.stem:
        return folder, [entry for entry in folder.rglob("*") if entry.is_file()]
    files = [config_path]
    for suffix, sub in SIDECARS:
        home = folder / sub if sub else folder
        sidecar = home / f"{config_path.stem}{suffix}"
        if sidecar.is_file():
            files.append(sidecar)
    return None, files


def delete_config_question(config_path: str | Path) -> str:
    """The question every frontend asks before ``delete_config``, the same words in each."""
    path = Path(config_path)
    folder, files = _deletion(path)
    noun = "file" if len(files) == 1 else "files"
    if folder is not None:
        return (
            f"Delete config '{path.stem}' and its folder {folder} ({len(files)} {noun}: "
            "scripts, logs, captures)? This cannot be undone."
        )
    return (
        f"Delete config '{path.stem}' ({len(files)} {noun} in {path.parent})? "
        "This cannot be undone."
    )


def delete_config(config_path: str | Path) -> int:
    """Delete a config -- the one way a config goes, in every frontend.

    The standard layout loses its whole folder: the ``.cfg``, run scripts,
    profile, logs and captures.  A config sharing a folder loses only its
    ``.cfg`` and stem-named sidecars.  The folder is first renamed aside,
    so a config another program holds open (Windows refuses to move a
    folder with an open file) is refused before anything is removed,
    never left half-deleted.

    Args:
        config_path: The config's ``.cfg``.

    Returns:
        How many files were removed.

    Raises:
        FileNotFoundError: There is no such ``.cfg``.
        RuntimeError: The path is the bundled read-only template.
        OSError: The config is in use, or a file could not be removed.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config not found: {path.stem}")
    cfg_data_dir(str(path))  # refuses the bundled template
    folder, files = _deletion(path)
    if folder is None:
        for file in files:
            file.unlink()
        return len(files)
    aside = folder.with_name(f".{folder.name}.deleting")
    if aside.exists():
        # A delete that failed partway left this; nothing in it is a config.
        shutil.rmtree(aside)
    try:
        folder.rename(aside)
    except OSError as e:
        raise OSError(f"Config in use, nothing deleted: {path.stem} ({e})") from e
    shutil.rmtree(aside)
    return len(files)


def connection_string(cfg: Mapping, level: str = "medium", actual_port: str = "") -> str:
    """Format connection info from config at different detail levels.

    Args:
        cfg: Config dict with serial parameters.
        level: "short" (port baud 8N1), "medium" (+ flow control if non-default),
            or "full" (+ encoding and line ending).
        actual_port: If non-empty, shown in place of ``cfg["serial"]["port"]``.
            Callers that know the resolved device (via ``port_obj.port``
            after connect) pass it here so the displayed port is the
            real device name, not the possibly-cryptic spec (SN, pipe
            fallback chain, etc).

    Returns:
        Formatted connection string.
    """
    serial = cfg["serial"]
    port = actual_port or serial["port"] or "?"
    baud = serial["baud_rate"]
    bits = serial["byte_size"]
    parity = serial["parity"]
    sb = serial["stop_bits"]
    sb_str = str(int(sb)) if sb == int(sb) else str(sb)
    fc = serial["flow_control"]

    base = f"{port} {baud} {bits}{parity}{sb_str}"
    if level == "short":
        return base
    if fc != "none":
        base += f" {fc}"
    if level == "medium":
        return base
    enc = cfg_value(cfg, "encoding")
    le = repr(cfg_value(cfg, "eol"))
    return f"{base} {enc} {le}"


def hardware_signals(port_obj: object) -> str:
    """Format hardware signal states from a serial port object.

    Args:
        port_obj: Serial port object (Serial or FakeSerial).

    Returns:
        String like "DTR=1 RTS=1 CTS=0 DSR=0 RI=0 CD=0", or empty if unavailable.
    """
    try:
        parts = []
        for name, attr in [
            ("DTR", "dtr"),
            ("RTS", "rts"),
            ("CTS", "cts"),
            ("DSR", "dsr"),
            ("RI", "ri"),
            ("CD", "cd"),
        ]:
            parts.append(f"{name}={int(getattr(port_obj, attr, 0))}")
        return " ".join(parts)
    # A backend that cannot read a modem line: a closed or lost port
    # (pyserial's SerialException is an OSError), or a URL handler without
    # them.  The banner omits the signals.
    except (OSError, ValueError, NotImplementedError):
        return ""


def cfg_log_path(config_path: str) -> str:
    """Return the default log file path for a config."""
    name = Path(config_path).stem + LOG_SUFFIX
    return str((cfg_data_dir(config_path) / name).resolve())


def session_log_file(cfg: Mapping[str, Any], config_path: str | None) -> Path | None:
    """The TUI's session log, which ``--vt100`` shares: ``log_file`` when set, else ``<cfg>.log``; None with no config.

    The TUI's ``session_log_path`` is this; nothing else reads ``log_file``
    or calls ``cfg_log_path`` for the session log (registry
    ``session-log-one-owner``).  A relative ``log_file`` resolves against
    the working directory, as it always has.
    """
    configured = cfg_value(cfg, "log_file")
    if configured:
        return Path(configured).resolve()
    if config_path:
        return Path(cfg_log_path(config_path))
    return None


# Where the MCP session log lived until 2026-11-05; prepare_cfg_folder moves it.
_OLD_MCP_DIR = "mcp"
_OLD_MCP_LOG = "session.log"


def mcp_log_path(config_path: str) -> Path:
    """Where the MCP server writes its session log: ``<stem>.mcp.log`` beside the ``.cfg``.

    One owner: the MCP host writes here and ``/log.list`` lists it from any
    frontend, beside the TUI's ``<stem>.log``.  A path, whether or not the
    file exists.
    """
    path = Path(config_path)
    return path.parent / f"{path.stem}{MCP_LOG_SUFFIX}"


def cfg_history_path(config_path: str | None) -> str:
    """Return the command-history file path (single owner; hosts call this).

    Per-config: ``<cfg dir>/<stem>.history`` next to the config file --
    the path both hosts have always written.  With no config loaded,
    fall back to ``HISTORY_FILE`` in the global config root (stable,
    unlike a cwd dotfile).  TUI and CLI both delegate here so the two
    frontends can never diverge again (they did: the CLI fallback was
    ``Path.cwd()`` while the TUI used ``cfg_dir()``).
    """
    if config_path:
        p = Path(config_path)
        return str(p.parent / f"{p.stem}{HISTORY_SUFFIX}")
    return str(cfg_dir() / HISTORY_FILE)


def cleanup_profile_temps(config_path: str) -> None:
    """Delete stale _profile_tmp_*.run files from the run directory."""
    run_dir = cfg_data_dir(config_path) / "run"
    if not run_dir.is_dir():
        return
    for path in run_dir.glob(PROFILE_TMP_GLOB):
        try:
            path.unlink()
        except OSError:
            pass


def cfg_plugins_dir(config_path: str) -> Path:
    """Return the plugin directory for a config (a path; it may not exist yet)."""
    return cfg_data_dir(config_path) / PLUGIN


def global_plugins_dir(root: Path | None = None) -> Path:
    """Return the global plugin directory (a path; it may not exist yet).

    A read: the loader treats a missing folder as no plugins.  The folder
    appears when the user creates it to drop a plugin in.

    Args:
        root: The cfg root whose ``plugin/`` child is the global layer;
            None resolves :func:`cfg_dir`.  An engine under test passes a
            temp folder so the checkout's own ``termapy_cfg/`` stays out.
    """
    return (root if root is not None else cfg_dir()) / PLUGIN


def library_dir(root: Path | None = None) -> Path:
    """Return the GLOBAL device library (a path; it may not exist yet).

    The ``lib/`` sibling of :func:`global_devices_dir`, holding a tree of
    ``.device.json`` files any config may pick parts FROM.  Unlike ``dev/``,
    nothing here loads -- it is a pool, so a thousand parts can sit in it
    without any of them reaching a symbol table.  The per-config layer
    (``<cfg>/lib/``, which wins a name clash) is
    ``termapy.devices.config_library_dir``; ``devices.library_layers``
    gives both in precedence order.

    Args:
        root: The cfg root whose ``lib/`` child is the library; None
            resolves :func:`cfg_dir`.
    """
    return (root if root is not None else cfg_dir()) / LIB


def global_devices_dir(root: Path | None = None) -> Path:
    """Return the global device directory (a path; it may not exist yet).

    The ``dev/`` sibling of :func:`global_plugins_dir`: register files
    shared by every config on this machine, since a chip is not specific
    to one build.

    Args:
        root: The cfg root whose ``dev/`` child is the global layer;
            None resolves :func:`cfg_dir`.
    """
    return (root if root is not None else cfg_dir()) / DEV


_ENV_RE = re.compile(r"\$\(env\.(\w+)(?:\|([^)]*))?\)")


def expand_env_str(text: str) -> str:
    """Expand $(env.NAME) and $(env.NAME|fallback) placeholders using os.environ.

    Unknown variables without a fallback are left unchanged (config must never
    crash due to a missing environment variable).

    Args:
        text: String potentially containing $(env.NAME) placeholders.

    Returns:
        String with known placeholders replaced.
    """

    def _replace(m: re.Match) -> str:
        val = os.environ.get(m.group(1))
        if val is not None:
            return val
        if m.group(2) is not None:
            return m.group(2)
        return m.group(0)

    return _ENV_RE.sub(_replace, text)


# Config keys whose value is ONE command dispatched on connect (TUI/CLI/MCP
# all run it through the normal REPL pipeline; several commands are a .run
# file the field runs -- see ``on_connect_commands``).
ON_CONNECT_KEYS: tuple[str, ...] = (
    "on_connect_cmd",
    "tui_on_connect_cmd",
    "cli_on_connect_cmd",
    "mcp_on_connect_cmd",
)

# The on-connect commands are deliberately NOT env-expanded at load time:
# doing so bypasses the per-command env gate and puts $(env.X) values on the
# wire for bare device commands -- the $(env.PATH) incident class.
# Dispatched normally, the env_var transform expands $(env.X) for repl
# commands but not for device text (env.py's TRANSFORM has no ``serial``
# variant), so these keys follow the same env-to-wire rule as typed input.
_ENV_EXPAND_EXCLUDED_KEYS = frozenset(ON_CONNECT_KEYS)


def _collect_bool_paths() -> frozenset[tuple[str, ...]]:
    """Every config key of kind ``bool``, as a path, from the one table (``CFG_KEYS``)."""
    return frozenset(tuple(record.path.split(".")) for record in CFG_KEYS if record.kind == "bool")


# The cfg keys that hold a boolean, by path (("echo",), ("serial",
# "custom_baud")), derived from the schema so a new boolean key is
# covered without a second list.  bool-one-vocabulary: a templated
# value landing on one of these is read with the vocabulary at
# expansion; a bare JSON string there is type-checked by validate_config.
BOOL_CFG_PATHS: frozenset[tuple[str, ...]] = _collect_bool_paths()


def _default_at(path: tuple[str, ...]) -> Any:
    """The DEFAULT_CFG value at a key path."""
    node: Any = DEFAULT_CFG
    for part in path:
        node = node[part]
    return node


def _expand_bool_value(
    path: tuple[str, ...],
    raw: str,
    expanded: str,
    warnings: list[str] | None,
) -> bool:
    """A templated boolean key: unresolved falls back to the default, loudly; anything else is read with the vocabulary."""
    dotted = ".".join(path)
    if "$(env." in expanded:
        default = _default_at(path)
        if warnings is not None:
            warnings.append(f"{dotted}: {raw} did not resolve; using default {json.dumps(default)}")
        return default
    return parse_bool(expanded, what=dotted)


def expand_env_cfg(
    cfg: dict,
    warnings: list[str] | None = None,
    _path: tuple[str, ...] = (),
) -> dict:
    """Expand $(env.NAME) in string values of a config dict, recursively.

    Mutates and returns *cfg*.  Recurses into nested dict values so
    env vars in ``cfg["serial"]["port"]`` (and any future grouped
    section) get expanded too -- the v22 migration nested pyserial
    keys under ``cfg["serial"]``, so a top-level-only expander
    silently stopped working for serial keys.  Non-string, non-dict
    values are left untouched, as are the connect-command keys in
    ``_ENV_EXPAND_EXCLUDED_KEYS`` (see that constant).

    A templated value on a boolean key (``BOOL_CFG_PATHS``) is read with
    the one vocabulary after expansion, so the cfg holds a real bool:
    the text ``"false"`` used to stay a string and read as True.  One
    that does not resolve falls back to the default and says so in
    *warnings*.

    Args:
        cfg: Config dict to expand in place.
        warnings: Collects a line for each templated boolean that did
            not resolve and fell back to its default.
        _path: The key path of *cfg* within the root, for the recursion.

    Returns:
        The same dict with string values expanded.

    Raises:
        ValueError: A templated boolean resolved to text outside the
            vocabulary (``Invalid echo: maybe (use ...)``).
    """
    for key, val in cfg.items():
        if key in _ENV_EXPAND_EXCLUDED_KEYS:
            continue
        path = (*_path, key)
        if isinstance(val, str) and "$(" in val:
            expanded = expand_env_str(val)
            if path in BOOL_CFG_PATHS:
                cfg[key] = _expand_bool_value(path, val, expanded, warnings)
            else:
                cfg[key] = expanded
        elif isinstance(val, dict):
            expand_env_cfg(val, warnings, path)
    return cfg


def validate_config(cfg: dict) -> list[str]:
    """Validate config values and return a list of warning strings.

    Every key gets ``check_cfg_value``, the one check the config editor and
    ``/cfg`` use too.  Keys not in ``DEFAULT_CFG`` are flagged: if they
    appear in ``DEPRECATED_CFG`` they're called out as deprecated with the
    rename-or-removal history; otherwise flagged as a potential typo.

    Args:
        cfg: Config dict to validate.

    Returns:
        List of warning strings (empty means valid).
    """
    warnings: list[str] = []

    # Config version
    ver = cfg.get("config_version")
    from_future = False
    if ver is not None and ver != CURRENT_CONFIG_VERSION:
        if ver > CURRENT_CONFIG_VERSION:
            # Cfg was created by a NEWER termapy.  The running
            # termapy doesn't know about fields the newer schema
            # added, so the per-key "unknown key (typo?)" noise
            # below would be misleading -- those aren't typos,
            # they're fields from the future.  Emit one clear
            # upgrade hint and suppress the per-key spam.
            from_future = True
            from termapy.install_info import upgrade_command

            warnings.append(
                f"cfg was created by a newer termapy "
                f"(config_version {ver} > {CURRENT_CONFIG_VERSION}).  "
                f"Upgrade with: {upgrade_command()}.  "
                f"Then restart termapy."
            )
        else:
            # Older cfg -- migrate_config() will bring it forward
            # automatically; the warning is just informational.
            warnings.append(f"config_version: {ver} (current is {CURRENT_CONFIG_VERSION})")

    # Unknown / deprecated keys (skip internal keys starting with _).
    # Skipped entirely when the cfg is from a newer termapy: the
    # "unknown" keys are almost certainly new schema, not typos,
    # and listing each one is noise rather than signal.
    if not from_future:
        for key in cfg:
            if key.startswith("_") or key in DEFAULT_CFG:
                continue
            hint = DEPRECATED_CFG.get(key)
            if hint:
                warnings.append(f"deprecated key: '{key}' ({hint})")
            else:
                warnings.append(f"unknown key: '{key}' (typo?)")

    # An on-connect field is one command (v30).  An older cfg (``--check``
    # reads it as it is on disk) has its chains moved into run files by the
    # v29 -> v30 step at load instead, so they are not its problem yet.
    chains_migrate = ver is not None and ver < 30
    for record in CFG_KEYS:
        if chains_migrate and record.kind == "command":
            continue
        value = _value_as_written(cfg, record.path)
        if value is None:
            continue
        finding = check_cfg_value(record.path, value, cfg)
        if finding is not None:
            warnings.append(_finding_text(record.path, value, finding))
    return warnings


def _value_as_written(cfg: Mapping, path: str) -> Any:
    """``cfg``'s value at ``path``, or ``None`` when the file does not hold it."""
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def _finding_text(path: str, value: object, finding: "CfgFinding") -> str:
    """A finding as a load warning: the key, what it must be, and what it holds, kept short."""
    shown = json.dumps(value, default=str)
    if value == "" or "\n" in str(value):
        return f"{path}: {finding.reason}"
    return f"{path}: {finding.reason}, got {shown if len(shown) <= 60 else shown[:57] + '...'}"


class CfgFinding(NamedTuple):
    """What ``check_cfg_value`` found wrong with a value: a load warns, the editor shows it red and ``/cfg`` refuses.

    Attributes:
        reason: What the value must be, as a clause that reads after its
            key: ``must be a positive integer``.
    """

    reason: str


# The bytes a line ending may hold.
_EOL_BYTES = "\r\n\0\x03\x04"


def _check_bool(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """A JSON boolean: text or a number here is type-checked, never parsed (bool-one-vocabulary)."""
    return None if isinstance(value, bool) else CfgFinding("must be true or false")


def _check_int(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """A whole number, at least ``record.min`` when the row sets one."""
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (record.min is None or value >= record.min)
    ):
        return None
    shapes = {None: "an integer", 0: "a non-negative integer", 1: "a positive integer"}
    shape = shapes.get(record.min, f"an integer of at least {record.min}")
    return CfgFinding(f"must be {shape}")


def _check_enum(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """One of the row's choices, as the file spells it; ``true`` is not the number 1."""
    if not isinstance(value, (bool, list, dict)) and value in record.choices:
        return None
    names = ", ".join(str(choice) for choice in sorted(record.choices, key=str))
    return CfgFinding(f"must be one of: {names}")


def _check_str(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """Text, whatever it says: a title, a format, a file name."""
    return None if isinstance(value, str) else CfgFinding("must be text")


def _check_regex(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """A pattern that compiles, so a bad one is found here and not at first use."""
    if not isinstance(value, str):
        return CfgFinding("must be text")
    try:
        re.compile(value)
    except re.error as e:
        return CfgFinding(f"does not compile: {e}")
    return None


def _check_encoding(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """The name of a codec Python has."""
    if not isinstance(value, str):
        return CfgFinding("must be text")
    try:
        codecs.lookup(value)
    except (LookupError, ValueError):
        return CfgFinding("must name a codec Python knows")
    return None


def _check_command(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """One command: a line break means a chain, which is skipped rather than sent as one line."""
    if not isinstance(value, str):
        return CfgFinding("must be text")
    if len(value.strip().splitlines()) > 1:
        return CfgFinding(
            f"one command per field, so it is skipped; put several in a .run file "
            f"and set {record.path} to {cmd_prefix(cfg)}run <name>"
        )
    return None


def _check_port(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """Anything but empty: a device name, a USB serial number, a reserved name, a chain or a URL.

    The zero-config CLI synthesizes an in-memory cfg with an empty port to
    bootstrap a REPL with no config file, but that path never hits this
    check; a config on disk does.
    """
    if not isinstance(value, str):
        return CfgFinding("must be text")
    if not value:
        return CfgFinding("must not be empty (use a device name, USB serial number, or 'DEMO')")
    return None


def check_baud(baud: int, *, custom: bool, remedy: str) -> str | None:
    """Why ``baud`` cannot be a port's rate, or None: a standard rate, or any from 300 up when ``custom``.

    The one rule for a typed rate (registry ``baud-standard-unless-custom``):
    a rate off ``STANDARD_BAUD_RATES`` must be asked for as custom, as the
    New Config dialog's custom mode asks, so a typo (``11520``) is refused
    rather than opened.  A config says so with ``custom_baud``; a command
    that sets a rate with ``custom=true``.

    Args:
        baud: The rate.
        custom: Whether the rate was asked for as custom.
        remedy: How the caller's user asks for custom, completing "``remedy``
            to allow it": ``set custom_baud to true``, ``add custom=true``.
    """
    if custom:
        return "custom baud requires >= 300" if baud < 300 else None
    if baud not in STANDARD_BAUD_RATES:
        return f"not a standard rate: {remedy} to allow it"
    return None


def _check_baud(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """A positive whole number, standard unless ``custom_baud`` is true (``check_baud``)."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return CfgFinding("must be a positive integer")
    custom = bool(cfg_value(cfg, "serial.custom_baud"))
    reason = check_baud(value, custom=custom, remedy="set custom_baud to true")
    return CfgFinding(reason) if reason else None


def _check_eol(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """Only line-ending and control bytes: junk like ``abc`` would go out after every line."""
    if isinstance(value, str) and all(char in _EOL_BYTES for char in value):
        return None
    return CfgFinding(
        r'must be CR/LF/NUL/ETX/EOT bytes only (e.g. "", "\r", "\n", "\r\n", "\0", '
        r'"\u0003" (ETX), "\u0004" (EOT)); '
        f"{cmd_prefix(cfg)}term.eol sets it by name"
    )


def _check_section(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """A JSON object: its leaves have rows of their own."""
    return None if isinstance(value, dict) else CfgFinding("must be a JSON object")


def _check_list(record: CfgKey, value: object, cfg: Mapping) -> CfgFinding | None:
    """A JSON list: the custom buttons, edited in the config editor."""
    return None if isinstance(value, list) else CfgFinding("must be a JSON list")


# One check per ``CfgKey.kind``.  A test holds every row's kind to this table.
CFG_CHECKS = {
    "bool": _check_bool,
    "int": _check_int,
    "enum": _check_enum,
    "str": _check_str,
    "path": _check_str,
    "regex": _check_regex,
    "encoding": _check_encoding,
    "command": _check_command,
    "port": _check_port,
    "baud": _check_baud,
    "eol": _check_eol,
    "section": _check_section,
    "list": _check_list,
}


def check_cfg_value(key: str, value: object, cfg: Mapping | None = None) -> CfgFinding | None:
    """What is wrong with ``value`` for config key ``key``, or ``None`` when it is fine.

    The one check behind a load (``validate_config``), the config editor
    and ``/cfg``, so "is this value OK" has one answer (registry
    ``cfg-schema-one-table``).

    Args:
        key: A path, a unique leaf or a custom button's field.  A name the
            schema does not hold has no rule, so it is fine here.
        value: The value as a config holds it: real JSON types, never text
            still to be parsed.
        cfg: The config it sits in, for the rule that reads a second key
            (``custom_baud``, ``cmd_prefix``).

    Returns:
        The finding, or ``None``.
    """
    record = cfg_key(key)
    if record is None:
        return None
    return CFG_CHECKS[record.kind](record, value, cfg or {})


def cfg_choice(key: str, value: object) -> object:
    """``value`` spelled as ``CFG_CHOICES`` has it when ``key`` is a fixed-value key, else as given.

    Text matches in any case: ``/cfg parity e`` stores ``E`` and ``/cfg
    custom_buttons_side Right`` stores ``right``, the spelling the file and
    its checks expect.  A numeric choice matches as a number, so ``/cfg
    stop_bits 1.5`` works whatever the key holds now.

    Raises:
        ValueError: ``value`` is not one of the key's choices.
    """
    choices = CFG_CHOICES.get(key)
    if choices is None:
        return value
    for choice in choices:
        if _same_choice(value, choice):
            return choice
    names = "/".join(str(choice) for choice in sorted(choices, key=str))
    raise ValueError(f"Invalid {key}: {value} (use {names})")


def _same_choice(value: object, choice: object) -> bool:
    """Whether ``value``, typed or held, is ``choice``: text in any case, a number by value."""
    if isinstance(choice, str):
        return isinstance(value, str) and choice.casefold() == value.casefold()
    if isinstance(value, str):
        try:
            return float(value) == choice
        except ValueError:
            return False
    return value == choice


# Exception types ``load_config`` can realistically raise.  Callers
# use this as the ``except`` target so a single narrowing rule covers
# every file-load site: OSError (missing file / permission denied /
# disappeared mid-read) and ValueError (JSONDecodeError is a subclass;
# also raised for invalid values caught during migration).  Anything
# else from ``load_config`` is a bug worth propagating.
CONFIG_LOAD_ERRORS: tuple[type[BaseException], ...] = (OSError, ValueError)


def on_connect_commands(cfg: Mapping[str, Any], mode_key: str) -> list[tuple[str, str]]:
    """The ``(key, command)`` pairs to run on connect, in order.

    ``on_connect_cmd`` (every frontend), then ``mode_key`` (the frontend's
    own, e.g. ``cli_on_connect_cmd``).  A field is one command, dispatched
    as written and never split; several commands belong in a .run file the
    field runs.  A field holding a line break is skipped, never sent:
    ``validate_config`` names it, and a line break must not reach a device
    inside what is sent as one line.

    Every frontend calls this right before it runs what it returns, so each
    command is recorded here in the diagnostic log with the key it came
    from: a script it starts reads as the config's, not the user's.
    """
    commands: list[tuple[str, str]] = []
    for key in ("on_connect_cmd", mode_key):
        command = str(cfg.get(key) or "").strip()
        if command and len(command.splitlines()) == 1:
            commands.append((key, command))
            diag_log.info("on connect: %s runs %s", key, command)
    return commands


def _write_migration_run_files(cfg: dict, path: str) -> list[str]:
    """Write the run files ``_migrate_v29_to_v30`` asked for; one warning each.

    Never overwrites a different file: a taken name gets ``_2``, ``_3`` ...
    and the field is pointed at the file actually written.  Runs before the
    cfg is saved, so the saved field never names a missing script.
    """
    pending: dict[str, tuple[str, str]] = cfg.pop("_migration_run_files", {})
    if not pending:
        return []
    run_folder = ensure_folder(cfg_data_dir(path) / RUN)
    warnings: list[str] = []
    for key, (name, text) in pending.items():
        stem, number = name, 1
        ext = FOLDER_EXTS[RUN]
        while (run_folder / f"{stem}{ext}").exists() and (
            (run_folder / f"{stem}{ext}").read_text(encoding="utf-8") != text
        ):
            number += 1
            stem = f"{name}_{number}"
        (run_folder / f"{stem}{ext}").write_text(text, encoding="utf-8")
        cfg[key] = f"{cmd_prefix(cfg)}run {stem}"
        warnings.append(
            f"{key} held {len(text.splitlines())} commands; they are now "
            f"{RUN}/{stem}.run and {key} is {cfg[key]}"
        )
    return warnings


def cfg_value_text(value: object) -> str:
    """A config value as termapy shows it: what ``/cfg <key>`` answers and a change reports.

    A switch is ``on`` / ``off``, as every setting command answers; a
    container is compact JSON, as the config file spells it; an empty string
    is ``""`` so it reads as one; anything else is as written, with no
    Python spelling (``True``, ``'right'``).

    Args:
        value: The value from the config.

    Returns:
        The text.
    """
    if isinstance(value, (dict, list)) or value is None or value == "":
        return json.dumps(value)
    return value_text(value)


def load_config(path: str) -> dict:
    """Load and validate JSON config, applying defaults for missing fields.

    Raises FileNotFoundError if the file doesn't exist. Config creation
    is handled by the caller (--demo flag or TUI interactive prompt).

    Args:
        path: Path to the JSON config file.

    Returns:
        Config dict with migrations applied and all defaults present.

    Raises:
        FileNotFoundError: If the config file does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    # UTF-8 whatever the platform's default, and a byte-order mark an editor
    # saved is skipped rather than failing the JSON parse.
    with open(path, encoding="utf-8-sig") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}") from e

    # Run migrations before applying defaults
    old_version = cfg.get("config_version", 0)
    cfg = migrate_config(cfg)
    migrated = old_version != CURRENT_CONFIG_VERSION
    changed = migrated

    for key, val in DEFAULT_CFG.items():
        if key not in cfg:
            # deepcopy so a mutable default (e.g. the custom_buttons list)
            # backfilled into a user config isn't an alias of the module-
            # global DEFAULT_CFG -- otherwise an in-session edit to the
            # user's buttons would mutate the process-wide default.
            cfg[key] = copy.deepcopy(val)
            changed = True
        elif isinstance(val, dict) and isinstance(cfg[key], dict):
            # One-level recursive backfill for nested-dict defaults
            # (e.g. cfg["serial"], cfg["ndjson_field_routing"]).
            # Lets users partial-set nested keys without losing their
            # siblings to "key missing" surprises at access sites.
            for sub_key, sub_val in val.items():
                if sub_key not in cfg[key]:
                    cfg[key][sub_key] = copy.deepcopy(sub_val)
                    changed = True
    cfg.pop("_migrated_from", None)  # clean up stale marker from older saves
    # Before anything is written: refuses the bundled template, sets up
    # the folder the migration's run files go in.
    prepare_cfg_folder(path)
    # Migration functions may stash one-shot warnings in
    # ``_migration_warnings`` (e.g. "your retired key was true; set
    # the env var").  Merge them with the expansion and structural
    # validation warnings so the user sees all through the single
    # existing display path.
    mig_warnings = cfg.pop("_migration_warnings", []) + _write_migration_run_files(cfg, path)
    if changed:
        write_config_file(path, cfg)
    if migrated:
        cfg["_migrated_from"] = old_version
    expand_warnings: list[str] = []
    cfg = expand_env_cfg(cfg, expand_warnings)
    config_warnings = validate_config(cfg)
    all_warnings = list(mig_warnings) + expand_warnings + config_warnings
    if all_warnings:
        cfg["_config_warnings"] = all_warnings
    cleanup_profile_temps(path)
    return cfg


def config_summary(path: Path) -> dict[str, Any]:
    """What a config listing shows for one cfg: ``port``, ``baud_rate``, ``title``.

    Shared by ``/cfg.list`` and the Cfg picker, so both show the same facts.
    All empty (``baud_rate`` None) when the file won't load -- a broken cfg
    still deserves a row, so it can be edited or deleted.  A title that only
    repeats the config's name (how new configs are written) reads as none.

    Read-only: the file is parsed and migrated in memory, never written.
    ``load_config`` is for loading a config, not listing one -- it writes
    newly added default keys back into the file and cleans that config's
    profile temps, so listing through it rewrote every config whenever
    ``DEFAULT_CFG`` grew.
    """
    try:
        with open(path, encoding="utf-8-sig") as f:  # as load_config reads it
            cfg = migrate_config(json.load(f))
    except Exception:  # see the docstring: a broken cfg still gets its row
        # Once per file: a listing reads every config each time it refreshes.
        diag_log.exception("config summary unreadable: %s", path, once=("summary", str(path)))
        return {"port": "", "baud_rate": None, "title": ""}
    serial_cfg = cfg.get("serial", {})
    title = str(cfg.get("title") or "")
    if title.strip().lower() == path.stem.lower():
        title = ""
    return {
        "port": str(serial_cfg.get("port") or ""),
        "baud_rate": serial_cfg.get("baud_rate") or None,
        "title": title,
    }


def file_manager_name(platform: str = sys.platform) -> str:
    """The name of this platform's file manager, for a label ("Finder").

    Windows and macOS each have one and everyone knows its name.  Linux
    has many, and most users know theirs only as the "Files" or "File
    Manager" entry in a menu, so naming Thunar or Dolphin would puzzle
    more people than it would please; "Files" is the GNOME, Mint and
    elementary menu label and reads as a plain noun everywhere else.  A
    verb like "Explore" reads as Windows to everyone else, which is why
    the label is a name at all.

    Args:
        platform: ``sys.platform``; a parameter so a test needs no patching.

    Returns:
        ``"Explorer"``, ``"Finder"``, or ``"Files"``.
    """
    if platform == "win32":
        return "Explorer"
    if platform == "darwin":
        return "Finder"
    return "Files"


def open_with_system(path: str) -> None:
    """Open a file or folder with the system default application.

    Platforms:

    - Windows: ``os.startfile`` dispatches the shell verb and returns
      immediately; the OS owns the child lifecycle, nothing to reap.
    - macOS / Linux: spawn a daemon thread that ``subprocess.run``'s
      the launcher (``open`` / ``xdg-open``).  The thread blocks on
      the short-lived launcher (usually < 100 ms) and then dies,
      which also reaps the child so it doesn't sit in the process
      table as a zombie.  Using ``run`` directly on the main thread
      would freeze the TUI while ``xdg-open`` resolves the default
      application; using a bare ``Popen`` without ``wait`` accumulates
      zombies over a long session.  The thread is ``daemon=True`` so
      an orphan launcher never keeps termapy alive.
    """
    if sys.platform == "win32":
        import os

        os.startfile(path)
        return
    _launch(["open", path] if sys.platform == "darwin" else ["xdg-open", path])


def _launch(cmd: list[str] | str) -> None:
    """Run a short-lived desktop launcher on a daemon thread, which reaps it.

    The one launcher behind ``open_with_system`` and
    ``reveal_in_file_manager``; why a thread rather than ``run`` or a bare
    ``Popen`` is in ``open_with_system``'s docstring.
    """
    import subprocess

    def _launch_and_reap() -> None:
        try:
            subprocess.run(cmd, check=False, timeout=60.0)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass  # launcher hung or missing -- best effort

    threading.Thread(target=_launch_and_reap, daemon=True).start()


def reveal_command(path: Path, platform: str = sys.platform) -> list[str] | str:
    """The command that shows ``path`` in the file manager with it selected.

    Windows takes ``explorer /select,"<path>"`` as ONE command line: the
    quotes must follow the comma, and an argument list would quote the
    whole ``/select,...`` token instead.  macOS has ``open -R``.  Linux has
    no standard "select this file" call, so the folder opens.

    Args:
        path: The file to show.
        platform: ``sys.platform``; a parameter so each form is testable
            anywhere.
    """
    target = path.resolve()
    if platform == "win32":
        return f'explorer /select,"{target}"'
    if platform == "darwin":
        return ["open", "-R", str(target)]
    return ["xdg-open", str(target.parent)]


def reveal_in_file_manager(path: str) -> None:
    """Show a file in the OS file manager with it selected (``reveal_command``)."""
    _launch(reveal_command(Path(path)))


def request_rx_buffer(port: Any) -> bool:
    """Ask the driver for a wider RX buffer than pyserial's 4 KB default.

    Best-effort by design.  ``set_buffer_size`` exists only on
    ``serialwin32``, and even there the driver may refuse or clamp -- neither
    is a reason to fail an otherwise good open, since the port works fine at
    the default size (it just loses bytes sooner under a main-thread stall;
    see ``SERIAL_RX_BUFFER_BYTES``).

    Args:
        port: An open port object, real or duck-typed.

    Returns:
        True if the request was made and accepted, False if the port has no
        such API or the driver refused.
    """
    setter = getattr(port, "set_buffer_size", None)
    if setter is None:
        return False
    try:
        setter(rx_size=SERIAL_RX_BUFFER_BYTES)
    except (OSError, serial.SerialException, ValueError):
        return False
    return True


def cap_folder_of(config_path: str) -> Path | None:
    """The ``cap/`` folder of the config at ``config_path``; None with no config."""
    return Path(config_path).parent / CAP if config_path else None


def _spy_log_path(name: str, cap_folder: Path | None) -> Path:
    """Where a ``spy://`` port's ``file=`` goes: under ``cap/``, or a refusal.

    Raises:
        ValueError: There is no config, or ``name`` is not a path relative to ``cap/``.
    """
    if cap_folder is None:
        raise ValueError("A spy:// log file needs a config, for its cap/ folder")
    given = Path(name)
    path = inside(cap_folder, name)
    # A full path and any ".." are refused as typed, even one that ends up back
    # in cap/.  Resolving catches what the text alone cannot: a symlink, and the
    # Windows forms (\x, D:x) that are not absolute but leave the folder.
    if given.is_absolute() or ".." in given.parts or path is None or path == cap_folder.resolve():
        raise ValueError(f"Invalid spy file: {name} (use a path relative to cap/)")
    return path


def spy_log_url(url: str, cap_folder: Path | None) -> str:
    """``url`` with a ``spy://`` port's ``file=`` moved under the config's ``cap/`` folder.

    pyserial opens ``file=`` for writing as soon as it reads the port string,
    so this is a file write that no path guard ever sees.  It takes only a
    path relative to ``cap/``, in every frontend and under no flag (registry
    ``spy-log-in-cap``).  Every other port string passes through untouched.

    Args:
        url: The resolved port string.
        cap_folder: The config's ``cap/`` folder, created when a log goes in it;
            None with no config.

    Returns:
        The port string to hand to pyserial.

    Raises:
        ValueError: A ``file=`` is not a path relative to ``cap/``, or there is
            no config to hold it.  Nothing has been written.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit

    parts = urlsplit(url)
    if parts.scheme != "spy":
        return url
    options = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "file" for key, _ in options):
        return url
    # Read as pyserial reads it, so an encoded "..%2F" is seen decoded here too.
    moved = [
        (key, str(_spy_log_path(value, cap_folder)) if key == "file" else value)
        for key, value in options
    ]
    for key, value in moved:
        if key == "file":
            ensure_folder(Path(value).parent)
    return f"{url.partition('?')[0]}?{urlencode(moved)}"


def open_serial(
    cfg: dict, *, cap_folder: Path | None = None, policy: PortPolicy | None = None
) -> Any:
    """Open serial port from config dict.

    ``cfg["serial"]["port"]`` may be a plain device name (``"COM3"``,
    ``"/dev/ttyUSB0"``), a USB serial number, a ``|``-separated fallback
    chain (``"A1B2C3D4|COM3"``), a reserved name (``"DEMO"``,
    ``"DEMO_FAIL"``), or a pyserial URL.  Resolution to a concrete
    device happens here via ``resolve_port_for_open()``; see
    ``resolve_port``'s docstring and ``help/ports.md`` for the full grammar.

    This is the one place termapy opens a port (registry
    ``port-policy-at-opener``), so the port policy
    (``TERMAPY_ALLOWED_PORTS``) is applied here, before a simulator is
    built, a ``spy://`` log is created, or pyserial touches a device or the
    network.

    If the resolved name is ``"DEMO"``, returns a ``FakeSerial``
    simulated device instead of a real serial connection.  If the
    resolved name is ``"DEMO_FAIL"``, raises ``OSError`` -- a test
    hook for exercising the open-failure path without needing a broken
    real port.

    Args:
        cfg: Config dict with serial settings.
        cap_folder: Where a ``spy://`` port's log goes (``spy_log_url``).
            Omitted, such a port is refused rather than written from the
            working folder.
        policy: The port policy; None is this process's
            (``env_flags.port_policy``).

    Returns:
        A serial port object (real or simulated).

    Raises:
        AmbiguousSerialNumberError: when ``cfg["serial"]["port"]`` is a
            spec whose serial-number candidate matches two or more
            connected devices.  Surfaces through to the connect failure
            path so the user sees which devices collided.
        PortNotAllowedError: The port policy refuses the port.
        ValueError: A ``spy://`` port's ``file=`` is refused.
    """
    from termapy.port_control import resolve_port_for_open

    serial_cfg = cfg["serial"]
    resolved = resolve_port_for_open(serial_cfg["port"], policy=policy)
    name = resolved.upper()
    if name == "DEMO":
        from termapy.demo import FakeSerial

        return FakeSerial(baudrate=serial_cfg["baud_rate"])
    if name == "DEMO_JSON":
        from termapy.demo_ndjson import FakeSerialNDJSON

        return FakeSerialNDJSON(baudrate=serial_cfg["baud_rate"])
    if name == "DEMO_VT100":
        from termapy.demo_vt100 import FakeSerialVT100

        return FakeSerialVT100(baudrate=serial_cfg["baud_rate"])
    if name == "DEMO_FAIL":
        raise OSError("DEMO_FAIL: simulated open failure")

    fc = serial_cfg["flow_control"]
    # serial_for_url handles both plain ports ("COM3", "/dev/ttyUSB0")
    # and URLs ("rfc2217://host:port", "socket://host:port", "loop://").
    port = serial.serial_for_url(
        spy_log_url(resolved, cap_folder),
        baudrate=serial_cfg["baud_rate"],
        bytesize=serial_cfg["byte_size"],
        parity=serial_cfg["parity"],
        stopbits=serial_cfg["stop_bits"],
        rtscts=(fc == "rtscts"),
        xonxoff=(fc == "xonxoff"),
        timeout=SERIAL_READ_TIMEOUT_S,
        # One process owns one port (registry port-open-exclusive).  Windows
        # refuses a second open by itself; on POSIX this is pyserial's flock,
        # without which a second termapy opens the port and splits its bytes.
        exclusive=True,
    )
    request_rx_buffer(port)
    return port


def cfg_relative_path(config_path: str, raw: str, ext: str = "") -> Path:
    """A user-typed file path, resolved the way the config folder implies.

    Absolute paths are taken as given.  A relative path is anchored to the
    config folder when it exists there -- ``/profile.load x.profile.json``
    and ``/sym.load x.symbols.json`` mean the files beside the cfg, not
    whatever the process CWD happens to be (under the MCP server that is
    wherever the client started it) -- and falls back to the CWD form
    otherwise, so the error names what the user typed.

    Args:
        config_path: The active config file (``""`` = no config).
        raw: The path as typed.
        ext: The extension the typed name may leave off, tried at each
            location (``default_file_name``); ``""`` tries none.

    Returns:
        The resolved path.
    """
    if Path(raw).is_absolute() or not config_path:
        return default_file_name(raw, ext)
    anchored = default_file_name(Path(config_path).parent / raw, ext)
    return anchored if anchored.exists() else default_file_name(raw, ext)


def not_found_message(subject: str, raw: str, config_path: str) -> str:
    """``"<Subject> not found: <raw>"``, plus the folder a relative path means.

    A relative path given to a command is anchored to the CONFIG folder,
    not the shell's working directory (:func:`cfg_relative_path`), so an
    error naming only what the user typed leaves out the one fact they
    need -- they retype variations of the same relative path while the
    command keeps looking somewhere else entirely.

    Takes the CONFIG PATH rather than the resolved path on purpose: when
    the anchored file does not exist, ``cfg_relative_path`` falls back to
    the raw path, so the resolved value is exactly the input in the case
    that most needs explaining.  The folder the relative path was measured
    from is what the user has to know.  An absolute path is unambiguous
    already, so it gets the short form.

    Names the config FOLDER, not its absolute path: the absolute form is a
    temp directory under test and would put a machine-specific string in
    the CLI gold, which must stay deterministic.  ``sym._display_path``
    makes the same trade for the same reason.

    Args:
        subject: What was missing, sentence case (``"Source file"``).
        raw: The path as the user typed it.
        config_path: The active config file (``""`` = no config).

    Returns:
        The message for ``CmdResult.fail(msg=...)``.
    """
    base = f"{subject} not found: {raw}"
    if Path(raw).is_absolute() or not config_path:
        return f"{base}{file_suggestion(Path(raw).parent, raw)}"
    cfg_folder = Path(config_path).parent
    hint = file_suggestion((cfg_folder / raw).parent, raw)
    return f"{base} (a relative path is measured from the {cfg_folder.name} config folder){hint}"


def config_names() -> list[str]:
    """The names of the configs under ``cfg_dir()``, each ``<name>/<name>.cfg``: what a config name may be."""
    return [path.stem for path in config_files(cfg_dir())]


def setup_demo_config(target_path: Path, *, force: bool = False) -> Path:
    """Copy bundled demo config files to the target directory.

    Creates ``<target_path>/demo/`` with config, scripts, and proto files.
    Only the folders it populates (``run/ proto/ plugin/ sym/ dev/``) are
    created; the rest appear on first use, like any config's.
    Does not overwrite existing files unless *force* is True.

    Args:
        target_path: Parent directory for the demo config folder.
        force: Overwrite existing files with bundled templates.

    Returns:
        Path to the demo config JSON file.
    """
    import importlib.resources

    demo_dir = target_path / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)

    config_path = demo_dir / "demo.cfg"

    # Source package
    pkg = importlib.resources.files("termapy.builtins.demo")

    # Copy config file
    if force or not config_path.exists():
        src = pkg / "demo.cfg"
        config_path.write_bytes(src.read_bytes())

    # Copy the symbol table into sym/ (auto-loaded by /sym.*)
    symbols_path = ensure_folder(demo_dir / SYM) / f"demo{SYMBOLS_SUFFIX}"
    if force or not symbols_path.exists():
        src = pkg / f"demo{SYMBOLS_SUFFIX}"
        symbols_path.write_bytes(src.read_bytes())

    # The board's UART block as a device file, auto-loaded beside the symbols.
    # The table's CONSOLE pointer constant holds this block's base, so the
    # demo shows an alias from the build out of the box (/sym CONSOLE_MODE).
    device_path = ensure_folder(demo_dir / DEV) / f"console{FOLDER_EXTS[DEV]}"
    if force or not device_path.exists():
        src = pkg / "dev" / f"console{FOLDER_EXTS[DEV]}"
        device_path.write_bytes(src.read_bytes())

    # The legacy-grammar memory profile: /profile.load demo_legacy.profile.json
    # drives /mem.* through the device's own `mem` syntax (template dialect).
    legacy_path = demo_dir / "demo_legacy.profile.json"
    if force or not legacy_path.exists():
        src = pkg / "demo_legacy.profile.json"
        legacy_path.write_bytes(src.read_bytes())

    # Copy run scripts
    run_dir = ensure_folder(demo_dir / "run")
    run_pkg = pkg / "run"
    for name in (
        "welcome.run",
        "at_demo.run",
        "gps_demo.run",
        "smoke_test.run",
        "status_check.run",
        "var_demo.run",
        "expect_test.run",
        "doc_screenshots.run",
        "crc_tour.run",
    ):
        dest = run_dir / name
        if force or not dest.exists():
            src = run_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy proto files
    proto_dir = ensure_folder(demo_dir / "proto")
    proto_pkg = pkg / "proto"
    for name in ("at_test.pro", "bitfield_inline.pro", "modbus_inline.pro"):
        dest = proto_dir / name
        if force or not dest.exists():
            src = proto_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy demo plugins
    plugin_dir = ensure_folder(demo_dir / "plugin")
    plugin_pkg = pkg / "plugin"
    for name in ("cmd.py", "probe.py", "temp_plot.py", "traffic.py"):
        dest = plugin_dir / name
        if force or not dest.exists():
            src = plugin_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy .gitignore for transient data
    gitignore_dest = demo_dir / ".gitignore"
    if force or not gitignore_dest.exists():
        src = pkg / ".gitignore"
        gitignore_dest.write_bytes(src.read_bytes())

    return config_path
