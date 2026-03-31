"""Config file management - paths, defaults, loading, and serial port setup.

Pure functions with no UI dependency. Used by app.py and tests.
"""

import codecs
import json
import os
import re
import sys
from pathlib import Path

import serial

from termapy.folders import FOLDER_MIGRATIONS, FOLDER_NAMES, HISTORY_FILE, PROFILE_TMP_GLOB
from termapy.defaults import (
    DEFAULT_CFG,
    STANDARD_BAUD_RATES,
    VALID_BYTE_SIZES,
    VALID_FLOW_CONTROLS,
    VALID_PARITIES,
    VALID_STOP_BITS,
)
from termapy.migration import CURRENT_CONFIG_VERSION, migrate_config

CFG_DIR = "termapy_cfg"


def cfg_dir() -> Path:
    """Return the config directory, creating it if needed.

    Raises:
        ValueError: If the path has a file extension (likely a file, not a dir).
    """
    d = Path(CFG_DIR)
    if d.suffix:
        raise ValueError(
            f"Config directory looks like a file: {CFG_DIR} "
            f"(has extension '{d.suffix}'). Use --cfg-dir for directories."
        )
    d.mkdir(exist_ok=True)
    return d


def migrate_json_to_cfg(directory: Path) -> None:
    """Rename any *.json config files to *.cfg (one-time migration).

    Safe to call repeatedly - skips if the .cfg file already exists.

    Args:
        directory: The config root directory to scan (e.g. termapy_cfg/).
    """
    for json_file in directory.glob("*/*.json"):
        cfg_file = json_file.with_suffix(".cfg")
        if not cfg_file.exists():
            json_file.rename(cfg_file)


def cfg_data_dir(config_path: str) -> Path:
    """Return the per-config data directory (for logs, screenshots, etc.).

    Config files live at termapy_cfg/<name>/<name>.cfg, so the data dir
    is just the parent directory of the config file.
    """
    d = Path(config_path).parent
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


def connection_string(cfg: dict, level: str = "medium") -> str:
    """Format connection info from config at different detail levels.

    Args:
        cfg: Config dict with serial parameters.
        level: "short" (port baud 8N1), "medium" (+ flow control if non-default),
            or "full" (+ encoding and line ending).

    Returns:
        Formatted connection string.
    """
    port = cfg.get("port", "?")
    baud = cfg.get("baud_rate", "?")
    bits = cfg.get("byte_size", 8)
    parity = cfg.get("parity", "N")
    sb = cfg.get("stop_bits", 1)
    sb_str = str(int(sb)) if sb == int(sb) else str(sb)
    fc = cfg.get("flow_control", "none")

    base = f"{port} {baud} {bits}{parity}{sb_str}"
    if level == "short":
        return base
    if fc != "none":
        base += f" {fc}"
    if level == "medium":
        return base
    enc = cfg.get("encoding", "utf-8")
    le = repr(cfg.get("line_ending", "\r"))
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


def cfg_history_path(config_path: str) -> str:
    """Return the command history file path for a config."""
    return str(cfg_data_dir(config_path) / HISTORY_FILE)


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


def expand_env_cfg(cfg: dict) -> dict:
    """Expand $(env.NAME) in all top-level string values of a config dict.

    Mutates and returns *cfg*. Non-string values and nested structures
    are left untouched.

    Args:
        cfg: Config dict to expand in place.

    Returns:
        The same dict with string values expanded.
    """
    for key, val in cfg.items():
        if isinstance(val, str) and "$(" in val:
            cfg[key] = expand_env_str(val)
    return cfg


def validate_config(cfg: dict) -> list[str]:
    """Validate config values and return a list of warning strings.

    Checks serial port settings, encoding, and numeric constraints.
    Unknown keys (not in DEFAULT_CFG) are flagged as potential typos.
    Non-standard baud rates produce a warning but are not rejected.

    Args:
        cfg: Config dict to validate.

    Returns:
        List of warning strings (empty means valid).
    """
    warnings: list[str] = []

    # Config version
    ver = cfg.get("config_version")
    if ver is not None and ver != CURRENT_CONFIG_VERSION:
        warnings.append(
            f"config_version: {ver} (current is {CURRENT_CONFIG_VERSION})"
        )

    # Unknown keys (skip internal keys starting with _)
    for key in cfg:
        if not key.startswith("_") and key not in DEFAULT_CFG:
            warnings.append(f"unknown key: '{key}' (typo?)")

    # Type + value checks for serial settings
    _check_set(cfg, "byte_size", int, VALID_BYTE_SIZES, warnings)
    _check_set(cfg, "parity", str, VALID_PARITIES, warnings)
    _check_set(cfg, "stop_bits", (int, float), VALID_STOP_BITS, warnings)
    _check_set(cfg, "flow_control", str, VALID_FLOW_CONTROLS, warnings)

    # Baud rate - warn on non-standard but don't reject
    val = cfg.get("baud_rate")
    if val is not None:
        if not isinstance(val, int):
            warnings.append(f"baud_rate: expected int, got {type(val).__name__}")
        elif val <= 0:
            warnings.append(f"baud_rate: must be positive, got {val}")
        elif val not in STANDARD_BAUD_RATES:
            rates = ", ".join(str(r) for r in STANDARD_BAUD_RATES)
            warnings.append(f"baud_rate: {val} is not a standard rate ({rates})")

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
) -> None:
    """Check that cfg[key] has the right type and is in the valid set."""
    val = cfg.get(key)
    if val is None:
        return
    if not isinstance(val, expected_type):
        warnings.append(f"{key}: expected {_type_name(expected_type)}, got {type(val).__name__}")
        return
    if val not in valid:
        warnings.append(f"{key}: invalid value {val!r}, expected one of {sorted(valid)}")


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
            cfg[key] = val
            changed = True
    cfg.pop("_migrated_from", None)  # clean up stale marker from older saves
    if changed:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=4)
    if migrated:
        cfg["_migrated_from"] = old_version
    cfg = expand_env_cfg(cfg)
    config_warnings = validate_config(cfg)
    if config_warnings:
        cfg["_config_warnings"] = config_warnings
    cleanup_profile_temps(path)
    return cfg


def open_with_system(path: str) -> None:
    """Open a file or folder with the system default application."""
    import subprocess

    if sys.platform == "win32":
        import os

        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def open_serial(cfg: dict) -> serial.Serial:
    """Open serial port from config dict.

    If port is ``"DEMO"``, returns a ``FakeSerial`` simulated device
    instead of a real serial connection.

    Args:
        cfg: Config dict with serial settings.

    Returns:
        A serial port object (real or simulated).
    """
    if cfg["port"].upper() == "DEMO":
        from termapy.demo import FakeSerial

        return FakeSerial(baudrate=cfg["baud_rate"])

    fc = cfg.get("flow_control", "none")
    return serial.Serial(
        port=cfg["port"],
        baudrate=cfg["baud_rate"],
        bytesize=cfg["byte_size"],
        parity=cfg["parity"],
        stopbits=cfg["stop_bits"],
        rtscts=(fc == "rtscts"),
        xonxoff=(fc == "xonxoff"),
        timeout=0.05,
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
    for name in ("welcome.run", "at_demo.run", "gps_demo.run", "smoke_test.run", "status_check.run", "var_demo.run", "expect_test.run"):
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
