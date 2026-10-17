"""``/log.fingerprint``: the session provenance report and each of its sections.

Runs on the ``CLITerminal`` DEMO rig.  The CLI has no session log, so
the report is read through ``--show``, which echoes it to the terminal;
the handler had no test at all until this file (2026-10-12).  Since
2026-10-16 the command declares ``session_log``, which the CLI lacks, so
the rig grants it: these tests are about the report's content, not about
which frontend may write it.
"""

from __future__ import annotations

import json

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import CapabilitySet


def _make(tmp_path, connect: bool) -> CLITerminal:
    """A CLI on the DEMO port, connected or not, its cfg folder under tmp_path."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200}}
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    caps = terminal.ctx.capabilities.union(CapabilitySet(session_log=True))
    terminal.ctx.capabilities = caps
    terminal.ctx.fs.capabilities = caps
    if connect:
        assert terminal._connect() is True, "the DEMO port opens"
    return terminal


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port."""
    terminal = _make(tmp_path, connect=True)
    yield terminal
    terminal._disconnect()


def _section(out: str, title: str) -> list[str]:
    """The indented lines under ``title:`` up to the next blank line."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == title) + 1
    body = []
    for line in lines[start:]:
        if not line.strip():
            break
        body.append(line.strip())
    return body


class TestFingerprint:

    def test_show_echoes_every_section(self, cli, capsys, tmp_path):
        """--show prints the report: identity, terminal, config, serial port, and runtime flags, framed by markers."""
        # Act
        result = cli.repl.dispatch_full("/log.fingerprint --show")

        # Assert
        out = capsys.readouterr().out
        assert result.success is True, result.error
        assert "=== Termapy session fingerprint ===" in out and "=== end fingerprint ===" in out
        for key in ("Termapy version", "Python", "Platform", "OS name", "Hostname"):
            assert key in out, f"{key} line"
        config = _section(out, "Config:")
        assert any(line.startswith("Config name") and line.endswith("rig") for line in config), config
        assert any(line.startswith("Config path") and line.endswith("rig.cfg") for line in config), config
        assert any(line.startswith("Line ending") and line.endswith("'\\r'") for line in config), config
        serial = _section(out, "Serial port:")
        assert "Connected           True" in serial, serial
        assert any(line.startswith("Port name") and line.endswith("DEMO") for line in serial), serial
        assert any(line.startswith("Baud rate") and line.endswith("115200") for line in serial), serial
        assert any(line.startswith("DTR") for line in serial), "control lines are reported"
        assert "Runtime flags:" in out
        assert result.value == str(out.count("\n", out.index("=== Termapy"), out.index("=== end") + 1) + 1), (
            "the value is the line count of the report"
        )

    def test_without_show_only_the_confirmation_prints(self, cli, capsys):
        """Without --show the terminal gets one confirmation line and none of the report."""
        result = cli.repl.dispatch_full("/log.fingerprint")
        out = capsys.readouterr().out
        assert result.success is True, result.error
        assert "Fingerprint written to log" in out
        assert "=== Termapy session fingerprint ===" not in out

    def test_disconnected_port_is_reported_as_such(self, tmp_path, capsys):
        """With no port open the serial section says so instead of reading parameters."""
        terminal = _make(tmp_path, connect=False)
        terminal.repl.dispatch_full("/log.fingerprint --show")
        serial = _section(capsys.readouterr().out, "Serial port:")
        assert "Connected           False" in serial, serial
        assert any("port handle" in line and "not available" in line for line in serial), serial

    def test_terminal_section_lists_a_recognized_variable(self, cli, capsys, monkeypatch):
        """A recognized terminal variable appears under Terminal: with its value."""
        # The environment is the input here, not a code path being replaced.
        monkeypatch.setenv("TERM_PROGRAM", "termapy-probe")
        cli.repl.dispatch_full("/log.fingerprint --show")
        terminal = _section(capsys.readouterr().out, "Terminal:")
        assert any(line.startswith("TERM_PROGRAM") and line.endswith("termapy-probe") for line in terminal), terminal

    def test_runtime_flags_list_the_session_toggles(self, cli, capsys):
        """Flags the session has set appear under Runtime flags:, one per line."""
        cli.repl.ctx.ns("flags")["hex"] = True
        cli.repl.dispatch_full("/log.fingerprint --show")
        flags = _section(capsys.readouterr().out, "Runtime flags:")
        assert any(line.startswith("hex") and line.endswith("True") for line in flags), flags
