"""Config file management - paths, defaults, loading, and serial port setup.

Pure functions with no UI dependency. Used by app.py and tests.
"""

import codecs
import copy
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

import serial

from termapy.defaults import (
    DEFAULT_CFG,
    STANDARD_BAUD_RATES,
    VALID_BYTE_SIZES,
    VALID_FLOW_CONTROLS,
    VALID_PARITIES,
    VALID_STOP_BITS,
)
from termapy.folders import (
    FOLDER_MIGRATIONS,
    FOLDER_NAMES,
    HISTORY_FILE,
    HISTORY_SUFFIX,
    PROFILE_TMP_GLOB,
)
from termapy.migration import (
    CURRENT_CONFIG_VERSION,
    DEPRECATED_CFG,
    migrate_config,
)

CFG_DIR: str | None = None  # set by --cfg-dir; None = use resolution chain

_LOCAL_DIR_NAME = "termapy_cfg"

# Read timeout for every opened serial port.  Deliberately small so the
# reader loop's blocking ``read()`` returns promptly and can notice the
# stop signal.  This bound is load-bearing for teardown correctness: it
# must stay well below ``serial_engine.READER_STOP_WAIT_S`` (see the
# teardown note in serial_engine.disconnect).  ``test_serial_engine``
# pins the margin so a future change here cannot silently arm the race.
SERIAL_READ_TIMEOUT_S = 0.05


def _os_default_cfg_dir() -> Path:
    """OS-standard config directory (auto-created if missing).

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
    d = base / "termapy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cfg_dir() -> Path:
    """Resolve the config directory using precedence chain.

    1. --cfg-dir flag (CFG_DIR set explicitly) -- must exist
    2. TERMAPY_CFG_DIR env var -- must exist
    3. ./termapy_cfg if it exists in cwd -- never auto-created
    4. OS default (~/.config/termapy etc.) -- auto-created
    """
    # 1. Explicit --cfg-dir
    if CFG_DIR is not None:
        d = Path(CFG_DIR)
        if d.suffix:
            raise SystemExit(
                f"termapy: config directory looks like a file: {CFG_DIR} "
                f"(has extension '{d.suffix}'). Use --cfg-dir for directories."
            )
        if not d.exists():
            raise SystemExit(
                f"termapy: config directory does not exist: {d.resolve()}"
            )
        return d

    # 2. Environment variable
    env = os.environ.get("TERMAPY_CFG_DIR")
    if env:
        d = Path(env)
        if not d.exists():
            raise SystemExit(
                f"termapy: TERMAPY_CFG_DIR does not exist: {d.resolve()}"
            )
        return d

    # 3. Local termapy_cfg/ in cwd (check, never create)
    local = Path(_LOCAL_DIR_NAME)
    if local.is_dir():
        return local

    # 4. OS default (auto-created)
    return _os_default_cfg_dir()


def migrate_json_to_cfg(directory: Path) -> None:
    """One-time migration: rename ``<name>/<name>.json`` to ``<name>/<name>.cfg``.

    Termapy's config-file naming invariant is strict: a config lives at
    ``<folder>/<folder>.cfg`` and *only* that filename is a config.  This
    migration recognizes exactly ONE old filename pattern -- the same
    shape with ``.json`` -- and renames it to the new extension.

    Any other ``.json`` file inside a config folder is by definition NOT
    a config and is left strictly alone.  In particular:

      - ``<folder>/<folder>.profile.json``  (v2 device profile)
      - ``<folder>/<folder>.schema.json``   (future schema-side data)
      - any ad-hoc ``.json`` a user dropped in the folder

    must survive this migration unchanged.  The earlier implementation
    used a permissive ``*/*.json`` glob and chewed on any ``.json`` it
    found, which silently corrupted profiles every time the host
    started.  This version uses an exact-name lookup: no globs, no
    pattern matching, no inference.

    Safe to call repeatedly: if the ``.cfg`` already exists, no rename
    happens.

    Args:
        directory: The config root directory to scan (e.g. ``termapy_cfg/``).
    """
    for subdir in directory.iterdir():
        if not subdir.is_dir():
            continue
        # The ONE filename this migration recognizes.  Exact match, not
        # a glob.  Any other .json file in subdir is unknown to this
        # migration and must be left alone.
        candidate = subdir / f"{subdir.name}.json"
        target = subdir / f"{subdir.name}.cfg"
        if candidate.exists() and not target.exists():
            candidate.rename(target)


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
        return p.resolve().is_relative_to(pkg_root)
    except (ImportError, OSError, TypeError, ValueError):
        return False


def cfg_data_dir(config_path: str) -> Path:
    """Return the per-config data directory (for logs, screenshots, etc.).

    Config files live at termapy_cfg/<name>/<name>.cfg, so the data dir
    is just the parent directory of the config file.

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
    d.mkdir(parents=True, exist_ok=True)
    # One-time folder renames (migration)
    for old_name, new_name in FOLDER_MIGRATIONS:
        old = d / old_name
        new = d / new_name
        if old.is_dir() and not new.exists():
            old.rename(new)
    for sub in FOLDER_NAMES:
        (d / sub).mkdir(exist_ok=True)
    # Write .gitignore for transient data (only if it doesn't exist)
    gitignore = d / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Termapy - ignore transient/generated data\n"
            "*.log\n"
            ".cmd_history.txt\n"
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


def connection_string(
    cfg: dict, level: str = "medium", actual_port: str = ""
) -> str:
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
    enc = cfg.get("encoding", "utf-8")
    le = repr(cfg.get("eol", "\r"))
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
        for name, attr in [("DTR", "dtr"), ("RTS", "rts"),
                           ("CTS", "cts"), ("DSR", "dsr"),
                           ("RI", "ri"), ("CD", "cd")]:
            parts.append(f"{name}={int(getattr(port_obj, attr, 0))}")
        return " ".join(parts)
    except (OSError, Exception):
        return ""


def cfg_log_path(config_path: str) -> str:
    """Return the default log file path for a config."""
    name = Path(config_path).stem + ".log"
    return str((cfg_data_dir(config_path) / name).resolve())


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
    for f in run_dir.glob(PROFILE_TMP_GLOB):
        try:
            f.unlink()
        except OSError:
            pass


def cfg_plugins_dir(config_path: str) -> Path:
    """Return the plugin directory for a config, creating it if needed."""
    return cfg_data_dir(config_path) / "plugin"


def global_plugins_dir() -> Path:
    """Return the global plugin directory, creating it if needed."""
    d = cfg_dir() / "plugin"
    d.mkdir(exist_ok=True)
    return d


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


# Config keys whose value is a command string dispatched on connect
# (TUI/CLI/MCP all run these through the normal REPL pipeline).  They are
# deliberately NOT env-expanded at load time: doing so bypasses the
# per-command env gate and puts $(env.X) values on the wire for bare
# device commands -- the $(env.PATH) incident class.  Dispatched normally,
# the env_var transform expands $(env.X) for repl commands but not for
# device text (env.py's TRANSFORM has no ``serial`` variant), so these keys
# follow the same env-to-wire rule as typed input.
_ENV_EXPAND_EXCLUDED_KEYS = frozenset(
    {
        "on_connect_cmd",
        "tui_on_connect_cmd",
        "cli_on_connect_cmd",
        "mcp_on_connect_cmd",
    }
)


def expand_env_cfg(cfg: dict) -> dict:
    """Expand $(env.NAME) in string values of a config dict, recursively.

    Mutates and returns *cfg*.  Recurses into nested dict values so
    env vars in ``cfg["serial"]["port"]`` (and any future grouped
    section) get expanded too -- the v22 migration nested pyserial
    keys under ``cfg["serial"]``, so a top-level-only expander
    silently stopped working for serial keys.  Non-string, non-dict
    values are left untouched, as are the connect-command keys in
    ``_ENV_EXPAND_EXCLUDED_KEYS`` (see that constant).

    Args:
        cfg: Config dict to expand in place.

    Returns:
        The same dict with string values expanded.
    """
    for key, val in cfg.items():
        if key in _ENV_EXPAND_EXCLUDED_KEYS:
            continue
        if isinstance(val, str) and "$(" in val:
            cfg[key] = expand_env_str(val)
        elif isinstance(val, dict):
            expand_env_cfg(val)
    return cfg


def validate_config(cfg: dict) -> list[str]:
    """Validate config values and return a list of warning strings.

    Checks serial port settings, encoding, and numeric constraints.
    Keys not in ``DEFAULT_CFG`` are flagged: if they appear in
    ``DEPRECATED_CFG`` they're called out as deprecated with the
    rename-or-removal history; otherwise flagged as a potential typo.
    Non-standard baud rates produce a warning but are not rejected.

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
            warnings.append(
                f"config_version: {ver} (current is {CURRENT_CONFIG_VERSION})"
            )

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

    # port must be a non-empty string.  A valid port is a literal
    # device name ("COM3", "/dev/ttyUSB0"), a USB serial number, a
    # reserved name ("DEMO", "DEMO_FAIL"), a pipe-separated fallback
    # chain of those, or a pyserial URL -- anything except empty.
    # The zero-config CLI synthesizes an in-memory cfg with port="" to
    # bootstrap a REPL with no config file, but that path never hits
    # validate_config (no load_config, no --check).  Any config
    # actually persisted to disk gets a warning here.
    serial = cfg.get("serial", {})
    p = serial.get("port")
    if p is not None:
        if not isinstance(p, str):
            warnings.append(f"serial.port: expected str, got {type(p).__name__}")
        elif p == "":
            warnings.append(
                "serial.port: must not be empty "
                "(use a device name, USB serial number, or 'DEMO')"
            )

    # Type + value checks for serial settings
    _check_set(serial, "byte_size", int, VALID_BYTE_SIZES, warnings, key_prefix="serial.")
    _check_set(serial, "parity", str, VALID_PARITIES, warnings, key_prefix="serial.")
    _check_set(serial, "stop_bits", (int, float), VALID_STOP_BITS, warnings, key_prefix="serial.")
    _check_set(serial, "flow_control", str, VALID_FLOW_CONTROLS, warnings, key_prefix="serial.")

    # custom_baud - must be bool
    cb = serial.get("custom_baud")
    if cb is not None and not isinstance(cb, bool):
        warnings.append(f"serial.custom_baud: expected bool, got {type(cb).__name__}")

    # Baud rate - standard rates only unless custom_baud is enabled
    val = serial.get("baud_rate")
    if val is not None:
        if not isinstance(val, int):
            warnings.append(f"serial.baud_rate: expected int, got {type(val).__name__}")
        elif val <= 0:
            warnings.append(f"serial.baud_rate: must be positive, got {val}")
        elif serial.get("custom_baud"):
            if val < 300:
                warnings.append(f"serial.baud_rate: custom baud requires >= 300, got {val}")
        elif val not in STANDARD_BAUD_RATES:
            warnings.append(
                f"serial.baud_rate: {val} is not a standard rate -- set custom_baud to true to allow non-standard rates"
            )

    # Encoding - must be a valid Python codec
    enc = cfg.get("encoding")
    if enc is not None:
        if not isinstance(enc, str):
            warnings.append(f"encoding: expected str, got {type(enc).__name__}")
        else:
            try:
                codecs.lookup(enc)
            except LookupError:
                warnings.append(f"encoding: unknown codec '{enc}'")

    # Numeric constraints
    _check_positive(cfg, "max_lines", warnings)
    _check_non_negative(cfg, "cmd_delay_ms", warnings)

    return warnings


def _check_set(
    cfg: dict,
    key: str,
    expected_type: type | tuple[type, ...],
    valid: set,
    warnings: list[str],
    key_prefix: str = "",
) -> None:
    """Check that cfg[key] has the right type and is in the valid set.

    ``key_prefix`` is prepended to ``key`` in any warning message,
    so callers checking nested sub-dicts (e.g. cfg["serial"]) can
    pass ``key_prefix="serial."`` to get user-visible warnings like
    ``serial.byte_size: ...`` instead of bare ``byte_size: ...``.
    """
    val = cfg.get(key)
    if val is None:
        return
    label = f"{key_prefix}{key}"
    if not isinstance(val, expected_type):
        warnings.append(
            f"{label}: expected {_type_name(expected_type)}, got {type(val).__name__}"
        )
        return
    if val not in valid:
        warnings.append(f"{label}: invalid value {val!r}, expected one of {sorted(valid)}")


def _check_positive(cfg: dict, key: str, warnings: list[str]) -> None:
    """Check that cfg[key] is a positive integer."""
    val = cfg.get(key)
    if val is None:
        return
    if not isinstance(val, int):
        warnings.append(f"{key}: expected int, got {type(val).__name__}")
    elif val <= 0:
        warnings.append(f"{key}: must be positive, got {val}")


def _check_non_negative(cfg: dict, key: str, warnings: list[str]) -> None:
    """Check that cfg[key] is a non-negative integer."""
    val = cfg.get(key)
    if val is None:
        return
    if not isinstance(val, int):
        warnings.append(f"{key}: expected int, got {type(val).__name__}")
    elif val < 0:
        warnings.append(f"{key}: must be non-negative, got {val}")


def _type_name(t: type | tuple[type, ...]) -> str:
    """Return a readable name for a type or tuple of types."""
    if isinstance(t, tuple):
        return "/".join(x.__name__ for x in t)
    return t.__name__


# Exception types ``load_config`` can realistically raise.  Callers
# use this as the ``except`` target so a single narrowing rule covers
# every file-load site: OSError (missing file / permission denied /
# disappeared mid-read) and ValueError (JSONDecodeError is a subclass;
# also raised for invalid values caught during migration).  Anything
# else from ``load_config`` is a bug worth propagating.
CONFIG_LOAD_ERRORS: tuple[type[BaseException], ...] = (OSError, ValueError)


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

    with open(path) as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}"
            ) from e

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
    if changed:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=4)
    if migrated:
        cfg["_migrated_from"] = old_version
    cfg = expand_env_cfg(cfg)
    # Migration functions may stash one-shot warnings in
    # ``_migration_warnings`` (e.g. "your retired key was true; set
    # the env var").  Merge them with structural validation warnings
    # so the user sees both through the single existing display path.
    mig_warnings = cfg.pop("_migration_warnings", [])
    config_warnings = validate_config(cfg)
    all_warnings = list(mig_warnings) + config_warnings
    if all_warnings:
        cfg["_config_warnings"] = all_warnings
    cleanup_profile_temps(path)
    return cfg


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
    import subprocess

    if sys.platform == "win32":
        import os

        os.startfile(path)
        return

    cmd = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]

    def _launch_and_reap() -> None:
        try:
            subprocess.run(cmd, check=False, timeout=60.0)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass  # launcher hung or missing -- best effort

    threading.Thread(target=_launch_and_reap, daemon=True).start()


def open_serial(cfg: dict) -> Any:
    """Open serial port from config dict.

    ``cfg["serial"]["port"]`` may be a plain device name (``"COM3"``,
    ``"/dev/ttyUSB0"``), a USB serial number, a ``|``-separated fallback
    chain (``"A1B2C3D4|COM3"``), a reserved name (``"DEMO"``,
    ``"DEMO_FAIL"``), or a pyserial URL.  Resolution to a concrete
    device happens here via ``resolve_port()``; see that function's
    docstring and ``help/ports.md`` for the full grammar.

    If the resolved name is ``"DEMO"``, returns a ``FakeSerial``
    simulated device instead of a real serial connection.  If the
    resolved name is ``"DEMO_FAIL"``, raises ``OSError`` -- a test
    hook for exercising the open-failure path without needing a broken
    real port.

    Args:
        cfg: Config dict with serial settings.

    Returns:
        A serial port object (real or simulated).

    Raises:
        AmbiguousSerialNumberError: when ``cfg["serial"]["port"]`` is a
            spec whose serial-number candidate matches two or more
            connected devices.  Surfaces through to the connect failure
            path so the user sees which devices collided.
    """
    from termapy.port_control import resolve_port

    serial_cfg = cfg["serial"]
    resolved = resolve_port(serial_cfg["port"])
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
    return serial.serial_for_url(
        resolved,
        baudrate=serial_cfg["baud_rate"],
        bytesize=serial_cfg["byte_size"],
        parity=serial_cfg["parity"],
        stopbits=serial_cfg["stop_bits"],
        rtscts=(fc == "rtscts"),
        xonxoff=(fc == "xonxoff"),
        timeout=SERIAL_READ_TIMEOUT_S,
    )


def setup_demo_config(target_path: Path, *, force: bool = False) -> Path:
    """Copy bundled demo config files to the target directory.

    Creates ``<target_path>/demo/`` with config, scripts, and proto files.
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

    # Copy run scripts
    run_dir = demo_dir / "run"
    run_dir.mkdir(exist_ok=True)
    run_pkg = pkg / "run"
    for name in ("welcome.run", "at_demo.run", "gps_demo.run", "smoke_test.run", "status_check.run", "var_demo.run", "expect_test.run", "doc_screenshots.run", "crc_tour.run"):
        dest = run_dir / name
        if force or not dest.exists():
            src = run_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy proto files
    proto_dir = demo_dir / "proto"
    proto_dir.mkdir(exist_ok=True)
    proto_pkg = pkg / "proto"
    for name in ("at_test.pro", "bitfield_inline.pro", "modbus_inline.pro"):
        dest = proto_dir / name
        if force or not dest.exists():
            src = proto_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy demo plugins
    plugin_dir = demo_dir / "plugin"
    plugin_dir.mkdir(exist_ok=True)
    plugin_pkg = pkg / "plugin"
    for name in ("cmd.py", "probe.py", "temp_plot.py"):
        dest = plugin_dir / name
        if force or not dest.exists():
            src = plugin_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy .gitignore for transient data
    gitignore_dest = demo_dir / ".gitignore"
    if force or not gitignore_dest.exists():
        src = pkg / ".gitignore"
        gitignore_dest.write_bytes(src.read_bytes())

    # Create standard subdirs
    for sub in ("ss", "cap", "prof"):
        (demo_dir / sub).mkdir(exist_ok=True)

    return config_path
