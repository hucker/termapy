"""bare-declared: a bare call's behavior is a declaration the dispatcher honors.

``Command(picker=...)`` opens a picker on a bare call in a host that has
pickers; everywhere else the handler runs its own bare path.  A plain
``ReplEngine`` stands in for the host: ``ctx.internal.open_picker`` is the
seam a TUI host wires (``app_hooks.register_tui_hooks``), so a recording
function there is the host, not a mock of the engine.
"""

from __future__ import annotations

import pytest

from termapy.plugins import CmdResult, PluginInfo
from termapy.repl import ReplEngine


class _PickerHost:
    """Records which picker the dispatcher asked the host to open."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    def open_picker(self, name: str) -> CmdResult:
        """Record ``name`` and report success, as the TUI's picker does."""
        self.opened.append(name)
        return CmdResult.ok(value=name)


@pytest.fixture
def engine_with_pickers():
    """A bare engine whose host offers pickers."""
    eng = ReplEngine({}, "", lambda text, color=None: None)
    host = _PickerHost()
    eng.ctx.internal.open_picker = host.open_picker
    return eng, host


def test_a_bare_declared_command_opens_its_picker(engine_with_pickers):
    """bare-declared: bare /cfg and /port open their pickers instead of running the handler."""
    # Arrange
    eng, host = engine_with_pickers

    # Act
    results = [eng.dispatch("cfg"), eng.dispatch("port")]

    # Assert
    assert all(result.success for result in results), "both bare calls succeed"
    assert host.opened == ["cfg", "port"], "each opened its declared picker"


def test_an_argument_reaches_the_handler_not_the_picker(engine_with_pickers):
    """/cfg baud_rate is a query, never the picker: only a bare call is intercepted."""
    # Arrange
    eng, host = engine_with_pickers

    # Act
    eng.dispatch("cfg baud_rate")

    # Assert
    assert host.opened == [], "an argument goes to the handler"


def _probe(calls: list[str]) -> PluginInfo:
    """A picker= command whose handler records each call it receives."""
    def _handler(ctx, args: str) -> CmdResult:
        calls.append(args)
        return CmdResult.ok(value="handler")
    return PluginInfo(name="probe", args="{x}", help="h", handler=_handler, picker="run")


def test_without_pickers_the_handler_runs_its_bare_path():
    """A host without pickers (CLI, MCP) hands a bare call to the handler's own fallback."""
    # Arrange -- open_picker stays None, as in the CLI
    eng = ReplEngine({}, "", lambda text, color=None: None)
    calls: list[str] = []
    eng.register_plugin(_probe(calls))

    # Act
    result = eng.dispatch("probe")

    # Assert
    assert result.value == "handler", "the handler answered the bare call"
    assert calls == [""], "it saw the bare call"


def test_with_pickers_the_handler_never_sees_a_bare_call(engine_with_pickers):
    """In a host with pickers the bare call is the picker's; an argument still reaches the handler."""
    # Arrange
    eng, host = engine_with_pickers
    calls: list[str] = []
    eng.register_plugin(_probe(calls))

    # Act
    eng.dispatch("probe")
    eng.dispatch("probe x")

    # Assert
    assert host.opened == ["run"], "the bare call opened the declared picker"
    assert calls == ["x"], "only the call with an argument reached the handler"


def test_an_unknown_picker_fails_at_construction():
    """A typo in picker= fails loud at load, naming the picker, instead of opening nothing."""
    with pytest.raises(ValueError, match="unknown picker 'runs'"):
        PluginInfo(name="x", args="", help="h", handler=lambda ctx, args: None, picker="runs")
