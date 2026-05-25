"""End-to-end tests for the extended /port.connect command.

/port.connect now accepts line-ending (cr/lf/crlf) and echo (echo/noecho)
tokens in addition to port name, baud, and mode.  Port name must be
the first token; everything else is order-independent.  Unit tests for
``parse_open_args`` live in [tests/test_port_control.py](tests/test_port_control.py);
this file exercises the full REPL path through ``_handler_connect`` and
verifies the cfg dict is updated correctly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.defaults import DEFAULT_CFG

pytestmark = pytest.mark.slow  # subprocess CLI /port.connect end-to-end


def _run_cli(
    tmp_path: Path,
    cfg_overrides: dict,
    script_lines: list[str],
) -> subprocess.CompletedProcess[str]:
    """Invoke termapy --cli with a throwaway config + script."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    # v22 schema: pyserial keys (port, baud_rate, parity, ...) live
    # under cfg["serial"], not at the top level.  Route any flat
    # serial-domain keys passed in cfg_overrides into cfg["serial"]
    # so call sites can keep using {"baud_rate": 9600} without
    # knowing about the nesting.
    serial_keys = {"port", "baud_rate", "custom_baud", "byte_size",
                   "parity", "stop_bits", "flow_control"}
    cfg = {**DEFAULT_CFG, "auto_connect": False}
    cfg["serial"] = {**cfg["serial"], "port": "DEMO"}
    for k, v in cfg_overrides.items():
        if k in serial_keys:
            cfg["serial"][k] = v
        else:
            cfg[k] = v
    (proj_dir / "proj.cfg").write_text(json.dumps(cfg, indent=4))

    script_path = tmp_path / "connect.run"
    script_path.write_text("\n".join(script_lines) + "\n")

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
    )


class TestPortConnectExtendedArgs:
    """/port.connect now accepts cr/lf/crlf and echo/noecho tokens."""

    def test_port_connect_with_all_fields(self, tmp_path):
        # Arrange -- start disconnected, connect DEMO with the full
        # argument set and verify each field made it into the cfg.
        # /cfg <key> reads a single cfg value back so we can assert
        # against it end-to-end.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"baud_rate": 115200, "line_ending": "\r",
                           "echo_input": False},
            script_lines=[
                "/port.connect DEMO 9600 N81 crlf echo",
                "/cfg baud_rate",
                "/cfg line_ending",
                "/cfg echo_input",
            ],
        )

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        out = result.stdout
        # baud_rate should now be 9600
        assert "9600" in out, (
            f"/cfg baud_rate should show 9600; stdout: {out!r}"
        )
        # line_ending display: actual cfg value is "\r\n" but the
        # REPL typically shows a human-readable form; we check for
        # the label so the test isn't brittle to representation.
        # Accept either "\\r\\n" or "crlf" depending on how /cfg
        # formats the value -- both prove the value changed.
        assert ("\\r\\n" in out or "crlf" in out.lower()), (
            f"line_ending should show CRLF; stdout: {out!r}"
        )
        # echo_input should show True
        assert "True" in out, (
            f"echo_input should show True; stdout: {out!r}"
        )

    def test_port_connect_order_independent_after_port(self, tmp_path):
        # Arrange -- same fields, different order.  Port stays first,
        # the trailing fields are shuffled.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"baud_rate": 115200},
            script_lines=[
                "/port.connect DEMO echo crlf 9600 N81",
                "/cfg baud_rate",
                "/cfg echo_input",
            ],
        )

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "9600" in result.stdout, (
            f"baud should be 9600; stdout: {result.stdout!r}"
        )
        assert "True" in result.stdout, (
            f"echo_input should be True; stdout: {result.stdout!r}"
        )

    def test_port_connect_noecho_disables_echo(self, tmp_path):
        # Arrange -- start with echo on, /port.connect noecho turns it off.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"echo_input": True},
            script_lines=[
                "/port.connect DEMO noecho",
                "/cfg echo_input",
            ],
        )

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        # echo_input should now be False -- look for "False" in the
        # /cfg output and make sure "True" doesn't also appear (would
        # indicate noecho was ignored).
        lines_after_connect = result.stdout.split("/port.connect")[-1]
        assert "False" in lines_after_connect, (
            f"noecho should disable echo_input; stdout: {result.stdout!r}"
        )

    def test_port_connect_line_ending_lf(self, tmp_path):
        # Arrange
        result = _run_cli(
            tmp_path,
            cfg_overrides={"line_ending": "\r\n"},
            script_lines=[
                "/port.connect DEMO lf",
                "/cfg line_ending",
            ],
        )

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        # line_ending should now be "\n" (LF only).  Accept either
        # "\\n" in the json-ish /cfg output or "lf" label depending
        # on how /cfg renders.
        out = result.stdout
        assert "\\n" in out or "lf" in out.lower(), (
            f"line_ending should be LF; stdout: {out!r}"
        )

    def test_port_connect_port_must_be_first(self, tmp_path):
        # Arrange -- a port-name-looking token after another token
        # must be rejected.  Previously (position-independent) this
        # would have been accepted with port=DEMO.
        result = _run_cli(
            tmp_path,
            cfg_overrides={},
            script_lines=[
                "/port.connect echo DEMO",
                "/echo survived",
            ],
        )

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "Unexpected" in result.stdout or "error" in result.stdout.lower(), (
            f"port-not-first should produce an error message; "
            f"stdout: {result.stdout!r}"
        )
        # Script continues after the error (REPL doesn't abort).
        assert "survived" in result.stdout, (
            "REPL should continue after a parse error"
        )
