"""Tests for ``ReplEngine.add_post_dispatch_observer`` and friends.

The observer pair (``add_post_dispatch_observer`` /
``remove_post_dispatch_observer``) is the foundation for
``/run.record``; it's also the path future features (audit log,
``/!!`` repeat-last, MCP event streams) will subscribe to.

Contract verified here:

- Observer fires after every ``dispatch()`` call.
- Fires for failed dispatches too -- the subscriber decides what
  to keep.
- Multiple observers all fire.
- An exception inside one observer doesn't break dispatch and
  doesn't prevent later observers from firing.
- ``remove_post_dispatch_observer`` is idempotent.
"""

from __future__ import annotations

import json

import pytest

from termapy.repl import ReplEngine


@pytest.fixture
def engine(tmp_path):
    """Minimal ReplEngine wired to capture output."""
    cfg = {"port": "COM4", "baud_rate": 115200, "line_ending": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output: list = []
    eng = ReplEngine(
        cfg, str(config_path), lambda t, c=None: output.append((t, c)),
    )
    flags = eng.ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    return eng


class TestPostDispatchObserver:
    def test_observer_fires_on_success(self, engine):
        # Arrange
        seen: list[tuple[str, bool]] = []
        engine.add_post_dispatch_observer(
            lambda line, result: seen.append((line, result.success))
        )

        # Act -- /app.ver is a no-op safe command that succeeds without
        # requiring any capabilities, so the test engine's bare context
        # can dispatch it cleanly.
        engine.dispatch("app.ver")

        # Assert
        assert seen == [("app.ver", True)], (
            f"observer should fire with (line, success=True), got {seen}"
        )

    def test_observer_fires_on_failed_dispatch(self, engine):
        # Arrange -- an unknown command produces CmdResult.fail().
        seen: list[tuple[str, bool]] = []
        engine.add_post_dispatch_observer(
            lambda line, result: seen.append((line, result.success))
        )

        # Act
        engine.dispatch("definitely_not_a_command_xyz")

        # Assert -- subscriber receives the failure (recorder will
        # ignore; an audit log might keep it).
        assert len(seen) == 1, "exactly one fire"
        line, success = seen[0]
        assert line == "definitely_not_a_command_xyz", "raw line preserved"
        assert success is False, "failure surfaced to observer"

    def test_multiple_observers_all_fire(self, engine):
        # Arrange
        a: list[str] = []
        b: list[str] = []
        engine.add_post_dispatch_observer(lambda line, _r: a.append(line))
        engine.add_post_dispatch_observer(lambda line, _r: b.append(line))

        # Act
        engine.dispatch("app.ver")

        # Assert
        assert a == ["app.ver"], "first observer fired"
        assert b == ["app.ver"], "second observer fired"

    def test_observer_exception_does_not_break_dispatch(self, engine):
        # Arrange -- one bad observer, one good observer registered after.
        good: list[str] = []

        def _bad(_line, _r):
            raise RuntimeError("observer is broken")

        engine.add_post_dispatch_observer(_bad)
        engine.add_post_dispatch_observer(lambda line, _r: good.append(line))

        # Act -- dispatch must not raise.
        result = engine.dispatch("app.ver")

        # Assert -- dispatch returned cleanly AND the second observer
        # still fired despite the first one exploding.
        assert result.success, "dispatch completed despite broken observer"
        assert good == ["app.ver"], (
            "later observers still fire after an earlier one explodes"
        )

    def test_remove_observer_stops_firing(self, engine):
        # Arrange
        seen: list[str] = []
        token = engine.add_post_dispatch_observer(
            lambda line, _r: seen.append(line)
        )

        # Act
        engine.dispatch("app.ver")
        engine.remove_post_dispatch_observer(token)
        engine.dispatch("app.ver")

        # Assert -- only the first dispatch fired the observer.
        assert seen == ["app.ver"], "observer removed before second dispatch"

    def test_remove_observer_is_idempotent(self, engine):
        # Arrange / Act -- removing an unregistered observer must not raise.
        engine.remove_post_dispatch_observer(lambda _l, _r: None)

        # Assert -- no exception means the test passes.  Sanity-check
        # the engine is still operable.
        result = engine.dispatch("app.ver")
        assert result.success, "engine still works after spurious remove"

    def test_observer_fires_for_empty_line(self, engine):
        # Arrange -- empty dispatch (user typed just the prefix).
        seen: list[tuple[str, bool]] = []
        engine.add_post_dispatch_observer(
            lambda line, result: seen.append((line, result.success))
        )

        # Act
        engine.dispatch("")

        # Assert -- observer sees the empty line.  Important for the
        # recorder's "preserve blank lines for readability" behavior.
        assert seen == [("", True)], (
            f"observer fires for empty dispatch, got {seen}"
        )

    def test_observer_token_is_the_callable_itself(self, engine):
        # Arrange / Act -- token returned by add_* IS the callable, so
        # remove_* can take either the original ref or the returned token.
        def _obs(_l, _r):
            pass

        token = engine.add_post_dispatch_observer(_obs)

        # Assert
        assert token is _obs, "token is the same callable (deregister via either)"
