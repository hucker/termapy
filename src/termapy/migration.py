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

CURRENT_CONFIG_VERSION = 12

# Keys that used to be valid config fields but have been removed or
# renamed by a migration.  Maps deprecated key -> a short message
# explaining what happened.  ``validate_config`` uses this to give a
# helpful warning ("deprecated: X was renamed to Y in v6") instead
# of the generic "unknown key (typo?)" when a user's config carries
# a stale field.
#
# In normal flow a deprecated key is cleaned up by the migration
# chain on first load.  This dict catches the edge cases: hand-edited
# configs that re-add an old key, configs with a version-mismatch
# that somehow bypassed migrations, or users inspecting a very old
# file with ``--check``.
#
# Keep sorted by the migration that retired the key so the schema
# history reads chronologically.
DEPRECATED_CFG: dict[str, str] = {
    # v1 -> v2: rename
    "add_date_to_cmd": "renamed to show_timestamps in v2",
    # v3 -> v4: renames
    "baudrate": "renamed to baud_rate in v4",
    "bytesize": "renamed to byte_size in v4",
    "stopbits": "renamed to stop_bits in v4",
    "autoconnect": "renamed to auto_connect in v4",
    "autoreconnect": "renamed to auto_reconnect in v4",
    "autoconnect_cmd": "renamed to auto_connect_cmd in v4",
    "pick": "renamed to pick_port in v4",
    # v3 -> v4: removal
    "command_history_items": "removed in v4 (no replacement)",
    # v4 -> v5: removal
    "pick_port": "removed in v5 (use $(env.NAME) config expansion)",
    # v5 -> v6: renames
    "echo_cmd": "renamed to echo_input in v6",
    "echo_cmd_fmt": "renamed to echo_input_fmt in v6",
    "auto_connect_cmd": "renamed to on_connect_cmd in v6",
    "inter_cmd_delay_ms": "renamed to cmd_delay_ms in v6",
    "show_eol": "renamed to show_line_endings in v6",
    "exception_traceback": "renamed to show_traceback in v6",
    "app_border_color": "renamed to border_color in v6",
    "repl_prefix": "renamed to cmd_prefix in v6",
    "read_only": "renamed to config_read_only in v6",
    # v7 -> v8: removal
    "cap_endian": "removed in v8 (endianness now lives in the format spec)",
}

# Migration functions: {from_version: callable(cfg) -> cfg}
MIGRATIONS: dict[int, Callable] = {}


def _migrate_v1_to_v2(cfg: dict) -> dict:
    """Rename add_date_to_cmd -> show_timestamps."""
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
    """Remove command_history_items, add read_only, rename keys, prefix ! -> /."""
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


def _migrate_v6_to_v7(cfg: dict) -> dict:
    """Add send_bare_enter option (default off)."""
    cfg.setdefault("send_bare_enter", False)
    return cfg


MIGRATIONS[6] = _migrate_v6_to_v7


def _migrate_v7_to_v8(cfg: dict) -> dict:
    """Remove cap_endian (endianness now in format spec byte order)."""
    cfg.pop("cap_endian", None)
    return cfg


MIGRATIONS[7] = _migrate_v7_to_v8


def _migrate_v8_to_v9(cfg: dict) -> dict:
    """Add cli_prompt and cli_echo_input options."""
    cfg.setdefault("cli_prompt", "$(CFG)> ")
    cfg.setdefault("cli_echo_input", False)
    return cfg


MIGRATIONS[8] = _migrate_v8_to_v9


def _migrate_v9_to_v10(cfg: dict) -> dict:
    """Add default_ui option (default 'tui')."""
    cfg.setdefault("default_ui", "tui")
    return cfg


MIGRATIONS[9] = _migrate_v9_to_v10


def _migrate_v10_to_v11(cfg: dict) -> dict:
    """Add file_xfer_root option (default empty string)."""
    cfg.setdefault("file_xfer_root", "")
    return cfg


MIGRATIONS[10] = _migrate_v10_to_v11


def _migrate_v11_to_v12(cfg: dict) -> dict:
    """Add custom_baud option (default False)."""
    cfg.setdefault("custom_baud", False)
    return cfg


MIGRATIONS[11] = _migrate_v11_to_v12


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
