"""Exception-safety tests for the ``ctx.serial.io()`` context manager.

The whole point of routing every synchronous serial-read path through
``with ctx.serial.io():`` (rather than bare ``engine.serial_claimed = True``
calls) is that the port releases on every exit path -- including when
the body raises.  These tests pin that property explicitly so a future
refactor can't quietly break it.
"""

from __future__ import annotations

import json

import pytest

from termapy.plugins import (
    CapabilitySet,
    EngineHandle,
    IOHandle,
    PluginContext,
    SerialHandle,
)
from termapy.repl import ReplEngine


# ── Shared fixture: minimal real engine + ctx with the serial_claim/release
#    callbacks wired to a recording ``Tracker`` so we can assert release
#    happens on every path. ──────────────────────────────────────────────


class _Tracker:
    """Records serial_claim / serial_release calls to verify they balance."""

    def __init__(self):
        self.claims: int = 0
        self.releases: int = 0

    @property
    def claimed(self) -> bool:
        """True if more claims than releases have happened so far."""
        return self.claims > self.releases


@pytest.fixture
def ctx_with_tracker(tmp_path):
    """A PluginContext whose serial_claim/serial_release update a Tracker.

    Doesn't need a real serial port -- ``serial_io`` only calls the
    claim/release callbacks; the I/O primitives inside the block are
    test-controlled.
    """
    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "echo_input": False,
        "line_ending": "\r",
        "encoding": "utf-8",
    }
    config_path = tmp_path / "test.cfg"
    config_path.write_text(json.dumps(cfg))

    output: list = []
    engine = ReplEngine(cfg, str(config_path),
                        lambda t, c=None: output.append((t, c)))
    api = EngineHandle(
        prefix="/",
        plugins=engine._plugins,
        in_script=lambda: engine.in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        coerce_type=ReplEngine._coerce_type,
        dispatch=engine.dispatch,
    )
    tracker = _Tracker()

    def _claim():
        tracker.claims += 1

    def _release():
        tracker.releases += 1

    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        engine=api,
        capabilities=CapabilitySet(serial_connected=True),
        io=IOHandle(
            _write=lambda t, c=None: None,
            _write_markup=lambda t: None,
        ),
        serial=SerialHandle(claim=_claim, release=_release),
    )
    return ctx, tracker


# ── Tests ────────────────────────────────────────────────────────────────────


class TestSerialIoContextManagerSafety:
    def test_normal_exit_releases(self, ctx_with_tracker):
        # Arrange
        ctx, tracker = ctx_with_tracker

        # Act
        with ctx.serial.io():
            assert tracker.claimed, "claim happened on enter"

        # Assert -- balanced after normal exit
        assert tracker.claims == 1, "exactly one claim"
        assert tracker.releases == 1, "exactly one release"
        assert tracker.claimed is False, "released after with-block exit"

    def test_exception_inside_block_still_releases(self, ctx_with_tracker):
        # Arrange
        ctx, tracker = ctx_with_tracker

        # Act -- raise inside the block; the with-statement must propagate
        with pytest.raises(ValueError):
            with ctx.serial.io():
                assert tracker.claimed, "claim happened before exception"
                raise ValueError("simulated send failure")

        # Assert -- release fired despite the exception
        assert tracker.claims == 1, "exactly one claim"
        assert tracker.releases == 1, "release still fired despite exception"
        assert tracker.claimed is False, "port released even on exception"

    def test_nested_blocks_each_balance(self, ctx_with_tracker):
        # Arrange -- nested with-blocks are unusual but should each balance.
        # The release-on-inner-exit happens before the outer block's release.
        ctx, tracker = ctx_with_tracker

        # Act
        with ctx.serial.io():
            with ctx.serial.io():
                pass
            # Inner block has released by now; outer is still claimed.
            assert tracker.releases == 1, "inner released"
            assert tracker.claimed is True, "outer still claimed"

        # Assert -- both blocks balanced
        assert tracker.claims == 2, "two claims (one per block)"
        assert tracker.releases == 2, "two releases (one per block)"
        assert tracker.claimed is False, "fully released after both exit"

    def test_exception_in_outer_block_after_inner_completed(
        self, ctx_with_tracker,
    ):
        # Arrange -- exception fires after the inner block releases but before
        # the outer block exits.  The outer release must still happen.
        ctx, tracker = ctx_with_tracker

        # Act
        with pytest.raises(RuntimeError):
            with ctx.serial.io():
                with ctx.serial.io():
                    pass
                # Inner already released; now outer raises.
                raise RuntimeError("late failure")

        # Assert
        assert tracker.claims == 2, "two claims"
        assert tracker.releases == 2, "both released despite outer raise"
        assert tracker.claimed is False, "port released even on late exception"


class TestEngineApiBareApiUnreachable:
    """The bare ``set_proto_active`` callback was removed from EngineHandle to
    force plugins through ``ctx.serial.io()``.  Verify the field doesn't
    exist on the dataclass so any plugin trying to call it gets
    AttributeError instead of silently working.
    """

    def test_engine_api_has_no_set_proto_active(self):
        # Arrange / Act
        api = EngineHandle(prefix="/", plugins={})

        # Assert
        assert not hasattr(api, "set_proto_active"), (
            "set_proto_active is removed; plugins must use ctx.serial.io()"
        )
        assert not hasattr(api, "set_serial_claimed"), (
            "set_serial_claimed is also not exposed -- the renamed bare API "
            "stays private to the engine for the same reason"
        )
