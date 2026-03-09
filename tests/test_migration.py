"""Tests for config schema versioning and migration chain."""

from termapy.migration import (
    CURRENT_CONFIG_VERSION,
    MIGRATIONS,
    migrate_config,
)


def test_legacy_config_gets_version():
    """Config with no config_version gets stamped to current."""
    cfg = {"port": "COM4", "baudrate": 115200}
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


def test_v2_to_v3_adds_command_history_items():
    """Migration v2→v3 adds command_history_items with default 30."""
    cfg = {"config_version": 2, "port": "COM4"}
    result = migrate_config(cfg)

    assert result["command_history_items"] == 30  # assert default value added
    assert result["config_version"] == CURRENT_CONFIG_VERSION


def test_v2_to_v3_preserves_existing_value():
    """Migration v2→v3 does not overwrite existing command_history_items."""
    cfg = {"config_version": 2, "port": "COM4", "command_history_items": 50}
    result = migrate_config(cfg)

    assert result["command_history_items"] == 50  # assert existing value preserved
    assert result["config_version"] == CURRENT_CONFIG_VERSION
