"""handler-returns-cmdresult: a handler that returns anything but a CmdResult fails the dispatch.

Until 2026-11-06 the dispatcher turned a handler's ``None`` into
``CmdResult.ok(value="")``, so a forgotten ``return`` read as success and a
script went on after a command that had answered nothing.  No built-in
relied on it (every registered handler declares ``-> CmdResult`` and ty
checks each path; ``tests/test_architecture.py`` pins that), so the
conversion only ever hid a bug in a plugin.  Now dispatch fails it once,
naming the command and what came back.
"""

import json

import pytest

from termapy.plugins import CmdResult
from termapy.repl import ReplEngine


@pytest.fixture
def engine(tmp_path):
    """A ReplEngine on a temp config, its output captured."""
    cfg = {"port": "COM4", "baud_rate": 115200, "eol": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    output: list[tuple[str, str | None]] = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
    eng.ctx.ns("flags")["output_level"] = "verbose"
    return eng, output


def _returns(value):
    """A handler that answers ``value``, whatever it is."""

    def _handler(ctx, args):
        """Answer the fixed value."""
        return value

    return _handler


def test_none_fails_naming_the_command(engine):
    """A handler that returns None fails, and the error says which command and what it returned."""
    # Arrange
    eng, _ = engine
    eng.register_hook("probe", "", "help", _returns(None))

    # Act
    result = eng.dispatch("probe")

    # Assert
    assert result.success is False, "a missing return is never a success"
    assert result.error == "/probe returned NoneType, not a CmdResult", (
        "names the command and the type"
    )


def test_a_wrong_type_fails_the_same_way(engine):
    """A handler returning a bare string fails with the same message, not an AttributeError."""
    # Arrange
    eng, _ = engine
    eng.register_hook("probe", "", "help", _returns("OK"))

    # Act
    result = eng.dispatch("probe")

    # Assert
    assert result.error == "/probe returned str, not a CmdResult", (
        "one phrasing for every wrong type"
    )


def test_the_failure_is_reported_once(engine):
    """failure-reported-once: dispatch prints the failure one time."""
    # Arrange
    eng, output = engine
    eng.register_hook("probe", "", "help", _returns(None))

    # Act
    eng.dispatch("probe")

    # Assert
    actual = sum("returned NoneType" in text for text, _ in output)
    assert actual == 1, f"printed once, at dispatch's exit: {output!r}"


def test_a_cmdresult_still_passes_through(engine):
    """A handler that returns a CmdResult answers exactly as before, its elapsed time stamped."""
    # Arrange
    eng, _ = engine
    eng.register_hook("probe", "", "help", _returns(CmdResult.ok(value="42")))

    # Act
    result = eng.dispatch("probe")

    # Assert
    assert (result.success, result.value) == (True, "42"), "the handler's own answer"
    assert result.elapsed_s >= 0, "dispatch stamps the time"
