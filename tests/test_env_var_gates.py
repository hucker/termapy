"""Tests for the env-var security gates.

Two gates live outside the cfg layer (a cfg cannot defend against
a hostile cfg setting its own flag):

  - ``TERMAPY_TRUSTED_PLUGINS_ONLY`` -- strict-mode plugin loading.
    When truthy, filesystem-discovered plugin folders are skipped;
    only built-ins (in site-packages) load.
  - ``TERMAPY_OS_CMD_ENABLED`` -- replaces the retired
    ``os_cmd_enabled`` cfg key for /os shell escapes.

Both are evaluated once at import time and cached as module-level
constants.  Tests use monkeypatch to override the cached constants
where the *behavior* needs to differ, rather than re-running module
imports inside the test process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy import env_flags
from termapy.migration import (
    CURRENT_CONFIG_VERSION,
    DEPRECATED_CFG,
    migrate_config,
)


class TestTruthyParser:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on", "  on  "])
    def test_truthy_values(self, value):
        """Any case and surrounding spaces are fine: 1, true, yes, and on all read as enabled."""
        # Arrange / Act
        actual = env_flags._truthy(value)
        # Assert
        assert actual is True, f"{value!r} should be truthy"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "   ", "maybe", "2"])
    def test_falsy_values(self, value):
        """Off words, blanks, and junk like maybe or 2 all read as false, never an error."""
        # Arrange / Act
        actual = env_flags._truthy(value)
        # Assert
        assert actual is False, f"{value!r} should be falsy"

    def test_none_is_falsy(self):
        """An unset variable (None from os.environ.get) reads as false."""
        # Arrange / Act / Assert
        assert env_flags._truthy(None) is False, "unset env var (None) reads false"


class TestOsCmdGate:
    """/os reads OS_CMD_ENABLED from env_flags, not from cfg."""

    def test_disabled_blocks_os(self, monkeypatch):
        """With the gate off /os refuses, and the error names TERMAPY_OS_CMD_ENABLED."""
        # Arrange
        from termapy.builtins.commands import os_cmd
        monkeypatch.setattr(os_cmd, "OS_CMD_ENABLED", False)

        captured: list[str] = []

        class _IO:
            def output(self, text, *_a, **_kw):
                captured.append(text)

        class _Ctx:
            io = _IO()

        # Act
        result = os_cmd._handler(_Ctx(), "echo hi")  # ty: ignore[invalid-argument-type]

        # Assert
        assert result.success is False, "blocked when env flag false"
        assert "TERMAPY_OS_CMD_ENABLED" in result.error, (
            "error names the env var so the user can self-rescue"
        )

    def test_enabled_runs_command(self, monkeypatch):
        """With the gate on /os runs the child and streams its stdout through io.output."""
        # Arrange
        from termapy.builtins.commands import os_cmd
        monkeypatch.setattr(os_cmd, "OS_CMD_ENABLED", True)

        captured: list[str] = []

        class _IO:
            def output(self, text, *_a, **_kw):
                captured.append(text)

        class _Ctx:
            io = _IO()

        # Act -- a portable echo via python so the test works on Windows
        result = os_cmd._handler(_Ctx(), "python -c \"print('zz_marker')\"")  # ty: ignore[invalid-argument-type]

        # Assert
        assert result.success is True, "succeeds when env flag true"
        assert any("zz_marker" in line for line in captured), (
            "child stdout reached the io.output channel"
        )

    def test_cfg_key_is_ignored(self, monkeypatch):
        """Setting os_cmd_enabled in a runtime cfg dict no longer enables /os.

        The cfg cannot grant /os to itself.  Even if some legacy code path
        leaves the key in the dict, the handler does not read it.
        """
        # Arrange -- env says no, "cfg" says yes; env wins.
        from termapy.builtins.commands import os_cmd
        monkeypatch.setattr(os_cmd, "OS_CMD_ENABLED", False)

        class _IO:
            def output(self, *_a, **_kw): pass

        class _Ctx:
            io = _IO()
            cfg = {"os_cmd_enabled": True}  # noise; should be ignored

        # Act
        result = os_cmd._handler(_Ctx(), "echo nope")  # ty: ignore[invalid-argument-type]

        # Assert
        assert result.success is False, "cfg key cannot grant /os"


class TestMigrationV18ToV19:
    """v18->v19 strips os_cmd_enabled; if it was True, a one-shot warning fires."""

    def test_key_stripped_when_present(self, tmp_path):
        """The retired key is removed and the cfg advances to the current schema version."""
        # Arrange
        cfg = {"config_version": 18, "os_cmd_enabled": True}
        # Act
        out = migrate_config(cfg)
        # Assert
        assert "os_cmd_enabled" not in out, "key retired from cfg"
        assert out["config_version"] == CURRENT_CONFIG_VERSION, "version advanced"

    def test_warning_when_was_true(self):
        """A cfg that had /os enabled is told the switch moved to TERMAPY_OS_CMD_ENABLED."""
        # Arrange
        cfg = {"config_version": 18, "os_cmd_enabled": True}
        # Act
        out = migrate_config(cfg)
        # Assert
        warnings = out.get("_migration_warnings", [])
        assert any("TERMAPY_OS_CMD_ENABLED" in warning for warning in warnings), (
            "user gets a one-shot pointer to the env var replacement"
        )

    def test_no_warning_when_was_false(self):
        """Stripping a key that was off is silent; only a lost enable is worth a warning."""
        # Arrange -- the common case: user had the default off.
        cfg = {"config_version": 18, "os_cmd_enabled": False}
        # Act
        out = migrate_config(cfg)
        # Assert
        warnings = out.get("_migration_warnings", [])
        assert not any("TERMAPY_OS_CMD_ENABLED" in warning for warning in warnings), (
            "silent strip when nothing was enabled"
        )

    def test_no_warning_when_key_absent(self):
        """A v18 cfg without the key migrates with no _migration_warnings entry at all."""
        # Arrange
        cfg = {"config_version": 18}
        # Act
        out = migrate_config(cfg)
        # Assert
        assert "_migration_warnings" not in out, "no warnings when nothing to migrate"

    def test_deprecated_cfg_has_helpful_message(self):
        """Hand-edited cfgs that re-add the key get caught by validate_config."""
        # Arrange / Act
        hint = DEPRECATED_CFG.get("os_cmd_enabled", "")
        # Assert
        assert "TERMAPY_OS_CMD_ENABLED" in hint, (
            "deprecation message names the env var"
        )


class TestStrictPluginMode:
    """TRUSTED_PLUGINS_ONLY=1 skips filesystem plugin discovery.

    Built-in plugins always load (they're shipped in site-packages,
    which is the new trust boundary).
    """

    @pytest.mark.slow  # fresh interpreter: the constant is frozen at import
    @pytest.mark.parametrize(("env_value", "expected"), [("1", True), (None, False)])
    def test_env_var_reaches_the_engine_default(self, env_value, expected):
        """The env var, read at import, becomes ReplEngine's trusted_only default; unset stays permissive."""
        # Arrange -- a subprocess, because re-importing in-process would
        # leave a mutated module behind for every later test.
        import os
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items() if k != "TERMAPY_TRUSTED_PLUGINS_ONLY"}
        if env_value is not None:
            env["TERMAPY_TRUSTED_PLUGINS_ONLY"] = env_value
        probe = (
            "import inspect\n"
            "from termapy.env_flags import TRUSTED_PLUGINS_ONLY\n"
            "from termapy.repl import ReplEngine\n"
            "default = inspect.signature(ReplEngine).parameters['trusted_only'].default\n"
            "print(TRUSTED_PLUGINS_ONLY, default)\n"
        )
        # Act
        result = subprocess.run(
            [sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=60,
        )
        # Assert
        assert result.returncode == 0, result.stderr
        actual = result.stdout.split()
        assert actual == [str(expected), str(expected)], (
            "constant and engine default both follow the env var"
        )


class TestCfgKeyRetired:
    """os_cmd_enabled is no longer in DEFAULT_CFG / CONFIG_FIELD_HELP."""

    def test_not_in_default_cfg(self):
        """New configs are not seeded with the retired key, so it cannot creep back in."""
        # Arrange
        from termapy.defaults import DEFAULT_CFG
        # Act / Assert
        assert "os_cmd_enabled" not in DEFAULT_CFG, (
            "retired key must not return as a default field"
        )

    def test_not_in_cfg_help(self):
        """The cfg editor has no help text for the retired key, so it is never suggested."""
        # Arrange
        from termapy.defaults import CFG_HELP
        # Act / Assert
        assert "os_cmd_enabled" not in CFG_HELP, (
            "help text for retired key must be removed so the editor "
            "doesn't suggest it"
        )

    def test_demo_cfg_does_not_carry_key(self):
        """The shipped demo.cfg template is free of the retired key as well."""
        # Arrange
        demo_cfg = (
            Path(__file__).resolve().parent.parent
            / "src" / "termapy" / "builtins" / "demo" / "demo.cfg"
        )
        # Act
        data = json.loads(demo_cfg.read_text(encoding="utf-8"))
        # Assert
        assert "os_cmd_enabled" not in data, "demo template kept current"
