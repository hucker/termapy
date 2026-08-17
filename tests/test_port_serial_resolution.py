"""End-to-end tests for USB serial-number resolution in port specs.

These tests exercise the full pipeline: ``cfg["port"]`` -> env
expansion -> ``resolve_port()`` -> ``open_serial()`` -> connected
device.  Unit tests for the pure resolver live in
[tests/test_port_control.py](tests/test_port_control.py); this file
covers the behavior a user actually sees in the TUI / CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from termapy.capture import CaptureEngine
from termapy.defaults import DEFAULT_CFG
from termapy.port_control import AmbiguousSerialNumberError
from termapy.serial_engine import SerialEngine

pytestmark = pytest.mark.slow  # subprocess CLI tests for port resolution


def _run_cli(
    tmp_path: Path,
    cfg_overrides: dict,
    script_lines: list[str],
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke termapy --cli with a throwaway config + script.

    Mirrors the pattern from test_cli_prefix.py.  ``env_overrides``
    adds to ``os.environ`` for the subprocess, which is how we turn
    on DEMO_FLEET for resolution tests.
    """
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    # Serial keys (port, baud_rate, etc.) live nested under
    # cfg["serial"] post-v22.  Callers still pass them flat through
    # cfg_overrides ({"port": "..."}), so we route those into the
    # sub-dict before serializing -- a flat top-level "port" would
    # be silently ignored by open_serial.
    _serial_keys = frozenset({
        "port", "baud_rate", "custom_baud", "byte_size",
        "parity", "stop_bits", "flow_control",
    })
    serial_overrides = {
        k: cfg_overrides[k] for k in cfg_overrides if k in _serial_keys
    }
    top_overrides = {
        k: v for k, v in cfg_overrides.items() if k not in _serial_keys
    }
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "DEMO", **serial_overrides},
        "auto_connect": True,
        **top_overrides,
    }
    (proj_dir / "proj.cfg").write_text(json.dumps(cfg, indent=4))

    script_path = tmp_path / "resolve.run"
    script_path.write_text("\n".join(script_lines) + "\n")

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    return subprocess.run(
        [
            sys.executable, "-c",
            "import sys; "
            f"sys.argv = ['termapy', 'proj', '--cli', "
            f"'--cfg-dir', {str(tmp_path)!r}, "
            f"'--run', {str(script_path)!r}, "
            f"'--no-color', '--term-width', '120']; "
            "from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


class TestCliResolvedFromBanner:
    """On successful connect, show "Resolved <spec> -> <actual>" banner."""

    def test_fallback_chain_to_demo_shows_resolved_line(self, tmp_path):
        # Arrange -- spec chain where the leading SN candidate can't
        # match anything (no DEMO_FLEET here), so the DEMO fallback
        # wins and the banner should announce the resolution.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"port": "UNKNOWN_SN|DEMO"},
            script_lines=["/echo done"],
        )

        # Assert
        assert result.returncode == 0, (
            f"CLI exited cleanly, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        assert "Resolved UNKNOWN_SN|DEMO -> DEMO" in result.stdout, (
            "banner should announce resolution to DEMO. "
            f"stdout: {result.stdout!r}"
        )

    def test_literal_port_no_resolved_line(self, tmp_path):
        # Arrange -- plain DEMO in/DEMO out, no resolution happened.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"port": "DEMO"},
            script_lines=["/echo done"],
        )

        # Assert -- no "Resolved" line appears because spec == actual.
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "Resolved " not in result.stdout, (
            "no resolved-from line when spec matches actual device. "
            f"stdout: {result.stdout!r}"
        )

    def test_connected_line_shows_actual_device_not_spec(self, tmp_path):
        # Arrange -- spec is a pipe chain that resolves to DEMO.  The
        # "Connected: X Y Z..." line should name the actual device
        # (DEMO), NOT the spec string ("UNKNOWN_SN|DEMO").
        result = _run_cli(
            tmp_path,
            cfg_overrides={"port": "UNKNOWN_SN|DEMO"},
            script_lines=["/echo done"],
        )

        # Assert -- connection_string uses the actual port when
        # connected, so it's useful as a status display.  Having the
        # raw spec leak into "Connected:" would be a regression.
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        connected_lines = [
            connected_line for connected_line in result.stdout.splitlines()
            if connected_line.startswith("Connected:")
        ]
        assert connected_lines, (
            f"expected a Connected: line; stdout: {result.stdout!r}"
        )
        line = connected_lines[0]
        assert "DEMO" in line, (
            f"Connected line should name the actual device; got {line!r}"
        )
        assert "UNKNOWN_SN|DEMO" not in line, (
            f"Connected line must not contain the raw spec; got {line!r}"
        )

    def test_port_info_shows_resolved_from_annotation(self, tmp_path):
        # Arrange -- regression for a Rich-markup-eats-brackets bug.
        # The "(resolved from SPEC)" annotation on /port.info's Port:
        # line was previously wrapped in square brackets which Rich
        # swallowed as malformed markup.  Parens are safe.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"port": "UNKNOWN_SN|DEMO"},
            script_lines=["/port.info"],
        )

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "resolved from UNKNOWN_SN|DEMO" in result.stdout, (
            "/port.info should show '(resolved from ...)' annotation. "
            f"stdout: {result.stdout!r}"
        )


class TestPortCommandStatusMessage:
    """`/port <arg>` status must name the resolved device, not the spec.

    Regression: a user typing /port <SN> saw "Resolved <SN> -> COM4"
    (correct) immediately followed by "Port changed to <SN> (session)"
    (wrong -- should also say COM4).  Two inconsistent messages for
    the same event.
    """

    def test_port_command_status_uses_resolved_device(self, tmp_path):
        # Arrange -- /port <something>|DEMO forces the fallback chain
        # to resolve to DEMO.  The status line after reconnect should
        # name DEMO, not the spec.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"port": "DEMO"},
            script_lines=[
                "/port.disconnect",
                "/port FAKE_SN|DEMO",
                "/echo done",
            ],
        )

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        changed_lines = [
            changed_line for changed_line in result.stdout.splitlines()
            if "Port changed to" in changed_line
        ]
        assert changed_lines, (
            f"expected a 'Port changed to' line; stdout: {result.stdout!r}"
        )
        line = changed_lines[0]
        assert "DEMO" in line, (
            f"status should name the actual device; got {line!r}"
        )
        assert "FAKE_SN|DEMO" not in line, (
            f"status must not contain the raw spec; got {line!r}"
        )


class TestPortCommandDoesNotWriteDisk:
    """`/port <arg>` mutates memory only; the on-disk cfg is untouched.

    This matters because the user's on-disk spec may be a portable
    fallback chain like "$(env.MY_SN)|COM3" -- we don't want a
    session-only override to silently clobber that.  The ConfigEditor
    dialog is the ONLY disk-persistence path.
    """

    def test_port_command_leaves_disk_config_unchanged(self, tmp_path):
        # Arrange -- start with "DEMO" in cfg, script issues /port to
        # switch.  We don't actually connect to anything after the
        # switch (there's nothing to connect to on CI); we just verify
        # the disk content doesn't change.
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        cfg = {**DEFAULT_CFG, "port": "DEMO", "auto_connect": True}
        cfg_file = proj_dir / "proj.cfg"
        cfg_file.write_text(json.dumps(cfg, indent=4))

        script_path = tmp_path / "port_override.run"
        script_path.write_text(
            "/port.disconnect\n"
            "/port A1B2C3D4\n"
            "/echo done\n"
        )

        # Act
        subprocess.run(
            [
                sys.executable, "-c",
                "import sys; "
                f"sys.argv = ['termapy', 'proj', '--cli', "
                f"'--cfg-dir', {str(tmp_path)!r}, "
                f"'--run', {str(script_path)!r}, "
                f"'--no-color', '--term-width', '120']; "
                "from termapy.entry import main; main()",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Assert -- disk file still names the original port.
        disk_cfg = json.loads(cfg_file.read_text())
        assert disk_cfg["port"] == "DEMO", (
            f"on-disk port should be unchanged, got {disk_cfg['port']!r}"
        )


class TestAmbiguousSerialNumberViaEngine:
    """AmbiguousSerialNumberError raised by resolve_port() during
    SerialEngine.connect() becomes a friendly last_error string.

    Exercised via a synthetic open_fn that raises -- simpler and more
    targeted than trying to assemble an actual duplicate-SN fleet in
    the enumeration table.
    """

    def test_ambiguity_produces_user_facing_message(self, tmp_path):
        # Arrange
        cfg = {**DEFAULT_CFG, "port": "0001"}

        def _raise_ambiguous(_cfg):
            raise AmbiguousSerialNumberError("0001", ["COM3", "COM7"])

        engine = SerialEngine(
            cfg=cfg,
            capture=MagicMock(spec=CaptureEngine),
            open_fn=_raise_ambiguous,
            log=lambda direction, text: None,
        )

        # Act
        actual = engine.connect()

        # Assert
        assert actual is False, "connect should fail on ambiguity"
        err = engine.last_error
        assert "0001" in err, f"SN must appear in error; got {err!r}"
        assert "COM3" in err and "COM7" in err, (
            f"both colliding devices must appear; got {err!r}"
        )
        assert "disambiguate" in err.lower() or "fallback" in err.lower(), (
            f"error should suggest next step; got {err!r}"
        )


class TestClassifySerialErrorWithSpec:
    """_classify_serial_error decorates "port not found" with a
    per-candidate trace and a list of currently-connected ports when
    the spec is a multi-candidate chain.
    """

    def test_not_found_with_fallback_chain_lists_candidates(
        self, tmp_path, monkeypatch
    ):
        # Arrange -- DEMO_FLEET gives known enumeration, spec names
        # two non-existent candidates.
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")
        from termapy.serial_engine import _classify_serial_error

        # Act -- synthesize a FileNotFoundError-like error.
        exc = FileNotFoundError("no such port")
        actual = _classify_serial_error(exc, "BOGUS1|BOGUS2")

        # Assert
        assert "BOGUS1" in actual, f"first candidate named; got {actual!r}"
        assert "BOGUS2" in actual, f"second candidate named; got {actual!r}"
        assert "not found" in actual.lower(), (
            f"each should be marked not found; got {actual!r}"
        )
        # DEMO_FLEET gives COM3, COM4, COM7 -- at least one should show
        # in the "Currently connected" list.
        assert "COM3" in actual or "COM4" in actual or "COM7" in actual, (
            f"should list currently-connected ports; got {actual!r}"
        )

    def test_not_found_single_candidate_uses_simple_message(self, tmp_path):
        # Arrange
        from termapy.serial_engine import _classify_serial_error

        # Act -- plain single port name, no pipe.
        exc = FileNotFoundError("no such port")
        actual = _classify_serial_error(exc, "COM99")

        # Assert -- keeps the short form for the common case.
        expected_substring = "Port not found"
        assert expected_substring in actual, (
            f"single-candidate spec keeps short message; got {actual!r}"
        )
        assert "Tried each candidate" not in actual, (
            "no candidate trace for single-candidate specs"
        )
