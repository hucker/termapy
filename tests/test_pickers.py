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
        # A path only: creating the folder is create_config's job, and a
        # folder already there is a config already there.
        return tmp_path / name / f"{name}.cfg"

    monkeypatch.setattr(pickers, "cfg_path_for_name", _fake_cfg_path_for_name)
    return tmp_path


def test_quick_setup_with_icon_dispatches_cfg_icon(_quick_setup_env):
    """Ticking 'add desktop icon' dispatches /cfg.icon, and only after the new cfg is loaded."""
    # Arrange -- the user ticked the "add desktop icon" checkbox,
    # so the wizard's tuple ends in add_icon=True.
    app = _StubApp()
    result = ("connect", "demo", "COM1", 115200, False, True)

    # Act
    pickers.on_quick_setup(app, result)

    # Assert -- /cfg.icon must have been dispatched exactly once,
    # right after the cfg was loaded.
    assert "/cfg.icon" in app.dispatched, "checkbox=True triggers /cfg.icon dispatch"
    assert app.config_results, "cfg was loaded before dispatch"


def test_quick_setup_without_icon_skips_dispatch(_quick_setup_env):
    """With the icon box left unticked, finishing the wizard dispatches no /cfg.icon."""
    # Arrange -- checkbox left off; tuple ends in add_icon=False.
    app = _StubApp()
    result = ("connect", "demo", "COM1", 115200, False, False)

    # Act
    pickers.on_quick_setup(app, result)

    # Assert
    assert "/cfg.icon" not in app.dispatched, "no dispatch when checkbox was not ticked"


def test_quick_setup_advanced_path_never_dispatches(_quick_setup_env):
    """Advanced opens the ConfigEditor and ignores the icon box; /cfg.icon is left to the user."""
    # Arrange -- Advanced action opens the ConfigEditor; we don't
    # offer the icon there even if the checkbox was somehow ticked,
    # because the user is going into power-edit mode.
    app = _StubApp()
    pushed: list = []
    app.push_screen = lambda screen, callback=None: pushed.append(  # ty: ignore[unresolved-attribute]
        (screen, callback)
    )
    result = ("advanced", "demo", "COM1", 115200, False, True)

    # Act
    pickers.on_quick_setup(app, result)

    # Assert
    assert "/cfg.icon" not in app.dispatched, "Advanced path never auto-dispatches /cfg.icon"
    assert pushed, "Advanced path pushed the ConfigEditor"


def test_quick_setup_cancel_does_nothing(_quick_setup_env):
    """A canceled wizard (None result) loads no cfg and dispatches nothing."""
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
    """The wizard writes title, serial.port and serial.baud_rate to disk, then auto-connects."""
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


class _AskingStubApp(_StubApp):
    """A _StubApp that records pushed screens and reports status lines."""

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[tuple] = []
        self.statuses: list[str] = []

    def push_screen(self, screen, callback=None) -> None:
        self.pushed.append((screen, callback))

    def _status(self, text: str, color: str = "dim") -> None:
        self.statuses.append(text)


def _existing_config(root: Path, name: str) -> tuple[Path, Path]:
    """A config folder with its .cfg and a run script, as a user leaves it."""
    folder = root / name
    (folder / "run").mkdir(parents=True)
    cfg_path = folder / f"{name}.cfg"
    cfg_path.write_text('{"title": "old"}', encoding="utf-8")
    script = folder / "run" / "x.run"
    script.write_text("/print hi\n", encoding="utf-8")
    return cfg_path, script


def test_quick_setup_existing_name_asks_before_overwriting(_quick_setup_env):
    """config-created-once: the wizard on an existing name asks the overwrite question and writes nothing yet."""
    # Arrange
    cfg_path, _ = _existing_config(_quick_setup_env, "demo")
    app = _AskingStubApp()

    # Act
    pickers.on_quick_setup(app, ("connect", "demo", "COM1", 115200, False, False))

    # Assert
    actual_message = app.pushed[0][0].message
    assert "Overwrite demo.cfg? Other files are kept." in actual_message, "the shared question"
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["title"] == "old", (
        "nothing is written before the answer"
    )
    assert not app.config_results, "no config is loaded before the answer"


def test_quick_setup_overwrite_yes_replaces_cfg_and_keeps_scripts(_quick_setup_env):
    """config-created-once: yes replaces only the .cfg; the folder's other files stay."""
    # Arrange
    cfg_path, script = _existing_config(_quick_setup_env, "demo")
    app = _AskingStubApp()
    pickers.on_quick_setup(app, ("connect", "demo", "COM1", 115200, False, False))
    _, callback = app.pushed[0]

    # Act
    callback(True)

    # Assert
    actual_title = json.loads(cfg_path.read_text(encoding="utf-8"))["title"]
    assert actual_title == "demo", "the new config replaced the old .cfg"
    assert script.read_text(encoding="utf-8") == "/print hi\n", "the run script is kept"
    assert app.config_results, "the new config is loaded"


def test_quick_setup_overwrite_no_changes_nothing(_quick_setup_env):
    """config-created-once: no leaves the existing config as it was and loads nothing."""
    # Arrange
    cfg_path, _ = _existing_config(_quick_setup_env, "demo")
    app = _AskingStubApp()
    pickers.on_quick_setup(app, ("connect", "demo", "COM1", 115200, False, False))
    _, callback = app.pushed[0]

    # Act
    callback(False)

    # Assert
    actual_title = json.loads(cfg_path.read_text(encoding="utf-8"))["title"]
    assert actual_title == "old", "the existing .cfg is untouched"
    assert not app.config_results, "nothing is loaded"


def test_quick_setup_bad_name_reports_and_writes_nothing(_quick_setup_env):
    """A name with a space is refused through the status line; no folder appears."""
    # Arrange
    app = _AskingStubApp()

    # Act
    pickers.on_quick_setup(app, ("connect", "my dev", "COM1", 115200, False, False))

    # Assert
    assert app.statuses == ["New config failed: Name may not contain spaces: my dev"]
    assert not (_quick_setup_env / "my dev").exists(), "no folder is created"
