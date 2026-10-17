"""A capture owns its serial claim from start to stop, inside a script or not.

``_stop_capture`` skipped the release while ``repl.in_script`` -- a guard
written for a script-wide proto mode that survived the rename to
``serial_claimed`` -- so after a scripted ``/cap.bin`` the port stayed
claimed: in the TUI until script end, in the CLI and MCP for the whole
session, and every line consumer (``/expect``, profile commands, MCP
responses) went blind (docs/review/2026-10-10-v0.76.0-fable-5.1.md, C3).
Runs on the ``CLITerminal`` DEMO rig (the ``tests/test_mem_commands.py``
shape) because the release lives in ``TerminalHost``, the base the CLI and
MCP share; the TUI's copy in ``capture_view`` is the same one-liner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins.capabilities import STATE_MESSAGES


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port, its cfg folder under tmp_path."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200}}
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


def _script(folder: Path, name: str, *lines: str) -> Path:
    path = folder / name
    path.write_text("".join(line + "\n" for line in lines))
    return path


class TestOneCaptureAtATime:
    def test_a_second_start_is_refused_until_the_first_stops(self, cli):
        """state-answered-by-gate: while a capture runs, the next start is the gate's refusal; after a stop it starts."""
        # Arrange
        first = cli.repl.dispatch("cap.bin a.bin bytes=1000")

        # Act
        second = cli.repl.dispatch("cap.bin b.bin bytes=1000")
        third = cli.repl.dispatch("cap.text c.txt timeout=5s")
        cli.repl.dispatch("cap.stop")
        fourth = cli.repl.dispatch("cap.bin d.bin bytes=1000")
        cli.repl.dispatch("cap.stop")

        # Assert
        assert first.success is True, first.error
        refused = [(result.success, result.error) for result in (second, third)]
        assert refused == [(False, STATE_MESSAGES["capture_idle"])] * 2, (
            "refused twice in a row, each kind"
        )
        assert fourth.success is True, f"the stop frees the next start: {fourth.error}"


class TestCaptureOwnsItsClaim:
    def test_scripted_stop_releases_the_claim(self, cli, tmp_path):
        """/cap.bin then /cap.stop inside one script leaves the port unclaimed."""
        # Arrange
        script = _script(tmp_path, "cap.run", "/cap.bin t.bin bytes=1000", "/cap.stop")

        # Act
        cli.repl.run_script(script)

        # Assert
        assert cli.engine.serial_claimed is False, "the claim died with the capture, not the script"

    def test_claim_outlives_the_script_while_the_capture_runs(self, cli, tmp_path):
        """A capture still running at script end keeps its claim until its own stop."""
        # Arrange / Act
        cli.repl.run_script(_script(tmp_path, "start.run", "/cap.bin t.bin bytes=1000"))
        held = cli.engine.serial_claimed
        cli.repl.run_script(_script(tmp_path, "stop.run", "/cap.stop"))

        # Assert
        assert held is True, "an active capture holds the claim past script end"
        assert cli.engine.serial_claimed is False, "released by its own stop"
