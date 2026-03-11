"""Config file management — paths, defaults, loading, and serial port setup.

Pure functions with no UI dependency. Used by app.py and tests.
"""

import json
import sys
from pathlib import Path

import serial

from termapy.migration import CURRENT_CONFIG_VERSION, migrate_config

CFG_DIR = "termapy_cfg"


def cfg_dir() -> Path:
    """Return the config directory, creating it if needed."""
    d = Path(CFG_DIR)
    d.mkdir(exist_ok=True)
    return d


def cfg_data_dir(config_path: str) -> Path:
    """Return the per-config data directory (for logs, screenshots, etc.).

    Config files live at termapy_cfg/<name>/<name>.json, so the data dir
    is just the parent directory of the config file.
    """
    d = Path(config_path).parent
    d.mkdir(parents=True, exist_ok=True)
    for sub in ("plugins", "ss", "scripts", "proto", "viz"):
        (d / sub).mkdir(exist_ok=True)
    return d


def cfg_path_for_name(name: str) -> Path:
    """Return the config file path for a given name: termapy_cfg/<name>/<name>.json."""
    return cfg_dir() / name / f"{name}.json"


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


DEFAULT_CFG = {
    "config_version": CURRENT_CONFIG_VERSION,
    # App
    "title": "",
    "app_border_color": "",
    "max_lines": 10000,
    "repl_prefix": "!",
    "os_cmd_enabled": False,
    # Serial
    "port": "COM4",
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "flow_control": "none",
    "encoding": "utf-8",
    "inter_cmd_delay_ms": 0,
    # Connection
    "autoconnect": False,
    "autoreconnect": False,
    "autoconnect_cmd": "",
    "line_ending": "\r",
    # Input echo
    "echo_cmd": False,
    "echo_cmd_fmt": "[purple]> {cmd}[/]",
    # Logging
    "log_file": "",
    # Diagnostics
    "exception_traceback": False,
    # Display
    "show_timestamps": False,
    "show_eol": False,
    "max_grep_lines": 100,
    "command_history_items": 30,
    # Custom buttons
    "custom_buttons": [
        {"enabled": True, "name": "Info", "command": "!info", "tooltip": "Project info"},
        {"enabled": False, "name": "Btn2", "command": "", "tooltip": "Custom button 2"},
        {"enabled": False, "name": "Btn3", "command": "", "tooltip": "Custom button 3"},
        {"enabled": False, "name": "Btn4", "command": "", "tooltip": "Custom button 4"},
    ],
}


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
        # Ensure it goes into termapy_cfg/<name>/<name>.json
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

    for key, val in DEFAULT_CFG.items():
        if key not in cfg:
            cfg[key] = val
            changed = True
    if changed:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=4)
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

        return FakeSerial(baudrate=cfg["baudrate"])

    fc = cfg.get("flow_control", "none")
    return serial.Serial(
        port=cfg["port"],
        baudrate=cfg["baudrate"],
        bytesize=cfg["bytesize"],
        parity=cfg["parity"],
        stopbits=cfg["stopbits"],
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

    config_path = demo_dir / "demo.json"

    # Source package
    pkg = importlib.resources.files("termapy.builtins.demo")

    # Copy config file
    if force or not config_path.exists():
        src = pkg / "demo.json"
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
    for name in ("at_test.pro", "modbus_test.pro"):
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
