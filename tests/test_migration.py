"""Tests for config schema versioning and migration chain."""

from termapy.migration import (
    CURRENT_CONFIG_VERSION,
    MIGRATIONS,
    migrate_config,
)


def test_legacy_config_gets_version():
    """Config with no config_version gets stamped to current."""
    cfg = {"port": "COM4", "baud_rate": 115200}
    result = migrate_config(cfg)
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_current_version_unchanged():
    """Config already at current version passes through unchanged."""
    cfg = {"config_version": CURRENT_CONFIG_VERSION, "port": "COM4"}
    result = migrate_config(cfg)
    assert result == cfg


def test_migration_chain_runs_in_order():
    """Migrations run sequentially from old version to current."""
    call_log = []

    def fake_v1_to_v2(cfg):
        call_log.append(1)
        cfg["added_by_v1"] = True
        return cfg

    def fake_v2_to_v3(cfg):
        call_log.append(2)
        cfg["added_by_v2"] = True
        return cfg

    # Temporarily patch MIGRATIONS and CURRENT_CONFIG_VERSION
    import termapy.migration as app_mod

    orig_version = app_mod.CURRENT_CONFIG_VERSION
    orig_migrations = app_mod.MIGRATIONS.copy()
    try:
        app_mod.CURRENT_CONFIG_VERSION = 3
        app_mod.MIGRATIONS = {1: fake_v1_to_v2, 2: fake_v2_to_v3}

        cfg = {"config_version": 1, "port": "COM4"}
        result = app_mod.migrate_config(cfg)

        assert call_log == [1, 2]
        assert result["added_by_v1"] is True
        assert result["added_by_v2"] is True
        assert result["config_version"] == 3
    finally:
        app_mod.CURRENT_CONFIG_VERSION = orig_version
        app_mod.MIGRATIONS = orig_migrations


def test_migration_skips_when_no_function():
    """Version gaps without migration functions still advance the version."""
    cfg = {"config_version": 0}
    result = migrate_config(cfg)
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v1_to_v2_renames_add_date_to_cmd():
    """Migration v1→v2 renames add_date_to_cmd to show_timestamps."""
    cfg = {"config_version": 1, "add_date_to_cmd": True, "port": "COM4"}
    result = migrate_config(cfg)

    assert result["show_timestamps"] is True  # assert renamed key has old value
    assert "add_date_to_cmd" not in result  # assert old key removed
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v1_to_v2_without_old_key():
    """Migration v1→v2 handles configs that never had add_date_to_cmd."""
    cfg = {"config_version": 1, "port": "COM4"}
    result = migrate_config(cfg)

    assert "add_date_to_cmd" not in result  # assert old key not introduced
    assert "show_timestamps" not in result  # assert new key not added by migration
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v2_to_v3_adds_then_v4_removes_command_history_items():
    """Migration v2→v3 adds command_history_items, v3→v4 removes it."""
    cfg = {"config_version": 2, "port": "COM4"}
    result = migrate_config(cfg)

    assert "command_history_items" not in result  # assert removed by v4
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v3_to_v4_removes_command_history_items():
    """Migration v3→v4 removes command_history_items from config."""
    cfg = {"config_version": 3, "port": "COM4", "command_history_items": 50}
    result = migrate_config(cfg)

    assert "command_history_items" not in result  # assert key removed
    assert result["config_read_only"] is False  # assert config_read_only added (via v4+v6)
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v3_to_v4_handles_missing_key():
    """Migration v3→v4 handles configs without command_history_items."""
    cfg = {"config_version": 3, "port": "COM4"}
    result = migrate_config(cfg)

    assert "command_history_items" not in result  # assert no error
    assert result["config_read_only"] is False  # assert config_read_only added (via v4+v6)
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v3_to_v4_preserves_existing_read_only():
    """Migration v3→v4 does not overwrite existing read_only value."""
    cfg = {"config_version": 3, "port": "COM4", "read_only": True}
    result = migrate_config(cfg)

    assert result["config_read_only"] is True  # assert existing value preserved (via v6 rename)
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v3_to_v4_changes_repl_prefix_bang_to_slash():
    """Migration v3→v4 changes repl_prefix from ! to /."""
    cfg = {"config_version": 3, "port": "COM4", "repl_prefix": "!"}
    result = migrate_config(cfg)

    assert result["cmd_prefix"] == "/"  # assert prefix migrated (via v6 rename)
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v3_to_v4_preserves_custom_repl_prefix():
    """Migration v3→v4 does not change non-! prefix values."""
    cfg = {"config_version": 3, "port": "COM4", "repl_prefix": ">>"}
    result = migrate_config(cfg)

    assert result["cmd_prefix"] == ">>"  # assert custom prefix unchanged (via v6 rename)
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v3_to_v4_renames_config_keys():
    """Migration v3→v4 renames compound config keys to use underscores."""
    cfg = {
        "config_version": 3,
        "baudrate": 9600,
        "bytesize": 7,
        "stopbits": 2,
        "autoconnect": True,
        "autoreconnect": True,
        "autoconnect_cmd": "ATZ",
    }
    result = migrate_config(cfg)

    assert result["baud_rate"] == 9600  # assert baudrate renamed
    assert result["byte_size"] == 7  # assert bytesize renamed
    assert result["stop_bits"] == 2  # assert stopbits renamed
    assert result["auto_connect"] is True  # assert autoconnect renamed
    assert result["auto_reconnect"] is True  # assert autoreconnect renamed
    assert result["on_connect_cmd"] == "ATZ"  # assert autoconnect_cmd renamed (via v4+v6)
    assert "baudrate" not in result  # assert old key removed
    assert "bytesize" not in result  # assert old key removed
    assert "stopbits" not in result  # assert old key removed
    assert "autoconnect" not in result  # assert old key removed
    assert "autoreconnect" not in result  # assert old key removed
    assert "autoconnect_cmd" not in result  # assert old key removed


def test_v5_to_v6_renames_config_keys():
    """Migration v5→v6 renames config keys for clarity and consistency."""
    # Arrange
    cfg = {
        "config_version": 5,
        "echo_cmd": True,
        "echo_cmd_fmt": "[purple]> {cmd}[/]",
        "auto_connect_cmd": "ATZ",
        "inter_cmd_delay_ms": 100,
        "show_eol": True,
        "exception_traceback": True,
        "app_border_color": "green",
        "repl_prefix": "/",
        "read_only": True,
    }

    # Act
    result = migrate_config(cfg)

    # Assert — new keys present with old values
    assert result["echo_input"] is True  # assert echo_cmd renamed
    assert result["echo_input_fmt"] == "[purple]> {cmd}[/]"  # assert echo_cmd_fmt renamed
    assert result["on_connect_cmd"] == "ATZ"  # assert auto_connect_cmd renamed
    assert result["cmd_delay_ms"] == 100  # assert inter_cmd_delay_ms renamed
    assert result["show_line_endings"] is True  # assert show_eol renamed
    assert result["show_traceback"] is True  # assert exception_traceback renamed
    assert result["border_color"] == "green"  # assert app_border_color renamed
    assert result["cmd_prefix"] == "/"  # assert repl_prefix renamed
    assert result["config_read_only"] is True  # assert read_only renamed

    # Assert — old keys removed
    for old_key in ("echo_cmd", "echo_cmd_fmt", "auto_connect_cmd",
                    "inter_cmd_delay_ms", "show_eol", "exception_traceback",
                    "app_border_color", "repl_prefix", "read_only"):
        assert old_key not in result  # assert old key removed

    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v5_to_v6_handles_missing_keys():
    """Migration v5→v6 handles configs that lack the old keys."""
    cfg = {"config_version": 5, "port": "COM4"}
    result = migrate_config(cfg)

    # Assert — no old or new keys introduced
    assert "echo_cmd" not in result
    assert "echo_input" not in result
    assert result["config_version"] == CURRENT_CONFIG_VERSION
