"""Config file management — paths, defaults, loading, and serial port setup.

Pure functions with no UI dependency. Used by app.py and tests.
"""

import json
import os
import re
import sys
from pathlib import Path

import serial

from termapy.defaults import DEFAULT_CFG
from termapy.migration import CURRENT_CONFIG_VERSION, migrate_config

CFG_DIR = "termapy_cfg"


def cfg_dir() -> Path:
    """Return the config directory, creating it if needed."""
    d = Path(CFG_DIR)
    d.mkdir(exist_ok=True)
    return d


def migrate_json_to_cfg(directory: Path) -> None:
    """Rename any *.json config files to *.cfg (one-time migration).

    Safe to call repeatedly — skips if the .cfg file already exists.

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
    for sub in ("plugins", "ss", "scripts", "proto", "viz"):
        (d / sub).mkdir(exist_ok=True)
    return d


def cfg_path_for_name(name: str) -> Path:
    """Return the config file path for a given name: termapy_cfg/<name>/<name>.cfg."""
    return cfg_dir() / name / f"{name}.cfg"


def cfg_log_path(config_path: str) -> str:
    """Return the default log file path for a config."""
    name = Path(config_path).stem + ".log"
    return str((cfg_data_dir(config_path) / name).resolve())


def cfg_history_path(config_path: str) -> str:
    """Return the command history file path for a config."""
    return str(cfg_data_dir(config_path) / ".cmd_history.txt")


def cfg_plugins_dir(config_path: str) -> Path:
    """Return the plugins directory for a config, creating it if needed."""
    return cfg_data_dir(config_path) / "plugins"


def global_plugins_dir() -> Path:
    """Return the global plugins directory, creating it if needed."""
    d = cfg_dir() / "plugins"
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


def load_config(path: str) -> dict:
    """Load and validate JSON config, applying defaults for missing fields.

    If the file doesn't exist, creates it with DEFAULT_CFG. On load, runs
    the migration chain and backfills any missing keys from defaults. Writes
    the file back if anything changed.

    Args:
        path: Path to the JSON config file.

    Returns:
        Config dict with migrations applied and all defaults present.
    """
    p = Path(path)
    if not p.exists():
        print(f"Config file not found: {path}", file=sys.stderr)
        # Ensure it goes into termapy_cfg/<name>/<name>.cfg
        if not p.parent or p.parent == Path("."):
            name = p.stem
            p = cfg_path_for_name(name)
            path = str(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        print(f"Creating default config at {p}")
        with open(p, "w") as f:
            json.dump(DEFAULT_CFG, f, indent=4)
        return dict(DEFAULT_CFG)

    with open(path) as f:
        cfg = json.load(f)

    # Run migrations before applying defaults
    old_version = cfg.get("config_version", 0)
    cfg = migrate_config(cfg)
    changed = old_version != CURRENT_CONFIG_VERSION
    if changed:
        cfg["_migrated_from"] = old_version

    for key, val in DEFAULT_CFG.items():
        if key not in cfg:
            cfg[key] = val
            changed = True
    if changed:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=4)
    return expand_env_cfg(cfg)


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

    # Copy scripts
    scripts_dir = demo_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    scripts_pkg = pkg / "scripts"
    for name in ("at_demo.run", "smoke_test.run", "status_check.run"):
        dest = scripts_dir / name
        if force or not dest.exists():
            src = scripts_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy proto files
    proto_dir = demo_dir / "proto"
    proto_dir.mkdir(exist_ok=True)
    proto_pkg = pkg / "proto"
    for name in ("at_test.pro", "bitfield_inline.pro", "modbus_inline.pro", "modbus_test.pro"):
        dest = proto_dir / name
        if force or not dest.exists():
            src = proto_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy demo plugins
    plugins_dir = demo_dir / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    plugins_pkg = pkg / "plugins"
    for name in ("cmd.py", "probe.py"):
        dest = plugins_dir / name
        if force or not dest.exists():
            src = plugins_pkg / name
            dest.write_bytes(src.read_bytes())

    # Copy demo visualizers
    viz_dir = demo_dir / "viz"
    viz_dir.mkdir(exist_ok=True)
    viz_pkg = pkg / "viz"
    for name in ("at_view.py", "modbus_view.py"):
        dest = viz_dir / name
        if force or not dest.exists():
            src = viz_pkg / name
            dest.write_bytes(src.read_bytes())

    # Create standard subdirs
    for sub in ("ss",):
        (demo_dir / sub).mkdir(exist_ok=True)

    return config_path
