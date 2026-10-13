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

Every env boolean is read through ``scripting.parse_bool``
(bool-one-vocabulary): absence is the declared default, and a present
token outside the vocabulary is a startup error termapy refuses to run
with -- the readers take an explicit environment and error list so that
contract is pinned without touching the process environment.
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


class TestGateReader:
    """Every env boolean goes through parse_bool: absence is the declared default, garbage is a startup error."""

    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on", "  on  ", "y", "t"])
    def test_truthy_values(self, value):
        """Any vocabulary token, in any case and with surrounding spaces, reads as enabled and records nothing."""
        errors: list[str] = []
        actual = env_flags._read_gate("TERMAPY_X", environ={"TERMAPY_X": value}, errors=errors)
        assert actual is True, f"{value!r} should be truthy"
        assert errors == [], "a valid token is not a startup error"

    @pytest.mark.parametrize(
        "environ", [{}, {"TERMAPY_X": ""}, {"TERMAPY_X": "   "}, {"TERMAPY_X": "off"}, {"TERMAPY_X": "0"}],
    )
    def test_unset_blank_and_off_read_false(self, environ):
        """Unset, blank and the off tokens read as disabled without an error: absence is the gate's declared default."""
        errors: list[str] = []
        actual = env_flags._read_gate("TERMAPY_X", environ=environ, errors=errors)
        assert actual is False, f"{environ!r} should read off"
        assert errors == []

    @pytest.mark.parametrize("value", ["maybe", "2", "ture"])
    def test_garbage_fails_closed_and_records_the_error(self, value):
        """A present token outside the vocabulary reads False AND records Invalid <VAR>: <value>; it used to mean off silently."""
        errors: list[str] = []
        actual = env_flags._read_gate(
            "TERMAPY_OS_CMD_ENABLED", environ={"TERMAPY_OS_CMD_ENABLED": value}, errors=errors,
        )
        assert actual is False, "fail closed; the process is about to refuse to start"
        expected = f"Invalid TERMAPY_OS_CMD_ENABLED: {value} (use on/off/true/false/yes/no/1/0/y/n/t/f)"
        assert errors == [expected], "the variable is named so the user can fix it"

    def test_the_override_reads_none_when_unset_and_records_garbage(self):
        """TERMAPY_GUI is an override: unset means no override (None); garbage is a startup error naming it."""
        errors: list[str] = []
        assert env_flags._read_override("TERMAPY_GUI", environ={}, errors=errors) is None, "no override"
        assert env_flags._read_override("TERMAPY_GUI", environ={"TERMAPY_GUI": "no"}, errors=errors) is False
        assert env_flags._read_override("TERMAPY_GUI", environ={"TERMAPY_GUI": "ture"}, errors=errors) is None
        assert errors == ["Invalid TERMAPY_GUI: ture (use on/off/true/false/yes/no/1/0/y/n/t/f)"]


class TestStartupRefusal:
    """termapy refuses to start on an environment boolean it cannot read, naming the variable, with no traceback."""

    def test_main_exits_one_with_the_message(self, capsys, monkeypatch):
        """A recorded startup error makes main() print termapy: <error> to stderr and exit 1 before any flag runs."""
        # Arrange -- a seeded error list; --ports would otherwise enumerate
        monkeypatch.setattr(env_flags, "STARTUP_ERRORS", ["Invalid TERMAPY_OS_CMD_ENABLED: ture (use on/off)"])
        monkeypatch.setattr("sys.argv", ["termapy", "--ports"])
        from termapy.entry import main

        # Act
        with pytest.raises(SystemExit) as exc:
            main()

        # Assert
        assert exc.value.code == 1, "a refused start is exit 1"
        assert capsys.readouterr().err == "termapy: Invalid TERMAPY_OS_CMD_ENABLED: ture (use on/off)\n"

    @pytest.mark.slow  # fresh interpreter: the environment is read at import
    def test_a_bad_variable_stops_a_real_process(self):
        """In a fresh interpreter TERMAPY_OS_CMD_ENABLED=ture makes termapy --ports exit 1 with the message, no traceback."""
        # Arrange
        import os
        import subprocess
        import sys

        env = dict(os.environ, TERMAPY_OS_CMD_ENABLED="ture")

        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--ports"],
            env=env, capture_output=True, text=True, timeout=60,
        )

        # Assert
        assert result.returncode == 1, result.stderr
        assert "termapy: Invalid TERMAPY_OS_CMD_ENABLED: ture (use on/off/" in result.stderr
        assert "Traceback" not in result.stderr, "a message, never a stack trace"


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
