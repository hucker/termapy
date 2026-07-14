"""Tests for config schema versioning and migration chain."""

from termapy.migration import (
    CURRENT_CONFIG_VERSION,
    migrate_config,
)


def test_legacy_config_gets_version():
    """Config with no config_version gets stamped to current."""
    cfg = {"port": "COM4", "baud_rate": 115200}
    result = migrate_config(cfg)
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "legacy config stamped to current version"


def test_current_version_unchanged():
    """Config already at current version passes through unchanged."""
    cfg = {"config_version": CURRENT_CONFIG_VERSION, "port": "COM4"}
    result = migrate_config(cfg)
    assert result == cfg, "current-version config passes through unchanged"


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
        app_mod.CURRENT_CONFIG_VERSION = 3  # ty: ignore[invalid-assignment]
        app_mod.MIGRATIONS = {1: fake_v1_to_v2, 2: fake_v2_to_v3}

        cfg = {"config_version": 1, "port": "COM4"}
        result = app_mod.migrate_config(cfg)

        assert call_log == [1, 2], "migrations ran in order"
        assert result["added_by_v1"] is True, "v1 migration applied"
        assert result["added_by_v2"] is True, "v2 migration applied"
        assert result["config_version"] == 3, "version advanced to current"
    finally:
        app_mod.CURRENT_CONFIG_VERSION = orig_version
        app_mod.MIGRATIONS = orig_migrations


def test_migration_skips_when_no_function():
    """Version gaps without migration functions still advance the version."""
    cfg = {"config_version": 0}
    result = migrate_config(cfg)
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced despite missing migration functions"


def test_v1_to_v2_renames_add_date_to_cmd():
    """Migration v1→v2 renames add_date_to_cmd to show_timestamps."""
    cfg = {"config_version": 1, "add_date_to_cmd": True, "port": "COM4"}
    result = migrate_config(cfg)

    assert result["timestamps"] is True, "renamed key has old value (v28: show_timestamps -> timestamps)"
    assert "add_date_to_cmd" not in result, "old key removed"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v1_to_v2_without_old_key():
    """Migration v1→v2 handles configs that never had add_date_to_cmd."""
    cfg = {"config_version": 1, "port": "COM4"}
    result = migrate_config(cfg)

    assert "add_date_to_cmd" not in result, "old key not introduced"
    assert "show_timestamps" not in result, "new key not added by migration"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v2_to_v3_adds_then_v4_removes_command_history_items():
    """Migration v2→v3 adds command_history_items, v3→v4 removes it."""
    cfg = {"config_version": 2, "port": "COM4"}
    result = migrate_config(cfg)

    assert "command_history_items" not in result, "removed by v4"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v3_to_v4_removes_command_history_items():
    """Migration v3→v4 removes command_history_items from config."""
    cfg = {"config_version": 3, "port": "COM4", "command_history_items": 50}
    result = migrate_config(cfg)

    assert "command_history_items" not in result, "key removed"
    assert result["config_read_only"] is False, "config_read_only added (via v4+v6)"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v3_to_v4_handles_missing_key():
    """Migration v3→v4 handles configs without command_history_items."""
    cfg = {"config_version": 3, "port": "COM4"}
    result = migrate_config(cfg)

    assert "command_history_items" not in result, "no error on missing key"
    assert result["config_read_only"] is False, "config_read_only added (via v4+v6)"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v3_to_v4_preserves_existing_read_only():
    """Migration v3→v4 does not overwrite existing read_only value."""
    cfg = {"config_version": 3, "port": "COM4", "read_only": True}
    result = migrate_config(cfg)

    assert result["config_read_only"] is True, "existing value preserved (via v6 rename)"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v3_to_v4_changes_repl_prefix_bang_to_slash():
    """Migration v3→v4 changes repl_prefix from ! to /."""
    cfg = {"config_version": 3, "port": "COM4", "repl_prefix": "!"}
    result = migrate_config(cfg)

    assert result["cmd_prefix"] == "/", "prefix migrated (via v6 rename)"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v3_to_v4_preserves_custom_repl_prefix():
    """Migration v3→v4 does not change non-! prefix values."""
    cfg = {"config_version": 3, "port": "COM4", "repl_prefix": ">>"}
    result = migrate_config(cfg)

    assert result["cmd_prefix"] == ">>", "custom prefix unchanged (via v6 rename)"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


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

    # v3->v4 renames to baud_rate/byte_size/stop_bits at top level;
    # v21->v22 then nests them under cfg["serial"].  This test
    # asserts on the post-full-chain shape (CURRENT_CONFIG_VERSION).
    assert result["serial"]["baud_rate"] == 9600, "baudrate renamed and nested"
    assert result["serial"]["byte_size"] == 7, "bytesize renamed and nested"
    assert result["serial"]["stop_bits"] == 2, "stopbits renamed and nested"
    assert result["auto_connect"] is True, "autoconnect renamed"
    assert result["auto_reconnect"] is True, "autoreconnect renamed"
    # autoconnect_cmd was renamed to on_connect_cmd in v4+v6, then v14->v15
    # split that universal value into per-mode TUI/CLI keys (clearing the
    # universal so MCP doesn't silently fire interactive-only commands).
    assert result["tui_on_connect_cmd"] == "ATZ", \
        "autoconnect_cmd renamed and migrated to tui_on_connect_cmd in v15"
    assert result["cli_on_connect_cmd"] == "ATZ", \
        "autoconnect_cmd renamed and migrated to cli_on_connect_cmd in v15"
    assert result["on_connect_cmd"] == "", "universal on_connect_cmd cleared in v15"
    assert "baudrate" not in result, "old key baudrate removed"
    assert "bytesize" not in result, "old key bytesize removed"
    assert "stopbits" not in result, "old key stopbits removed"
    assert "autoconnect" not in result, "old key autoconnect removed"
    assert "autoreconnect" not in result, "old key autoreconnect removed"
    assert "autoconnect_cmd" not in result, "old key autoconnect_cmd removed"


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

    # Assert - new keys present with old values
    assert result["echo"] is True, "echo_cmd renamed (v28: echo_input -> echo)"
    assert result["echo_fmt"] == "[purple]> {cmd}[/]", "echo_cmd_fmt renamed (v28: -> echo_fmt)"
    # v6 renamed auto_connect_cmd -> on_connect_cmd; v15 then split its
    # value into per-mode interactive keys.
    assert result["tui_on_connect_cmd"] == "ATZ", \
        "auto_connect_cmd renamed; v15 migrated value to tui_on_connect_cmd"
    assert result["cli_on_connect_cmd"] == "ATZ", \
        "auto_connect_cmd renamed; v15 migrated value to cli_on_connect_cmd"
    assert result["on_connect_cmd"] == "", "universal cleared in v15"
    assert result["cmd_delay_ms"] == 100, "inter_cmd_delay_ms renamed"
    assert result["line_endings"] is True, "show_eol renamed (v28: show_line_endings -> line_endings)"
    assert result["show_traceback"] is True, "exception_traceback renamed"
    assert result["border_color"] == "green", "app_border_color renamed"
    assert result["cmd_prefix"] == "/", "repl_prefix renamed"
    assert result["config_read_only"] is True, "read_only renamed"

    # Assert - old keys removed
    for old_key in ("echo_cmd", "echo_cmd_fmt", "auto_connect_cmd",
                    "inter_cmd_delay_ms", "show_eol", "exception_traceback",
                    "app_border_color", "repl_prefix", "read_only"):
        assert old_key not in result, f"old key {old_key} removed"

    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v5_to_v6_handles_missing_keys():
    """Migration v5→v6 handles configs that lack the old keys."""
    cfg = {"config_version": 5, "port": "COM4"}
    result = migrate_config(cfg)

    # Assert - no old or new keys introduced
    assert "echo_cmd" not in result, "old key not introduced"
    assert "echo_input" not in result, "new key not added by migration"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v6_to_v7_adds_send_bare_enter():
    """Migration v6→v7 adds send_bare_enter defaulting to False."""
    # Arrange
    cfg = {"config_version": 6, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["send_bare_enter"] is False, "default added"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v6_to_v7_preserves_existing_send_bare_enter():
    """Migration v6→v7 does not overwrite existing send_bare_enter value."""
    # Arrange
    cfg = {"config_version": 6, "port": "COM4", "send_bare_enter": True}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["send_bare_enter"] is True, "existing value preserved"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v7_to_v8_removes_cap_endian():
    """Migration v7→v8 removes cap_endian from config."""
    # Arrange
    cfg = {"config_version": 7, "port": "COM4", "cap_endian": "le"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert "cap_endian" not in result, "key removed"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v7_to_v8_handles_missing_cap_endian():
    """Migration v7→v8 handles configs without cap_endian."""
    # Arrange
    cfg = {"config_version": 7, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert "cap_endian" not in result, "no error on missing key"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v10_to_v11_adds_file_xfer_root():
    """Migration v10→v11 adds file_xfer_root."""
    # Arrange
    cfg = {"config_version": 10, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["file_xfer_root"] == "", "file_xfer_root defaults to empty string"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v10_to_v11_preserves_existing_file_xfer_root():
    """Migration v10→v11 preserves existing file_xfer_root."""
    # Arrange
    cfg = {"config_version": 10, "port": "COM4", "file_xfer_root": "C:\\builds"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["file_xfer_root"] == "C:\\builds", "existing file_xfer_root preserved"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v11_to_v12_adds_custom_baud():
    """Migration v11->v12 adds custom_baud (lands under serial.* after v22)."""
    # Arrange
    cfg = {"config_version": 11, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert -- post-v22, custom_baud lives under cfg["serial"].
    assert result["serial"]["custom_baud"] is False, (
        "custom_baud defaults to False (nested under serial after v22)"
    )
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v11_to_v12_preserves_existing_custom_baud():
    """Migration v11->v12 preserves existing custom_baud (lands under serial.* after v22)."""
    # Arrange
    cfg = {"config_version": 11, "port": "COM4", "custom_baud": True}

    # Act
    result = migrate_config(cfg)

    # Assert -- post-v22, the preserved value lives under cfg["serial"].
    assert result["serial"]["custom_baud"] is True, (
        "existing custom_baud preserved (nested under serial after v22)"
    )
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v14_to_v15_moves_on_connect_cmd_to_per_mode_keys():
    """Migration v14->v15 preserves TUI/CLI behavior by moving the
    existing on_connect_cmd into per-mode interactive keys, leaving
    MCP a clean slate."""
    # Arrange
    cfg = {"config_version": 14, "port": "COM4", "on_connect_cmd": "AT+VER"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["on_connect_cmd"] == "", "universal cleared"
    assert result["tui_on_connect_cmd"] == "AT+VER", "TUI keeps the old behavior"
    assert result["cli_on_connect_cmd"] == "AT+VER", "CLI keeps the old behavior"
    assert result["mcp_on_connect_cmd"] == "", "MCP gets a clean slate"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, \
        "version advanced to current"


def test_v14_to_v15_empty_on_connect_cmd():
    """Cfg with no on_connect_cmd content gets all four keys empty."""
    # Arrange
    cfg = {"config_version": 14, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["on_connect_cmd"] == "", "universal stays empty"
    assert result["tui_on_connect_cmd"] == "", "TUI empty"
    assert result["cli_on_connect_cmd"] == "", "CLI empty"
    assert result["mcp_on_connect_cmd"] == "", "MCP empty"


def test_v14_to_v15_preserves_explicit_per_mode_keys():
    """If a hand-edited cfg already has per-mode keys, don't clobber them."""
    # Arrange
    cfg = {
        "config_version": 14,
        "port": "COM4",
        "on_connect_cmd": "AT+VER",
        "mcp_on_connect_cmd": "echo off",  # user pre-set this
    }

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["mcp_on_connect_cmd"] == "echo off", \
        "user-set mcp_on_connect_cmd preserved (setdefault skipped overwrite)"
    assert result["tui_on_connect_cmd"] == "AT+VER", "TUI gets migrated value"
    assert result["cli_on_connect_cmd"] == "AT+VER", "CLI gets migrated value"
    assert result["on_connect_cmd"] == "", "universal cleared regardless"


def test_v15_to_v16_adds_request_err_pattern():
    """Migration v15->v16 adds request_err_pattern with sensible default."""
    # Arrange
    cfg = {"config_version": 15, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert
    actual = result["request_err_pattern"]
    expected = r"(?i)^(ERROR|ERR|FAULT)\b"
    assert actual == expected, "default matches ERROR/ERR/FAULT, case-insensitive"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, \
        "version advanced to current"


def test_v15_to_v16_preserves_explicit_request_err_pattern():
    """User-set request_err_pattern is not overwritten by the migration."""
    # Arrange
    cfg = {
        "config_version": 15,
        "port": "COM4",
        "request_err_pattern": r"^FAIL",
    }

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["request_err_pattern"] == r"^FAIL", \
        "explicit user value preserved"


def test_v15_to_v16_empty_request_err_pattern_preserved():
    """Empty string (user disabled error detection) survives migration."""
    # Arrange
    cfg = {
        "config_version": 15,
        "port": "COM4",
        "request_err_pattern": "",  # explicit disable
    }

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["request_err_pattern"] == "", "empty disable preserved"


# ── v19 -> v20: /color renamed to /term.color in on-connect chains ─────────


def test_v19_to_v20_rewrites_color_in_on_connect_cmd():
    """/color at a command boundary becomes /term.color."""
    # Arrange
    cfg = {
        "config_version": 19,
        "cli_on_connect_cmd": "/color off",
    }
    # Act
    result = migrate_config(cfg)
    # Assert
    assert result["cli_on_connect_cmd"] == "/term.color off", "verb rewritten"


def test_v19_to_v20_rewrites_color_in_multi_line_chain():
    """Each command in a \\n-separated chain is checked independently."""
    # Arrange
    cfg = {
        "config_version": 19,
        "tui_on_connect_cmd": "echo on\n/color off\n/help",
    }
    # Act
    result = migrate_config(cfg)
    # Assert
    actual = result["tui_on_connect_cmd"]
    expected = "echo on\n/term.color off\n/help"
    assert actual == expected, (
        f"/color rewritten only at line boundary; got {actual!r}"
    )


def test_v19_to_v20_does_not_rewrite_color_in_argument_text():
    """``/print "/color was teal"`` keeps the literal /color in the args."""
    # Arrange -- /color appears mid-string (after a quote, after a space
    # that isn't a command boundary), so the conservative regex must
    # leave it alone.
    cfg = {
        "config_version": 19,
        "on_connect_cmd": '/print "the /color was teal"',
    }
    # Act
    result = migrate_config(cfg)
    # Assert -- literal /color survives inside the argument text.
    assert result["on_connect_cmd"] == '/print "the /color was teal"', (
        "argument-text /color must not be rewritten"
    )


def test_v19_to_v20_idempotent_on_already_renamed():
    """Running twice doesn't double-rewrite /term.color."""
    # Arrange
    cfg = {"config_version": 19, "mcp_on_connect_cmd": "/term.color on"}
    # Act
    result = migrate_config(cfg)
    # Assert
    assert result["mcp_on_connect_cmd"] == "/term.color on", "no double prefix"


def test_v19_to_v20_covers_all_four_on_connect_fields():
    """Every per-frontend on-connect field gets the rewrite."""
    # Arrange
    cfg = {
        "config_version": 19,
        "on_connect_cmd": "/color on",
        "tui_on_connect_cmd": "/color on",
        "cli_on_connect_cmd": "/color on",
        "mcp_on_connect_cmd": "/color on",
    }
    # Act
    result = migrate_config(cfg)
    # Assert
    for key in (
        "on_connect_cmd",
        "tui_on_connect_cmd",
        "cli_on_connect_cmd",
        "mcp_on_connect_cmd",
    ):
        assert result[key] == "/term.color on", f"{key} rewritten"


def test_v22_to_v23_rewrites_ver_in_all_on_connect_fields():
    """v22->v23 rewrites /ver -> /app.ver in every on-connect chain."""
    # Arrange
    cfg = {
        "config_version": 22,
        "on_connect_cmd": "/ver",
        "tui_on_connect_cmd": "/ver",
        "cli_on_connect_cmd": "/ver",
        "mcp_on_connect_cmd": "/ver",
    }
    # Act
    result = migrate_config(cfg)
    # Assert
    for key in (
        "on_connect_cmd",
        "tui_on_connect_cmd",
        "cli_on_connect_cmd",
        "mcp_on_connect_cmd",
    ):
        assert result[key] == "/app.ver", f"{key} rewritten to /app.ver"


def test_v22_to_v23_rewrites_ver_subcommands_and_chains():
    """/ver.latest / /ver.info rewrite too; only the verb, at line boundaries."""
    # Arrange -- a chain with a subcommand and a following command.
    cfg = {
        "config_version": 22,
        "tui_on_connect_cmd": "/ver.info\n/cls",
    }
    # Act
    result = migrate_config(cfg)
    # Assert
    actual = result["tui_on_connect_cmd"]
    expected = "/app.ver.info\n/cls"
    assert actual == expected, (
        f"/ver.info rewritten at the line boundary; got {actual!r}"
    )


def test_v22_to_v23_does_not_rewrite_verbose_or_argument_text():
    """The verb guard leaves /verbose, /version, and literal /ver in args alone."""
    # Arrange
    cfg = {
        "config_version": 22,
        "on_connect_cmd": "/verbose on",
        "cli_on_connect_cmd": '/print "the /ver string"',
    }
    # Act
    result = migrate_config(cfg)
    # Assert -- /verbose is not /ver (no word boundary); arg-text /ver survives.
    assert result["on_connect_cmd"] == "/verbose on", (
        "/verbose must not be rewritten to /app.verbose"
    )
    assert result["cli_on_connect_cmd"] == '/print "the /ver string"', (
        "argument-text /ver must not be rewritten"
    )


def test_v23_to_v24_rewrites_placeholders_in_all_on_connect_fields():
    """v23->v24 rewrites {clock}/{datetime} -> $() in every on-connect chain."""
    # Arrange
    cfg = {
        "config_version": 23,
        "on_connect_cmd": "/log.start run_{datetime}.log",
        "tui_on_connect_cmd": "/print {clock}",
        "cli_on_connect_cmd": "/print {clock} {datetime}",
        "mcp_on_connect_cmd": "AT+SET {datetime}",
    }
    # Act
    result = migrate_config(cfg)
    # Assert
    assert result["on_connect_cmd"] == "/log.start run_$(DATETIME:%Y%m%d_%H%M%S).log", (
        "{datetime} -> $(DATETIME:%Y%m%d_%H%M%S)"
    )
    assert result["tui_on_connect_cmd"] == "/print $(TIME)", "{clock} -> $(TIME)"
    assert result["cli_on_connect_cmd"] == "/print $(TIME) $(DATETIME:%Y%m%d_%H%M%S)", (
        "both placeholders rewritten in one chain"
    )
    assert result["mcp_on_connect_cmd"] == "AT+SET $(DATETIME:%Y%m%d_%H%M%S)", (
        "{datetime} rewritten in a device command too"
    )


def test_v23_to_v24_leaves_proto_results_template_alone():
    """proto_results_template's {datetime} is a str.format placeholder -- untouched."""
    # Arrange -- the proto template is a different mechanism, not the {} scripting system
    cfg = {
        "config_version": 23,
        "proto_results_template": "{name}-{proto_name}-{datetime}.json",
    }
    # Act
    result = migrate_config(cfg)
    # Assert -- verbatim, its {datetime} kept
    assert result["proto_results_template"] == "{name}-{proto_name}-{datetime}.json", (
        "proto_results_template must not be rewritten"
    )


def test_v23_to_v24_preserves_per_run_placeholders():
    """{seqN}/{starttime}/{elapsed} in a chain are per-run stamps -- not rewritten."""
    # Arrange
    cfg = {
        "config_version": 23,
        "on_connect_cmd": "/ss.svg cap_{seq1+}_{starttime}",
    }
    # Act
    result = migrate_config(cfg)
    # Assert -- untouched; only the ambient wall-clock placeholders moved
    assert result["on_connect_cmd"] == "/ss.svg cap_{seq1+}_{starttime}", (
        "per-run placeholders stay in the {} scripting system"
    )


def test_v24_to_v25_drops_cli_echo_input():
    """Migration v24->v25 removes the dead cli_echo_input key.

    REPL-command echo moved to a session-only flag (echo_repl) with no cfg
    key, so the migration only prunes the retired cli_echo_input.
    """
    # Arrange
    cfg = {"config_version": 24, "port": "COM4", "cli_echo_input": True}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert "cli_echo_input" not in result, "dead cli_echo_input removed"
    assert "echo_repl" not in result, "echo_repl is a session flag, not a cfg key"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v25_to_v26_adds_rx_newline():
    """Migration v25->v26 adds rx_newline defaulting to auto."""
    # Arrange
    cfg = {"config_version": 25, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert -- v26 adds rx_newline; v28 then renames it to eol_rx.
    assert result["eol_rx"] == "auto", "rx_newline default added (v28: -> eol_rx)"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v25_to_v26_preserves_existing_rx_newline():
    """Migration v25->v26 does not overwrite an existing rx_newline value."""
    # Arrange
    cfg = {"config_version": 25, "port": "COM4", "rx_newline": "crlf"}

    # Act
    result = migrate_config(cfg)

    # Assert -- value carried through the v28 rename to eol_rx.
    assert result["eol_rx"] == "crlf", "existing rx_newline preserved (v28: -> eol_rx)"


def test_v26_to_v27_adds_strip_device_echo():
    """Migration v26->v27 adds strip_device_echo defaulting to False."""
    # Arrange
    cfg = {"config_version": 26, "port": "COM4"}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["strip_device_echo"] is False, "strip_device_echo default added"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v26_to_v27_preserves_existing_strip_device_echo():
    """Migration v26->v27 does not overwrite an existing value."""
    # Arrange
    cfg = {"config_version": 26, "port": "COM4", "strip_device_echo": True}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["strip_device_echo"] is True, "existing value preserved"


def test_v27_to_v28_renames_keys_to_command_names():
    """Migration v27->v28 renames display keys to match /term.* command names."""
    # Arrange -- a v27 config with all the old key names + values.
    cfg = {
        "config_version": 27,
        "echo_input": True,
        "echo_input_fmt": "> {cmd}",
        "line_ending": "\r\n",
        "rx_newline": "crlf",
        "show_line_endings": True,
        "show_line_numbers": True,
        "hex_mode": True,
        "show_timestamps": True,
    }

    # Act
    result = migrate_config(cfg)

    # Assert -- new names carry the old values; old names gone.
    assert result["echo"] is True, "echo_input -> echo"
    assert result["echo_fmt"] == "> {cmd}", "echo_input_fmt -> echo_fmt"
    assert result["eol"] == "\r\n", "line_ending -> eol"
    assert result["eol_rx"] == "crlf", "rx_newline -> eol_rx"
    assert result["line_endings"] is True, "show_line_endings -> line_endings"
    assert result["line_no"] is True, "show_line_numbers -> line_no"
    assert result["hex"] is True, "hex_mode -> hex"
    assert result["timestamps"] is True, "show_timestamps -> timestamps"
    for old in ("echo_input", "echo_input_fmt", "line_ending", "rx_newline",
                "show_line_endings", "show_line_numbers", "hex_mode",
                "show_timestamps"):
        assert old not in result, f"old key {old} removed"
    assert result["config_version"] == CURRENT_CONFIG_VERSION, "version advanced to current"


def test_v27_to_v28_new_name_wins_when_both_present():
    """If both old and new keys exist, the new-name value is kept."""
    # Arrange
    cfg = {"config_version": 27, "echo_input": True, "echo": False}

    # Act
    result = migrate_config(cfg)

    # Assert
    assert result["echo"] is False, "pre-existing new key wins over old"
    assert "echo_input" not in result, "old key dropped"


def test_migration_steps_recorded_per_version():
    """Each step with a migrator appends a labelled line to _migration_steps."""
    # Arrange -- a config from v17 needs six steps to reach v23.
    cfg = {"config_version": 17}

    # Act
    result = migrate_config(cfg)

    # Assert -- one step entry per migrator that ran, labelled
    # "v<from> -> v<to>: <description>".
    steps = result.get("_migration_steps", [])
    assert len(steps) == 11, (
        f"v17 -> v28 covers eleven migrators "
        f"(17->18 ... 26->27, 27->28); "
        f"got {len(steps)}: {steps!r}"
    )
    expected_prefixes = (
        "v17 -> v18",
        "v18 -> v19",
        "v19 -> v20",
        "v20 -> v21",
        "v21 -> v22",
        "v22 -> v23",
        "v23 -> v24",
        "v24 -> v25",
        "v25 -> v26",
        "v26 -> v27",
        "v27 -> v28",
    )
    for step, prefix in zip(steps, expected_prefixes, strict=True):
        assert step.startswith(prefix), (
            f"step {step!r} should start with {prefix!r}"
        )


def test_migration_steps_include_docstring_summary():
    """The label after the version range is the migrator's docstring summary."""
    # Arrange -- v20 -> v21 has a docstring starting
    # "Add ``record_enabled`` toggle for the Record button ...".
    # v21->v22 (nest pyserial keys) and v22->v23 (/ver rewrite) also run.
    cfg = {"config_version": 20}

    # Act
    result = migrate_config(cfg)

    # Assert -- v20->v21 step shape (the one this test is about).
    steps = result.get("_migration_steps", [])
    assert len(steps) == 8, (
        f"eight steps (v20->v21 ... v26->v27, v27->v28); got {steps!r}"
    )
    assert "record_enabled" in steps[0], (
        f"v20->v21 step line carries the migrator's docstring summary; got {steps[0]!r}"
    )


def test_no_steps_recorded_when_already_current():
    """A cfg already at CURRENT_CONFIG_VERSION runs zero migrators."""
    # Arrange / Act
    cfg = {"config_version": CURRENT_CONFIG_VERSION}
    result = migrate_config(cfg)

    # Assert -- no _migration_steps key (or empty); the absence
    # signals "nothing migrated" to the display path.
    assert "_migration_steps" not in result or result["_migration_steps"] == [], (
        "no steps when nothing to migrate"
    )


def test_v21_to_v22_nests_pyserial_keys():
    """Pyserial constructor args move from top level into cfg['serial']."""
    # Arrange -- v21 cfg with all 7 pyserial keys flat plus some
    # non-serial keys that should stay flat after migration.
    cfg = {
        "config_version": 21,
        "port": "COM4",
        "baud_rate": 9600,
        "custom_baud": True,
        "byte_size": 8,
        "parity": "E",
        "stop_bits": 2,
        "flow_control": "rtscts",
        # Non-serial keys that should NOT move.
        "encoding": "utf-8",
        "line_ending": "\n",
        "cmd_delay_ms": 100,
    }

    # Act
    result = migrate_config(cfg)

    # Assert -- all 7 pyserial keys nested under cfg["serial"].
    expected_serial = {
        "port": "COM4",
        "baud_rate": 9600,
        "custom_baud": True,
        "byte_size": 8,
        "parity": "E",
        "stop_bits": 2,
        "flow_control": "rtscts",
    }
    actual_serial = result.get("serial", {})
    assert actual_serial == expected_serial, (
        f"all 7 pyserial keys nested under cfg['serial']; got {actual_serial!r}"
    )

    # Assert -- the 7 flat keys are gone from the top level.
    for key in expected_serial:
        assert key not in result, (
            f"flat top-level {key!r} removed after nesting; still present"
        )

    # Assert -- non-serial keys stay where they were.
    assert result["encoding"] == "utf-8", "encoding stays flat top-level"
    assert result["eol"] == "\n", "TX newline stays flat top-level (v28: line_ending -> eol)"
    assert result["cmd_delay_ms"] == 100, "cmd_delay_ms stays flat top-level"

    # Assert -- version advanced.
    assert result["config_version"] == CURRENT_CONFIG_VERSION, (
        "version advanced to current"
    )


def test_v21_to_v22_idempotent_for_partial_v22_shape():
    """A cfg that's somehow already partially nested doesn't lose data.

    Normal flow never produces a partially-nested cfg, but the
    migration step uses ``setdefault`` defensively so a flat key
    can't clobber an already-nested value (e.g. if a user hand-edits
    a cfg between versions or restores from a stale backup).
    """
    # Arrange -- port is flat, baud_rate is already nested.
    cfg = {
        "config_version": 21,
        "port": "COM4",
        "serial": {"baud_rate": 9600},
    }

    # Act
    result = migrate_config(cfg)

    # Assert -- nested value wins, flat value moves in alongside.
    assert result["serial"]["baud_rate"] == 9600, (
        "pre-existing nested baud_rate preserved"
    )
    assert result["serial"]["port"] == "COM4", (
        "flat port moved into serial sub-dict"
    )
    assert "port" not in result, "flat port removed from top level"


def test_v21_to_v22_empty_cfg_creates_empty_serial():
    """A v21 cfg with no pyserial keys gets an empty serial sub-dict.

    The defaults-backfill in ``load_config`` fills serial.* from
    DEFAULT_CFG once one-level recursive backfill runs; this test
    just verifies the migration step itself doesn't drop the key.
    """
    # Arrange
    cfg = {"config_version": 21, "title": "no-serial cfg"}

    # Act
    result = migrate_config(cfg)

    # Assert -- empty serial sub-dict, ready for backfill.
    assert result["serial"] == {}, (
        "serial sub-dict created (empty) so defaults-backfill fills it"
    )
