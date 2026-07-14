"""Tests for picker callbacks in ``src/termapy/pickers.py``.

Currently covers the QuickSetup wizard's optional "add desktop
icon" checkbox -- when the user ticks it, the wizard's result
tuple carries ``add_icon=True`` and ``on_quick_setup`` should
dispatch ``/cfg.icon`` immediately after the cfg is loaded.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from termapy import pickers


class _StubApp:
    """Minimal stand-in for SerialTerminal needed by ``on_quick_setup``.

    Records dispatches into a list so tests can assert what
    ``app.ctx.dispatch`` was called with.  Skips the real connect
    path -- that touches Textual / serial which we don't have.
    """

    def __init__(self) -> None:
        self.dispatched: list[str] = []
        self.config_results: list[tuple] = []
        self.connect_calls: int = 0
        self.ctx = SimpleNamespace(dispatch=self.dispatched.append)

    def _on_config_result(self, result: tuple) -> None:
        self.config_results.append(result)

    def _connect(self) -> None:
        self.connect_calls += 1


@pytest.fixture
def _quick_setup_env(tmp_path, monkeypatch):
    """Redirect cfg paths into tmp_path so on_quick_setup never
    touches the user's real termapy_cfg/."""
    def _fake_cfg_path_for_name(name: str) -> Path:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{name}.cfg"

    monkeypatch.setattr(pickers, "cfg_path_for_name", _fake_cfg_path_for_name)
    monkeypatch.setattr(pickers, "cfg_data_dir", lambda p: Path(p).parent)
    return tmp_path


def test_quick_setup_with_icon_dispatches_cfg_icon(_quick_setup_env):
    # Arrange -- the user ticked the "add desktop icon" checkbox,
    # so the wizard's tuple ends in add_icon=True.
    app = _StubApp()
    result = ("connect", "demo", "COM1", 115200, False, True)

    # Act
    pickers.on_quick_setup(app, result)

    # Assert -- /cfg.icon must have been dispatched exactly once,
    # right after the cfg was loaded.
    assert "/cfg.icon" in app.dispatched, (
        "checkbox=True triggers /cfg.icon dispatch"
    )
    assert app.config_results, "cfg was loaded before dispatch"


def test_quick_setup_without_icon_skips_dispatch(_quick_setup_env):
    # Arrange -- checkbox left off; tuple ends in add_icon=False.
    app = _StubApp()
    result = ("connect", "demo", "COM1", 115200, False, False)

    # Act
    pickers.on_quick_setup(app, result)

    # Assert
    assert "/cfg.icon" not in app.dispatched, (
        "no dispatch when checkbox was not ticked"
    )


def test_quick_setup_advanced_path_never_dispatches(_quick_setup_env):
    # Arrange -- Advanced action opens the ConfigEditor; we don't
    # offer the icon there even if the checkbox was somehow ticked,
    # because the user is going into power-edit mode.
    app = _StubApp()
    pushed: list = []
    app.push_screen = lambda screen, callback=None: pushed.append(
        (screen, callback)
    )
    result = ("advanced", "demo", "COM1", 115200, False, True)

    # Act
    pickers.on_quick_setup(app, result)

    # Assert
    assert "/cfg.icon" not in app.dispatched, (
        "Advanced path never auto-dispatches /cfg.icon"
    )
    assert pushed, "Advanced path pushed the ConfigEditor"


def test_quick_setup_cancel_does_nothing(_quick_setup_env):
    # Arrange -- user canceled the modal.
    app = _StubApp()

    # Act
    pickers.on_quick_setup(app, None)

    # Assert
    assert not app.dispatched, "no dispatch on cancel"
    assert not app.config_results, "no cfg loaded on cancel"


def test_quick_setup_writes_cfg_file_with_expected_contents(
    _quick_setup_env,
):
    # Arrange / Act
    app = _StubApp()
    result = ("connect", "demo", "COM1", 115200, False, False)
    pickers.on_quick_setup(app, result)

    # Assert -- sanity that the rest of the wizard's contract still
    # holds: cfg file is written with the chosen fields and the
    # cfg dict was passed to _on_config_result.
    cfg_path = _quick_setup_env / "demo" / "demo.cfg"
    assert cfg_path.is_file(), "cfg file was written"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["title"] == "demo", "title field carries the cfg name"
    assert data["serial"]["port"] == "COM1", "serial.port carries the selection (post-v22)"
    assert data["serial"]["baud_rate"] == 115200, "serial.baud_rate written (post-v22)"
    assert app.connect_calls == 1, "auto-connect fired (port present)"
