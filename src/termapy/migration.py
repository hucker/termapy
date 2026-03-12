"""Config schema versioning and migration chain.

Each config has a "config_version" integer. Migration functions transform
configs from one version to the next. On load, migrate_config() runs all
needed migrations sequentially to bring the config up to date.

To add a migration:
    1. Bump CURRENT_CONFIG_VERSION
    2. Write a function: def _migrate_vN_to_vN1(cfg): ... return cfg
    3. Add it to MIGRATIONS: {N: _migrate_vN_to_vN1}
"""

from typing import Callable

CURRENT_CONFIG_VERSION = 6

# Migration functions: {from_version: callable(cfg) -> cfg}
MIGRATIONS: dict[int, Callable] = {}


def _migrate_v1_to_v2(cfg: dict) -> dict:
    """Rename add_date_to_cmd → show_timestamps."""
    if "add_date_to_cmd" in cfg:
        cfg["show_timestamps"] = cfg.pop("add_date_to_cmd")
    return cfg


def _migrate_v2_to_v3(cfg: dict) -> dict:
    """Add command_history_items with default of 30."""
    if "command_history_items" not in cfg:
        cfg["command_history_items"] = 30
    return cfg


_KEY_RENAMES_V4 = {
    "baudrate": "baud_rate",
    "bytesize": "byte_size",
    "stopbits": "stop_bits",
    "autoconnect": "auto_connect",
    "autoreconnect": "auto_reconnect",
    "autoconnect_cmd": "auto_connect_cmd",
}


def _migrate_v3_to_v4(cfg: dict) -> dict:
    """Remove command_history_items, add read_only, rename keys, prefix ! → /."""
    cfg.pop("command_history_items", None)
    cfg.setdefault("read_only", False)
    if cfg.get("repl_prefix") == "!":
        cfg["repl_prefix"] = "/"
    for old, new in _KEY_RENAMES_V4.items():
        if old in cfg:
            cfg[new] = cfg.pop(old)
    if "pick" in cfg:
        cfg["pick_port"] = cfg.pop("pick")
    return cfg


def _migrate_v4_to_v5(cfg: dict) -> dict:
    """Remove pick_port (superseded by $(env.NAME) config expansion)."""
    cfg.pop("pick_port", None)
    return cfg


_KEY_RENAMES_V6 = {
    "echo_cmd": "echo_input",
    "echo_cmd_fmt": "echo_input_fmt",
    "auto_connect_cmd": "on_connect_cmd",
    "inter_cmd_delay_ms": "cmd_delay_ms",
    "show_eol": "show_line_endings",
    "exception_traceback": "show_traceback",
    "app_border_color": "border_color",
    "repl_prefix": "cmd_prefix",
    "read_only": "config_read_only",
}


def _migrate_v5_to_v6(cfg: dict) -> dict:
    """Rename config fields for clarity and consistency."""
    for old, new in _KEY_RENAMES_V6.items():
        if old in cfg:
            cfg[new] = cfg.pop(old)
    return cfg


MIGRATIONS[1] = _migrate_v1_to_v2
MIGRATIONS[2] = _migrate_v2_to_v3
MIGRATIONS[3] = _migrate_v3_to_v4
MIGRATIONS[4] = _migrate_v4_to_v5
MIGRATIONS[5] = _migrate_v5_to_v6


def migrate_config(cfg: dict) -> dict:
    """Run config through the migration chain to bring it up to date.

    Applies migration functions sequentially from the config's current
    version to CURRENT_CONFIG_VERSION. Versions without a migration
    function are skipped (version number still advances).

    Args:
        cfg: Config dict to migrate (modified in place).

    Returns:
        The migrated config dict with config_version set to current.
    """
    v = cfg.get("config_version", 0)
    while v < CURRENT_CONFIG_VERSION:
        if v in MIGRATIONS:
            cfg = MIGRATIONS[v](cfg)
        v += 1
    cfg["config_version"] = CURRENT_CONFIG_VERSION
    return cfg
