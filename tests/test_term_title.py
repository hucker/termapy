"""The terminal's title follows the session, and /term.title names it (registry ``terminal-title-follows-the-session``).

Driven on the CLI DEMO rig (``conftest.py``'s ``cli``): what the host records
as the title is what it hands the terminal, so the record is what these
tests read; Rich writes the sequence only to a real terminal.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from termapy.cli import CLITerminal

pytestmark = pytest.mark.rule("terminal-title-follows-the-session")


def _another_cli(cli, tmp_path, **kwargs) -> CLITerminal:
    """A second CLI on the rig's config as written, built as the rig is but not connected."""
    cfg = json.loads(Path(cli.config_path).read_text())
    terminal = CLITerminal(cfg, cli.config_path, no_color=True, term_width=120, **kwargs)
    terminal.repl.global_root = tmp_path
    return terminal


def _recording_console() -> tuple[Console, io.StringIO]:
    """A console that counts as a terminal and keeps every byte written to it."""
    out = io.StringIO()
    return Console(file=out, force_terminal=True, legacy_windows=False, width=120), out


def test_a_fresh_start_titles_the_terminal_before_anything_runs(cli, tmp_path):
    """A CLI just built, nothing typed and no port open, already carries the config's name: a load ends where this start does."""
    fresh = _another_cli(cli, tmp_path)
    assert fresh.terminal_title == "rig", fresh.terminal_title


def test_a_one_shot_leaves_the_shells_title_alone(cli, tmp_path):
    """An interactive CLI hands its title to the terminal; -e and --run, one command in the user's shell, write none."""
    # Arrange
    interactive = _another_cli(cli, tmp_path)
    oneshot = _another_cli(cli, tmp_path, exec_cmd="/term.info")
    interactive.console, interactive_out = _recording_console()
    oneshot.console, oneshot_out = _recording_console()

    # Act
    interactive._refresh_terminal_title()
    oneshot._refresh_terminal_title()

    # Assert
    assert interactive_out.getvalue() == "\x1b]0;rig\x07", "the OSC 0 title sequence, once"
    assert oneshot_out.getvalue() == "", "a one-shot writes no title"
    assert oneshot.terminal_title == "rig", "the record stands, so /term.title still answers"


def test_the_title_follows_connect_disconnect_and_load(cli):
    """Connected it is the config's name with the port and settings; disconnected the name alone; a load sets it again."""
    # Arrange / Act / Assert -- the rig is connected to DEMO at 115200
    assert cli.terminal_title == "rig DEMO 115200 8N1", cli.terminal_title
    cli._disconnect()
    assert cli.terminal_title == "rig", "disconnected, the config's name alone"
    assert cli._connect() is True
    assert cli.terminal_title == "rig DEMO 115200 8N1", "connected again, the port is back"


def test_term_title_keeps_a_session_title_and_auto_returns_it(cli):
    """/term.title bench-A is the title until auto, and the bare form shows whichever stands."""
    # Act
    set_result = cli.repl.dispatch_full("/term.title bench-A")
    shown = cli.repl.dispatch_full("/term.title")
    after_connect = (cli._disconnect(), cli._connect(), cli.terminal_title)[2]
    auto = cli.repl.dispatch_full("/term.title auto")

    # Assert
    assert (set_result.value, shown.value) == ("bench-A", "bench-A"), "set and shown"
    assert after_connect == "bench-A", "a reconnect keeps the session title"
    assert auto.value == "rig DEMO 115200 8N1", "auto is the config's own again"


def test_a_config_load_clears_a_session_title(cli):
    """A load resets the session, the title with it: the loaded config's own title stands."""
    cli.repl.dispatch_full("/term.title bench-A")
    # The host's load, which every /cfg.load route ends in; the rig's root is not a config root.
    result = cli._switch_to_cfg_path(cli.config_path)
    assert result.success, result.error
    assert cli.terminal_title.startswith("rig"), cli.terminal_title
    assert "bench-A" not in cli.terminal_title, "the override did not survive the load"


def test_term_title_needs_a_terminal(cli):
    """The command declares the console capability, so MCP and a browser-served TUI are refused by the gate."""
    plugin = cli.repl._plugins["term.title"]
    assert plugin.needs is not None and plugin.needs.console is True
