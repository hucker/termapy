"""The demo traffic plugin's /traffic.snoop, on the per-config plugin path --demo uses.

The demo plugins are what plugin authors copy, so they read bytes with the
shared reader (``protocol.parse_data``: hex in any spelling or case, quoted
text) and print hex uppercase.  Until 2026-10-13 no test drove
``/traffic.snoop`` at all; the move to the shared reader left four uses of a
renamed variable in its match and timeout messages, caught only by ruff and
ty.  The rig copies the plugin into the config's ``plugin/`` folder, where the
engine loads it at config load exactly as ``--demo`` sets it up, and connects
the DEMO port (the ``tests/test_port_commands.py`` shape).
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.demo import FakeSerial

_TRAFFIC = Path(__file__).resolve().parent.parent / "src" / "termapy" / "builtins" / "demo" / "plugin" / "traffic.py"


@pytest.fixture
def cli(tmp_path):
    """A connected CLI on the DEMO port whose config folder holds the demo traffic plugin."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200}}
    config_path = tmp_path / "rig" / "rig.cfg"
    (config_path.parent / "plugin").mkdir(parents=True)
    shutil.copy(_TRAFFIC, config_path.parent / "plugin" / "traffic.py")
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    # What CLITerminal.run() does first: resolve the plugin folders, so the
    # per-config traffic.py registers its commands.
    terminal.repl.fire_lifecycle("on_app_start")
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


def _run_line(cli: CLITerminal, tmp_path: Path, line: str) -> None:
    """Run one line as a script via /run: /traffic.snoop blocks, which only a script may (block_until)."""
    script = tmp_path / "snoop.run"
    script.write_text(line + "\n", encoding="utf-8")
    cli.repl.dispatch_full(f"/run {script}")


def test_snoop_matches_a_quoted_text_pattern(cli, tmp_path, capsys):
    """A quoted-text pattern is matched in the RX stream and reported as uppercase hex."""
    # Arrange -- the device answers AT with OK a moment after snoop starts watching
    port = cli.engine.port_obj
    assert isinstance(port, FakeSerial), "the DEMO engine opens a FakeSerial"
    timer = threading.Timer(0.3, port.write, args=(b"AT\r",))
    capsys.readouterr()

    # Act
    timer.start()
    _run_line(cli, tmp_path, '/traffic.snoop "OK" timeout=5s')
    timer.join()

    # Assert
    out = capsys.readouterr().out
    assert "Matched" in out and "4F4B" in out, f"the pattern, as uppercase hex, matched: {out!r}"


def test_snoop_reads_hex_in_either_case(cli, tmp_path, capsys):
    """A lowercase hex pattern means the same bytes as uppercase; the timeout names it in uppercase."""
    capsys.readouterr()
    _run_line(cli, tmp_path, "/traffic.snoop 7e 7f timeout=50ms")
    assert "waiting for 7E7F" in capsys.readouterr().out, "nothing sends 7E 7F"


def test_snoop_refuses_a_bad_pattern(cli, tmp_path, capsys):
    """A pattern that is neither hex nor quoted text is the shared reader's Parse error."""
    capsys.readouterr()
    _run_line(cli, tmp_path, "/traffic.snoop ZZ")
    assert "Parse error: " in capsys.readouterr().out
