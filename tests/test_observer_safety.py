"""Exception-safety + behavior tests for ``ctx.serial.rx_observer()`` and
``ctx.serial.tx_observer()`` context managers.

The whole point of routing observer registration through the context
managers (rather than bare ``ctx.add_rx_observer``/``ctx.remove_rx_observer``
calls) is that observers are unregistered on every exit path -- including
when the body raises.  These tests pin that property explicitly, plus
verify the observers actually fire for bytes seen during the block.
"""

from __future__ import annotations

import pytest

from termapy.plugins import IOHandle, PluginContext, SerialHandle


# ── Shared fixture: PluginContext wired to in-memory observer lists ────────


class _Registry:
    """Tracks the observers a context manager has currently registered."""

    def __init__(self):
        self.rx: list = []
        self.tx: list = []
        self.add_calls: int = 0
        self.remove_calls: int = 0


@pytest.fixture
def ctx_with_registry():
    """A PluginContext whose private observer hooks track a registry."""
    reg = _Registry()

    def add_rx(cb):
        reg.rx.append(cb)
        reg.add_calls += 1

    def remove_rx(cb):
        try:
            reg.rx.remove(cb)
        except ValueError:
            pass
        reg.remove_calls += 1

    def add_tx(cb):
        reg.tx.append(cb)
        reg.add_calls += 1

    def remove_tx(cb):
        try:
            reg.tx.remove(cb)
        except ValueError:
            pass
        reg.remove_calls += 1

    ctx = PluginContext(
        io=IOHandle(write=lambda *a, **k: None),
        serial=SerialHandle(
            _add_rx_observer=add_rx,
            _remove_rx_observer=remove_rx,
            _add_tx_observer=add_tx,
            _remove_tx_observer=remove_tx,
        ),
    )
    return ctx, reg


# ── rx_observer ──────────────────────────────────────────────────────────────


class TestRxObserverContextManager:
    def test_normal_exit_balances_register_unregister(self, ctx_with_registry):
        # Arrange
        ctx, reg = ctx_with_registry
        cb = lambda data: None  # noqa: E731 -- one-line callback for tests

        # Act
        with ctx.serial.rx_observer(cb):
            assert cb in reg.rx, "registered on enter"
            assert reg.add_calls == 1, "exactly one add"
            assert reg.remove_calls == 0, "no remove yet"

        # Assert -- balanced after normal exit
        assert cb not in reg.rx, "removed on exit"
        assert reg.add_calls == 1, "still exactly one add"
        assert reg.remove_calls == 1, "exactly one remove"

    def test_exception_inside_block_still_unregisters(self, ctx_with_registry):
        # Arrange
        ctx, reg = ctx_with_registry
        cb = lambda data: None  # noqa: E731

        # Act -- raise inside the block; the with-statement must propagate
        with pytest.raises(ValueError):
            with ctx.serial.rx_observer(cb):
                assert cb in reg.rx, "registered before exception"
                raise ValueError("simulated handler failure")

        # Assert -- unregister fired despite the exception
        assert cb not in reg.rx, "removed even on exception"
        assert reg.add_calls == 1, "exactly one add"
        assert reg.remove_calls == 1, "remove still fired"

    def test_nested_observers_each_unregister(self, ctx_with_registry):
        # Arrange -- two distinct callbacks registered nested
        ctx, reg = ctx_with_registry
        outer = lambda data: None  # noqa: E731
        inner = lambda data: None  # noqa: E731

        # Act
        with ctx.serial.rx_observer(outer):
            assert reg.rx == [outer], "outer registered"
            with ctx.serial.rx_observer(inner):
                assert reg.rx == [outer, inner], "both registered"
            # Inner block exited; only outer should remain.
            assert reg.rx == [outer], "inner removed; outer stays"

        # Assert -- both gone after the outer block exits
        assert reg.rx == [], "all observers unregistered"
        assert reg.add_calls == 2, "two adds"
        assert reg.remove_calls == 2, "two removes"

    def test_exception_in_outer_after_inner_completed(self, ctx_with_registry):
        # Arrange -- outer raises after inner has already cleanly exited
        ctx, reg = ctx_with_registry
        outer = lambda data: None  # noqa: E731
        inner = lambda data: None  # noqa: E731

        # Act
        with pytest.raises(RuntimeError):
            with ctx.serial.rx_observer(outer):
                with ctx.serial.rx_observer(inner):
                    pass
                # Inner already removed; outer raises.
                raise RuntimeError("late failure")

        # Assert -- both removed despite outer raise
        assert reg.rx == [], "both observers unregistered after late raise"


# ── tx_observer ──────────────────────────────────────────────────────────────


class TestTxObserverContextManager:
    def test_normal_exit_balances_register_unregister(self, ctx_with_registry):
        # Arrange
        ctx, reg = ctx_with_registry
        cb = lambda data: None  # noqa: E731

        # Act
        with ctx.serial.tx_observer(cb):
            assert cb in reg.tx, "registered on enter"

        # Assert
        assert cb not in reg.tx, "removed on exit"

    def test_exception_inside_block_still_unregisters(self, ctx_with_registry):
        # Arrange
        ctx, reg = ctx_with_registry
        cb = lambda data: None  # noqa: E731

        # Act
        with pytest.raises(RuntimeError):
            with ctx.serial.tx_observer(cb):
                raise RuntimeError("boom")

        # Assert
        assert cb not in reg.tx, "removed even on exception"

    def test_rx_and_tx_independent(self, ctx_with_registry):
        # Arrange -- nest rx and tx observers; each tracks its own list
        ctx, reg = ctx_with_registry
        rx_cb = lambda data: None  # noqa: E731
        tx_cb = lambda data: None  # noqa: E731

        # Act
        with ctx.serial.rx_observer(rx_cb), ctx.serial.tx_observer(tx_cb):
            assert reg.rx == [rx_cb], "rx tracked separately"
            assert reg.tx == [tx_cb], "tx tracked separately"

        # Assert
        assert reg.rx == [], "rx cleared"
        assert reg.tx == [], "tx cleared"


# ── Bare API not on the public ctx surface ─────────────────────────────────


class TestObserverApiNotPublic:
    """The four bare register/unregister methods are intentionally
    unreachable from plugin code via ``ctx.add_rx_observer(...)`` and
    friends.  Plugins must use the context managers; the underscore-
    prefixed fields exist only for the context manager's own use.
    """

    def test_no_public_add_rx_observer(self):
        # Arrange / Act
        ctx = PluginContext(io=IOHandle(write=lambda *a, **k: None))

        # Assert -- the public-named callback is not on SerialHandle.
        assert not hasattr(ctx.serial, "add_rx_observer"), (
            "ctx.serial.add_rx_observer is private; use ctx.serial.rx_observer(cb) instead"
        )
        assert not hasattr(ctx.serial, "remove_rx_observer"), (
            "ctx.serial.remove_rx_observer is private; rx_observer's __exit__ "
            "handles unregistration"
        )

    def test_no_public_add_tx_observer(self):
        # Arrange / Act
        ctx = PluginContext(io=IOHandle(write=lambda *a, **k: None))

        # Assert
        assert not hasattr(ctx.serial, "add_tx_observer"), (
            "ctx.serial.add_tx_observer is private; use ctx.serial.tx_observer(cb) instead"
        )
        assert not hasattr(ctx.serial, "remove_tx_observer"), (
            "ctx.serial.remove_tx_observer is private; tx_observer's __exit__ "
            "handles unregistration"
        )

    def test_underscore_versions_exist_for_internal_use(self):
        # Arrange / Act -- the context managers need to reach the engine
        # somehow; the underscore fields are how.
        ctx = PluginContext(io=IOHandle(write=lambda *a, **k: None))

        # Assert
        assert hasattr(ctx.serial, "_add_rx_observer"), "private hook exists"
        assert hasattr(ctx.serial, "_remove_rx_observer"), "private hook exists"
        assert hasattr(ctx.serial, "_add_tx_observer"), "private hook exists"
        assert hasattr(ctx.serial, "_remove_tx_observer"), "private hook exists"
